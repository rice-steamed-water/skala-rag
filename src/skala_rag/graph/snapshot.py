"""Freeze validated JSON State payloads without external I/O or Graph routing."""

from collections.abc import Callable, Collection
from datetime import date, datetime

from pydantic import ValidationError

from skala_rag.contracts.candidates import EligibilityResult
from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.evaluation import EvaluationSnapshot
from skala_rag.contracts.evidence import Evidence
from skala_rag.contracts.ids import snapshot_id
from skala_rag.contracts.inputs import RunInput
from skala_rag.contracts.retrieval import RetrievalRecord
from skala_rag.contracts.sources import Chunk, Source
from skala_rag.contracts.state import InvestmentState
from skala_rag.graph.reducers import merge_errors


class SnapshotInvalid(ValueError):
    """Redacted failure; the caller routes this candidate to archive/advance."""

    error_code = "SNAPSHOT_INVALID"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SnapshotInvalid(message)


def _count(value: int) -> int:
    _require(type(value) is int and value >= 0, "Invalid snapshot counter")
    return value


def _source_date(source: Source) -> date:
    value = source.published_at or source.retrieved_at
    return value.date() if isinstance(value, datetime) else value


def _build_snapshot(
    candidate_id: str,
    state: InvestmentState,
    run_input: RunInput,
    *,
    run_id: str,
    index_version: str,
    schema_version: str,
    allowed_source_ids: Collection[str],
    industry_evidence_ids: Collection[str],
) -> EvaluationSnapshot:
    context = {"execution_mode": run_input.execution_mode}
    evidence = {}
    sources = {}
    allowed = set(allowed_source_ids)
    industry = set(industry_evidence_ids)
    for key, payload in state.get("evidence", {}).items():
        # Unrelated companies/industry claims do not enter this candidate's boundary.
        relevant = payload.get("candidate_id") == candidate_id or key in industry
        if not relevant:
            continue
        item = Evidence.model_validate(payload, context=context)
        _require(key == item.evidence_id, "Evidence map key mismatch")
        _require(
            (item.scope == "company" and item.candidate_id == candidate_id)
            or (
                item.scope == "industry"
                and item.candidate_id is None
                and key in industry
            ),
            "Evidence scope or company attribution mismatch",
        )
        _require(item.source_id in state.get("sources", {}), "Missing evidence Source")
        source = Source.model_validate(
            state["sources"][item.source_id], context=context
        )
        _require(source.source_id == item.source_id, "Source map key mismatch")
        if (
            source.source_id not in allowed
            or _source_date(source) > run_input.as_of
            or (item.event_date is not None and item.event_date > run_input.as_of)
            or (item.value_as_of is not None and item.value_as_of > run_input.as_of)
        ):
            continue
        evidence[key] = item
        sources[source.source_id] = source

    # Only admitted corrections can invalidate historical evidence.
    superseded = {item.supersedes for item in evidence.values() if item.supersedes}
    for key in evidence:
        seen = {key}
        previous = evidence[key].supersedes
        while previous in evidence:
            _require(previous not in seen, "Cyclic supersession")
            seen.add(previous)
            previous = evidence[previous].supersedes

    invalid = set(superseded)
    for item in evidence.values():
        _require(
            all(
                key in state.get("evidence", {}) for key in item.supporting_evidence_ids
            ),
            "Missing supporting Evidence",
        )
    # Propagate invalidation through arbitrarily long derived chains.
    while True:
        newly_invalid = {
            key
            for key, item in evidence.items()
            if any(
                dep in invalid or dep not in evidence
                for dep in item.supporting_evidence_ids
            )
        }
        if newly_invalid <= invalid:
            break
        invalid.update(newly_invalid)
    active = {key: item for key, item in evidence.items() if key not in invalid}

    eligibility = EligibilityResult.model_validate(
        state.get("eligibility_results", {}).get(candidate_id), context=context
    )
    revision = _count(state.get("evidence_revisions", {}).get(candidate_id, 0))
    _require(
        eligibility.run_id == run_id
        and eligibility.candidate_id == candidate_id
        and eligibility.policy_version == run_input.policy_version
        and eligibility.as_of == run_input.as_of
        and eligibility.evidence_revision <= revision
        and eligibility.status == "eligible",
        "Eligibility context mismatch or not eligible",
    )
    _require(
        bool(eligibility.evidence_ids)
        and all(key in active for key in eligibility.evidence_ids),
        "Eligibility evidence invalidated or unavailable",
    )

    records = {}
    chunks = {}
    visiting = set()
    visited = set()

    def visit(key: str) -> None:
        _require(key not in visiting, "Cyclic supporting Evidence")
        if key in visited:
            return
        visiting.add(key)
        item = active[key]
        for dep in item.supporting_evidence_ids:
            _require(dep in active, "Unavailable supporting Evidence")
            visit(dep)
        for conflict in item.conflicts_with:
            _require(conflict in active, "Unavailable conflicting Evidence")
        visiting.remove(key)
        visited.add(key)

    history = {}
    for payload in state.get("retrieval_history", []):
        key = payload.get("retrieval_id")
        _require(
            key not in history or history[key] == payload, "Retrieval ID collision"
        )
        history[key] = payload
    for key, item in active.items():
        visit(key)
        for path in item.provenance:
            _require(path.retrieval_id in history, "Missing RetrievalRecord")
            record = RetrievalRecord.model_validate(history[path.retrieval_id])
            _require(
                record.run_id == run_id
                and record.candidate_id in (None, candidate_id)
                and record.status == "ok"
                and item.source_id in record.source_ids
                and item.evidence_id in record.evidence_ids,
                "RetrievalRecord attribution mismatch",
            )
            _require(
                record.started_at <= record.finished_at,
                "RetrievalRecord time order mismatch",
            )
            for field, expected in (
                ("corpus_version", run_input.corpus_version),
                ("index_version", index_version),
                ("as_of", run_input.as_of.isoformat()),
            ):
                arguments = record.arguments_without_secrets
                _require(
                    field not in arguments or arguments[field] == expected,
                    "RetrievalRecord request context mismatch",
                )
            if path.chunk_id is not None:
                _require(
                    path.chunk_id in record.chunk_ids, "Chunk not returned by retrieval"
                )
                _require(path.chunk_id in state.get("chunks", {}), "Missing Chunk")
                chunk = Chunk.model_validate(
                    state["chunks"][path.chunk_id], context=context
                )
                _require(
                    chunk.chunk_id == path.chunk_id
                    and chunk.source_id == item.source_id
                    and chunk.corpus_version == run_input.corpus_version
                    and chunk.scope == item.scope
                    and (
                        item.scope == "industry" or candidate_id in chunk.candidate_ids
                    )
                    and chunk.locator == item.locator
                    and item.excerpt in chunk.text,
                    "Chunk source, scope, locator or excerpt mismatch",
                )
                chunks[chunk.chunk_id] = chunk
            records[record.retrieval_id] = record

    current_round = _count(state.get("evaluation_rounds", {}).get(candidate_id, 0))
    identifier = snapshot_id(
        run_id, candidate_id, current_round + 1, revision, run_input.policy_version
    )
    _require(identifier not in state.get("snapshots", {}), "Snapshot ID already stored")
    return EvaluationSnapshot.model_validate(
        dict(
            schema_version=schema_version,
            snapshot_id=identifier,
            run_id=run_id,
            candidate_id=candidate_id,
            evaluation_round=current_round + 1,
            evidence_revision=revision,
            policy_version=run_input.policy_version,
            corpus_version=run_input.corpus_version,
            index_version=index_version,
            as_of=run_input.as_of,
            evidence_ids=sorted(active),
            evidence=active,
            sources={
                item.source_id: sources[item.source_id] for item in active.values()
            },
            chunks=chunks,
            retrieval_records=records,
        ),
        context=context,
    )


