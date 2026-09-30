"""M2 component validation: actual module boundaries, no provider/config defaults.

Callers provide an admitted research State and runtime-backed LLMs for real runs.
This module never creates eligibility evidence or declares whole-M2 completion.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass

from skala_rag.agents.evidence_extraction import (
    extract_evidence,
    link_record,
    rag_segment,
    verify_provenance,
)
from skala_rag.agents.technology import TechnologyEvaluation, evaluate_technology
from skala_rag.contracts import (
    Candidate,
    EvaluationSnapshot,
    RetrievalBundle,
    RunInput,
    ToolResult,
)
from skala_rag.contracts.evaluation import EvaluationResult
from skala_rag.contracts.interfaces import Clock, StructuredLLM
from skala_rag.contracts.state import InvestmentState
from skala_rag.graph.reducers import merge_evidence
from skala_rag.graph.snapshot import freeze_snapshot
from skala_rag.prompts.evidence_extraction import PROMPT_VERSION
from skala_rag.scoring.catalog import ScoringPolicy
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM


class TraceInvalid(ValueError):
    """Static redacted boundary failure, not missing/zero or a successful trace."""


def payload_hash(snapshot: EvaluationSnapshot) -> str:
    return hashlib.sha256(
        json.dumps(
            snapshot.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def verify_trace(
    snapshot: EvaluationSnapshot,
    evaluation: TechnologyEvaluation,
    *,
    execution_mode: str,
) -> list[dict]:
    """Require successful evaluation citations to close against real retrieved pages."""
    context = {"execution_mode": execution_mode}
    snapshot = EvaluationSnapshot.model_validate(
        snapshot.model_dump(mode="python"), context=context
    )
    result = EvaluationResult.model_validate(
        evaluation.result.model_dump(mode="python"), context=context
    )
    if result.status != "success" or result.evaluation is None:
        raise TraceInvalid("technology evaluation failed")
    for field in (
        "run_id",
        "candidate_id",
        "snapshot_id",
        "policy_version",
        "evidence_revision",
        "evaluation_round",
    ):
        if getattr(result, field) != getattr(snapshot, field):
            raise TraceInvalid("evaluation/snapshot context mismatch")
    for item in snapshot.evidence.values():
        if verify_provenance(
            item, records=snapshot.retrieval_records, chunks=snapshot.chunks
        ):
            raise TraceInvalid("snapshot provenance invalid")
    cited = {
        (c.criterion_id, eid)
        for c in result.evaluation.criteria
        for eid in c.evidence_ids
    }
    if not cited or not evaluation.trace:
        raise TraceInvalid("no evidence was used by technology evaluation")
    if {(t.criterion_id, t.evidence_id) for t in evaluation.trace} != cited:
        raise TraceInvalid("criterion/trace citation mismatch")
    rows = []
    for t in evaluation.trace:
        item = snapshot.evidence.get(t.evidence_id)
        chunk = snapshot.chunks.get(t.chunk_id)
        record = snapshot.retrieval_records.get(t.retrieval_id)
        if (
            item is None
            or chunk is None
            or record is None
            or t.snapshot_id != snapshot.snapshot_id
            or item.source_id != chunk.source_id
            or t.criterion_id not in item.criterion_ids
            or t.evidence_id not in evaluation.allowed_evidence_ids
            or not any(
                p.method == "rag"
                and p.chunk_id == t.chunk_id
                and p.retrieval_id == t.retrieval_id
                for p in item.provenance
            )
        ):
            raise TraceInvalid("retrieval/evidence/criterion closure mismatch")
        rows.append(
            dict(
                run_id=snapshot.run_id,
                criterion_id=t.criterion_id,
                evidence_id=t.evidence_id,
                retrieval_id=t.retrieval_id,
                chunk_id=t.chunk_id,
                source_id=chunk.source_id,
                snapshot_id=snapshot.snapshot_id,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                locator=chunk.locator,
            )
        )
    return rows


@dataclass(frozen=True)
class M2Trace:
    state: InvestmentState
    snapshot: EvaluationSnapshot
    evaluation: TechnologyEvaluation
    receipt: dict


def run_technology_trace(
    *,
    candidate: Candidate,
    state: InvestmentState,
    run_input: RunInput,
    retrieval: ToolResult[RetrievalBundle],
    selected_chunk_ids: Sequence[str],
    extract_llm: StructuredLLM,
    evaluate_llm: StructuredLLM,
    policy: ScoringPolicy,
    rubric: Mapping[str, object],
    clock: Clock,
    run_id: str,
    index_version: str,
    schema_version: str,
    allowed_source_ids: Sequence[str],
    max_repairs: int,
) -> M2Trace:
    """A single injected retrieval → LLM extraction → freeze → Technology run.

    Validate existing admission State before spending LLM requests. Neither an
    unknown eligibility nor a model-generated eligible label is substituted.
    All writes are detached; failures cannot publish a success receipt or State.
    """
    kwargs = dict(
        run_id=run_id,
        index_version=index_version,
        schema_version=schema_version,
        allowed_source_ids=allowed_source_ids,
        industry_evidence_ids=[],
        clock=clock.now,
    )
    initial = freeze_snapshot(
        candidate.candidate_id, deepcopy(state), run_input, **kwargs
    )
    if any(
        verify_provenance(e, records=initial.retrieval_records, chunks=initial.chunks)
        for e in initial.evidence.values()
    ):
        raise TraceInvalid("admission State provenance invalid")
    if run_input.execution_mode == "live":
        for llm in (extract_llm, evaluate_llm):
            if (
                type(llm) is not RuntimeStructuredLLM
                or type(llm.transport) is not OpenAIResponsesAttempt
                or llm.runtime.policy.execution_mode != "live"
                or llm.call.run_id != run_id
                or llm.call.candidate_id != candidate.candidate_id
                or llm.readiness.missing
            ):
                raise TraceInvalid("real trace requires ready runtime-backed provider")
        if extract_llm.runtime.ledger is not evaluate_llm.runtime.ledger:
            raise TraceInvalid(
                "real extraction/evaluation must share one request ledger"
            )
    if retrieval.status != "ok" or retrieval.data is None:
        raise TraceInvalid("retrieval did not return successful chunks")
    records = [r for r in retrieval.retrieval_records if r.status == "ok"]
    if (
        len(records) != 1
        or records[0].run_id != run_id
        or records[0].candidate_id != candidate.candidate_id
    ):
        raise TraceInvalid("retrieval run/candidate history mismatch")
    if not selected_chunk_ids or len(set(selected_chunk_ids)) != len(
        selected_chunk_ids
    ):
        raise TraceInvalid("selected chunks must be unique and nonempty")
    by_id = {c.chunk_id: c for c in retrieval.data.chunks}
    if not set(selected_chunk_ids) <= set(by_id):
        raise TraceInvalid("selected chunk was not returned by retrieval")
    working = deepcopy(state)
    record = records[0].model_copy(deep=True)
    if any(
        r["retrieval_id"] == record.retrieval_id for r in working["retrieval_history"]
    ):
        raise TraceInvalid("retrieval ID already exists in input State")
    for kind, values, id_field in (
        ("sources", retrieval.data.sources.values(), "source_id"),
        ("chunks", retrieval.data.chunks, "chunk_id"),
    ):
        for value in values:
            key = getattr(value, id_field)
            if key in working[kind] and working[kind][key] != value.model_dump(
                mode="json"
            ):
                raise TraceInvalid("source/chunk snapshot replacement")
    # Validate the complete selected segments before the first model request.
    segments = [
        rag_segment(by_id[cid], retrieval.data, record, schema_version=schema_version)
        for cid in selected_chunk_ids
    ]
    criteria = sorted(
        c.criterion_id for c in policy.criteria if c.dimension == "technology"
    )
    rejected = []
    extracted_ids = []
    for segment in segments:
        extraction = extract_evidence(
            segment,
            llm=extract_llm,
            candidate=candidate,
            criterion_ids=criteria,
            as_of=run_input.as_of,
            schema_version=schema_version,
            execution_mode=run_input.execution_mode,
        )
        record = link_record(record, extraction, segment)
        working["evidence"] = merge_evidence(
            working["evidence"],
            {eid: e.model_dump(mode="json") for eid, e in extraction.evidence.items()},
        )
        extracted_ids.extend(extraction.evidence)
        rejected.extend(r.reason.value for r in extraction.rejected)
    if not extracted_ids:
        raise TraceInvalid("no source-validated evidence was extracted")
    for kind, values, id_field in (
        ("sources", retrieval.data.sources.values(), "source_id"),
        ("chunks", retrieval.data.chunks, "chunk_id"),
    ):
        for value in values:
            key = getattr(value, id_field)
            payload = value.model_dump(mode="json")
            if key in working[kind] and working[kind][key] != payload:
                raise TraceInvalid("source/chunk snapshot replacement")
            working[kind][key] = payload
    working["retrieval_history"].extend(
        r.model_dump(mode="json")
        for r in retrieval.retrieval_records
        if r.status != "ok"
    )
    working["retrieval_history"].append(record.model_dump(mode="json"))
    working["evidence_revisions"][candidate.candidate_id] += 1
    snapshot = freeze_snapshot(candidate.candidate_id, working, run_input, **kwargs)
    digest = payload_hash(snapshot)
    evaluated = evaluate_technology(
        snapshot,
        rubric=rubric,
        llm=evaluate_llm,
        policy=policy,
        clock=clock,
        schema_version=schema_version,
        execution_mode="fixture" if run_input.execution_mode == "fixture" else "real",
        max_repairs=max_repairs,
    )
    if payload_hash(snapshot) != digest:
        raise TraceInvalid("evaluation mutated frozen snapshot")
    rows = verify_trace(snapshot, evaluated, execution_mode=run_input.execution_mode)
    if not {r["evidence_id"] for r in rows} & set(extracted_ids):
        raise TraceInvalid("evaluation did not use newly extracted RAG evidence")
    receipt = dict(
        run_id=run_id,
        execution_mode=run_input.execution_mode,
        status="technology_component_trace_verified",
        whole_m2_verified=False,
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=digest,
        trace=rows,
        extracted_evidence_ids=sorted(set(extracted_ids)),
        rejected_claim_reasons=rejected,
        policy_version=policy.policy_version,
        rubric_version=rubric["rubric_version"],
        corpus_version=snapshot.corpus_version,
        index_version=snapshot.index_version,
        extraction_prompt_version=PROMPT_VERSION,
        evaluation_prompt_version=evaluated.prompt_version,
    )
    return M2Trace(working, snapshot, evaluated, receipt)
