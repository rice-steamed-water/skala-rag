"""All-candidate fixture v3 controller using #20 coverage and #24 LangGraph.

This is deliberately separate from baseline candidates.py and ReportInput. It does
not implement discovery policy, reporting, CLI or live providers. Research control
is Python; only the five-way evaluation subgraph is LangGraph.
"""

from collections.abc import Callable, Collection, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, TypeVar

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
from skala_rag.graph.evaluation_v3 import build_evaluation_graph_v3
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

T = TypeVar("T")


class RecoverableResearchFailure(Exception):
    """Explicit transient failure; its message is never persisted by controller."""


class ResearchLoopFailure(ValueError):
    """Controller-owned redacted research failure code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ResearchRequestV3:
    run_id: str
    candidate: dict
    research: object
    coverage: CoverageResult
    evidence: tuple[Evidence, ...]
    attempt: int
    remaining_requests: int
    research_gaps: tuple[ResearchGap, ...] = ()


@dataclass(frozen=True)
class ResearchResponseV3:
    candidate_id: str
    base_evidence_revision: int
    evidence: Sequence[Evidence | dict]


@dataclass(frozen=True)
class CandidateStagesV3:
    """Externally injected stages; no default source, model or random selection."""

    discover: Callable[[], Sequence[Candidate | dict]]
    normalize: Callable[[Sequence[dict]], Sequence[Candidate | dict]]
    research: Callable[[dict], object]
    eligibility: Callable[[dict, object], EligibilityResult | dict]
    collect: Callable[[dict, object], Sequence[Evidence | dict]]
    freeze: Callable[[dict, EligibilityResult, object], EvaluationSnapshot | dict]
    additional_research: Callable[[ResearchRequestV3], ResearchResponseV3] | None = None
    freeze_with_evidence: (
        Callable[
            [dict, EligibilityResult, CoverageResult, tuple[Evidence, ...]],
            EvaluationSnapshot | dict,
        ]
        | None
    ) = None
    unresolved_conflicts: (
        Callable[[dict, tuple[Evidence, ...]], Sequence[str]] | None
    ) = None
    gap_templates: (
        Callable[[dict, CoverageResult], Sequence[ResearchGap | dict]] | None
    ) = None


@dataclass(frozen=True)
class CandidateRunV3:
    schema_version: str
    run_id: str
    policy_version: str
    status: str
    candidate_index: int
    outcomes: dict[str, CandidateOutcome]
    scores: dict[str, ScoreSummary]
    decisions: dict[str, InvestmentDecision]
    selection: SelectionResultV3
    errors: tuple[WorkflowError, ...]
    baseline_report_input: None = None
    reporting_gap: str = (
        "V3 SelectionResult and ScoreSummary require a versioned v3 ReportInput/"
        "context adapter; baseline ReportInput/report graph is incompatible."
    )
    research_retry_count: dict[str, int] = field(default_factory=dict)
    research_stop_reasons: dict[str, str] = field(default_factory=dict)
    coverage_results: dict[str, CoverageResult] = field(default_factory=dict)
    research_gaps: dict[str, tuple[ResearchGap, ...]] = field(default_factory=dict)


def run_candidates_v3(
    stages: CandidateStagesV3,
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
) -> CandidateRunV3:
    """Process every normalized candidate; #24 graph performs the five-way barrier.

    An unsuccessful candidate advances once. Only terminal successful six-dimension
    promotions reach scoring. Callbacks are fixture-bound and receive detached data.
    """
    if not isinstance(policy, V3Policy) or policy.execution_mode != "fixture":
        raise ValueError("fixture V3Policy required")
    if not run_id.strip() or not schema_version.strip():
        raise ValueError("run/schema required")
    if (
        catalog.policy_version != catalog_policy_version
        or {c.criterion_id: (c.dimension, c.weight) for c in catalog.criteria}
        != {c.criterion_id: (c.dimension, c.weight) for c in policy.criteria}
        or catalog.dimension_weights
        != {w.dimension: w.weight for w in policy.dimension_weights}
    ):
        raise ValueError("coverage catalog differs from v3 approved catalog")
    graph = build_evaluation_graph_v3(
        evaluators,
        criteria=policy.criteria,
        policy_version=policy.policy_version,
        run_id=run_id,
        schema_version=schema_version,
        industry_evidence_dimensions=industry_evidence_dimensions,
        applicability_validator=applicability_verifier,
        clock=clock,
    ).compile()

    retry_counts: dict[str, int] = {}
    stop_reasons: dict[str, str] = {}
    coverages: dict[str, CoverageResult] = {}
    research_gaps: dict[str, tuple[ResearchGap, ...]] = {}

    def timed(step, cid, operation: Callable[[], T], input_ids=()) -> T:
        started_at = datetime.now(timezone.utc).isoformat()
        started = perf_counter()
        status = "failed"
        output_ids = []
        try:
            value = operation()
            if isinstance(value, CoverageResult):
                coverages[cid] = value.model_copy(deep=True)
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
                        execution_mode="fixture",
                        research_retry_count=retry_counts.get(cid, 0),
                        research_stop_reason=stop_reasons.get(cid),
                        controller="python",
                        evidence_revision=coverages[cid].evidence_revision
                        if cid in coverages
                        else None,
                        missing_criterion_ids=list(coverages[cid].missing_criterion_ids)
                        if cid in coverages
                        else [],
                        unresolved_conflicts=list(coverages[cid].unresolved_conflicts)
                        if cid in coverages
                        else [],
                    )
                )

    outcomes: dict[str, CandidateOutcome] = {}
    scores: dict[str, ScoreSummary] = {}
    decisions: dict[str, InvestmentDecision] = {}
    errors: list[WorkflowError] = []
    index = 0

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
            error_id=f"v3:{run_id}:{index}:{stage}:{attempt}",
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
        if candidate_id in outcomes:
            raise ValueError("candidate archive conflict")
        outcomes[candidate_id] = CandidateOutcome(
            schema_version=schema_version,
            candidate_id=candidate_id,
            status=status,
            eligibility_result_id=eligible.eligibility_result_id
            if eligible and not failures
            else None,
            decision_id=decision.decision_id if decision else None,
            failure_ids=[e.error_id for e in failures],
            summary_reason="; ".join(decision.reason_codes) if decision else status,
        )
        timed("archive", candidate_id, lambda: None)

    def advance(cid):
        nonlocal index
        index += 1
        timed("advance", cid, lambda: None)

    try:
        discovered = [
            Candidate.model_validate(
                c, context={"execution_mode": "fixture"}
            ).model_dump(mode="json")
            for c in stages.discover()
        ]
        candidates = [
            Candidate.model_validate(
                c, context={"execution_mode": "fixture"}
            ).model_dump(mode="json")
            for c in stages.normalize(deepcopy(discovered))
        ]
        if len({c["candidate_id"] for c in candidates}) != len(candidates):
            raise ValueError("duplicate normalized candidate")
        if not {c["candidate_id"] for c in candidates} <= {
            c["candidate_id"] for c in discovered
        }:
            raise ValueError("normalize introduced unknown candidate")
    except Exception:
        failure = error("discovery_normalize", None)
        errors.append(failure)
        selection = select_best_v3(
            [], policy, run_id=run_id, schema_version=schema_version
        )
        return CandidateRunV3(
            schema_version,
            run_id,
            policy.policy_version,
            "discovery_failed",
            0,
            outcomes,
            scores,
            decisions,
            selection,
            tuple(errors),
        )

    for candidate in candidates:
        cid = candidate["candidate_id"]
        retry_counts[cid] = 0
        eligibility = None
        stage = "research"
        try:
            research = stages.research(deepcopy(candidate))
            stage = "eligibility"
            eligibility = EligibilityResult.model_validate(
                stages.eligibility(deepcopy(candidate), deepcopy(research))
            )
            if (
                eligibility.run_id != run_id
                or eligibility.schema_version != schema_version
                or eligibility.candidate_id != cid
                or eligibility.policy_version != policy.policy_version
            ):
                raise ValueError("eligibility generation mismatch")
            if eligibility.status != "eligible":
                finish(
                    "ineligible"
                    if eligibility.status == "ineligible"
                    else "eligibility_unknown",
                    cid,
                    eligibility,
                )
                advance(cid)
                continue
            stage = "collect"
            evidence = [
                Evidence.model_validate(item, context={"execution_mode": "fixture"})
                for item in stages.collect(deepcopy(candidate), deepcopy(research))
            ]
            collected = {
                item.evidence_id: item.model_dump(mode="json") for item in evidence
            }
            if len(collected) != len(evidence) or any(
                item.schema_version != schema_version
                or not (
                    (item.scope == "company" and item.candidate_id == cid)
                    or (item.scope == "industry" and item.candidate_id is None)
                )
                for item in evidence
            ):
                raise ValueError("invalid collected Evidence identity")
            initial_revision = eligibility.evidence_revision
            evidence_revision = initial_revision
            new_evidence_ids: set[str] = set()

            def recompute_coverage():
                conflicts = (
                    stages.unresolved_conflicts(
                        deepcopy(candidate), tuple(deepcopy(evidence))
                    )
                    if stages.unresolved_conflicts
                    else sorted(
                        {
                            identifier
                            for item in evidence
                            if item.conflicts_with
                            for identifier in (item.evidence_id, *item.conflicts_with)
                        }
                    )
                )
                result = timed(
                    "coverage",
                    cid,
                    lambda: check_coverage_v3(
                        cid,
                        evidence,
                        catalog,
                        evidence_revision=evidence_revision,
                        schema_version=schema_version,
                        execution_mode="fixture",
                        policy_version=policy.policy_version,
                        support_check=support_check,
                        applicability_assessments=applicability_assessments(cid),
                        applicability_check=applicability_check,
                        unresolved_conflict_ids=conflicts,
                    ),
                    list(collected),
                )
                coverages[cid] = result.model_copy(deep=True)
                if stages.gap_templates:
                    templates = [
                        ResearchGap.model_validate(g)
                        for g in stages.gap_templates(
                            deepcopy(candidate), result.model_copy(deep=True)
                        )
                    ]
                    research_gaps[cid] = tuple(
                        build_research_gaps_v3(
                            result,
                            catalog,
                            templates,
                            policy_version=policy.policy_version,
                        )
                    )
                return result

            stage = "coverage"
            coverage = recompute_coverage()
            last_research_failed = False
            while not coverage.research_ready:
                stage = "research_gate"
                if (
                    retry_counts[cid]
                    == policy.research.additional_requests_per_candidate
                ):
                    if last_research_failed:
                        stop_reasons[cid] = "failed_exhausted"
                        raise ResearchLoopFailure("RESEARCH_FAILED_EXHAUSTED")
                    stop_reasons[cid] = "exhausted"
                    research_gaps[cid] = tuple(
                        g.model_copy(deep=True, update={"status": "exhausted"})
                        for g in research_gaps.get(cid, ())
                    )
                    timed("research_exhausted", cid, lambda: None)
                    break
                if stages.additional_research is None:
                    stop_reasons[cid] = "callback_required"
                    raise ResearchLoopFailure("RESEARCH_CALLBACK_REQUIRED")
                retry_counts[cid] += 1
                timed("research_gate", cid, lambda: None)
                request = ResearchRequestV3(
                    run_id,
                    deepcopy(candidate),
                    deepcopy(research),
                    coverage.model_copy(deep=True),
                    tuple(deepcopy(evidence)),
                    retry_counts[cid],
                    policy.research.additional_requests_per_candidate
                    - retry_counts[cid],
                    tuple(deepcopy(research_gaps.get(cid, ()))),
                )
                stage = "additional_research"
                try:
                    response = timed(
                        stage,
                        cid,
                        lambda: stages.additional_research(request),
                        list(collected),
                    )
                except RecoverableResearchFailure:
                    failure = error(
                        stage,
                        cid,
                        "Recoverable research failure (details redacted)",
                        "RESEARCH_RECOVERABLE_FAILURE",
                        True,
                        retry_counts[cid],
                    )
                    errors.append(failure)
                    last_research_failed = True
                    timed("research_recoverable_failure", cid, lambda: None)
                    continue
                except Exception:
                    stop_reasons[cid] = "terminal_failure"
                    raise ResearchLoopFailure("RESEARCH_TERMINAL_FAILURE") from None
                last_research_failed = False
                if (
                    not isinstance(response, ResearchResponseV3)
                    or response.candidate_id != cid
                    or response.base_evidence_revision != coverage.evidence_revision
                ):
                    raise ResearchLoopFailure("RESEARCH_RESPONSE_INVALID")
                batch = [
                    Evidence.model_validate(e, context={"execution_mode": "fixture"})
                    for e in response.evidence
                ]
                if len({e.evidence_id for e in batch}) != len(batch) or any(
                    e.schema_version != schema_version
                    or not (
                        (e.scope == "company" and e.candidate_id == cid)
                        or (e.scope == "industry" and e.candidate_id is None)
                    )
                    or (
                        e.evidence_id in collected
                        and e.model_dump(mode="json") != collected[e.evidence_id]
                    )
                    for e in batch
                ):
                    raise ResearchLoopFailure("RESEARCH_RESPONSE_INVALID")
                new = [e for e in batch if e.evidence_id not in collected]
                if new:
                    new_evidence_ids.update(e.evidence_id for e in new)
                    evidence_revision += 1
                    evidence.extend(new)
                    collected.update(
                        {e.evidence_id: e.model_dump(mode="json") for e in new}
                    )
                stage = "coverage"
                coverage = recompute_coverage()
            if coverage.research_ready:
                stop_reasons[cid] = "ready"
                timed("research_ready", cid, lambda: None)
            stage = "freeze"
            if (
                evidence_revision != initial_revision
                and stages.freeze_with_evidence is None
            ):
                raise ResearchLoopFailure("RESEARCH_FREEZE_REQUIRED")
            freeze_eligibility = eligibility.model_copy(
                deep=True, update={"evidence_revision": evidence_revision}
            )

            def freeze_snapshot():
                payload = (
                    stages.freeze_with_evidence(
                        deepcopy(candidate),
                        freeze_eligibility,
                        coverage.model_copy(deep=True),
                        tuple(deepcopy(evidence)),
                    )
                    if stages.freeze_with_evidence
                    else stages.freeze(
                        deepcopy(candidate),
                        freeze_eligibility,
                        coverage.model_copy(deep=True),
                    )
                )
                return EvaluationSnapshot.model_validate(
                    payload, context={"execution_mode": "fixture"}
                )

            snapshot = timed("freeze", cid, freeze_snapshot, list(collected))
            validate_snapshot_generation_v3(
                snapshot,
                run_id=run_id,
                cid=cid,
                schema_version=schema_version,
                policy=policy,
                coverage=coverage,
            )
            stage = "freeze_admission"
            validate_snapshot_admission_v3(
                snapshot,
                run_id=run_id,
                cid=cid,
                new_evidence_ids=new_evidence_ids,
                collected=collected,
            )
            stage = "evaluate"
            frozen = snapshot.model_dump(mode="json")
            graph_state = dict(
                snapshot_v3=frozen,
                current_candidate_id=cid,
                evaluation_rounds={cid: snapshot.evaluation_round},
                evidence_revisions={cid: snapshot.evidence_revision},
                run_input={
                    "execution_mode": "fixture",
                    "policy_version": policy.policy_version,
                },
                snapshots={snapshot.snapshot_id: frozen},
                candidates=candidates,
                candidate_index=index,
                candidate_outcomes={},
                candidate_status={},
                errors=[],
            )
            evaluated = timed(
                "evaluation_join",
                cid,
                lambda: graph.invoke(graph_state),
                [snapshot.snapshot_id],
            )
            if evaluated["evaluation_status_v3"] != "success":
                failure_ids = set(evaluated["evaluation_failure_ids_v3"])
                failures = [
                    WorkflowError.model_validate(e)
                    for e in evaluated["errors"]
                    if e["error_id"] in failure_ids and e.get("candidate_id") == cid
                ]
                if not failures or evaluated["candidate_index"] != index + 1:
                    raise ValueError("evaluation failure did not archive/advance once")
                errors.extend(failures)
                finish("failed", cid, failures=failures)
                advance(cid)
                continue
            if (
                evaluated["candidate_index"] != index
                or len(evaluated["evaluations_v3"]) != 6
            ):
                raise ValueError("evaluation advanced or promoted partial dimensions")
            dims = []
            for dimension in (
                "founder",
                "market",
                "technology",
                "moat",
                "traction",
                "deal_terms",
            ):
                key = evaluation_key(cid, snapshot.evaluation_round, dimension)
                dims.append(Evaluation.model_validate(evaluated["evaluations_v3"][key]))
            stage = "score"
            summary = timed(
                "score",
                cid,
                lambda: aggregate_scores_v3(
                    dims,
                    policy,
                    applicability_verifier=applicability_verifier,
                    snapshot=snapshot,
                ),
                [snapshot.snapshot_id],
            )
            decision = timed(
                "decision",
                cid,
                lambda: decide_v3(summary, policy),
                [summary.score_summary_id],
            )
            scores[cid] = summary
            decisions[cid] = decision
            finish(
                "recommend"
                if decision.label.startswith("RECOMMEND")
                else decision.label.lower(),
                cid,
                eligibility,
                decision,
            )
        except (ZeroDenominatorV3, NoApplicableCriteria) as exc:
            dimension = getattr(exc, "dimension", None) or "total"
            failure = error(
                stage,
                cid,
                f"Zero applicable denominator: {dimension}",
                error_code="ZERO_APPLICABLE_DENOMINATOR",
            )
            errors.append(failure)
            finish("failed", cid, failures=[failure])
        except ResearchLoopFailure as exc:
            failure = error(
                stage, cid, error_code=exc.code, attempt=max(1, retry_counts[cid])
            )
            errors.append(failure)
            finish("failed", cid, failures=[failure])
        except Exception:
            failure = error(
                stage,
                cid,
                error_code="SNAPSHOT_INVALID"
                if stage in ("freeze", "freeze_admission")
                else "UPSTREAM_INVALID",
            )
            errors.append(failure)
            finish("failed", cid, failures=[failure])
        advance(cid)

    rows: list[dict[str, Any]] = []
    for cid in sorted(outcomes):
        summary = scores.get(cid)
        decision = decisions.get(cid)
        rows.append(
            dict(
                candidate_id=cid,
                eligibility_status="eligible" if summary else "unknown",
                status="evaluated" if summary else outcomes[cid].status,
                label=decision.label if decision else None,
                normalized_score=summary.normalized_score if summary else None,
                weighted_missing_pct=summary.weighted_missing_pct if summary else None,
                applicable_weight=summary.applicable_weight if summary else None,
                score_summary_id=summary.score_summary_id if summary else None,
            )
        )
    selection = timed(
        "selector",
        None,
        lambda: select_best_v3(
            rows, policy, run_id=run_id, schema_version=schema_version
        ),
        [item.score_summary_id for item in scores.values()],
    )
    if not candidates:
        status = "no_candidates"
    elif scores:
        status = "ready_for_v3_reporting"
    elif all(outcome.status == "failed" for outcome in outcomes.values()):
        status = "all_eligible_failed"
    elif any(outcome.status == "failed" for outcome in outcomes.values()):
        status = "eligible_failed_no_success"
    else:
        status = "no_eligible_candidates"
    return CandidateRunV3(
        schema_version,
        run_id,
        policy.policy_version,
        status,
        index,
        outcomes,
        scores,
        decisions,
        selection,
        tuple(errors),
        research_retry_count=retry_counts,
        research_stop_reasons=stop_reasons,
        coverage_results=coverages,
        research_gaps=research_gaps,
    )


def validate_snapshot_generation_v3(
    snapshot, *, run_id, cid, schema_version, policy, coverage
):
    """Pin the frozen generation before admitting its evidence closure."""
    if (
        snapshot.run_id != run_id
        or snapshot.candidate_id != cid
        or snapshot.schema_version != schema_version
        or snapshot.policy_version != policy.policy_version
        or snapshot.evidence_revision != coverage.evidence_revision
        or snapshot.evaluation_round < 1
    ):
        raise ValueError("frozen snapshot generation mismatch")


def validate_snapshot_admission_v3(
    snapshot, *, run_id, cid, new_evidence_ids, collected
):
    """Reuse the collector-to-Source/Record/Chunk attribution admission gate."""
    if not new_evidence_ids <= snapshot.evidence.keys():
        raise ValueError("snapshot omitted admitted research Evidence")
    if any(
        key not in collected or item.model_dump(mode="json") != collected[key]
        for key, item in snapshot.evidence.items()
    ):
        raise ValueError("snapshot Evidence not admitted by collector")
    for item in snapshot.evidence.values():
        if item.source_id not in snapshot.sources:
            raise ValueError("snapshot missing Evidence Source")
        if any(
            dep not in snapshot.evidence
            for dep in (*item.supporting_evidence_ids, *item.conflicts_with)
        ):
            raise ValueError("snapshot missing related Evidence")
        for path in item.provenance:
            record = snapshot.retrieval_records.get(path.retrieval_id)
            if (
                record is None
                or record.run_id != run_id
                or record.candidate_id not in (None, cid)
                or record.status != "ok"
                or record.started_at > record.finished_at
                or item.source_id not in record.source_ids
                or item.evidence_id not in record.evidence_ids
                or (path.chunk_id is not None and path.chunk_id not in record.chunk_ids)
            ):
                raise ValueError("snapshot RetrievalRecord attribution mismatch")
            if path.chunk_id is not None:
                chunk = snapshot.chunks.get(path.chunk_id)
                if (
                    chunk is None
                    or chunk.source_id != item.source_id
                    or chunk.corpus_version != snapshot.corpus_version
                    or chunk.locator != item.locator
                    or chunk.scope != item.scope
                    or item.excerpt not in chunk.text
                    or (item.scope == "company" and cid not in chunk.candidate_ids)
                ):
                    raise ValueError("snapshot Chunk attribution mismatch")
