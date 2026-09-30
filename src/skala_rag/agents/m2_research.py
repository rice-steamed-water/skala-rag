"""Company Research → persisted admission State → optional #62 Technology trace.

Artifacts are private, gitignored local outputs. Eligibility is computed from
the returned observations, never supplied as an eligible flag by the caller.
"""

import json
import os
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from skala_rag.agents.eligibility import check_eligibility
from skala_rag.agents.evidence_extraction import verify_provenance
from skala_rag.agents.m2_trace import M2Trace, TraceInvalid
from skala_rag.contracts import (
    Candidate,
    CompanyResearchBundle,
    RunInput,
    ToolBudget,
    ToolResult,
)
from skala_rag.contracts.interfaces import ResearchCompany
from skala_rag.contracts.state import InvestmentState, create_initial_state
from skala_rag.tools.source_fetch import check_as_of


@dataclass(frozen=True)
class ResearchState:
    state: InvestmentState
    receipt: dict


def assemble_research_state(
    *,
    candidate: Candidate,
    run_input: RunInput,
    run_id: str,
    result: ToolResult[CompanyResearchBundle],
) -> ResearchState:
    """Validate provider output, link actual evidence paths and run #18 admission."""
    context = {"execution_mode": run_input.execution_mode}
    candidate = Candidate.model_validate(
        candidate.model_dump(mode="python"), context=context
    )
    result = ToolResult[CompanyResearchBundle].model_validate(
        result.model_dump(mode="python"), context=context
    )
    if not run_id.strip() or candidate.country not in run_input.countries:
        raise TraceInvalid("research candidate/run scope mismatch")
    state = create_initial_state(run_input.model_dump(mode="json"))
    cid = candidate.candidate_id
    state["candidates"] = [candidate.model_dump(mode="json")]
    state["current_candidate_id"] = cid
    state["candidate_status"][cid] = "researching"
    state["evidence_revisions"][cid] = 0
    state["research_retry_count"][cid] = 0
    records = {}
    for record in result.retrieval_records:
        if (
            record.retrieval_id in records
            or record.run_id != run_id
            or record.candidate_id != cid
            or record.started_at > record.finished_at
        ):
            raise TraceInvalid("research history attribution mismatch")
        records[record.retrieval_id] = record.model_copy(deep=True)
    if any(
        e.run_id != run_id or e.candidate_id not in (None, cid) for e in result.errors
    ):
        raise TraceInvalid("research error attribution mismatch")
    state["errors"] = [e.model_dump(mode="json") for e in result.errors]
    receipt = dict(
        run_id=run_id,
        candidate_id=cid,
        execution_mode=run_input.execution_mode,
        research_status=result.status,
        eligibility_status=None,
        policy_version=run_input.policy_version,
        corpus_version=run_input.corpus_version,
        whole_m2_verified=False,
        technology_status="not_started",
    )
    if result.status in ("failed", "unavailable"):
        state["candidate_status"][cid] = "failed"
        state["workflow_status"] = "failed"
        state["run_outcome"] = "technical_failure"
    else:
        if result.data is None:
            raise TraceInvalid("successful research omitted bundle")
        bundle = result.data
        if (
            bundle.profile.candidate_id != cid
            or bundle.profile.as_of != run_input.as_of
        ):
            raise TraceInvalid("research profile attribution mismatch")
        for sid, source in bundle.sources.items():
            if sid != source.source_id:
                raise TraceInvalid("research source map key mismatch")
            if not check_as_of(source, run_input.as_of).admitted:
                raise TraceInvalid("research source is after the run cutoff")
        for eid, evidence in bundle.evidence.items():
            if (
                eid != evidence.evidence_id
                or evidence.candidate_id != cid
                or evidence.scope != "company"
                or evidence.source_id not in bundle.sources
                or (
                    evidence.event_date is not None
                    and evidence.event_date > run_input.as_of
                )
                or (
                    evidence.value_as_of is not None
                    and evidence.value_as_of > run_input.as_of
                )
            ):
                raise TraceInvalid("research evidence attribution mismatch")
            # CompanyResearch's per-request records precede extraction. Link only
            # actual paths to successful requests containing the observed Source.
            for path in evidence.provenance:
                record = records.get(path.retrieval_id)
                if (
                    path.method not in ("web", "api")
                    or path.chunk_id is not None
                    or record is None
                    or record.status != "ok"
                    or evidence.source_id not in record.source_ids
                ):
                    raise TraceInvalid(
                        "research evidence has no actual provider request"
                    )
                record.evidence_ids = sorted(set(record.evidence_ids) | {eid})
            if verify_provenance(evidence, records=records, chunks={}):
                raise TraceInvalid("research evidence provenance invalid")
        eligibility = check_eligibility(
            bundle.profile,
            bundle.evidence,
            {"policy_version": run_input.policy_version},
            run_id=run_id,
            evidence_revision=1,
        )
        state["company_profiles"][cid] = bundle.profile.model_dump(mode="json")
        state["sources"] = {
            sid: s.model_dump(mode="json") for sid, s in bundle.sources.items()
        }
        state["evidence"] = {
            eid: e.model_dump(mode="json") for eid, e in bundle.evidence.items()
        }
        state["evidence_revisions"][cid] = 1
        state["eligibility_results"][cid] = eligibility.model_dump(mode="json")
        state["candidate_status"][cid] = {
            "eligible": "evaluating",
            "unknown": "eligibility_unknown",
            "ineligible": "ineligible",
        }[eligibility.status]
        receipt.update(
            eligibility_status=eligibility.status, reason_codes=eligibility.reason_codes
        )
    state["retrieval_history"] = [r.model_dump(mode="json") for r in records.values()]
    receipt["retrieval_ids"] = list(records)
    receipt["source_ids"] = sorted(state["sources"])
    receipt["evidence_ids"] = sorted(state["evidence"])
    receipt["error_codes"] = [e.error_code for e in result.errors]
    receipt["providers"] = {
        name: status
        for record in records.values()
        for name, status in record.arguments_without_secrets.get(
            "providers", {}
        ).items()
    }
    return ResearchState(state, receipt)


