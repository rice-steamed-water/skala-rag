"""Company Research → persisted admission State → optional #62 Technology trace.

Artifacts are private, gitignored local outputs. Eligibility is computed from
the returned observations, never supplied as an eligible flag by the caller.
"""

import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from skala_rag.agents.eligibility import check_eligibility
from skala_rag.agents.evidence_extraction import verify_provenance
from skala_rag.agents.m2_trace import M2Trace, TraceInvalid
from skala_rag.contracts import (
    Candidate,
    CompanyResearchBundle,
    RunInput,
    Source,
    ToolBudget,
    ToolResult,
)
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode
from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.interfaces import ResearchCompany
from skala_rag.contracts.state import InvestmentState, create_initial_state
from skala_rag.tools.company_research import EXTRACTOR_TOOL_ERRORS, TOOL_NAME
from skala_rag.tools.source_fetch import check_as_of, snapshot_source_id


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
        for record in records.values():
            args = record.arguments_without_secrets
            # Reserved producer metadata is untyped JSON: validate its shape
            # before a malformed marker can masquerade as an unmarked failure.
            providers = args.get("providers", {})
            if not isinstance(providers, dict):
                raise TraceInvalid("research summary providers invalid")
            for metadata in providers.values():
                if not isinstance(metadata, dict):
                    raise TraceInvalid("research summary provider invalid")
                notes = metadata.get("notes")
                if not isinstance(notes, list) or any(
                    not isinstance(note, str) for note in notes
                ):
                    raise TraceInvalid("research summary provider notes invalid")
            provider = providers.get("official-homepage")
            if (
                provider is not None
                and provider.get("required") is True
                and any(
                    note.startswith("EXTRACTOR_FAILED:") for note in provider["notes"]
                )
                and ("extractor_error" not in args or "retained_sources" not in args)
            ):
                raise TraceInvalid("required extractor failure metadata omitted")
            if "retained_sources" not in args and "extractor_error" not in args:
                continue
            if (
                record.tool_name != TOOL_NAME
                or record.status != result.status
                or record.error_id not in {e.error_id for e in result.errors}
                or args.get("as_of") != run_input.as_of.isoformat()
            ):
                raise TraceInvalid("retained research summary attribution mismatch")
            payloads = args.get("retained_sources", {})
            if not isinstance(payloads, dict) or set(payloads) != set(
                record.source_ids
            ):
                raise TraceInvalid("retained research source map mismatch")
            for sid, payload in payloads.items():
                try:
                    source = Source.model_validate(payload, context=context)
                except ValidationError:
                    raise TraceInvalid(
                        "retained research source payload invalid"
                    ) from None
                if (
                    sid != source.source_id
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", source.content_hash)
                    or sid
                    != snapshot_source_id(
                        source.url or source.local_path, source.content_hash
                    )
                    or not check_as_of(source, run_input.as_of).admitted
                    or not any(
                        fetch.tool_name.startswith(f"{TOOL_NAME}/")
                        and fetch.status == "ok"
                        and fetch.error_id is None
                        and sid in fetch.source_ids
                        and fetch.started_at <= source.retrieved_at <= fetch.finished_at
                        for fetch in records.values()
                    )
                    or (sid in state["sources"] and state["sources"][sid] != payload)
                ):
                    raise TraceInvalid(
                        "retained research source has no valid successful fetch"
                    )
                state["sources"][sid] = source.model_dump(mode="json")
            if "extractor_error" in args:
                try:
                    original = WorkflowError.model_validate(args["extractor_error"])
                    code = ErrorCode(original.error_code)
                except (ValidationError, ValueError):
                    raise TraceInvalid("retained extractor error invalid") from None
                boundary = next(
                    e for e in result.errors if e.error_id == record.error_id
                )
                providers = args.get("providers")
                if not isinstance(providers, dict):
                    raise TraceInvalid("retained extractor providers invalid")
                provider = providers.get("official-homepage")
                if not isinstance(provider, dict):
                    raise TraceInvalid("retained extractor provider invalid")
                if (
                    original.run_id != run_id
                    or original.candidate_id != cid
                    or original.error_id != boundary.error_id
                    or original.node != boundary.node
                    or original.attempt != boundary.attempt
                    or original.timestamp != boundary.timestamp
                    or original.retryable != ERROR_SPECS[code].retryable
                    or EXTRACTOR_TOOL_ERRORS.get(code, code).value
                    != boundary.error_code
                    or provider.get("required") is not True
                    or provider.get("status") != result.status
                    or provider.get("error_code") != boundary.error_code
                    or provider.get("notes") != [f"EXTRACTOR_FAILED:{code.value}"]
                ):
                    raise TraceInvalid("retained extractor error attribution mismatch")
                # Do not trust arbitrary error text from JSON metadata.
                original.message_redacted = (
                    "required official homepage eligibility extraction failed"
                )
                state["errors"] = [
                    original.model_dump(mode="json")
                    if e["error_id"] == original.error_id
                    else e
                    for e in state["errors"]
                ]
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
    receipt["error_codes"] = [e["error_code"] for e in state["errors"]]
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
