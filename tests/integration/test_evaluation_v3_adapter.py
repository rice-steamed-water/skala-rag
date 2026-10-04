"""Offline terminals against the installed five-way LangGraph barrier.

The approved Market cases execute PR134's real evaluator with synthetic facts and
FakeLLM; their four sibling branches are explicitly synthetic terminal envelopes.
Earlier draft-generation cases remain separate compatibility coverage.
"""

from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Lock

import pytest
import yaml
from tests.fixtures.loader import load_common_fixtures
from tests.unit.test_market import Case as SyntheticMarketCase

from skala_rag.agents.evaluation import output_from_evaluation
from skala_rag.agents.evaluation_v3_adapter import (
    adapt_baseline_branch_result,
    bind_baseline_evaluator_v3,
)
from skala_rag.agents.founder import evaluate_founder_fixture
from skala_rag.agents.market import MarketTarget, evaluate_market
from skala_rag.agents.moat_verification import validate_core_artifact
from skala_rag.agents.technology import evaluate_technology
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.evaluation import EvaluationResult, EvaluationSnapshot
from skala_rag.contracts.interfaces import LLMError
from skala_rag.contracts.v3 import BRANCH_DIMENSIONS, EvaluationBranchResult
from skala_rag.contracts.v3 import Evaluation as V3Evaluation
from skala_rag.fakes import FakeClock, FakeLLM
from skala_rag.graph.evaluation_v3 import (
    archive_advance_failure_v3,
    build_evaluation_graph_v3,
)
from skala_rag.scoring.approval_registry import pinned_approval_registry
from skala_rag.scoring.approved_policy import load_approved_policy
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


