"""Fixture-only outer LangGraph, with node-local detached state.

An explicit profile consumes the existing pre-research selection API. No live
admission, model, provider, or checkpoint persistence is introduced. The Python
controller remains the independent compatibility oracle.
"""

import hashlib
import json
from collections.abc import Callable, Collection, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import TypeAdapter

from skala_rag import run_settings
from skala_rag.contracts.candidates import Candidate, EligibilityResult
from skala_rag.contracts.coverage import ResearchGap
from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.evaluation import EvaluationSnapshot
from skala_rag.contracts.evidence import Evidence
from skala_rag.contracts.ids import evaluation_key
from skala_rag.contracts.reports import CandidateOutcome
from skala_rag.contracts.v3 import (
    CoverageResult,
    Evaluation,
    InvestmentDecision,
    ScoreSummary,
)
from skala_rag.graph import snapshot as snapshot_module
from skala_rag.graph.candidates_v3 import (
    CandidateRunV3,
    CandidateStagesV3,
    RecoverableResearchFailure,
    ResearchLoopFailure,
    ResearchRequestV3,
    ResearchResponseV3,
    validate_snapshot_admission_v3,
    validate_snapshot_generation_v3,
)
from skala_rag.graph.evaluation_v3 import build_evaluation_graph_v3
from skala_rag.graph.research_artifacts_v3 import (
    ArtifactResearchFailure,
    CompanyResearchArtifactsV3,
    active_evidence_v3,
    consume_outcome_v3,
    initialize_artifacts_v3,
    retain_outcome_errors_v3,
)
from skala_rag.scoring.aggregate_v3 import (
    ApplicabilityVerifier,
    ZeroDenominatorV3,
    aggregate_scores_v3,
)
from skala_rag.scoring.catalog import ScoringPolicy
from skala_rag.scoring.coverage_v3 import (
    ApplicabilityCheck,
    NoApplicableCriteria,
    build_research_gaps_v3,
    check_coverage_v3,
)
from skala_rag.scoring.decision_v3 import decide_v3
from skala_rag.scoring.selector_v3 import SelectionResultV3, select_best_v3
from skala_rag.scoring.v3_policy import V3Policy
from skala_rag.source_only_v3 import SourceOnlyV3, select_source_only_terminal_v3


class CandidateWorkflowStateV3(TypedDict, total=False):
    data: dict
    result: CandidateRunV3


def candidate_recursion_limit_v3(candidate_count: int, policy: V3Policy) -> int:
    """Execution guard from a finite supplied population, never a candidate cap.

    One candidate takes at most eleven outer steps plus three per additional
    request; add discovery, normalize, final iterator, selector, and slack.
    Inner parallel graph has its own constant depth (< LangGraph default 25).
    """
    if type(candidate_count) is not int or candidate_count < 0:
        raise ValueError("finite nonnegative candidate count required")
    return 8 + candidate_count * (
        16 + 3 * policy.research.additional_requests_per_candidate
    )


def _encode(value):
    if type(value) is CompanyResearchArtifactsV3:
        return {
            "_artifact_seed_v3": TypeAdapter(CompanyResearchArtifactsV3).dump_python(
                value, mode="json"
            )
        }
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        # Research is an opaque caller context in CandidateStagesV3, not a DTO
        # owned by this controller. Preserve its Python type and detached-copy
        # semantics; callbacks must never place clients or secrets in it.
        return {
            k: deepcopy(v)
            if k == "research" and type(v) is not CompanyResearchArtifactsV3
            else _encode(v)
            for k, v in value.items()
        }
    if isinstance(value, (tuple, list, set)):
        return [_encode(v) for v in value]
    return deepcopy(value)


def _decode(payload):
    data = deepcopy(payload)
    research = data.get("research")
    if type(research) is dict and set(research) == {"_artifact_seed_v3"}:
        data["research"] = CompanyResearchArtifactsV3(**research["_artifact_seed_v3"])
    for key, model in (
        ("eligibility", EligibilityResult),
        ("coverage", CoverageResult),
        ("snapshot", EvaluationSnapshot),
        ("summary", ScoreSummary),
        ("decision", InvestmentDecision),
    ):
        if data.get(key) is not None:
            data[key] = model.model_validate(
                data[key], context={"execution_mode": "fixture"}
            )
    for key, model in (
        ("coverages", CoverageResult),
        ("outcomes", CandidateOutcome),
        ("scores", ScoreSummary),
        ("decisions", InvestmentDecision),
    ):
        data[key] = {k: model.model_validate(v) for k, v in data.get(key, {}).items()}
    data["research_gaps"] = {
        k: tuple(ResearchGap.model_validate(g) for g in v)
        for k, v in data.get("research_gaps", {}).items()
    }
    for key in ("errors", "failures"):
        data[key] = [WorkflowError.model_validate(e) for e in data.get(key, [])]
    data["evidence"] = [
        Evidence.model_validate(e, context={"execution_mode": "fixture"})
        for e in data.get("evidence", [])
    ]
    data["new_evidence_ids"] = set(data.get("new_evidence_ids", []))
    return data


