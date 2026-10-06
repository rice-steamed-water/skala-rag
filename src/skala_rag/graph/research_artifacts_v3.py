"""Detached fixture provenance contract shared by the two existing consumers.

The producer stays outside State. These bindings are data, not live admission.
"""

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from pydantic import TypeAdapter

from skala_rag.agents.evidence_research import EvidenceResearch, ResearchOutcome
from skala_rag.contracts.candidates import EligibilityResult
from skala_rag.contracts.common import Contract
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode
from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.evidence import Evidence
from skala_rag.contracts.inputs import RunInput
from skala_rag.contracts.retrieval import RetrievalRecord
from skala_rag.contracts.sources import Chunk, Source
from skala_rag.contracts.state import create_initial_state
from skala_rag.contracts.tools import ToolBudget
from skala_rag.graph.reducers import (
    merge_errors,
    merge_evidence_with_changes,
    merge_result_maps,
    merge_sources,
)
from skala_rag.graph.snapshot import _build_snapshot
from skala_rag.rag.adapter import IndexedRetriever, index_identity
from skala_rag.rag.retrieval import source_date


class ArtifactResearchFailure(ValueError):
    """Original typed producer failures, never synthesized replacements."""

    def __init__(self, errors):
        self.errors = tuple(e.model_copy(deep=True) for e in errors)
        super().__init__("Research outcome failed")


def retain_outcome_errors_v3(errors, owned):
    """Keep validated original errors in the public run, including optional calls."""
    payloads = merge_errors(
        [e.model_dump(mode="json") for e in errors], owned["state"]["errors"]
    )
    return [WorkflowError.model_validate(e) for e in payloads]


@dataclass(frozen=True)
class CompanyResearchArtifactsV3:
    candidate_id: str
    run_id: str
    schema_version: str
    evidence_revision: int
    sources: Mapping[str, Source | dict]
    chunks: Mapping[str, Chunk | dict]
    records: Sequence[RetrievalRecord | dict]
    evidence: Mapping[str, Evidence | dict]


class _ObservedResearchV3:
    """Synchronous fixture observer; original adapter results remain untouched."""

    def __init__(self, producer, retrieve, receipts):
        self.producer = producer
        self.retrieve = retrieve
        self.receipts = receipts

    def run(self, candidate, gaps, budget):
        producer, retrieve = self.producer, self.retrieve
        if producer._retrieve is not retrieve:
            raise ValueError("pinned research retriever changed")
        if retrieve._runtime.policy.execution_mode != "fixture":
            raise ValueError("fixture research runtime binding mismatch")
        self.receipts.clear()

        def observe(request):
            if retrieve._runtime.policy.execution_mode != "fixture":
                raise ValueError("fixture research runtime binding mismatch")
            result = retrieve(request)
            # Copy at the adapter boundary, before research links Evidence or a
            # supplied outcome can reattribute a genuine runtime error ID.
            records = [r.model_dump(mode="json") for r in result.retrieval_records]
            receipt = dict(
                records=records,
                status=result.status,
                terminal_errors=[e.model_dump(mode="json") for e in result.errors],
                attempt_errors={
                    r["error_id"]: retrieve._runtime.error_history[
                        r["error_id"]
                    ].model_dump(mode="json")
                    for r in records
                    if r["error_id"] in retrieve._runtime.error_history
                },
                research_tool=producer._rag[0],
                research_required=producer._rag[1],
                research_initial=not gaps,
            )
            for record in records:
                self.receipts[record["retrieval_id"]] = receipt
            return result

        # Observation only: no mode, index, policy, budget or runtime edits. Both
        # consumers already invoke research synchronously. Restore even on error.
        producer._retrieve = observe
        try:
            return producer.run(candidate, gaps, budget)
        finally:
            producer._retrieve = retrieve