def save_research_state(
    research: ResearchState,
    *,
    root: Path,
    output_dir: Path,
    secret_values: Sequence[str],
) -> None:
    """Save private artifacts; reject path escape, overwrite and known secrets."""
    outputs = (root / "outputs").resolve()
    target = output_dir.resolve()
    if target == outputs or not target.is_relative_to(outputs) or target.exists():
        raise TraceInvalid("research artifacts require a new directory under outputs")
    payloads = {
        "research-state.json": json.dumps(
            research.state, ensure_ascii=False, indent=2, allow_nan=False
        ),
        "research-receipt.json": json.dumps(
            research.receipt, ensure_ascii=False, indent=2, allow_nan=False
        ),
    }
    for payload in payloads.values():
        if any(
            secret and (secret in payload or json.dumps(secret)[1:-1] in payload)
            for secret in secret_values
        ):
            raise TraceInvalid("research artifact contains a configured secret")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(mode=0o700)
    # Receipt is the completion marker, written only after the State is complete.
    for filename, payload in payloads.items():
        fd = os.open(target / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())


def run_research_to_trace(
    *,
    candidate: Candidate,
    run_input: RunInput,
    run_id: str,
    research_tool: ResearchCompany,
    budget: ToolBudget,
    root: Path,
    output_dir: Path,
    secret_values: Sequence[str],
    technology: Callable[[InvestmentState], M2Trace] | None,
    runtime_observations: Mapping[str, object] | None = None,
) -> ResearchState | M2Trace:
    """Always save actual research before attempting a gated Technology trace."""
    run_input = RunInput.model_validate(run_input.model_dump(mode="python"))
    candidate = Candidate.model_validate(
        candidate.model_dump(mode="python"),
        context={"execution_mode": run_input.execution_mode},
    )
    if not run_id.strip() or candidate.country not in run_input.countries:
        raise TraceInvalid("research candidate/run scope mismatch")
    # Reject artifact mistakes before network/model requests.
    target = output_dir.resolve()
    outputs = (root / "outputs").resolve()
    if target == outputs or not target.is_relative_to(outputs) or target.exists():
        raise TraceInvalid("research artifacts require a new directory under outputs")
    result = research_tool(candidate, budget)
    research = assemble_research_state(
        candidate=candidate, run_input=run_input, run_id=run_id, result=result
    )
    research.receipt["runtime"] = deepcopy(dict(runtime_observations or {}))
    research.receipt["technology_status"] = (
        "ready"
        if research.receipt["eligibility_status"] == "eligible"
        and technology is not None
        else "blocked_technology_not_configured"
        if research.receipt["eligibility_status"] == "eligible"
        else "blocked_admission"
    )
    save_research_state(
        research, root=root, output_dir=output_dir, secret_values=secret_values
    )
    if research.receipt["eligibility_status"] != "eligible" or technology is None:
        return research
    # The persisted admission State stays intact if the trace fails.
    return technology(deepcopy(research.state))