def build_candidate_workflow_v3(
    stages: CandidateStagesV3 | None,
    evaluators: Mapping[str, Callable],
    *,
    policy: V3Policy,
    catalog: ScoringPolicy,
    catalog_policy_version: str,
    run_id: str,
    schema_version: str,
    support_check: Callable,
    applicability_assessments: Callable,
    applicability_check: ApplicabilityCheck,
    applicability_verifier: ApplicabilityVerifier | None,
    industry_evidence_dimensions: Collection[str],
    clock: Callable[[], datetime],
    trace_events: list[dict] | None = None,
    run_profile: run_settings.RunProfile | None = None,
    source_only: SourceOnlyV3 | None = None,
) -> StateGraph:
    """Build the existing outer graph; source-only needs an explicit boundary."""
    return _build_candidate_workflow_v3(
        stages,
        evaluators,
        policy=policy,
        catalog=catalog,
        catalog_policy_version=catalog_policy_version,
        run_id=run_id,
        schema_version=schema_version,
        support_check=support_check,
        applicability_assessments=applicability_assessments,
        applicability_check=applicability_check,
        applicability_verifier=applicability_verifier,
        industry_evidence_dimensions=industry_evidence_dimensions,
        clock=clock,
        trace_events=trace_events,
        run_profile=run_profile,
        source_only=source_only,
        _entrypoint="discover",
    )