@dataclass(frozen=True)
class EvidenceResearchBindingV3:
    research: EvidenceResearch
    budget: ToolBudget
    run_input: RunInput
    run_id: str
    schema_version: str
    index_version: str
    allowed_source_ids: frozenset[str]
    industry_evidence_ids: frozenset[str]
    _index_identity: str | None = dataclass_field(default=None, init=False, repr=False)
    _index_tool_name: str | None = dataclass_field(default=None, init=False, repr=False)
    _attempt_receipts: dict = dataclass_field(
        default_factory=dict, init=False, repr=False
    )

    def pin(self, *, run_id, schema_version, policy_version):
        run = RunInput.model_validate(self.run_input)
        budget = ToolBudget.model_validate(self.budget)
        producer = self.research
        if (
            type(producer) is not EvidenceResearch
            or producer.execution_mode != "fixture"
            or run.execution_mode != "fixture"
            or run.schema_version != schema_version
            or self.run_id != run_id
            or self.schema_version != schema_version
            or run.policy_version != policy_version
            or budget.schema_version != schema_version
            or producer._run_id != run_id
            or producer._schema_version != schema_version
            or producer._corpus_version != run.corpus_version
            or producer._index_version != self.index_version
            or producer._as_of != run.as_of
            or set(producer._allowed_source_ids) != set(self.allowed_source_ids)
        ):
            raise ValueError("fixture research binding mismatch")
        pinned = EvidenceResearchBindingV3(
            producer,
            budget.model_copy(deep=True),
            run.model_copy(deep=True),
            run_id,
            schema_version,
            self.index_version,
            frozenset(self.allowed_source_ids),
            frozenset(self.industry_evidence_ids),
        )
        retrieve = producer._retrieve
        if type(retrieve) is IndexedRetriever:
            if retrieve._runtime.policy.execution_mode != "fixture":
                raise ValueError("fixture research runtime binding mismatch")
            identity = index_identity(retrieve._snapshot)
            if (
                identity != retrieve._identity
                or retrieve._snapshot.corpus_version != run.corpus_version
                or retrieve._snapshot.index_version != self.index_version
            ):
                raise ValueError("fixture research index binding mismatch")
            object.__setattr__(pinned, "_index_identity", identity)
            object.__setattr__(pinned, "_index_tool_name", retrieve._tool_name)
            object.__setattr__(
                pinned,
                "research",
                _ObservedResearchV3(producer, retrieve, pinned._attempt_receipts),
            )
        return pinned


def _validate_contract_generation(value, schema_version):
    """Pin typed nested DTOs, never interpret opaque JSON schema_version keys."""
    if isinstance(value, Contract):
        if value.schema_version != schema_version:
            raise ValueError("artifact nested schema generation mismatch")
        for field in type(value).model_fields:
            _validate_contract_generation(getattr(value, field), schema_version)
    elif isinstance(value, Mapping):
        for item in value.values():
            _validate_contract_generation(item, schema_version)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_contract_generation(item, schema_version)


def _payloads(artifacts, binding, cid):
    context = {"execution_mode": "fixture"}
    result = {}
    for name, model, identifier in (
        ("sources", Source, "source_id"),
        ("chunks", Chunk, "chunk_id"),
        ("evidence", Evidence, "evidence_id"),
    ):
        result[name] = {}
        for key, value in getattr(artifacts, name).items():
            payload = (
                value.model_dump(mode="json") if hasattr(value, "model_dump") else value
            )
            item = model.model_validate(payload, context=context)
            _validate_contract_generation(item, binding.schema_version)
            if (
                key != getattr(item, identifier)
                or item.schema_version != binding.schema_version
            ):
                raise ValueError("artifact identity mismatch")
            result[name][key] = item.model_dump(mode="json")
    records = [
        RetrievalRecord.model_validate(
            r.model_dump(mode="json") if hasattr(r, "model_dump") else r
        )
        for r in artifacts.records
    ]
    result["retrieval_history"] = [r.model_dump(mode="json") for r in records]
    if len({r.retrieval_id for r in records}) != len(records) or any(
        r.run_id != binding.run_id
        or r.schema_version != binding.schema_version
        or r.candidate_id not in (None, cid)
        for r in records
    ):
        raise ValueError("artifact record generation mismatch")
    for e in result["evidence"].values():
        if not (
            (e["scope"] == "company" and e["candidate_id"] == cid)
            or (
                e["scope"] == "industry"
                and e["candidate_id"] is None
                and e["evidence_id"] in binding.industry_evidence_ids
            )
        ):
            raise ValueError("artifact evidence candidate mismatch")
    for record in records:
        _validate_contract_generation(record, binding.schema_version)
        args = record.arguments_without_secrets
        for field, expected in (
            ("execution_mode", binding.run_input.execution_mode),
            ("corpus_version", binding.run_input.corpus_version),
            ("index_version", binding.index_version),
            ("as_of", binding.run_input.as_of.isoformat()),
        ):
            if field in args and args[field] != expected:
                raise ValueError("artifact record request mismatch")
        if "index_identity" in args:
            if (
                binding._index_identity is None
                or record.tool_name != binding._index_tool_name
            ):
                raise ValueError(
                    "artifact record index identity declared but unverifiable"
                )
            if args["index_identity"] != binding._index_identity:
                raise ValueError("artifact record index identity mismatch")
        if record.started_at > record.finished_at:
            raise ValueError("artifact record time order mismatch")
    return result