def freeze_snapshot(
    candidate_id: str,
    state: InvestmentState,
    run_input: RunInput,
    *,
    run_id: str,
    index_version: str,
    schema_version: str,
    allowed_source_ids: Collection[str],
    industry_evidence_ids: Collection[str],
    clock: Callable[[], datetime],
) -> EvaluationSnapshot:
    """Store a detached snapshot and increment the candidate round on success.

    Admission lists come from the collector/approved manifest, never defaults.
    Failure records a redacted WorkflowError and raises SnapshotInvalid; rounds
    and snapshots remain unchanged. Archive/advance is the caller's responsibility.
    The returned DTO and stored JSON are separate copies, not live State references.
    """
    try:
        validated_input = RunInput.model_validate(run_input)
        _require(
            state.get("run_input") == validated_input.model_dump(mode="json"),
            "RunInput differs from State",
        )
        snapshot = _build_snapshot(
            candidate_id,
            state,
            validated_input,
            run_id=run_id,
            index_version=index_version,
            schema_version=schema_version,
            allowed_source_ids=allowed_source_ids,
            industry_evidence_ids=industry_evidence_ids,
        )
    except (SnapshotInvalid, ValidationError) as exc:
        # Never log Pydantic's input payloads, excerpts or external URLs.
        message = (
            str(exc) if isinstance(exc, SnapshotInvalid) else "Invalid snapshot DTO"
        )
        attempt = 1 + sum(
            error.get("node") == "freeze_snapshot"
            and error.get("candidate_id") == candidate_id
            and error.get("run_id") == run_id
            for error in state.get("errors", [])
        )
        error = WorkflowError(
            schema_version=schema_version,
            error_id=f"snapshot-error:{run_id}:{candidate_id}:{attempt}",
            run_id=run_id,
            candidate_id=candidate_id,
            node="freeze_snapshot",
            error_code="SNAPSHOT_INVALID",
            message_redacted=message,
            retryable=False,
            attempt=attempt,
            timestamp=clock(),
        )
        state["errors"] = merge_errors(
            state.get("errors", []), [error.model_dump(mode="json")]
        )
        raise SnapshotInvalid(message) from None
    stored = snapshot.model_dump(mode="json")
    state["snapshots"] = {**state.get("snapshots", {}), snapshot.snapshot_id: stored}
    state["evaluation_rounds"] = {
        **state.get("evaluation_rounds", {}),
        candidate_id: snapshot.evaluation_round,
    }
    return snapshot.model_copy(deep=True)
