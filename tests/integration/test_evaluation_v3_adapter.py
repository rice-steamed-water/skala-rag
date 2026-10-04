"""Offline synthetic envelopes against the installed five-way LangGraph barrier.

Market is caller-injected terminal data, not the absent PR134 implementation.
"""

from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Lock

import pytest
import yaml
from tests.fixtures.loader import load_common_fixtures

from skala_rag.agents.evaluation import output_from_evaluation
from skala_rag.agents.evaluation_v3_adapter import (
    adapt_baseline_branch_result,
    bind_baseline_evaluator_v3,
)
from skala_rag.agents.founder import evaluate_founder_fixture
from skala_rag.agents.technology import evaluate_technology
from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.evaluation import EvaluationResult
from skala_rag.contracts.v3 import BRANCH_DIMENSIONS, EvaluationBranchResult
from skala_rag.fakes import FakeClock, FakeLLM
from skala_rag.graph.evaluation_v3 import (
    archive_advance_failure_v3,
    build_evaluation_graph_v3,
)
from skala_rag.scoring.catalog import load_policy

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.fixture
def case():
    policy = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
    fixtures = load_common_fixtures(policy)
    snapshot = next(iter(fixtures.snapshots.values()))

    def evaluation(dimension):
        return fixtures.evaluations[
            f"{snapshot.candidate_id}:{snapshot.evaluation_round}:{dimension}"
        ].model_copy(deep=True)

    def baseline(branch):
        e = evaluation(branch)
        identity = {
            field: getattr(e, field)
            for field in (
                "schema_version",
                "run_id",
                "candidate_id",
                "dimension",
                "evaluation_round",
                "snapshot_id",
                "evidence_revision",
                "policy_version",
            )
        }
        return EvaluationResult(**identity, status="success", evaluation=e, errors=[])

    def already_v3(branch):
        dimensions = {d: evaluation(d).model_dump() for d in BRANCH_DIMENSIONS[branch]}
        raw = baseline("founder").model_dump()
        raw.pop("dimension")
        raw.pop("evaluation")
        return EvaluationBranchResult.model_validate(
            dict(raw, branch_id=branch, evaluations=dimensions)
        )

    state = dict(
        snapshot_v3=snapshot.model_dump(mode="json"),
        current_candidate_id=snapshot.candidate_id,
        evaluation_rounds={snapshot.candidate_id: snapshot.evaluation_round},
        evidence_revisions={snapshot.candidate_id: snapshot.evidence_revision},
        run_input={
            "policy_version": snapshot.policy_version,
            "execution_mode": "fixture",
        },
        snapshots={snapshot.snapshot_id: snapshot.model_dump(mode="json")},
        candidates=[{"candidate_id": snapshot.candidate_id}],
        candidate_index=0,
        candidate_outcomes={},
        candidate_status={},
        errors=[],
    )
    return snapshot, policy, baseline, already_v3, state


def terminal_failure(result):
    error = WorkflowError(
        schema_version=result.schema_version,
        error_id="synthetic-original-timeout",
        run_id=result.run_id,
        candidate_id=result.candidate_id,
        node=result.dimension,
        error_code="TOOL_TIMEOUT",
        message_redacted="Synthetic original transport timeout",
        retryable=True,
        attempt=3,
        timestamp=NOW,
    )
    return EvaluationResult.model_validate(
        dict(result.model_dump(), status="failure", evaluation=None, errors=[error])
    )


@pytest.mark.parametrize(
    "fault",
    [None, "founder", "market", "technology", "bd_half", "generation", "exception"],
)
def test_five_parallel_callbacks_atomic_six_or_zero_promotions(case, fault):
    snapshot, policy, baseline, already_v3, state = case
    before = deepcopy(state)
    barrier = Barrier(5, timeout=5)
    lock = Lock()
    calls = Counter()
    original_failure = None
    if fault in ("founder", "market", "technology"):
        original_failure = terminal_failure(baseline(fault))

    def upstream(branch):
        def evaluate(received):
            with lock:
                calls[branch] += 1
            barrier.wait()
            assert received == snapshot and received is not snapshot
            if branch in ("founder", "market", "technology"):
                if branch == fault:
                    return original_failure
                if fault == "exception" and branch == "technology":
                    raise RuntimeError("secret synthetic provider content")
                result = baseline(branch)
                if fault == "generation" and branch == "market":
                    raw = result.model_dump()
                    raw["policy_version"] = "v3-operational-1.0.0"
                    raw["evaluation"]["policy_version"] = "v3-operational-1.0.0"
                    return EvaluationResult.model_validate(raw)
                return result
            result = already_v3(branch)
            if fault == "bd_half" and branch == "business_deal":
                return result.model_copy(
                    update={"evaluations": {"traction": result.evaluations["traction"]}}
                )
            return result

        return evaluate

    callbacks = {}
    for branch in BRANCH_DIMENSIONS:
        evaluate = upstream(branch)
        callbacks[branch] = (
            bind_baseline_evaluator_v3(
                branch,
                evaluate,
                criteria=policy.criteria,
                industry_evidence_dimensions=set(),
            )
            if branch in ("founder", "market", "technology")
            else evaluate
        )
    graph = build_evaluation_graph_v3(
        callbacks,
        criteria=policy.criteria,
        policy_version=policy.policy_version,
        run_id=snapshot.run_id,
        schema_version=snapshot.schema_version,
        industry_evidence_dimensions=set(),
        applicability_validator=None,
        clock=lambda: NOW,
    ).compile()
    events = list(graph.stream(state, stream_mode=["updates", "values"]))
    out = [value for mode, value in events if mode == "values"][-1]
    updates = [value for mode, value in events if mode == "updates"]
    assert calls == Counter({branch: 1 for branch in BRANCH_DIMENSIONS})
    assert sum("join_v3" in event for event in updates) == 1
    assert len(out["branch_results_v3"]) == 5
    assert state == before
    if fault is None:
        assert out["evaluation_status_v3"] == "success"
        assert len(out["evaluations_v3"]) == 6
        assert out["candidate_index"] == 0
        assert sum("archive_advance_v3" in event for event in updates) == 0
        # Existing Moat / atomic BD fixture envelopes are not rebound or rewritten.
        for branch in ("moat", "business_deal"):
            stored = next(
                r for r in out["branch_results_v3"].values() if r["branch_id"] == branch
            )
            assert stored == already_v3(branch).model_dump(mode="json")
    else:
        assert out["evaluation_status_v3"] == "failure"
        assert out["evaluations_v3"] == {}
        assert out["candidate_index"] == 1
        assert out["current_candidate_id"] is None
        assert sum("archive_advance_v3" in event for event in updates) == 1
        assert archive_advance_failure_v3(out) == {}
        archived = out["candidate_outcomes"][snapshot.candidate_id]
        assert archived["failure_ids"] == out["evaluation_failure_ids_v3"]
        assert "secret synthetic provider content" not in str(out)
        assert "score_summaries" not in out
        if original_failure is not None:
            assert out["errors"] == [
                e.model_dump(mode="json") for e in original_failure.errors
            ]
            assert archived["failure_ids"] == [original_failure.errors[0].error_id]