def initialize_artifacts_v3(seed, eligibility: EligibilityResult, candidate, binding):
    cid = candidate["candidate_id"]
    if type(seed) is not CompanyResearchArtifactsV3 or (
        seed.candidate_id != cid
        or seed.run_id != binding.run_id
        or seed.schema_version != binding.schema_version
        or type(seed.evidence_revision) is not int
        or seed.evidence_revision < 0
        or seed.evidence_revision != eligibility.evidence_revision
        or eligibility.as_of != binding.run_input.as_of
    ):
        raise ValueError("CompanyResearch artifact seed mismatch")
    incoming = _payloads(seed, binding, cid)
    _validate_admitted_payloads(incoming, binding, cid)
    if any(
        r["status"] in ("failed", "unavailable") or r["error_id"] is not None
        for r in incoming["retrieval_history"]
    ):
        raise ValueError(
            "CompanyResearch seed needs successful or empty original records"
        )
    state = create_initial_state(binding.run_input.model_dump(mode="json"))
    state.update(incoming)
    state.update(
        candidates=[deepcopy(candidate)],
        current_candidate_id=cid,
        eligibility_results={cid: eligibility.model_dump(mode="json")},
        evidence_revisions={cid: seed.evidence_revision},
        evaluation_rounds={cid: 0},
    )
    validate_artifacts_v3(state, binding, cid)
    return dict(state=state, batches=[])


def validate_artifacts_v3(state, binding, cid):
    # History stays admitted and dated even when supersession removes active facts.
    _validate_admitted_payloads(state, binding, cid)
    _validate_history_provenance(state, binding, cid)
    return _build_snapshot(
        cid,
        state,
        binding.run_input,
        run_id=binding.run_id,
        index_version=binding.index_version,
        schema_version=binding.schema_version,
        allowed_source_ids=binding.allowed_source_ids,
        industry_evidence_ids=binding.industry_evidence_ids,
    )


def active_evidence_v3(owned, binding, cid):
    """Use the existing snapshot's active view without pruning original State."""
    snapshot = validate_artifacts_v3(owned["state"], binding, cid)
    return {k: e.model_dump(mode="json") for k, e in snapshot.evidence.items()}