@pytest.fixture
def approved_market_case(monkeypatch):
    import socket

    def deny_network(*args, **kwargs):
        raise AssertionError("Market integration regression must remain offline")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    registry = pinned_approval_registry(ROOT)
    approvals = registry.policy_approvals()
    policy = load_approved_policy(
        ROOT / "configs/scoring.v3.json",
        approvals=approvals,
        approval_verifier=registry.verify_policy,
        execution_mode="fixture",
    )
    rubric = yaml.safe_load((ROOT / "configs/rubrics/core.yaml").read_text())
    validate_core_artifact(rubric, registry.core_approval(), registry.verify_core)
    diagnosis = registry.diagnose(approvals=approvals)
    assert all(a.content_binding == "matched" for a in diagnosis.artifacts)
    assert rubric["status"] == "approved"
    assert diagnosis.semantic_review == "unreviewed"
    assert diagnosis.runtime_admission == "not_admitted"
    assert diagnosis.campaign_approval == "unapproved"
    synthetic = SyntheticMarketCase()
    # Construct the operational input BEFORE evaluating; never stamp a result.
    raw = synthetic.snapshot.model_dump()
    raw.update(
        policy_version=policy.policy_version,
        snapshot_id="synthetic-approved-market-integration",
    )

    def assert_original_schema(value):
        if isinstance(value, dict):
            if "schema_version" in value:
                assert value["schema_version"] == synthetic.snapshot.schema_version
            for nested in value.values():
                assert_original_schema(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_original_schema(nested)

    # Includes frozen Evidence, source/chunk/retrieval and corpus metadata.
    assert_original_schema(raw)
    snapshot = EvaluationSnapshot.model_validate(
        raw, context={"execution_mode": "fixture"}
    )
    return snapshot, policy, rubric, synthetic


def synthetic_sibling_terminal(branch, snapshot, policy):
    """Injected data, NOT actual Founder/Technology/Moat/Business & Deal calls."""
    identity = {
        field: getattr(snapshot, field)
        for field in (
            "schema_version",
            "run_id",
            "candidate_id",
            "evaluation_round",
            "snapshot_id",
            "evidence_revision",
            "policy_version",
        )
    }
    evaluations = {}
    for dimension in BRANCH_DIMENSIONS[branch]:
        assessments = [
            dict(
                schema_version=snapshot.schema_version,
                criterion_id=c.criterion_id,
                status="missing",
                rating=None,
                evidence_ids=[],
                rationale="Synthetic terminal; no evaluator or research executed",
                missing_reason="not_disclosed",
            )
            for c in policy.criteria
            if c.dimension == dimension
        ]
        evaluations[dimension] = V3Evaluation(
            **identity,
            dimension=dimension,
            rubric_version="synthetic-sibling-not-semantically-reviewed",
            criteria=assessments,
            research_gaps=[],
            caveats=[],
        )
    return EvaluationBranchResult(
        **identity,
        branch_id=branch,
        status="success",
        evaluations=evaluations,
        errors=[],
    )


@pytest.mark.parametrize(
    "fault,expected_llm_calls",
    [
        (None, 1),
        ("timeout", 1),
        ("schema", 1),
        ("generation", 1),
        ("industry", 1),
        ("citation", 2),
        ("earlier_year", 0),
        ("live", 0),
    ],
    ids=[
        "success",
        "original-terminal-failure",
        "wrong-schema",
        "wrong-generation",
        "unauthorized-industry",
        "wrong-criterion-citation",
        "earlier-year",
        "live-preflight",
    ],
)
def test_actual_market_approved_binder_five_way_offline(
    approved_market_case, fault, expected_llm_calls
):
    snapshot, policy, rubric, synthetic = approved_market_case
    if fault == "earlier_year":
        raw = snapshot.model_dump()
        raw["evidence"]["ev-sam"]["value_as_of"] = "2019-12-31"
        snapshot = EvaluationSnapshot.model_validate(
            raw, context={"execution_mode": "fixture"}
        )
    output = synthetic.output()
    if fault == "citation":
        output["criteria"][0]["evidence_ids"] = ["ev-cagr"]
    outputs = (
        [LLMError(ErrorCode.LLM_TIMEOUT, "Synthetic redacted transport timeout")]
        if fault == "timeout"
        else [output, deepcopy(output)]
    )
    llm = FakeLLM(outputs)
    calls, terminals, sibling_terminals = Counter(), [], {}
    lock, barrier = Lock(), Barrier(5, timeout=5)

    def arrive(branch, received):
        with lock:
            calls[branch] += 1
        barrier.wait()
        assert received == snapshot and received is not snapshot

    def actual_market(received):
        arrive("market", received)
        input_policy = policy
        if fault == "generation":
            # Produce a genuinely different original generation, not rewritten output.
            input_policy = load_policy(
                ROOT / "configs/scoring.draft.json", execution_mode="fixture"
            )
            raw = received.model_dump()
            raw["policy_version"] = input_policy.policy_version
            received = EvaluationSnapshot.model_validate(
                raw, context={"execution_mode": "fixture"}
            )
        result = evaluate_market(
            received,
            target_market=MarketTarget(
                segment_id="kr-logistics-amr", geographies=("KR", "GLOBAL")
            ),
            market_links=synthetic.links,
            rubric=rubric,
            llm=llm,
            policy=input_policy,
            clock=FakeClock(NOW),
            schema_version="synthetic-wrong"
            if fault == "schema"
            else received.schema_version,
        )
        terminals.append(result)
        return result

    def synthetic_callback(branch):
        def callback(received):
            arrive(branch, received)
            terminal = synthetic_sibling_terminal(branch, received, policy)
            with lock:
                sibling_terminals[branch] = terminal
            return terminal

        return callback

    bound_market = bind_baseline_evaluator_v3(
        "market",
        actual_market,
        criteria=policy.criteria,
        industry_evidence_dimensions=set() if fault == "industry" else {"market"},
    )
    callbacks = {
        branch: bound_market if branch == "market" else synthetic_callback(branch)
        for branch in BRANCH_DIMENSIONS
    }
    state = dict(
        snapshot_v3=snapshot.model_dump(mode="json"),
        current_candidate_id=snapshot.candidate_id,
        evaluation_rounds={snapshot.candidate_id: snapshot.evaluation_round},
        evidence_revisions={snapshot.candidate_id: snapshot.evidence_revision},
        run_input={
            "execution_mode": "live" if fault == "live" else "fixture",
            "policy_version": policy.policy_version,
        },
        snapshots={snapshot.snapshot_id: snapshot.model_dump(mode="json")},
        candidates=[{"candidate_id": snapshot.candidate_id}],
        candidate_index=0,
        candidate_outcomes={},
        candidate_status={},
        errors=[],
    )
    before = deepcopy(state)
    events = list(
        build_evaluation_graph_v3(
            callbacks,
            criteria=policy.criteria,
            policy_version=policy.policy_version,
            run_id=snapshot.run_id,
            schema_version=snapshot.schema_version,
            industry_evidence_dimensions={"market"},
            applicability_validator=None,
            clock=lambda: NOW,
        )
        .compile()
        .stream(state, stream_mode=["updates", "values"])
    )
    out = [value for mode, value in events if mode == "values"][-1]
    updates = [value for mode, value in events if mode == "updates"]
    assert state == before
    assert len(llm.calls) == expected_llm_calls
    assert calls == (
        Counter() if fault == "live" else Counter({b: 1 for b in BRANCH_DIMENSIONS})
    )
    assert sum("join_v3" in event for event in updates) == (0 if fault == "live" else 1)
    for branch, terminal in sibling_terminals.items():
        stored = next(
            r for r in out["branch_results_v3"].values() if r["branch_id"] == branch
        )
        assert stored == terminal.model_dump(mode="json")
    if fault is None:
        assert out["evaluation_status_v3"] == "success"
        assert len(out["evaluations_v3"]) == 6
        assert len(terminals) == 1 and terminals[0].status == "success"
        stored = next(
            r for r in out["branch_results_v3"].values() if r["branch_id"] == "market"
        )
        original = terminals[0].evaluation.model_dump(mode="json")
        promoted = deepcopy(stored["evaluations"]["market"])
        # V3 adds only empty N/A slots; every baseline field remains exact.
        for assessment in promoted["criteria"]:
            for field in (
                "applicability_reason",
                "applicability_rule_id",
                "applicability_evidence_ids",
            ):
                assert assessment.pop(field) is None
        assert promoted == original
        assert (
            stored["policy_version"]
            == terminals[0].policy_version
            == snapshot.policy_version
        )
        assert (
            stored["schema_version"]
            == terminals[0].schema_version
            == snapshot.schema_version
        )
        assert out["candidate_index"] == 0
        assert sum("archive_advance_v3" in event for event in updates) == 0
    else:
        assert out["evaluation_status_v3"] == "failure"
        assert out["evaluations_v3"] == {}
        assert out["candidate_index"] == 1 and out["current_candidate_id"] is None
        assert sum("archive_advance_v3" in event for event in updates) == 1
        assert archive_advance_failure_v3(out) == {}
        assert (
            out["candidate_outcomes"][snapshot.candidate_id]["failure_ids"]
            == out["evaluation_failure_ids_v3"]
        )
        assert "score_summaries" not in out
        if fault in {"timeout", "citation"}:
            assert len(terminals) == 1 and terminals[0].status == "failure"
            # Preserve every original redacted error field; no adapter-owned retry.
            assert out["errors"] == [
                e.model_dump(mode="json") for e in terminals[0].errors
            ]
            assert out["evaluation_failure_ids_v3"] == [
                e.error_id for e in terminals[0].errors
            ]
        elif fault in {"generation", "schema", "industry"}:
            assert len(terminals) == 1 and terminals[0].status == "success"
            original = terminals[0]
            if fault == "generation":
                assert original.policy_version == "main-draft-0.1.0"
                assert original.evaluation.policy_version == original.policy_version
                assert original.policy_version != snapshot.policy_version
            elif fault == "schema":
                assert original.schema_version == "synthetic-wrong"
                assert original.evaluation.schema_version == original.schema_version
                assert all(
                    c.schema_version == original.schema_version
                    for c in original.evaluation.criteria
                )
            else:
                assert any(
                    snapshot.evidence[eid].scope == "industry"
                    for c in original.evaluation.criteria
                    for eid in c.evidence_ids
                )
            assert out["errors"][0]["error_code"] == "UPSTREAM_INVALID"
        else:
            assert terminals == []