def _build_candidate_workflow_v3(
    stages: CandidateStagesV3 | None,
    evaluators: Mapping[str, Callable],
    *,
    policy: V3Policy,
    catalog: ScoringPolicy,
    catalog_policy_version: str,
    run_id: str,
    schema_version: str,
    support_check: Callable,
    applicability_assessments: Callable,
    applicability_check: ApplicabilityCheck,
    applicability_verifier: ApplicabilityVerifier | None,
    industry_evidence_dimensions: Collection[str],
    clock: Callable[[], datetime],
    trace_events: list[dict] | None = None,
    run_profile: run_settings.RunProfile | None = None,
    source_only: SourceOnlyV3 | None = None,
    _entrypoint: str,
) -> StateGraph:
    if _entrypoint not in ("discover", "candidate_iterator", "selector"):
        raise ValueError("invalid internal outer graph entrypoint")
    if not isinstance(policy, V3Policy) or policy.execution_mode != "fixture":
        raise ValueError("fixture V3Policy required")
    if not run_id.strip() or not schema_version.strip():
        raise ValueError("run/schema required")
    if run_profile is not None:
        run_settings._validate_profile(run_profile)
        if run_profile.run_id != run_id:
            raise ValueError("profile/controller run_id mismatch")
        run_profile = deepcopy(run_profile)
    if source_only is not None:
        if type(source_only) is not SourceOnlyV3 or stages is not None or evaluators:
            raise ValueError(
                "explicit source-only boundary requires no fixture stages/evaluators"
            )
        source_only = replace(
            source_only, run_profile=deepcopy(source_only.run_profile)
        )
        source_only.validate(
            policy=policy,
            run_id=run_id,
            schema_version=schema_version,
            run_profile=run_profile,
        )
    elif stages is None:
        raise ValueError("fixture stages required")
    execution_mode = "live" if source_only else "fixture"
    binding = (
        stages.evidence_research.pin(
            run_id=run_id,
            schema_version=schema_version,
            policy_version=policy.policy_version,
        )
        if stages is not None and stages.evidence_research
        else None
    )
    research_tool = None
    research_tool_configuration_failed = False
    if (
        catalog.policy_version != catalog_policy_version
        or {c.criterion_id: (c.dimension, c.weight) for c in catalog.criteria}
        != {c.criterion_id: (c.dimension, c.weight) for c in policy.criteria}
        or catalog.dimension_weights
        != {w.dimension: w.weight for w in policy.dimension_weights}
    ):
        raise ValueError("coverage catalog differs from v3 approved catalog")
    graph = (
        None
        if source_only
        else build_evaluation_graph_v3(
            evaluators,
            criteria=policy.criteria,
            policy_version=policy.policy_version,
            run_id=run_id,
            schema_version=schema_version,
            industry_evidence_dimensions=industry_evidence_dimensions,
            applicability_validator=applicability_verifier,
            clock=clock,
        ).compile()
    )

    def helpers(data):
        def timed(step, cid, operation: Callable, input_ids=()):
            started_at = datetime.now(timezone.utc).isoformat()
            started = perf_counter()
            status = "failed"
            output_ids = []
            try:
                value = operation()
                if isinstance(value, CoverageResult):
                    data["coverages"][cid] = value.model_copy(deep=True)
                status = "ok"
                if isinstance(value, dict):
                    output_ids = list(value.get("evaluations_v3", {}))
                    if value.get("evaluation_status_v3") == "failure":
                        status = "failed"
                        output_ids = list(value.get("evaluation_failure_ids_v3", []))
                else:
                    for field in ("score_summary_id", "decision_id", "candidate_id"):
                        identifier = getattr(value, field, None)
                        if identifier:
                            output_ids.append(identifier)
                    if isinstance(value, SelectionResultV3):
                        output_ids = (
                            [value.selected_candidate_id]
                            if value.selected_candidate_id
                            else []
                        )
                return value
            finally:
                if trace_events is not None:
                    trace_events.append(
                        dict(
                            run_id=run_id,
                            candidate_id=cid,
                            step=step,
                            started_at=started_at,
                            duration_seconds=perf_counter() - started,
                            input_ids=list(input_ids),
                            output_ids=output_ids,
                            status=status,
                            execution_mode=execution_mode,
                            research_retry_count=data["retry_counts"].get(cid, 0),
                            research_stop_reason=data["stop_reasons"].get(cid),
                            controller="langgraph",
                            evidence_revision=data["coverages"][cid].evidence_revision
                            if cid in data["coverages"]
                            else None,
                            missing_criterion_ids=list(
                                data["coverages"][cid].missing_criterion_ids
                            )
                            if cid in data["coverages"]
                            else [],
                            unresolved_conflicts=list(
                                data["coverages"][cid].unresolved_conflicts
                            )
                            if cid in data["coverages"]
                            else [],
                        )
                    )

        def error(
            stage: str,
            cid: str | None,
            message: str = "Invalid v3 candidate stage",
            error_code: str = "UPSTREAM_INVALID",
            retryable: bool = False,
            attempt: int = 1,
        ) -> WorkflowError:
            return WorkflowError(
                schema_version=schema_version,
                error_id=f"v3:{run_id}:{data['index']}:{stage}:{attempt}",
                run_id=run_id,
                candidate_id=cid,
                node=stage,
                error_code=error_code,
                message_redacted=message,
                retryable=retryable,
                attempt=attempt,
                timestamp=clock(),
            )

        def finish(
            status: str,
            candidate_id: str,
            eligible: EligibilityResult | None = None,
            decision: InvestmentDecision | None = None,
            failures: Sequence[WorkflowError] = (),
        ) -> None:
            if candidate_id in data["outcomes"]:
                raise ValueError("candidate archive conflict")
            data["outcomes"][candidate_id] = CandidateOutcome(
                schema_version=schema_version,
                candidate_id=candidate_id,
                status=status,
                eligibility_result_id=eligible.eligibility_result_id
                if eligible and (not failures)
                else None,
                decision_id=decision.decision_id if decision else None,
                failure_ids=[e.error_id for e in failures],
                summary_reason="; ".join(decision.reason_codes) if decision else status,
            )
            timed("archive", candidate_id, lambda: None)

        return timed, error, finish

    def discover(data, timed, error, finish):
        data.clear()
        data.update(
            retry_counts={},
            stop_reasons={},
            coverages={},
            research_gaps={},
            research_artifacts={},
            outcomes={},
            scores={},
            decisions={},
            errors=[],
            index=0,
            stage="discovery_normalize",
            route="normalize",
            selection_receipt=None,
        )
        if source_only is not None:
            outcome = source_only.accept_discovery()
            data["source_only_detail"] = dict(
                run_input=source_only.run_input.model_dump(mode="json"),
                budget=source_only.budget.model_dump(mode="json"),
                budget_scope="per_candidate",
                replay_budget_scope="no_new_network_not_charged_to_fresh_fetch_deadline",
                profile=asdict(source_only.run_profile),
                input_binding_sha256=source_only.input_binding_sha256,
                discovery=dict(
                    result=source_only.discovery_result.model_dump(mode="json"),
                    status=outcome.status,
                ),
                research={},
                capture_replay_inputs=json.loads(source_only.research_replays_json),
                usage=dict(
                    provider_company_research_calls=0,
                    captured_replays=0,
                    provider_configuration_attempts=0,
                    physical_http_requests="unmeasured",
                    per_candidate={},
                ),
            )
            data["discovered"] = (
                [c.model_dump(mode="json") for c in outcome.bundle.candidates]
                if outcome.bundle
                else []
            )
            data["errors"].extend(outcome.errors)
            if outcome.status == "failed":
                data["candidates"] = []
                data["discovery_failed"] = True
                data["route"] = "selector"
            return
        data["discovered"] = [
            Candidate.model_validate(
                c, context={"execution_mode": "fixture"}
            ).model_dump(mode="json")
            for c in stages.discover()
        ]

    def normalize(data, timed, error, finish):
        if run_profile is not None:
            selected = run_settings.normalize_and_select(
                [
                    Candidate.model_validate(
                        c, context={"execution_mode": execution_mode}
                    )
                    for c in data["discovered"]
                ],
                profile=run_profile,
                execution_mode=execution_mode,
            )
            data["selection_receipt"] = selected.receipt
            normalized = selected.candidates
        else:
            normalized = stages.normalize(deepcopy(data["discovered"]))
        data["candidates"] = [
            Candidate.model_validate(
                c, context={"execution_mode": execution_mode}
            ).model_dump(mode="json")
            for c in normalized
        ]
        if len({c["candidate_id"] for c in data["candidates"]}) != len(
            data["candidates"]
        ):
            raise ValueError("duplicate normalized candidate")
        if not {c["candidate_id"] for c in data["candidates"]} <= {
            c["candidate_id"] for c in data["discovered"]
        }:
            raise ValueError("normalize introduced unknown candidate")
        data["route"] = "candidate_iterator"

    def candidate_iterator(data, timed, error, finish):
        if data["index"] == len(data["candidates"]):
            data["route"] = "selector"
            return
        data["candidate"] = data["candidates"][data["index"]]
        data["cid"] = data["candidate"]["candidate_id"]
        data["retry_counts"][data["cid"]] = 0
        data["eligibility"] = None
        data["failures"] = []
        data["terminal_status"] = None
        for key in (
            "snapshot",
            "summary",
            "decision",
            "coverage",
            "research",
            "evaluated",
            "request",
            "dims",
        ):
            data.pop(key, None)
        data["route"] = "research"

    def research(data, timed, error, finish):
        nonlocal research_tool, research_tool_configuration_failed
        data["stage"] = "research"
        if source_only is not None:
            usage = data["source_only_detail"]["usage"]
            candidate_usage = dict(
                provider_company_research_calls=0, captured_replays=0
            )
            usage["per_candidate"][data["cid"]] = candidate_usage
            if source_only.has_replay(data["cid"]):
                usage["captured_replays"] += 1
                candidate_usage["captured_replays"] += 1
            elif research_tool is None:
                if research_tool_configuration_failed:
                    raise ValueError("source-only provider configuration failed")

                def record_configuration_attempt():
                    usage["provider_configuration_attempts"] += 1

                try:
                    research_tool = source_only.make_tool(
                        on_configuration_attempt=record_configuration_attempt
                    )
                except Exception:
                    # Cache only the failure, never an exception/client in State.
                    research_tool_configuration_failed = True
                    raise ValueError(
                        "source-only provider configuration failed"
                    ) from None

            def record_provider_call():
                usage["provider_company_research_calls"] += 1
                candidate_usage["provider_company_research_calls"] += 1

            detail = source_only.research(
                deepcopy(data["candidate"]),
                research_tool,
                on_provider_call=record_provider_call,
            )
            data["source_only_detail"]["research"][data["cid"]] = deepcopy(detail)
            data["research"] = detail
            data["route"] = "eligibility"
            return
        data["research"] = stages.research(deepcopy(data["candidate"]))
        data["route"] = "eligibility"

    def eligibility_node(data, timed, error, finish):
        data["stage"] = "eligibility"
        if source_only is not None:
            state = data["research"]["state"]
            if state.get("run_outcome") == "technical_failure":
                failures = [WorkflowError.model_validate(e) for e in state["errors"]]
                if not failures:
                    raise ValueError("source-only technical failure omitted errors")
                data["errors"].extend(failures)
                data["failures"] = failures
                data["terminal_status"] = "failed"
                data["route"] = "archive"
                return
            data["eligibility"] = EligibilityResult.model_validate(
                state["eligibility_results"][data["cid"]]
            )
        else:
            data["eligibility"] = EligibilityResult.model_validate(
                stages.eligibility(
                    deepcopy(data["candidate"]), deepcopy(data["research"])
                )
            )
        if (
            data["eligibility"].run_id != run_id
            or data["eligibility"].schema_version != schema_version
            or data["eligibility"].candidate_id != data["cid"]
            or (data["eligibility"].policy_version != policy.policy_version)
        ):
            raise ValueError("eligibility generation mismatch")
        if data["eligibility"].status != "eligible":
            data["terminal_status"] = (
                "ineligible"
                if data["eligibility"].status == "ineligible"
                else "eligibility_unknown"
            )
            data["route"] = "archive"
        elif source_only is not None:
            failure = error(
                "source_only_evaluation",
                data["cid"],
                "Source-only evaluation is not ready or authorized",
                error_code="SOURCE_ONLY_EVALUATION_NOT_READY",
            )
            data["errors"].append(failure)
            data["failures"] = [failure]
            data["terminal_status"] = "failed"
            data["route"] = "archive"
        else:
            data["route"] = "collect"

    def collect(data, timed, error, finish):
        data["stage"] = "collect"
        if binding:
            owned = initialize_artifacts_v3(
                data["research"], data["eligibility"], data["candidate"], binding
            )
            data["research_artifacts"][data["cid"]] = owned
            outcome = binding.research.run(
                Candidate.model_validate(
                    data["candidate"], context={"execution_mode": "fixture"}
                ),
                (),
                binding.budget.model_copy(deep=True),
            )
            consume_outcome_v3(owned, outcome, binding, data["cid"], initial=True)
            data["errors"] = retain_outcome_errors_v3(data["errors"], owned)
            data["evidence"] = [
                Evidence.model_validate(e, context={"execution_mode": "fixture"})
                for e in active_evidence_v3(owned, binding, data["cid"]).values()
            ]
        else:
            data["evidence"] = [
                Evidence.model_validate(item, context={"execution_mode": "fixture"})
                for item in stages.collect(
                    deepcopy(data["candidate"]), deepcopy(data["research"])
                )
            ]
        data["collected"] = {
            item.evidence_id: item.model_dump(mode="json") for item in data["evidence"]
        }
        if len(data["collected"]) != len(data["evidence"]) or any(
            (
                item.schema_version != schema_version
                or not (
                    item.scope == "company"
                    and item.candidate_id == data["cid"]
                    or (item.scope == "industry" and item.candidate_id is None)
                )
                for item in data["evidence"]
            )
        ):
            raise ValueError("invalid collected Evidence identity")
        data["initial_revision"] = data["eligibility"].evidence_revision
        data["evidence_revision"] = (
            owned["state"]["evidence_revisions"][data["cid"]]
            if binding
            else data["initial_revision"]
        )
        data["new_evidence_ids"]: set[str] = set()
        data["last_research_failed"] = False
        data["route"] = "coverage"

    def coverage_node(data, timed, error, finish):
        data["stage"] = "coverage"
        conflicts = (
            stages.unresolved_conflicts(
                deepcopy(data["candidate"]), tuple(deepcopy(data["evidence"]))
            )
            if stages.unresolved_conflicts
            else sorted(
                {
                    identifier
                    for item in data["evidence"]
                    if item.conflicts_with
                    for identifier in (item.evidence_id, *item.conflicts_with)
                }
            )
        )
        result = timed(
            "coverage",
            data["cid"],
            lambda: check_coverage_v3(
                data["cid"],
                data["evidence"],
                catalog,
                evidence_revision=data["evidence_revision"],
                schema_version=schema_version,
                execution_mode="fixture",
                policy_version=policy.policy_version,
                support_check=support_check,
                applicability_assessments=applicability_assessments(data["cid"]),
                applicability_check=applicability_check,
                unresolved_conflict_ids=conflicts,
            ),
            list(data["collected"]),
        )
        data["coverages"][data["cid"]] = result.model_copy(deep=True)
        if stages.gap_templates:
            templates = [
                ResearchGap.model_validate(g)
                for g in stages.gap_templates(
                    deepcopy(data["candidate"]), result.model_copy(deep=True)
                )
            ]
            data["research_gaps"][data["cid"]] = tuple(
                build_research_gaps_v3(
                    result, catalog, templates, policy_version=policy.policy_version
                )
            )
        data["coverage"] = result
        data["route"] = "research_gate"

    def research_gate(data, timed, error, finish):
        data["stage"] = "research_gate"
        if data["coverage"].research_ready:
            data["stop_reasons"][data["cid"]] = "ready"
            timed("research_ready", data["cid"], lambda: None)
            data["route"] = "freeze"
            return
        if (
            data["retry_counts"][data["cid"]]
            == policy.research.additional_requests_per_candidate
        ):
            if data["last_research_failed"]:
                data["stop_reasons"][data["cid"]] = "failed_exhausted"
                raise ResearchLoopFailure("RESEARCH_FAILED_EXHAUSTED")
            data["stop_reasons"][data["cid"]] = "exhausted"
            data["research_gaps"][data["cid"]] = tuple(
                (
                    g.model_copy(deep=True, update={"status": "exhausted"})
                    for g in data["research_gaps"].get(data["cid"], ())
                )
            )
            timed("research_exhausted", data["cid"], lambda: None)
            data["route"] = "freeze"
            return
        if stages.additional_research is None and binding is None:
            data["stop_reasons"][data["cid"]] = "callback_required"
            raise ResearchLoopFailure("RESEARCH_CALLBACK_REQUIRED")
        data["retry_counts"][data["cid"]] += 1
        timed("research_gate", data["cid"], lambda: None)
        data["route"] = "additional_research"

    def additional_research(data, timed, error, finish):
        data["stage"] = "additional_research"
        request = ResearchRequestV3(
            run_id,
            deepcopy(data["candidate"]),
            deepcopy(data["research"]),
            data["coverage"].model_copy(deep=True),
            tuple(deepcopy(data["evidence"])),
            data["retry_counts"][data["cid"]],
            policy.research.additional_requests_per_candidate
            - data["retry_counts"][data["cid"]],
            tuple(deepcopy(data["research_gaps"].get(data["cid"], ()))),
        )
        if binding:
            if not request.research_gaps:
                raise ResearchLoopFailure("RESEARCH_RESPONSE_INVALID")
            owned = data["research_artifacts"][data["cid"]]
            owned["state"]["research_retry_count"][data["cid"]] = request.attempt
            outcome = timed(
                data["stage"],
                data["cid"],
                lambda: binding.research.run(
                    Candidate.model_validate(
                        data["candidate"], context={"execution_mode": "fixture"}
                    ),
                    request.research_gaps,
                    binding.budget.model_copy(deep=True),
                ),
                list(data["collected"]),
            )
            changed = consume_outcome_v3(
                owned, outcome, binding, data["cid"], initial=False
            )
            data["errors"] = retain_outcome_errors_v3(data["errors"], owned)
            data["new_evidence_ids"].update(changed)
            data["evidence_revision"] = owned["state"]["evidence_revisions"][
                data["cid"]
            ]
            data["collected"] = active_evidence_v3(owned, binding, data["cid"])
            data["new_evidence_ids"].intersection_update(data["collected"])
            data["evidence"] = [
                Evidence.model_validate(e, context={"execution_mode": "fixture"})
                for e in data["collected"].values()
            ]
            data["route"] = "coverage"
            return
        try:
            response = timed(
                data["stage"],
                data["cid"],
                lambda: stages.additional_research(request),
                list(data["collected"]),
            )
        except RecoverableResearchFailure:
            failure = error(
                data["stage"],
                data["cid"],
                "Recoverable research failure (details redacted)",
                "RESEARCH_RECOVERABLE_FAILURE",
                True,
                data["retry_counts"][data["cid"]],
            )
            data["errors"].append(failure)
            data["last_research_failed"] = True
            timed("research_recoverable_failure", data["cid"], lambda: None)
            data["route"] = "research_gate"
            return
        except Exception:
            data["stop_reasons"][data["cid"]] = "terminal_failure"
            raise ResearchLoopFailure("RESEARCH_TERMINAL_FAILURE") from None
        data["last_research_failed"] = False
        if (
            not isinstance(response, ResearchResponseV3)
            or response.candidate_id != data["cid"]
            or response.base_evidence_revision != data["coverage"].evidence_revision
        ):
            raise ResearchLoopFailure("RESEARCH_RESPONSE_INVALID")
        batch = [
            Evidence.model_validate(e, context={"execution_mode": "fixture"})
            for e in response.evidence
        ]
        if len({e.evidence_id for e in batch}) != len(batch) or any(
            (
                e.schema_version != schema_version
                or not (
                    e.scope == "company"
                    and e.candidate_id == data["cid"]
                    or (e.scope == "industry" and e.candidate_id is None)
                )
                or (
                    e.evidence_id in data["collected"]
                    and e.model_dump(mode="json") != data["collected"][e.evidence_id]
                )
                for e in batch
            )
        ):
            raise ResearchLoopFailure("RESEARCH_RESPONSE_INVALID")
        new = [e for e in batch if e.evidence_id not in data["collected"]]
        if new:
            data["new_evidence_ids"].update((e.evidence_id for e in new))
            data["evidence_revision"] += 1
            data["evidence"].extend(new)
            data["collected"].update(
                {e.evidence_id: e.model_dump(mode="json") for e in new}
            )
        data["route"] = "coverage"

    def freeze(data, timed, error, finish):
        data["stage"] = "freeze"
        if (
            data["evidence_revision"] != data["initial_revision"]
            and stages.freeze_with_evidence is None
            and binding is None
        ):
            raise ResearchLoopFailure("RESEARCH_FREEZE_REQUIRED")
        freeze_eligibility = data["eligibility"].model_copy(
            deep=True, update={"evidence_revision": data["evidence_revision"]}
        )

        def freeze_snapshot():
            if binding:
                owned = data["research_artifacts"][data["cid"]]
                return snapshot_module.freeze_snapshot(
                    data["cid"],
                    owned["state"],
                    binding.run_input,
                    run_id=run_id,
                    index_version=binding.index_version,
                    schema_version=schema_version,
                    allowed_source_ids=binding.allowed_source_ids,
                    industry_evidence_ids=binding.industry_evidence_ids,
                    clock=clock,
                )
            payload = (
                stages.freeze_with_evidence(
                    deepcopy(data["candidate"]),
                    freeze_eligibility,
                    data["coverage"].model_copy(deep=True),
                    tuple(deepcopy(data["evidence"])),
                )
                if stages.freeze_with_evidence
                else stages.freeze(
                    deepcopy(data["candidate"]),
                    freeze_eligibility,
                    data["coverage"].model_copy(deep=True),
                )
            )
            return EvaluationSnapshot.model_validate(
                payload, context={"execution_mode": "fixture"}
            )

        data["snapshot"] = timed(
            "freeze", data["cid"], freeze_snapshot, list(data["collected"])
        )
        validate_snapshot_generation_v3(
            data["snapshot"],
            run_id=run_id,
            cid=data["cid"],
            schema_version=schema_version,
            policy=policy,
            coverage=data["coverage"],
        )
        data["stage"] = "freeze_admission"
        validate_snapshot_admission_v3(
            data["snapshot"],
            run_id=run_id,
            cid=data["cid"],
            new_evidence_ids=data["new_evidence_ids"],
            collected=data["collected"],
        )
        data["route"] = "evaluation_join"

    def evaluation_join(data, timed, error, finish):
        data["stage"] = "evaluate"
        frozen = data["snapshot"].model_dump(mode="json")
        graph_state = dict(
            snapshot_v3=frozen,
            current_candidate_id=data["cid"],
            evaluation_rounds={data["cid"]: data["snapshot"].evaluation_round},
            evidence_revisions={data["cid"]: data["snapshot"].evidence_revision},
            run_input={
                "execution_mode": "fixture",
                "policy_version": policy.policy_version,
            },
            snapshots={data["snapshot"].snapshot_id: frozen},
            candidates=data["candidates"],
            candidate_index=data["index"],
            candidate_outcomes={},
            candidate_status={},
            errors=[],
        )
        data["evaluated"] = timed(
            "evaluation_join",
            data["cid"],
            lambda: graph.invoke(graph_state),
            [data["snapshot"].snapshot_id],
        )
        if data["evaluated"]["evaluation_status_v3"] != "success":
            failure_ids = set(data["evaluated"]["evaluation_failure_ids_v3"])
            failures = [
                WorkflowError.model_validate(e)
                for e in data["evaluated"]["errors"]
                if e["error_id"] in failure_ids and e.get("candidate_id") == data["cid"]
            ]
            if (
                not failures
                or data["evaluated"]["candidate_index"] != data["index"] + 1
            ):
                raise ValueError("evaluation failure did not archive/advance once")
            data["errors"].extend(failures)
            data["failures"] = failures
            data["terminal_status"] = "failed"
            data["route"] = "archive"
            return
        data["route"] = "score"

    def score(data, timed, error, finish):
        if (
            data["evaluated"]["candidate_index"] != data["index"]
            or len(data["evaluated"]["evaluations_v3"]) != 6
        ):
            raise ValueError("evaluation advanced or promoted partial dimensions")
        data["dims"] = []
        for dimension in (
            "founder",
            "market",
            "technology",
            "moat",
            "traction",
            "deal_terms",
        ):
            key = evaluation_key(
                data["cid"], data["snapshot"].evaluation_round, dimension
            )
            data["dims"].append(
                Evaluation.model_validate(data["evaluated"]["evaluations_v3"][key])
            )
        data["stage"] = "score"
        data["summary"] = timed(
            "score",
            data["cid"],
            lambda: aggregate_scores_v3(
                data["dims"],
                policy,
                applicability_verifier=applicability_verifier,
                snapshot=data["snapshot"],
            ),
            [data["snapshot"].snapshot_id],
        )
        data["route"] = "decision"

    def decision_node(data, timed, error, finish):
        data["decision"] = timed(
            "decision",
            data["cid"],
            lambda: decide_v3(data["summary"], policy),
            [data["summary"].score_summary_id],
        )
        data["scores"][data["cid"]] = data["summary"]
        data["decisions"][data["cid"]] = data["decision"]
        data["terminal_status"] = (
            "recommend"
            if data["decision"].label.startswith("RECOMMEND")
            else data["decision"].label.lower()
        )
        data["route"] = "archive"

    def archive(data, timed, error, finish):
        finish(
            data["terminal_status"],
            data["cid"],
            data["eligibility"],
            data.get("decision"),
            data["failures"],
        )
        data["route"] = "advance"

    def advance(data, timed, error, finish):
        data["index"] += 1
        timed("advance", data["cid"], lambda: None)
        data["route"] = "candidate_iterator"

    def selector(data, timed, error, finish):
        rows: list[dict[str, Any]] = []
        for data["cid"] in sorted(data["outcomes"]):
            data["summary"] = data["scores"].get(data["cid"])
            data["decision"] = data["decisions"].get(data["cid"])
            rows.append(
                dict(
                    candidate_id=data["cid"],
                    eligibility_status=(
                        data["source_only_detail"]["research"]
                        .get(data["cid"], {})
                        .get("state", {})
                        .get("eligibility_results", {})
                        .get(data["cid"], {})
                        .get("status", "unknown")
                        if source_only
                        else "eligible"
                        if data["summary"]
                        else "unknown"
                    ),
                    status="evaluated"
                    if data["summary"]
                    else data["outcomes"][data["cid"]].status,
                    label=data["decision"].label if data["decision"] else None,
                    normalized_score=data["summary"].normalized_score
                    if data["summary"]
                    else None,
                    weighted_missing_pct=data["summary"].weighted_missing_pct
                    if data["summary"]
                    else None,
                    applicable_weight=data["summary"].applicable_weight
                    if data["summary"]
                    else None,
                    score_summary_id=data["summary"].score_summary_id
                    if data["summary"]
                    else None,
                )
            )
        selection = timed(
            "selector",
            None,
            lambda: (select_source_only_terminal_v3 if source_only else select_best_v3)(
                rows, policy, run_id=run_id, schema_version=schema_version
            ),
            [item.score_summary_id for item in data["scores"].values()],
        )
        if source_only and data.get("discovery_failed"):
            selection = replace(selection, reason="SOURCE_ONLY_DISCOVERY_FAILED")
        if not data["candidates"]:
            status = "no_candidates"
        elif data["scores"]:
            status = "ready_for_v3_reporting"
        elif all((outcome.status == "failed" for outcome in data["outcomes"].values())):
            status = "all_eligible_failed"
        elif any((outcome.status == "failed" for outcome in data["outcomes"].values())):
            status = "eligible_failed_no_success"
        else:
            status = "no_eligible_candidates"
        if source_only and any(
            outcome.status == "failed" for outcome in data["outcomes"].values()
        ):
            status = (
                "source_only_technical_failure"
                if all(
                    outcome.status == "failed" for outcome in data["outcomes"].values()
                )
                else "source_only_partial_failure"
            )
        if source_only is not None:
            inputs = data["source_only_detail"]["capture_replay_inputs"]
            provided = set(inputs)
            attempted = {
                cid
                for cid, usage in data["source_only_detail"]["usage"][
                    "per_candidate"
                ].items()
                if usage["captured_replays"]
            }
            receipt = data.get("selection_receipt")
            data["source_only_detail"]["capture_replay"] = dict(
                provided_ids=sorted(provided),
                attempted_ids=sorted(attempted),
                unused_ids=sorted(provided - attempted),
                excluded_ids=sorted(provided & set(receipt.excluded_ids))
                if receipt
                else [],
                dedup_merged_ids=sorted(
                    provided & {merge.merged_candidate_id for merge in receipt.merges}
                )
                if receipt
                else [],
                input_sha256=hashlib.sha256(
                    source_only.research_replays_json.encode()
                ).hexdigest(),
                attribution_limits="declared_identity_checked_opaque_metadata_not_identity_proof",
            )
        data["result"] = CandidateRunV3(
            schema_version,
            run_id,
            policy.policy_version,
            status,
            data["index"],
            data["outcomes"],
            data["scores"],
            data["decisions"],
            selection,
            tuple(data["errors"]),
            research_retry_count=data["retry_counts"],
            research_stop_reasons=data["stop_reasons"],
            coverage_results=data["coverages"],
            research_gaps=data["research_gaps"],
            selection_receipt=data.get("selection_receipt"),
            execution_mode=execution_mode,
            replay_scope=source_only.replay_scope if source_only else None,
            source_only_detail=deepcopy(data.get("source_only_detail", {})),
            research_artifacts=deepcopy(data.get("research_artifacts", {})),
        )
        if data.get("discovery_failed"):
            data["result"] = replace(data["result"], status="discovery_failed")
        data["route"] = "__end__"

    operations = {
        "discover": discover,
        "normalize": normalize,
        "candidate_iterator": candidate_iterator,
        "research": research,
        "eligibility": eligibility_node,
        "collect": collect,
        "coverage": coverage_node,
        "research_gate": research_gate,
        "additional_research": additional_research,
        "freeze": freeze,
        "evaluation_join": evaluation_join,
        "score": score,
        "decision": decision_node,
        "archive": archive,
        "advance": advance,
        "selector": selector,
    }

    def node(name, operation):
        def call(state):
            data = _decode(state.get("data", {}))
            timed, error, finish = helpers(data)
            try:
                if source_only is not None and name in (
                    "discover",
                    "normalize",
                    "research",
                    "eligibility",
                ):
                    timed(
                        name,
                        data.get("cid"),
                        lambda: operation(data, timed, error, finish),
                    )
                else:
                    operation(data, timed, error, finish)
            except Exception as exc:
                if isinstance(exc, ArtifactResearchFailure):
                    data["errors"].extend(exc.errors)
                    data["failures"] = list(exc.errors)
                    data["terminal_status"] = "failed"
                    data["route"] = "archive"
                    return {"data": _encode(data)}
                if name in ("discover", "normalize"):
                    failure = error("discovery_normalize", None)
                    data["errors"].append(failure)
                    data["candidates"] = []
                    data["discovery_failed"] = True
                    data["route"] = "selector"
                elif name in ("candidate_iterator", "archive", "advance", "selector"):
                    raise
                else:
                    stage, cid = data["stage"], data["cid"]
                    if isinstance(exc, (ZeroDenominatorV3, NoApplicableCriteria)):
                        dimension = getattr(exc, "dimension", None) or "total"
                        failure = error(
                            stage,
                            cid,
                            f"Zero applicable denominator: {dimension}",
                            error_code="ZERO_APPLICABLE_DENOMINATOR",
                        )
                    elif isinstance(exc, ResearchLoopFailure):
                        failure = error(
                            stage,
                            cid,
                            error_code=exc.code,
                            attempt=max(1, data["retry_counts"][cid]),
                        )
                    else:
                        failure = error(
                            stage,
                            cid,
                            error_code="SNAPSHOT_INVALID"
                            if stage in ("freeze", "freeze_admission")
                            else "UPSTREAM_INVALID",
                        )
                    data["errors"].append(failure)
                    data["failures"] = [failure]
                    data["terminal_status"] = "failed"
                    data["route"] = "archive"
            result = data.pop("result", None)
            update = {"data": _encode(data)}
            if result is not None:
                update["result"] = result
            return update

        return call

    outer = StateGraph(CandidateWorkflowStateV3)
    routes = {
        "discover": ("normalize", "selector"),
        "normalize": ("candidate_iterator", "selector"),
        "candidate_iterator": ("research", "selector"),
        "research": ("eligibility", "archive"),
        "eligibility": ("collect", "archive"),
        "collect": ("coverage", "archive"),
        "coverage": ("research_gate", "archive"),
        "research_gate": ("additional_research", "freeze", "archive"),
        "additional_research": ("coverage", "research_gate", "archive"),
        "freeze": ("evaluation_join", "archive"),
        "evaluation_join": ("score", "archive"),
        "score": ("decision", "archive"),
        "decision": ("archive",),
        "archive": ("advance",),
        "advance": ("candidate_iterator",),
        "selector": (END,),
    }
    for name, operation in operations.items():
        outer.add_node(name, node(name, operation))
        outer.add_conditional_edges(
            name,
            lambda state: state["data"]["route"],
            {target: target for target in routes[name]},
        )
    outer.add_edge(START, _entrypoint)
    return outer