def _validate_history_provenance(state, binding, cid):
    """Preserve Source/Record/Chunk guards for inactive historical Evidence too."""
    history = {r["retrieval_id"]: r for r in state["retrieval_history"]}
    for item in state["evidence"].values():
        for path in item["provenance"]:
            record = history.get(path["retrieval_id"])
            if (
                record is None
                or record["run_id"] != binding.run_id
                or record["candidate_id"] not in (None, cid)
                or record["status"] != "ok"
                or item["source_id"] not in record["source_ids"]
                or item["evidence_id"] not in record["evidence_ids"]
            ):
                raise ValueError("historical Evidence record attribution mismatch")
            if path["chunk_id"] is not None:
                chunk = state["chunks"].get(path["chunk_id"])
                if (
                    path["chunk_id"] not in record["chunk_ids"]
                    or chunk is None
                    or chunk["source_id"] != item["source_id"]
                    or chunk["corpus_version"] != binding.run_input.corpus_version
                    or chunk["scope"] != item["scope"]
                    or (
                        item["scope"] == "company" and cid not in chunk["candidate_ids"]
                    )
                    or chunk["locator"] != item["locator"]
                    or item["excerpt"] not in chunk["text"]
                ):
                    raise ValueError("historical Evidence Chunk attribution mismatch")


def _original_attempt_error(record, outcome, records, error_map, binding, cid):
    """Resolve history only through this invocation's exact observed owning call."""
    receipt = binding._attempt_receipts.get(record["retrieval_id"])
    if receipt is None:
        raise ValueError("failed record omitted original error")
    originals = receipt["records"]
    ids = tuple(r["retrieval_id"] for r in originals)
    owners = [c for c in outcome.calls if record["retrieval_id"] in c.retrieval_ids]
    # The producer can keep a successful adapter result then fail extraction
    # before appending ToolCall. That rejected partial batch still has an exact
    # observed adapter owner, not an invented successful research call.
    extraction_partial = (
        not owners
        and outcome.status == "failed"
        and receipt["status"] in ("ok", "empty")
        and not receipt["terminal_errors"]
        and any(
            e.node == "evidence_research"
            and e.error_code in ("LLM_TIMEOUT", "LLM_FAILED", "LLM_OUTPUT_INVALID")
            for e in outcome.errors
        )
        and all(not any(rid in c.retrieval_ids for c in outcome.calls) for rid in ids)
    )
    if not extraction_partial and (
        len(owners) != 1
        or owners[0].retrieval_ids != ids
        or owners[0].tool != receipt["research_tool"]
        or owners[0].status != receipt["status"]
    ):
        raise ValueError("runtime attempt call ownership mismatch")
    for original in originals:
        supplied = records.get(original["retrieval_id"])
        # Research adds attribution and successful extracted Evidence IDs. All
        # other adapter fields and runtime call/attempt metadata stay original.
        if supplied is None or any(
            supplied[k] != v
            for k, v in original.items()
            if k not in ("arguments_without_secrets", "evidence_ids")
        ):
            raise ValueError("runtime attempt record mismatch")
        if original["status"] in ("failed", "unavailable") and (
            supplied["evidence_ids"] != original["evidence_ids"]
        ):
            raise ValueError("failed runtime attempt claims Evidence")
        args = supplied["arguments_without_secrets"]
        if (
            args.get("research_tool") != receipt["research_tool"]
            or args.get("research_required") is not receipt["research_required"]
            or args.get("research_initial") is not receipt["research_initial"]
            or args.get("research_gap_id") not in {g.gap_id for g in outcome.gaps}
        ):
            raise ValueError("runtime attempt research attribution mismatch")
        if any(
            args.get(k) != v
            for k, v in original["arguments_without_secrets"].items()
            if k not in ("execution_mode", "index_identity")
        ):
            raise ValueError("runtime attempt metadata mismatch")
    # The runtime's terminal errors must still be in ResearchOutcome.errors.
    # History can never repair an omitted or forged terminal error.
    if any(error_map.get(e["error_id"]) != e for e in receipt["terminal_errors"]):
        raise ValueError("runtime terminal original error omitted or changed")
    payload = receipt["attempt_errors"].get(record["error_id"])
    if payload is None:
        raise ValueError("runtime attempt original error unavailable")
    error = WorkflowError.model_validate(payload)
    current = binding.research.retrieve._runtime.error_history.get(error.error_id)
    spec = ERROR_SPECS[ErrorCode(error.error_code)]
    if (
        current is None
        or current.model_dump(mode="json") != payload
        or error.error_id != record["error_id"]
        or error.run_id != record["run_id"]
        or error.run_id != binding.run_id
        or error.candidate_id != cid
        or error.candidate_id != record["candidate_id"]
        or error.schema_version != binding.schema_version
        or error.schema_version != record["schema_version"]
        or error.node != binding._index_tool_name
        or record["tool_name"] != binding._index_tool_name
        or error.attempt < 1
        or error.attempt != record["arguments_without_secrets"].get("attempt")
        or error.timestamp.isoformat()
        != RetrievalRecord.model_validate(record).finished_at.isoformat()
        or spec.tool_status != record["status"]
        or error.retryable != spec.retryable
    ):
        raise ValueError("runtime attempt error attribution mismatch")
    if error.error_id in error_map:
        if error_map[error.error_id] != payload:
            raise ValueError("runtime original error changed")
    elif not error.retryable or error.error_id in {
        e["error_id"] for e in receipt["terminal_errors"]
    }:
        raise ValueError("nonhistorical runtime error omitted")
    return error


