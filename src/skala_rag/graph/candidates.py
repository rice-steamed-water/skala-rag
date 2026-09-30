"""Fixture candidate loop; injected stages, deterministic decision and handoff."""

import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from langgraph.graph import END, START, StateGraph

from skala_rag.contracts.candidates import Candidate, CompanyProfile, EligibilityResult
from skala_rag.contracts.common import Contract, Text
from skala_rag.contracts.coverage import CoverageResult, ResearchGap
from skala_rag.contracts.decisions import ScoreSummary
from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.evaluation import (
    Evaluation,
    EvaluationResult,
    EvaluationSnapshot,
)
from skala_rag.contracts.evidence import Evidence
from skala_rag.contracts.ids import evaluation_key
from skala_rag.contracts.inputs import RunInput
from skala_rag.contracts.reports import CandidateOutcome, ReportInput
from skala_rag.contracts.retrieval import RetrievalRecord
from skala_rag.contracts.sources import Chunk, Source
from skala_rag.contracts.state import InvestmentState
from skala_rag.graph.reducers import merge_evidence, merge_result_maps, merge_sources
from skala_rag.graph.snapshot import SnapshotInvalid
from skala_rag.scoring.catalog import ScoringPolicy
from skala_rag.scoring.decide import decide
from skala_rag.scoring.summary import build_investment_decision

Stage = Callable[[InvestmentState], Mapping[str, Any]]


class Explanation(Contract):
    """An explanation has no writable label, grade, score or policy fields."""

    rationale: Text
    risks: list[Text]
    limitations: list[Text]
    evidence_ids: list[Text]


@dataclass(frozen=True)
class CandidateNodes:
    discover: Stage
    normalize: Stage
    research: Stage
    eligibility: Stage
    collect: Stage
    coverage: Stage
    freeze: Stage
    evaluate: Stage
    aggregate: Stage
    explain: Callable[[InvestmentState], Explanation]


class StageFailure(Exception):
    """A stage's already-redacted terminal errors, not missing observations."""

    def __init__(self, errors: list[WorkflowError]):
        if not errors:
            raise ValueError("StageFailure requires at least one WorkflowError")
        super().__init__("Candidate graph stage failed")
        self.errors = errors


_WRITERS = {
    "discover": {"candidates", "sources", "retrieval_history"},
    "normalize": {"candidates"},
    "research": {
        "company_profiles",
        "sources",
        "evidence",
        "retrieval_history",
        "evidence_revisions",
    },
    "eligibility": {"eligibility_results"},
    "collect": {
        "sources",
        "chunks",
        "evidence",
        "retrieval_history",
        "evidence_revisions",
    },
    "coverage": {"coverage_results", "research_gaps"},
    "freeze": {"snapshots", "evaluation_rounds"},
    "evaluate": {"evaluation_results", "evaluations"},
    "aggregate": {"score_summaries"},
}
_REDUCER_MAPS = {"sources", "chunks", "evidence", "evaluation_results"}
_PAYLOAD_MAPS = {
    "sources": (Source, "source_id"),
    "chunks": (Chunk, "chunk_id"),
    "evidence": (Evidence, "evidence_id"),
    "company_profiles": (CompanyProfile, "candidate_id"),
    "eligibility_results": (EligibilityResult, "candidate_id"),
    "coverage_results": (CoverageResult, "candidate_id"),
    "snapshots": (EvaluationSnapshot, "snapshot_id"),
    "evaluations": (Evaluation, None),
    "evaluation_results": (EvaluationResult, None),
    "score_summaries": (ScoreSummary, "candidate_id"),
}
_CANDIDATE_MAPS = {
    "company_profiles",
    "eligibility_results",
    "coverage_results",
    "research_gaps",
    "evidence_revisions",
    "evaluation_rounds",
    "score_summaries",
}