def test_existing_founder_fixture_binding_no_second_llm_call(case):
    snapshot, policy, baseline, _, _ = case
    rubric = yaml.safe_load((ROOT / "configs/rubrics/core.yaml").read_text())
    llm = FakeLLM([output_from_evaluation(baseline("founder").evaluation)])
    links = {eid: "synthetic-founder" for eid in snapshot.evidence_ids}
    results = []

    def upstream(received):
        result = evaluate_founder_fixture(
            received,
            founder_person_ids={"synthetic-founder"},
            verified_person_by_evidence_id=links,
            rubric=rubric,
            llm=llm,
            policy=policy,
            clock=FakeClock(NOW),
            schema_version=snapshot.schema_version,
        )
        results.append(result)
        return result

    out = bind_baseline_evaluator_v3(
        "founder",
        upstream,
        criteria=policy.criteria,
        industry_evidence_dimensions=set(),
    )(snapshot)
    assert out.status == "success"
    assert len(results) == len(llm.calls) == 1
    assert (
        out.evaluations["founder"].criteria[0].evidence_ids
        == results[0].evaluation.criteria[0].evidence_ids
    )


def test_existing_technology_explicit_result_and_caller_owned_trace_receipt(case):
    snapshot, policy, baseline, _, _ = case
    rubric = yaml.safe_load((ROOT / "configs/rubrics/core.yaml").read_text())
    llm = FakeLLM([output_from_evaluation(baseline("technology").evaluation)])
    receipts = []

    def upstream(received):
        receipt = evaluate_technology(
            received,
            rubric=rubric,
            llm=llm,
            policy=policy,
            clock=FakeClock(NOW),
            schema_version=snapshot.schema_version,
            execution_mode="fixture",
        )
        receipts.append(receipt)
        return receipt.result

    out = bind_baseline_evaluator_v3(
        "technology",
        upstream,
        criteria=policy.criteria,
        industry_evidence_dimensions=set(),
    )(snapshot)
    assert out.status == "success"
    assert len(receipts) == len(llm.calls) == 1
    receipt = receipts[0]
    assert receipt.trace and receipt.allowed_evidence_ids and receipt.prompt_version
    cited = {
        (c.criterion_id, eid)
        for c in out.evaluations["technology"].criteria
        for eid in c.evidence_ids
    }
    assert {(t.criterion_id, t.evidence_id) for t in receipt.trace} == cited
    for trace in receipt.trace:
        assert trace.snapshot_id == snapshot.snapshot_id
        assert trace.chunk_id in snapshot.chunks
        assert trace.retrieval_id in snapshot.retrieval_records
    with pytest.raises(ValueError, match="Invalid baseline"):
        adapt_baseline_branch_result(
            receipt,
            branch_id="technology",
            snapshot=snapshot,
            criteria=policy.criteria,
            industry_evidence_dimensions=set(),
        )


def test_graph_live_mode_still_denied_before_bound_upstream(case):
    snapshot, policy, baseline, already_v3, state = case
    calls = []
    callbacks = {}
    for branch in BRANCH_DIMENSIONS:

        def upstream(s, b=branch):
            calls.append(b)
            return baseline(b)

        callbacks[branch] = (
            bind_baseline_evaluator_v3(
                branch,
                upstream,
                criteria=policy.criteria,
                industry_evidence_dimensions=set(),
            )
            if branch in ("founder", "market", "technology")
            else lambda s, b=branch: calls.append(b) or already_v3(b)
        )
    state["run_input"]["execution_mode"] = "live"
    out = (
        build_evaluation_graph_v3(
            callbacks,
            criteria=policy.criteria,
            policy_version=policy.policy_version,
            run_id=snapshot.run_id,
            schema_version=snapshot.schema_version,
            industry_evidence_dimensions=set(),
            applicability_validator=None,
            clock=lambda: NOW,
        )
        .compile()
        .invoke(state)
    )
    assert calls == []
    assert out["evaluations_v3"] == {}
    assert out["evaluation_status_v3"] == "failure"
    assert out["candidate_index"] == 1