def _validate_outcome_carriers(outcome, incoming, binding, cid):
    errors = [
        WorkflowError.model_validate(e.model_dump(mode="json")) for e in outcome.errors
    ]
    if any(
        e.run_id != binding.run_id
        or e.schema_version != binding.schema_version
        or e.candidate_id != cid
        for e in errors
    ):
        raise ValueError("research error generation mismatch")
    error_payloads = merge_errors([], [e.model_dump(mode="json") for e in errors])
    error_map = {e["error_id"]: e for e in error_payloads}
    records = {r["retrieval_id"]: r for r in incoming["retrieval_history"]}
    gap_ids = {g.gap_id for g in outcome.gaps}
    if (
        len(gap_ids) != len(outcome.gaps)
        or any(
            g.candidate_id != cid or g.schema_version != binding.schema_version
            for g in outcome.gaps
        )
        or type(outcome.skipped) is not int
        or outcome.skipped < 0
        or len(outcome.calls) > binding.budget.max_calls
    ):
        raise ValueError("research batch accounting mismatch")
    attempt_errors = []
    for record in records.values():
        failed = record["status"] in ("failed", "unavailable")
        error_id = record["error_id"]
        if failed:
            if (
                record["retrieval_id"] in binding._attempt_receipts
                or error_id not in error_map
            ):
                original = _original_attempt_error(
                    record, outcome, records, error_map, binding, cid
                )
                if error_id not in error_map:
                    attempt_errors.append(original)
                payload = original.model_dump(mode="json")
            else:
                payload = error_map[error_id]
            if (
                ERROR_SPECS[ErrorCode(payload["error_code"])].tool_status
                != record["status"]
            ):
                raise ValueError("record error status mismatch")
        elif error_id is not None:
            raise ValueError("nonfailed record claims error")
        codes = record["arguments_without_secrets"].get("research_error_codes", [])
        if any(code not in {e.error_code for e in errors} for code in codes):
            raise ValueError("record error carrier incomplete")
    successful = outcome.status in ("ok", "empty")
    owned_record_ids = set()
    for call in outcome.calls:
        if (
            call.gap_id not in gap_ids
            or any(rid not in records for rid in call.retrieval_ids)
            or any(
                code not in {e.error_code for e in errors} for code in call.error_codes
            )
        ):
            raise ValueError("research call carrier incomplete")
        if successful:
            ids = set(call.retrieval_ids)
            if len(ids) != len(call.retrieval_ids) or ids & owned_record_ids:
                raise ValueError("research record has duplicate call ownership")
            owned_record_ids.update(ids)
            if call.status in ("ok", "empty") and (
                not call.retrieval_ids
                or records[call.retrieval_ids[-1]]["status"] != call.status
            ):
                raise ValueError("research call terminal record mismatch")
        for rid in call.retrieval_ids:
            args = records[rid]["arguments_without_secrets"]
            if any(
                args.get(k) != v
                for k, v in (
                    ("research_tool", call.tool),
                    ("research_required", call.required),
                    ("research_gap_id", call.gap_id),
                    ("research_initial", outcome.initial),
                )
            ):
                raise ValueError("research call record mismatch")
        if call.status in ("failed", "unavailable") and not call.error_codes:
            raise ValueError("failed call omitted original error")
        if (
            call.required
            and call.status in ("failed", "unavailable")
            and outcome.status not in ("failed", "unavailable")
        ):
            raise ValueError("required failed call promoted")
    # Extraction can fail after _keep stores records but before appending a call.
    # Such failed outcomes retain original errors/partial inputs, not admitted facts.
    if successful and owned_record_ids != set(records):
        raise ValueError("successful research outcome has orphan records")
    if outcome.status in ("failed", "unavailable") and not errors:
        raise ValueError("failed ResearchOutcome omitted original errors")
    if (outcome.status == "ok" and not outcome.evidence) or (
        outcome.status == "empty" and outcome.evidence
    ):
        raise ValueError("outcome status/evidence mismatch")
    return errors, attempt_errors