def build_candidate_graph(
    nodes: CandidateNodes,
    policy: ScoringPolicy,
    *,
    run_id: str,
    schema_version: str,
    clock: Callable[[], datetime],
):
    """Return a StateGraph ending at ReportInput, without report generation.

    Stages receive detached State and return JSON deltas restricted to their writers.
    No defaults enable live providers or unresolved v3 policy. Compile/invoke is up
    to the runner; for five candidates use a recursion_limit above the stage count.

    Coverage gaps send the candidate back to the same collect stage (v3 Evidence
    Research) at most ``max_research_retries_per_candidate`` times. The count is
    spent before each request and never refunded; once spent, open gaps become
    exhausted and stay missing. Post-evaluation research is not wired (#82).
    """

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    require(isinstance(run_id, str) and bool(run_id.strip()), "Missing run_id")
    require(
        isinstance(schema_version, str) and bool(schema_version.strip()),
        "Missing schema_version",
    )

    def run_input(state):
        run = RunInput.model_validate(state["run_input"])
        require(run.execution_mode == "fixture", "Candidate graph is fixture-only")
        require(run.policy_version == policy.policy_version, "Policy version mismatch")
        return run

    def fail(state, name, errors=None):
        cid = state.get("current_candidate_id")
        if errors is None:
            errors = [
                WorkflowError(
                    schema_version=schema_version,
                    error_id=f"graph:{run_id}:{name}:{state.get('candidate_index', 0)}",
                    run_id=run_id,
                    candidate_id=cid,
                    node=name,
                    error_code="UPSTREAM_INVALID",
                    message_redacted="Invalid candidate graph stage",
                    retryable=False,
                    attempt=1,
                    timestamp=clock(),
                )
            ]
        validated = [WorkflowError.model_validate(error) for error in errors]
        if not all(
            e.run_id == run_id and e.candidate_id in (None, cid) for e in validated
        ):
            return fail(state, name)
        validated = [
            error.model_copy(update={"candidate_id": cid}) for error in validated
        ]
        update = {"errors": [e.model_dump(mode="json") for e in validated]}
        if cid is None:
            update.update(workflow_status="failed", run_outcome="technical_failure")
        else:
            update["candidate_status"] = {**state["candidate_status"], cid: "failed"}
        return update

    def stage(name, recoverable=False):
        callback = getattr(nodes, name)

        def call(state):
            detached = deepcopy(state)
            try:
                run_input(state)
                delta = dict(callback(detached))
                require(set(delta) <= _WRITERS[name], "Stage writer violation")
                delta = json.loads(json.dumps(delta, allow_nan=False))
                context = {"execution_mode": "fixture"}
                cid = state.get("current_candidate_id")
                for field, value in delta.items():
                    if field in _PAYLOAD_MAPS:
                        model, identifier = _PAYLOAD_MAPS[field]
                        for key, payload in value.items():
                            dto = model.model_validate(payload, context=context)
                            expected = (
                                getattr(dto, identifier)
                                if identifier
                                else evaluation_key(
                                    dto.candidate_id,
                                    dto.evaluation_round,
                                    dto.dimension,
                                )
                            )
                            require(key == expected, "Stage payload map key mismatch")
                            if hasattr(dto, "candidate_id"):
                                require(
                                    dto.candidate_id in (None, cid)
                                    or state.get(field, {}).get(key) == payload,
                                    "Stage payload belongs to another candidate",
                                )
                    if field in _CANDIDATE_MAPS:
                        require(
                            all(
                                key == cid or state.get(field, {}).get(key) == payload
                                for key, payload in value.items()
                            ),
                            "Stage modifies another candidate",
                        )
                    if field in ("evidence_revisions", "evaluation_rounds"):
                        require(
                            all(
                                type(n) is int and n >= state.get(field, {}).get(key, 0)
                                for key, n in value.items()
                            ),
                            "Invalid stage counter",
                        )
                    if field == "research_gaps":
                        for key, gaps in value.items():
                            require(
                                all(
                                    ResearchGap.model_validate(gap).candidate_id == key
                                    for gap in gaps
                                ),
                                "ResearchGap attribution mismatch",
                            )
                    if field in _REDUCER_MAPS or field in ("snapshots", "evaluations"):
                        merger = {
                            "sources": merge_sources,
                            "evidence": merge_evidence,
                        }.get(field, merge_result_maps)
                        # Validate conflicts before LangGraph applies its reducer.
                        merger(state.get(field, {}), value)
                if "retrieval_history" in delta:
                    records = {}
                    for payload in [
                        *state.get("retrieval_history", []),
                        *delta["retrieval_history"],
                    ]:
                        record = RetrievalRecord.model_validate(payload)
                        require(record.run_id == run_id, "Retrieval run mismatch")
                        records = merge_result_maps(
                            records, {record.retrieval_id: payload}
                        )
                    delta["retrieval_history"] = list(records.values())
                for field, value in list(delta.items()):
                    if isinstance(value, dict) and field not in _REDUCER_MAPS:
                        delta[field] = {**state.get(field, {}), **value}
                return delta
            except SnapshotInvalid:
                recorded = [
                    e
                    for e in detached.get("errors", [])
                    if e not in state.get("errors", [])
                ]
                return fail(state, name, recorded or None)
            except StageFailure as exc:
                update = fail(state, name, exc.errors)
                if recoverable:
                    # A failed research retry is spent; its gaps stay missing.
                    update.pop("candidate_status", None)
                return update
            except Exception:
                return fail(state, name)

        return call

    def collect(state):
        cid = state["current_candidate_id"]
        return stage("collect", recoverable=state["research_retry_count"][cid] > 0)(
            state
        )

    def coverage(state):
        """Coverage recomputes the candidate's whole gap list on every pass."""
        delta = stage("coverage")(state)
        cid = state["current_candidate_id"]
        if delta.get("candidate_status", {}).get(cid) == "failed":
            return delta
        if cid not in delta.get("research_gaps", {}):
            gaps = delta.get("research_gaps", state["research_gaps"])
            delta["research_gaps"] = {**gaps, cid: []}
        return delta

    def research_gate(state):
        """Spend a retry before research, or mark open gaps exhausted."""
        try:
            cid = state["current_candidate_id"]
            gaps = [
                ResearchGap.model_validate(gap)
                for gap in state["research_gaps"].get(cid, [])
            ]
            require(all(gap.candidate_id == cid for gap in gaps), "Foreign gap")
            if not any(gap.status == "open" for gap in gaps):
                return {}
            used = state["research_retry_count"][cid]
            if used < policy.budgets.max_research_retries_per_candidate:
                return {
                    "research_retry_count": {
                        **state["research_retry_count"],
                        cid: used + 1,
                    }
                }
            exhausted = [
                gap.model_copy(update={"status": "exhausted"})
                if gap.status == "open"
                else gap
                for gap in gaps
            ]
            return {
                "research_gaps": {
                    **state["research_gaps"],
                    cid: [gap.model_dump(mode="json") for gap in exhausted],
                }
            }
        except Exception:
            return fail(state, "research_gate")

    def after_gate(state):
        cid = state["current_candidate_id"]
        if state["candidate_status"][cid] == "failed":
            return "archive"
        gaps = state["research_gaps"].get(cid, [])
        return "collect" if any(g["status"] == "open" for g in gaps) else "freeze"

    def validate_candidates(state):
        run = run_input(state)
        candidates = [
            Candidate.model_validate(c, context={"execution_mode": run.execution_mode})
            for c in state["candidates"]
        ]
        require(
            len({c.candidate_id for c in candidates}) == len(candidates),
            "Duplicate candidate IDs",
        )
        require(
            all(
                set(c.discovery_source_ids) <= set(state["sources"]) for c in candidates
            ),
            "Missing discovery Source",
        )
        for cid in {sid for c in candidates for sid in c.discovery_source_ids}:
            source = Source.model_validate(
                state["sources"][cid], context={"execution_mode": "fixture"}
            )
            require(source.source_id == cid, "Discovery Source key mismatch")
        limited = candidates[: policy.budgets.max_candidates]
        return {"candidates": [c.model_dump(mode="json") for c in limited]}

    def normalize(state):
        delta = stage("normalize")(state)
        if delta.get("workflow_status") == "failed":
            return delta
        try:
            merged = {**state, **delta}
            return validate_candidates(merged)
        except (ValueError, TypeError, KeyError):
            return fail(state, "normalize")

    def left(state):
        if state["workflow_status"] == "failed":
            return "end"
        return (
            "select"
            if state["candidate_index"] < len(state["candidates"])
            else "summary"
        )

    def select(state):
        cid = state["candidates"][state["candidate_index"]]["candidate_id"]
        require(cid not in state["candidate_outcomes"], "Candidate already archived")
        update = {
            "current_candidate_id": cid,
            "candidate_status": {**state["candidate_status"], cid: "researching"},
        }
        for field in (
            "research_retry_count",
            "evaluation_rounds",
            "evidence_revisions",
        ):
            values = dict(state[field])
            values.setdefault(cid, 0)
            update[field] = values
        return update

    def eligibility(state):
        delta = stage("eligibility")(state)
        if (
            state["current_candidate_id"] in delta.get("candidate_status", {})
            and delta["candidate_status"][state["current_candidate_id"]] == "failed"
        ):
            return delta
        try:
            cid = state["current_candidate_id"]
            result = EligibilityResult.model_validate(
                delta.get("eligibility_results", {}).get(cid)
            )
            run = run_input(state)
            require(
                result.run_id == run_id
                and result.candidate_id == cid
                and result.policy_version == run.policy_version
                and result.as_of == run.as_of,
                "Eligibility context mismatch",
            )
            statuses = {
                "eligible": "evaluating",
                "ineligible": "ineligible",
                "unknown": "eligibility_unknown",
            }
            delta["candidate_status"] = {
                **state["candidate_status"],
                cid: statuses[result.status],
            }
            return delta
        except (ValueError, TypeError, KeyError):
            return fail(state, "eligibility")

    def outcome(state, status):
        cid = state["current_candidate_id"]
        eligible = state["eligibility_results"].get(cid, {})
        decision = state["investment_decisions"].get(cid, {})
        if status == "failed":
            eligible, decision = {}, {}
        failures = [
            e["error_id"] for e in state["errors"] if e.get("candidate_id") == cid
        ]
        return CandidateOutcome(
            schema_version=schema_version,
            candidate_id=cid,
            status=status,
            eligibility_result_id=eligible.get("eligibility_result_id"),
            decision_id=decision.get("decision_id"),
            failure_ids=failures,
            summary_reason="; ".join(
                decision.get("reason_codes") or eligible.get("reason_codes") or [status]
            ),
        ).model_dump(mode="json")

    def archive(state):
        cid = state["current_candidate_id"]
        require(cid not in state["candidate_outcomes"], "Candidate outcome overwrite")
        return {
            "candidate_outcomes": {
                **state["candidate_outcomes"],
                cid: outcome(state, state["candidate_status"][cid]),
            }
        }

    def advance(state):
        cid = state["current_candidate_id"]
        require(
            cid == state["candidates"][state["candidate_index"]]["candidate_id"]
            and cid in state["candidate_outcomes"],
            "Invalid candidate advance",
        )
        return {
            "candidate_index": state["candidate_index"] + 1,
            "current_candidate_id": None,
        }

    def decision(state):
        try:
            cid = state["current_candidate_id"]
            summary = ScoreSummary.model_validate(state["score_summaries"][cid])
            snapshot = EvaluationSnapshot.model_validate(
                state["snapshots"][summary.snapshot_id],
                context={"execution_mode": "fixture"},
            )
            require(
                summary.run_id == run_id
                and summary.candidate_id == cid
                and summary.policy_version == policy.policy_version
                and summary.evaluation_round == state["evaluation_rounds"][cid]
                and summary.evidence_revision == state["evidence_revisions"][cid],
                "Score context mismatch",
            )
            require(
                all(
                    getattr(summary, field) == getattr(snapshot, field)
                    for field in (
                        "run_id",
                        "candidate_id",
                        "evaluation_round",
                        "evidence_revision",
                        "policy_version",
                    )
                ),
                "Score snapshot mismatch",
            )
            immutable = decide(
                summary.observed_score,
                summary.missing_weight,
                summary.dimension_ratings,
                policy.thresholds,
            )
            view = deepcopy(state)
            # Supply the computed policy result, never a mutable State decision.
            view["investment_decisions"] = {
                cid: {
                    "label": immutable.label,
                    "report_grade": immutable.report_grade,
                    "reason_codes": list(immutable.reason_codes),
                }
            }
            explanation = Explanation.model_validate(nodes.explain(view))
            require(
                set(explanation.evidence_ids) <= set(snapshot.evidence),
                "Explanation cites outside snapshot",
            )
            result = build_investment_decision(
                summary,
                policy,
                schema_version=schema_version,
                evidence_ids=explanation.evidence_ids,
                rationale=explanation.rationale,
                risks=explanation.risks,
                limitations=explanation.limitations,
            )
            return {
                "investment_decisions": {
                    **state["investment_decisions"],
                    cid: result.model_dump(mode="json"),
                },
                "candidate_status": {
                    **state["candidate_status"],
                    cid: result.label.lower(),
                },
            }
        except StageFailure as exc:
            return fail(state, "decision", exc.errors)
        except Exception:
            return fail(state, "decision")

    def handoff(state, selected=None):
        outcomes = dict(state["candidate_outcomes"])
        statuses = dict(state["candidate_status"])
        if selected is not None:
            outcomes[selected] = outcome(state, "recommend")
            for candidate in state["candidates"][state["candidate_index"] + 1 :]:
                cid = candidate["candidate_id"]
                statuses[cid] = "not_evaluated"
                outcomes[cid] = CandidateOutcome(
                    schema_version=schema_version,
                    candidate_id=cid,
                    status="not_evaluated",
                    failure_ids=[],
                    summary_reason="Stopped at first RECOMMEND (D03)",
                ).model_dump(mode="json")
        permitted = set()
        for cid, item in outcomes.items():
            if item["status"] in ("not_evaluated", "failed"):
                continue
            eligible = state["eligibility_results"].get(cid)
            if eligible:
                permitted.update(eligible["evidence_ids"])
            score = state["score_summaries"].get(cid)
            if score and item["decision_id"]:
                permitted.update(
                    state["snapshots"][score["snapshot_id"]]["evidence_ids"]
                )
        run = run_input(state)
        report = ReportInput(
            schema_version=schema_version,
            run_id=run_id,
            mode="single_candidate" if selected else "no_recommendation",
            selected_candidate_id=selected,
            candidate_outcomes=[
                outcomes[c["candidate_id"]] for c in state["candidates"]
            ],
            permitted_evidence_ids=sorted(permitted),
            as_of=run.as_of,
            corpus_version=run.corpus_version,
            policy_version=run.policy_version,
        )
        all_failed = bool(outcomes) and all(
            o["status"] == "failed" for o in outcomes.values()
        )
        if selected:
            result = "recommended"
        elif not outcomes:
            result = "no_candidates"
        elif all_failed:
            result = "technical_failure"
        elif any(
            o["status"] in ("eligibility_unknown", "failed")
            or "INSUFFICIENT_EVIDENCE" in o["summary_reason"]
            for o in outcomes.values()
        ):
            result = "insufficient_evidence"
        else:
            result = "no_recommendation"
        return {
            "report_input": None if all_failed else report.model_dump(mode="json"),
            "candidate_outcomes": outcomes,
            "candidate_status": statuses,
            "selected_candidate_id": selected,
            "run_outcome": result,
            "workflow_status": "failed" if all_failed else "running",
        }

    def after_stage(state):
        cid = state["current_candidate_id"]
        return "archive" if state["candidate_status"][cid] == "failed" else "next"

    graph = StateGraph(InvestmentState)
    for name in _WRITERS:
        graph.add_node(
            name,
            {
                "normalize": normalize,
                "eligibility": eligibility,
                "collect": collect,
                "coverage": coverage,
            }.get(name)
            or stage(name),
        )
    for name, function in [
        ("research_gate", research_gate),
        ("select", select),
        ("archive", archive),
        ("advance", advance),
        ("decision", decision),
        ("selection", lambda state: handoff(state, state["current_candidate_id"])),
        ("summary", handoff),
    ]:
        graph.add_node(name, function)
    graph.add_edge(START, "discover")
    graph.add_conditional_edges(
        "discover",
        lambda state: "end" if state["workflow_status"] == "failed" else "normalize",
        {"end": END, "normalize": "normalize"},
    )
    graph.add_conditional_edges(
        "normalize", left, {"end": END, "select": "select", "summary": "summary"}
    )
    graph.add_edge("select", "research")
    graph.add_conditional_edges(
        "research", after_stage, {"archive": "archive", "next": "eligibility"}
    )
    graph.add_conditional_edges(
        "eligibility",
        lambda state: (
            "collect"
            if state["candidate_status"][state["current_candidate_id"]] == "evaluating"
            else "archive"
        ),
        {"collect": "collect", "archive": "archive"},
    )
    for name, following in [
        ("collect", "coverage"),
        ("coverage", "research_gate"),
        ("freeze", "evaluate"),
        ("evaluate", "aggregate"),
        ("aggregate", "decision"),
    ]:
        graph.add_conditional_edges(
            name, after_stage, {"archive": "archive", "next": following}
        )
    graph.add_conditional_edges(
        "research_gate",
        after_gate,
        {"archive": "archive", "collect": "collect", "freeze": "freeze"},
    )
    graph.add_conditional_edges(
        "decision",
        lambda state: (
            "selection"
            if state["candidate_status"][state["current_candidate_id"]] == "recommend"
            else "archive"
        ),
        {"selection": "selection", "archive": "archive"},
    )
    graph.add_edge("archive", "advance")
    graph.add_conditional_edges(
        "advance", left, {"end": END, "select": "select", "summary": "summary"}
    )
    graph.add_edge("selection", END)
    graph.add_edge("summary", END)
    return graph
