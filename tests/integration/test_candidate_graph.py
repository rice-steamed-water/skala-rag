"""T08/T09 on real LangGraph, wholly synthetic stage outputs and evidence."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from skala_rag.contracts.evaluation import EvaluationResult
from skala_rag.contracts.ids import eligibility_result_id, evaluation_key
from skala_rag.contracts.inputs import RunInput
from skala_rag.contracts.reports import ReportInput
from skala_rag.contracts.state import create_initial_state
from skala_rag.graph.candidates import (
    CandidateNodes,
    Explanation,
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