def _validate_admitted_payloads(incoming, binding, cid):
    for source in incoming["sources"].values():
        item = Source.model_validate(source, context={"execution_mode": "fixture"})
        if (
            item.source_id not in binding.allowed_source_ids
            or source_date(item) > binding.run_input.as_of
        ):
            raise ValueError("inadmissible Source")
    for item in incoming["chunks"].values():
        if (
            item["source_id"] not in incoming["sources"]
            or item["corpus_version"] != binding.run_input.corpus_version
            or (item["scope"] == "company" and cid not in item["candidate_ids"])
        ):
            raise ValueError("inadmissible Chunk")
    for item in incoming["evidence"].values():
        if item["source_id"] not in incoming["sources"]:
            raise ValueError("outcome omitted Evidence Source")
        if any(
            item[field] is not None
            and item[field] > binding.run_input.as_of.isoformat()
            for field in ("event_date", "value_as_of")
        ):
            raise ValueError("inadmissible Evidence cutoff")


def consume_outcome_v3(owned, outcome, binding, cid, *, initial):
    if type(outcome) is not ResearchOutcome or outcome.initial is not initial:
        raise ValueError("explicit ResearchOutcome required")
    # Retained inputs are JSON diagnostics until every carrier and closure validates.
    adapter = TypeAdapter(ResearchOutcome)
    batch = adapter.dump_python(outcome, mode="json", warnings=False)
    batch = json.loads(json.dumps(batch, allow_nan=False))
    batch["admission"] = "rejected_input"
    owned["batches"].append(batch)
    outcome = adapter.validate_python(
        {k: v for k, v in batch.items() if k != "admission"},
        context={"execution_mode": "fixture"},
    )
    incoming = _payloads(outcome, binding, cid)
    errors, attempt_errors = _validate_outcome_carriers(outcome, incoming, binding, cid)
    batch["attempt_errors"] = [e.model_dump(mode="json") for e in attempt_errors]
    if outcome.status in ("failed", "unavailable"):
        owned["state"]["errors"] = merge_errors(
            owned["state"]["errors"], [e.model_dump(mode="json") for e in errors]
        )
        raise ArtifactResearchFailure(errors)
    _validate_admitted_payloads(incoming, binding, cid)
    state = deepcopy(owned["state"])
    state["errors"] = merge_errors(
        state["errors"], [e.model_dump(mode="json") for e in errors]
    )
    state["sources"] = merge_sources(state["sources"], incoming["sources"])
    state["chunks"] = merge_result_maps(state["chunks"], incoming["chunks"])
    history = {r["retrieval_id"]: r for r in state["retrieval_history"]}
    history = merge_result_maps(
        history, {r["retrieval_id"]: r for r in incoming["retrieval_history"]}
    )
    state["retrieval_history"] = list(history.values())
    state["evidence"], changed = merge_evidence_with_changes(
        state["evidence"], incoming["evidence"]
    )
    if changed:
        state["evidence_revisions"][cid] += 1
    validate_artifacts_v3(state, binding, cid)
    owned["state"] = state
    batch["admission"] = "admitted"
    return changed