def run_candidate_workflow_v3(
    stages: CandidateStagesV3 | None,
    evaluators: Mapping[str, Callable],
    *,
    graph_events: list | None = None,
    run_profile: run_settings.RunProfile | None = None,
    **options,
) -> CandidateRunV3:
    """Execute the real outer flow with its exact normalized-population bound.

    Stream discovery/normalize to a static interrupt without a checkpointer.
    Hand the detached update to a fresh, non-persistent compilation of the same
    node/edge flow starting at its next node. No callback is replayed; opaque
    research contexts never enter checkpoint serialization. This is an in-process
    handoff, not persistent resume. Events are unmodified LangGraph stream tuples.
    """
    graph = build_candidate_workflow_v3(
        stages, evaluators, run_profile=run_profile, **options
    ).compile(checkpointer=False)
    # Pin the validated binding before any callback can mutate caller-owned data.
    run_profile = deepcopy(run_profile)
    config = {"recursion_limit": 8}
    result = None
    state = None

    def consume(graph, initial, **interrupts):
        nonlocal result, state
        for event in graph.stream(
            initial,
            config,
            stream_mode="updates",
            subgraphs=True,
            **interrupts,
        ):
            if graph_events is not None:
                graph_events.append(event)
            namespace, updates = event
            if not namespace:
                for name, update in updates.items():
                    if name != "__interrupt__":
                        state = update
                if "selector" in updates:
                    result = updates["selector"]["result"]

    # Discovery/normalization failure must also stop before selector: this keeps
    # the handoff limited to the selected population/receipt, never research.
    consume(graph, {}, interrupt_after=["normalize"], interrupt_before=["selector"])
    if state is None or state["data"]["route"] not in (
        "candidate_iterator",
        "selector",
    ):
        raise RuntimeError("outer graph did not reach normalized handoff")
    source_only = options.get("source_only")
    if source_only is not None and any(
        not source_only.has_replay(c["candidate_id"])
        for c in state["data"]["candidates"]
    ):
        # Selection is already pinned. Old captures do not spend a fresh deadline.
        source_only.validate_fresh_admission()
    config["recursion_limit"] = candidate_recursion_limit_v3(
        len(state["data"]["candidates"]), options["policy"]
    )
    continuation = _build_candidate_workflow_v3(
        stages,
        evaluators,
        run_profile=run_profile,
        _entrypoint=state["data"]["route"],
        **options,
    ).compile(checkpointer=False)
    consume(continuation, deepcopy(state))
    if result is None:
        raise RuntimeError("outer graph did not reach terminal selector")
    return result
