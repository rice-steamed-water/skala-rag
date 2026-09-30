"""T07/T08/T09/T20 on real LangGraph, wholly synthetic stage outputs and evidence."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.evaluation import EvaluationResult
from skala_rag.contracts.ids import eligibility_result_id, evaluation_key
from skala_rag.contracts.inputs import RunInput
from skala_rag.contracts.reports import ReportInput
from skala_rag.contracts.state import create_initial_state
from skala_rag.graph.candidates import (
    CandidateNodes,
    Explanation,
    StageFailure,
    build_candidate_graph,
)
from skala_rag.graph.snapshot import freeze_snapshot
from skala_rag.scoring.aggregate import DIMENSIONS
from skala_rag.scoring.catalog import load_policy
from skala_rag.scoring.summary import build_score_summary


@pytest.fixture
def harness():
    policy = load_policy("configs/scoring.draft.json", execution_mode="fixture")
    base = json.loads(
        (Path(__file__).parents[1] / "fixtures/contracts.json").read_text()
    )
    payload = deepcopy(base["RunInput"])
    payload["policy_version"] = policy.policy_version
    run = RunInput.model_validate(payload)
    calls = []

    def make(cases):
        candidates = []
        for i, _ in enumerate(cases):
            candidate = deepcopy(base["Candidate"])
            candidate.update(candidate_id=f"co-{i}", canonical_name=f"Synthetic {i}")
            candidates.append(candidate)

        def current(state):
            cid = state["current_candidate_id"]
            return cid, cases[int(cid.split("-")[1])]

        def discover(state):
            return {
                "candidates": candidates,
                "sources": {"src-synthetic": deepcopy(base["Source"])},
            }

        def research(state):
            cid, _ = current(state)
            calls.append(
                (
                    "research",
                    cid,
                    state["research_retry_count"][cid],
                    state["evaluation_rounds"][cid],
                    state["evidence_revisions"][cid],
                )
            )
            return {}

        def collect(state):
            cid, _ = current(state)
            ev = deepcopy(base["Evidence"])
            ev.update(
                evidence_id=f"ev-{cid}",
                candidate_id=cid,
                criterion_ids=[c.criterion_id for c in policy.criteria],
            )
            ev["provenance"][0].update(
                retrieval_id=f"ret-{cid}", chunk_id=f"chunk-{cid}"
            )
            chunk = deepcopy(base["Chunk"])
            chunk.update(chunk_id=f"chunk-{cid}", candidate_ids=[cid])
            record = deepcopy(base["RetrievalRecord"])
            record.update(
                retrieval_id=f"ret-{cid}",
                candidate_id=cid,
                status="ok",
                source_ids=["src-synthetic"],
                chunk_ids=[chunk["chunk_id"]],
                evidence_ids=[ev["evidence_id"]],
            )
            return {
                "evidence": {ev["evidence_id"]: ev},
                "chunks": {chunk["chunk_id"]: chunk},
                "retrieval_history": [*state["retrieval_history"], record],
            }

        def eligibility(state):
            cid, case = current(state)
            item = deepcopy(base["EligibilityResult"])
            item.update(
                eligibility_result_id=eligibility_result_id(
                    "run-synthetic", cid, 0, policy.policy_version
                ),
                candidate_id=cid,
                policy_version=policy.policy_version,
                status=case if case in ("ineligible", "unknown") else "eligible",
                reason_codes=[f"SYNTHETIC:{case}"],
                evidence_ids=[f"ev-{cid}"]
                if case not in ("ineligible", "unknown")
                else [],
            )
            return {"eligibility_results": {cid: item}}

        def freeze(state):
            cid, _ = current(state)
            snapshot = freeze_snapshot(
                cid,
                state,
                run,
                run_id="run-synthetic",
                index_version="synthetic-index",
                schema_version="synthetic-1",
                allowed_source_ids={"src-synthetic"},
                industry_evidence_ids=set(),
                clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
            )
            return {
                "snapshots": {snapshot.snapshot_id: snapshot.model_dump(mode="json")},
                "evaluation_rounds": {cid: snapshot.evaluation_round},
            }

        def evaluate(state):
            cid, case = current(state)
            calls.append(("evaluate", cid))
            snapshot = next(
                s for s in state["snapshots"].values() if s["candidate_id"] == cid
            )
            results = {}
            for dimension in DIMENSIONS:
                criteria = []
                for i, criterion in enumerate(
                    c for c in policy.criteria if c.dimension == dimension
                ):
                    rating = 4 if case == "recommend" else 3
                    if case == "pass" and i == 0:
                        rating = 2
                    criteria.append(
                        dict(
                            schema_version="synthetic-1",
                            criterion_id=criterion.criterion_id,
                            status="observed",
                            rating=rating,
                            evidence_ids=[f"ev-{cid}"],
                            rationale="Synthetic assumed rating; unmeasured",
                        )
                    )
                ev = dict(
                    schema_version="synthetic-1",
                    run_id="run-synthetic",
                    candidate_id=cid,
                    dimension=dimension,
                    evaluation_round=snapshot["evaluation_round"],
                    snapshot_id=snapshot["snapshot_id"],
                    evidence_revision=0,
                    policy_version=policy.policy_version,
                    rubric_version="synthetic-rubric",
                    criteria=criteria,
                    research_gaps=[],
                    caveats=["Synthetic only"],
                )
                result = EvaluationResult.model_validate(
                    {
                        **{
                            k: v
                            for k, v in ev.items()
                            if k
                            not in (
                                "rubric_version",
                                "criteria",
                                "research_gaps",
                                "caveats",
                            )
                        },
                        "status": "success",
                        "evaluation": ev,
                        "errors": [],
                    }
                )
                results[
                    evaluation_key(cid, snapshot["evaluation_round"], dimension)
                ] = result.model_dump(mode="json")
            return {"evaluation_results": results}

        def aggregate(state):
            cid, _ = current(state)
            values = [
                EvaluationResult.model_validate(value)
                for value in state["evaluation_results"].values()
                if value["candidate_id"] == cid
            ]
            score = build_score_summary(values, policy, schema_version="synthetic-1")
            return {"score_summaries": {cid: score.model_dump(mode="json")}}

        def explain(state):
            cid, _ = current(state)
            return Explanation(
                schema_version="synthetic-1",
                rationale="Synthetic explanation",
                risks=[],
                limitations=["Fixture, not measured"],
                evidence_ids=[f"ev-{cid}"],
            )

        nodes = CandidateNodes(
            discover=discover,
            normalize=lambda s: {},
            research=research,
            eligibility=eligibility,
            collect=collect,
            coverage=lambda s: {},
            freeze=freeze,
            evaluate=evaluate,
            aggregate=aggregate,
            explain=explain,
        )
        return nodes

    def execute(nodes, state=None):
        graph = build_candidate_graph(
            nodes,
            policy,
            run_id="run-synthetic",
            schema_version="synthetic-1",
            clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
        ).compile()
        return graph.invoke(
            state or create_initial_state(run.model_dump(mode="json")),
            {"recursion_limit": 200},
        )

    return make, execute, calls, run, policy


@pytest.mark.parametrize("case", ["ineligible", "unknown", "pass", "watchlist"])
def test_all_candidates_exhausted_once(harness, case):
    make, execute, calls, _, _ = harness
    result = execute(make([case, case, case]))
    report = ReportInput.model_validate(result["report_input"])
    expected = "eligibility_unknown" if case == "unknown" else case
    assert report.mode == "no_recommendation"
    assert [o.status for o in report.candidate_outcomes] == [expected] * 3
    assert result["candidate_index"] == 3
    assert result["current_candidate_id"] is None
    assert [call[1] for call in calls if call[0] == "research"] == [
        "co-0",
        "co-1",
        "co-2",
    ]
    if case in ("unknown", "ineligible"):
        assert not result["score_summaries"]
        assert not any(call[0] == "evaluate" for call in calls)


def test_watchlist_pass_then_first_recommend(harness):
    make, execute, calls, _, _ = harness
    result = execute(make(["watchlist", "pass", "recommend", "recommend"]))
    report = ReportInput.model_validate(result["report_input"])
    assert report.mode == "single_candidate"
    assert report.selected_candidate_id == "co-2"
    assert [o.status for o in report.candidate_outcomes] == [
        "watchlist",
        "pass",
        "recommend",
        "not_evaluated",
    ]
    assert result["candidate_index"] == 2
    assert "co-3" not in result["score_summaries"]
    assert not any(call[1] == "co-3" for call in calls)
    assert result["report"] is None and result["workflow_status"] == "running"


def test_zero_candidates_has_explicit_summary(harness):
    make, execute, _, _, _ = harness
    result = execute(make([]))
    assert result["run_outcome"] == "no_candidates"
    assert result["report_input"]["candidate_outcomes"] == []
    assert result["report_input"]["mode"] == "no_recommendation"


def test_candidate_maps_preserve_existing_counts(harness):
    make, execute, calls, run, _ = harness
    state = create_initial_state(run.model_dump(mode="json"))
    state["research_retry_count"]["co-0"] = 2
    result = execute(make(["ineligible", "ineligible"]), state)
    assert result["research_retry_count"] == {"co-0": 2, "co-1": 0}
    assert calls[:2] == [("research", "co-0", 2, 0, 0), ("research", "co-1", 0, 0, 0)]


def test_snapshot_failure_archives_and_advances(harness):
    make, execute, calls, _, _ = harness
    nodes = make(["recommend", "recommend"])
    good = nodes.collect

    def broken_first(state):
        update = good(state)
        if state["current_candidate_id"] == "co-0":
            update["chunks"] = {}
        return update

    result = execute(replace(nodes, collect=broken_first))
    assert result["candidate_outcomes"]["co-0"]["status"] == "failed"
    assert result["candidate_outcomes"]["co-0"]["eligibility_result_id"] is None
    assert result["candidate_outcomes"]["co-0"]["failure_ids"]
    assert result["errors"][0]["error_code"] == "SNAPSHOT_INVALID"
    assert result["selected_candidate_id"] == "co-1"
    assert ("evaluate", "co-0") not in calls
    assert "ev-co-0" not in result["report_input"]["permitted_evidence_ids"]


def test_all_technical_failures_do_not_produce_report_input(harness):
    make, execute, _, _, _ = harness
    nodes = make(["recommend", "recommend"])

    def failed(state):
        raise RuntimeError("secret external payload")

    result = execute(replace(nodes, research=failed))
    assert result["workflow_status"] == "failed"
    assert result["run_outcome"] == "technical_failure"
    assert result["report_input"] is None
    assert result["candidate_index"] == 2
    assert "secret external payload" not in json.dumps(result["errors"])


def test_explanation_cannot_override_policy_label(harness):
    make, execute, _, _, _ = harness
    nodes = make(["watchlist"])

    def malicious(state):
        return {
            "schema_version": "synthetic-1",
            "rationale": "Synthetic",
            "risks": [],
            "limitations": [],
            "evidence_ids": [],
            "label": "RECOMMEND",
        }

    result = execute(replace(nodes, explain=malicious))
    assert result["candidate_outcomes"]["co-0"]["status"] == "failed"
    assert result["selected_candidate_id"] is None
    assert not result["investment_decisions"]


def test_explanation_mutation_does_not_change_policy(harness):
    make, execute, _, _, _ = harness
    nodes = make(["watchlist"])
    original = nodes.explain

    def mutate(state):
        state["investment_decisions"]["co-0"]["label"] = "RECOMMEND"
        state["candidate_index"] = 99
        return original(state)

    result = execute(replace(nodes, explain=mutate))
    assert result["investment_decisions"]["co-0"]["label"] == "WATCHLIST"
    assert result["candidate_index"] == 1


def test_stage_cannot_write_candidate_controller_fields(harness):
    make, execute, _, _, _ = harness
    nodes = make(["recommend"])
    result = execute(replace(nodes, research=lambda state: {"candidate_index": 99}))
    assert result["candidate_index"] == 1
    assert result["candidate_outcomes"]["co-0"]["status"] == "failed"


def test_max_candidate_budget_applied_before_iteration(harness):
    make, execute, calls, _, policy = harness
    result = execute(make(["ineligible"] * (policy.budgets.max_candidates + 1)))
    assert len(result["candidates"]) == policy.budgets.max_candidates
    assert len(calls) == policy.budgets.max_candidates


def test_discovery_error_is_not_zero_candidates(harness):
    make, execute, _, _, _ = harness
    nodes = make([])

    def failed(state):
        raise RuntimeError("secret source data")

    result = execute(replace(nodes, discover=failed))
    assert result["workflow_status"] == "failed"
    assert result["run_outcome"] == "technical_failure"
    assert result["report_input"] is None


def test_tool_failure_attributed_to_current_candidate(harness):
    make, execute, _, _, _ = harness
    nodes = make(["recommend"])

    def failed(state):
        raise StageFailure(
            [
                WorkflowError(
                    schema_version="synthetic-1",
                    error_id="tool-error",
                    run_id="run-synthetic",
                    candidate_id=None,
                    node="research",
                    error_code="TOOL_TIMEOUT",
                    message_redacted="Synthetic timeout",
                    retryable=True,
                    attempt=1,
                    timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
                )
            ]
        )

    result = execute(replace(nodes, research=failed))
    assert result["candidate_outcomes"]["co-0"]["failure_ids"] == ["tool-error"]
    assert result["errors"][0]["error_code"] == "TOOL_TIMEOUT"


def test_live_input_rejected_before_discovery_call(harness):
    make, execute, calls, run, _ = harness
    state = create_initial_state(run.model_dump(mode="json"))
    state["run_input"]["execution_mode"] = "live"
    nodes = make(["recommend"])

    def should_not_run(state):
        calls.append(("external-call", None))
        raise AssertionError("Must not call live discovery")

    result = execute(replace(nodes, discover=should_not_run), state)
    assert not calls
    assert result["workflow_status"] == "failed"


@pytest.mark.parametrize(
    "kind", ["missing_source", "duplicate_candidate", "invalid_source"]
)
def test_discovery_reference_errors_are_redacted(harness, kind):
    make, execute, _, _, _ = harness
    nodes = make(["recommend"])
    original = nodes.discover

    def bad(state):
        delta = original(state)
        if kind == "missing_source":
            delta["sources"] = {}
        elif kind == "duplicate_candidate":
            delta["candidates"].append(deepcopy(delta["candidates"][0]))
        else:
            delta["sources"]["src-synthetic"]["source_kind"] = "secret-invalid"
        return delta

    result = execute(replace(nodes, discover=bad))
    assert result["workflow_status"] == "failed"
    assert "secret-invalid" not in json.dumps(result["errors"])


def test_stage_cannot_overwrite_other_candidate_result(harness):
    make, execute, _, _, _ = harness
    nodes = make(["ineligible", "ineligible"])
    original = nodes.eligibility

    def bad(state):
        delta = original(state)
        if state["current_candidate_id"] == "co-1":
            previous = deepcopy(state["eligibility_results"]["co-0"])
            previous["status"] = "eligible"
            delta["eligibility_results"]["co-0"] = previous
        return delta

    result = execute(replace(nodes, eligibility=bad))
    assert result["eligibility_results"]["co-0"]["status"] == "ineligible"
    assert result["candidate_outcomes"]["co-1"]["status"] == "failed"


def test_stage_receives_detached_state(harness):
    make, execute, _, _, _ = harness
    nodes = make(["ineligible"])

    def mutate(state):
        state["sources"].clear()
        state["candidate_index"] = 99
        return {}

    result = execute(replace(nodes, research=mutate))
    assert "src-synthetic" in result["sources"]
    assert result["candidate_index"] == 1


def test_source_core_conflict_fails_candidate_before_reducer(harness):
    make, execute, _, _, _ = harness
    nodes = make(["recommend", "recommend"])
    original = nodes.collect

    def bad_first(state):
        delta = original(state)
        if state["current_candidate_id"] == "co-0":
            source = deepcopy(state["sources"]["src-synthetic"])
            source["title"] = "Conflicting synthetic core"
            delta["sources"] = {source["source_id"]: source}
        return delta

    result = execute(replace(nodes, collect=bad_first))
    assert result["candidate_outcomes"]["co-0"]["status"] == "failed"
    assert result["selected_candidate_id"] == "co-1"
    assert result["sources"]["src-synthetic"]["title"] == "Synthetic document"


def test_explanation_cannot_cite_other_candidate_evidence(harness):
    make, execute, _, _, _ = harness
    nodes = make(["watchlist", "recommend"])
    original = nodes.explain

    def wrong_reference(state):
        result = original(state)
        if state["current_candidate_id"] == "co-1":
            result.evidence_ids = ["ev-co-0"]
        return result

    result = execute(replace(nodes, explain=wrong_reference))
    assert result["candidate_outcomes"]["co-0"]["status"] == "watchlist"
    assert result["candidate_outcomes"]["co-1"]["status"] == "failed"
    assert result["selected_candidate_id"] is None


# --- #25 coverage retry loop: per-candidate budget (T07, T20 fixture part) ---


def _gap(cid, status="open"):
    base = json.loads(
        (Path(__file__).parents[1] / "fixtures/contracts.json").read_text()
    )
    gap = deepcopy(base["ResearchGap"])
    gap.update(gap_id=f"gap-{cid}", candidate_id=cid, status=status)
    return gap


def _always_gap(state):
    cid = state["current_candidate_id"]
    return {"research_gaps": {cid: [_gap(cid)]}}


def _tracked_collect(nodes, calls, failing_retry=None):
    """Record the retry count each Evidence Research call sees."""
    original = nodes.collect

    def collect(state):
        cid = state["current_candidate_id"]
        n = state["research_retry_count"][cid]
        calls.append(("collect", cid, n))
        if n and failing_retry:
            raise failing_retry(cid, n)
        return original(state)

    return collect


def test_coverage_gap_retries_stop_at_candidate_budget(harness):
    make, execute, calls, _, policy = harness
    nodes = make(["recommend"])
    nodes = replace(nodes, coverage=_always_gap, collect=_tracked_collect(nodes, calls))
    result = execute(nodes)
    limit = policy.budgets.max_research_retries_per_candidate
    # Initial collection is excluded; each retry sees the count spent beforehand.
    assert [c for c in calls if c[0] == "collect"] == [
        ("collect", "co-0", n) for n in range(limit + 1)
    ]
    assert result["research_retry_count"]["co-0"] == limit
    assert [g["status"] for g in result["research_gaps"]["co-0"]] == ["exhausted"]
    # Exhaustion keeps the gap missing and proceeds to a single evaluation.
    assert [c for c in calls if c[0] == "evaluate"] == [("evaluate", "co-0")]
    assert result["selected_candidate_id"] == "co-0"


def test_coverage_gap_resolved_after_one_retry(harness):
    make, execute, calls, _, _ = harness
    nodes = make(["recommend"])

    def coverage(state):
        status = "open" if state["research_retry_count"]["co-0"] == 0 else "resolved"
        return {"research_gaps": {"co-0": [_gap("co-0", status)]}}

    result = execute(
        replace(nodes, coverage=coverage, collect=_tracked_collect(nodes, calls))
    )
    assert [c[2] for c in calls if c[0] == "collect"] == [0, 1]
    assert result["research_retry_count"]["co-0"] == 1
    assert result["research_gaps"]["co-0"][0]["status"] == "resolved"


def test_no_gap_makes_no_retry(harness):
    make, execute, calls, _, _ = harness
    nodes = make(["recommend"])
    result = execute(replace(nodes, collect=_tracked_collect(nodes, calls)))
    assert [c for c in calls if c[0] == "collect"] == [("collect", "co-0", 0)]
    assert result["research_retry_count"]["co-0"] == 0
    assert result["research_gaps"]["co-0"] == []


def test_evaluation_gaps_do_not_start_research(harness):
    make, execute, calls, _, _ = harness
    nodes = make(["recommend"])
    original = nodes.evaluate

    def evaluate(state):
        delta = original(state)
        for result in delta["evaluation_results"].values():
            result["evaluation"]["research_gaps"] = [_gap("co-0")]
        return delta

    result = execute(
        replace(nodes, evaluate=evaluate, collect=_tracked_collect(nodes, calls))
    )
    # v3 (#82): post-evaluation research is forbidden.
    assert [c for c in calls if c[0] == "collect"] == [("collect", "co-0", 0)]
    assert result["research_retry_count"]["co-0"] == 0
    assert result["evaluation_rounds"]["co-0"] == 1


def test_budget_is_per_candidate_and_history_preserved(harness):
    make, execute, calls, _, policy = harness
    nodes = make(["watchlist", "recommend"])
    nodes = replace(nodes, coverage=_always_gap, collect=_tracked_collect(nodes, calls))
    result = execute(nodes)
    limit = policy.budgets.max_research_retries_per_candidate
    seen = [c[1:] for c in calls if c[0] == "collect"]
    assert seen == [("co-0", n) for n in range(limit + 1)] + [
        ("co-1", n) for n in range(limit + 1)
    ]
    assert result["research_retry_count"] == {"co-0": limit, "co-1": limit}
    assert result["research_gaps"]["co-0"][0]["status"] == "exhausted"
    ids = {r["retrieval_id"] for r in result["retrieval_history"]}
    assert {"ret-co-0", "ret-co-1"} <= ids


def test_failed_retry_is_spent_without_refund(harness):
    make, execute, calls, _, policy = harness
    nodes = make(["recommend"])

    def timeout(cid, n):
        return StageFailure(
            [
                WorkflowError(
                    schema_version="synthetic-1",
                    error_id=f"retry-timeout-{n}",
                    run_id="run-synthetic",
                    candidate_id=cid,
                    node="collect",
                    error_code="TOOL_TIMEOUT",
                    message_redacted="Synthetic timeout",
                    retryable=True,
                    attempt=1,
                    timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
                )
            ]
        )

    nodes = replace(
        nodes,
        coverage=_always_gap,
        collect=_tracked_collect(nodes, calls, failing_retry=timeout),
    )
    result = execute(nodes)
    limit = policy.budgets.max_research_retries_per_candidate
    assert [c[2] for c in calls if c[0] == "collect"] == list(range(limit + 1))
    assert result["research_retry_count"]["co-0"] == limit
    assert [e["error_id"] for e in result["errors"]] == [
        f"retry-timeout-{n}" for n in range(1, limit + 1)
    ]
    # The failed retry keeps the gap missing instead of failing the candidate.
    assert result["candidate_status"]["co-0"] == "recommend"
    assert result["research_gaps"]["co-0"][0]["status"] == "exhausted"


def test_initial_collect_failure_still_fails_candidate(harness):
    make, execute, _, _, _ = harness
    nodes = make(["recommend", "recommend"])
    original = nodes.collect

    def failing_first(state):
        if state["current_candidate_id"] == "co-0":
            raise RuntimeError("secret initial failure")
        return original(state)

    result = execute(replace(nodes, collect=failing_first, coverage=_always_gap))
    assert result["candidate_outcomes"]["co-0"]["status"] == "failed"
    assert result["research_retry_count"]["co-0"] == 0
    assert result["selected_candidate_id"] == "co-1"


def test_zero_research_budget_makes_no_retry(harness):
    make, execute, calls, _, policy = harness
    nodes = make(["recommend"])
    nodes = replace(nodes, coverage=_always_gap, collect=_tracked_collect(nodes, calls))
    zero = policy.model_copy(
        update={
            "budgets": policy.budgets.model_copy(
                update={"max_research_retries_per_candidate": 0}
            )
        }
    )
    graph = build_candidate_graph(
        nodes,
        zero,
        run_id="run-synthetic",
        schema_version="synthetic-1",
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    ).compile()
    result = graph.invoke(
        create_initial_state(harness[3].model_dump(mode="json")),
        {"recursion_limit": 200},
    )
    assert [c for c in calls if c[0] == "collect"] == [("collect", "co-0", 0)]
    assert result["research_gaps"]["co-0"][0]["status"] == "exhausted"
