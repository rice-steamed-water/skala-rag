"""Offline terminals against the installed five-way LangGraph barrier.

The approved Market cases execute PR134's real evaluator with synthetic facts and
FakeLLM; their four sibling branches are explicitly synthetic terminal envelopes.
Separate combined cases execute real Market and approved Technology with three
synthetic siblings and caller-owned synthetic review authority (not actual RAG).
The five-implementation matrix calls all five existing Python evaluators against
one immutable pinned generation, using FakeLLM and synthetic review/facts only.
Earlier draft-generation cases remain separate compatibility coverage.
"""

from collections import Counter
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Lock

import pytest
import yaml
from tests.fixtures.loader import load_common_fixtures
from tests.unit.test_market import Case as SyntheticMarketCase
from tests.unit.test_technology_approved import case as synthetic_technology_case

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


@pytest.fixture
def approved_market_technology_case(approved_market_case):
    """One synthetic frozen corpus, real pinned approval/content verification."""
    from skala_rag.agents.technology_verification import checked_snapshot

    snapshot, policy, rubric, market = approved_market_case
    snapshot = snapshot.model_copy(deep=True)
    # Close the synthetic Source/Chunk/Record/Evidence tree BEFORE either call.
    # This is consistent fixture attribution, not execution of a RAG retriever.
    for record in snapshot.retrieval_records.values():
        record.evidence_ids = [
            eid
            for eid, e in snapshot.evidence.items()
            if any(p.retrieval_id == record.retrieval_id for p in e.provenance)
        ]
    for e in snapshot.evidence.values():
        for p in e.provenance:
            if p.chunk_id is not None:
                snapshot.chunks[p.chunk_id].text += "\n" + e.excerpt
    snapshot = checked_snapshot(snapshot)
    technology_output = synthetic_technology_case()[1]
    assert {c.criterion_id for c in technology_output.criteria} == {
        c.criterion_id for c in policy.criteria if c.dimension == "technology"
    }
    return snapshot, policy, rubric, market, technology_output


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "market_timeout",
        "technology_timeout",
        "stale_whole_snapshot",
        "wrong_evidence",
        "wrong_reference",
        "nested_generation",
        "review_exception",
        "live",
    ],
)
def test_actual_market_and_approved_technology_same_frozen_graph(
    approved_market_technology_case, fault
):
    from skala_rag.agents.moat_verification import (
        core_artifact_digest,
        frozen_snapshot_digest,
    )
    from skala_rag.agents.technology import evaluate_technology_approved_fixture
    from skala_rag.agents.technology_verification import (
        ReviewedTechnologyAnchor,
        TechnologyReviewError,
    )

    snapshot, policy, rubric, market, technology_output = (
        approved_market_technology_case
    )
    registry = pinned_approval_registry(ROOT)
    original_digest = frozen_snapshot_digest(snapshot)
    if fault == "stale_whole_snapshot":
        # Only unrelated Market content changes; all five see that same new input.
        snapshot.evidence["ev-demand"].claim += " changed non-Technology fact"
        assert frozen_snapshot_digest(snapshot) != original_digest
    elif fault == "nested_generation":
        snapshot.evidence["ev-demand"].schema_version = "synthetic-stale-generation"
    frozen = snapshot.model_dump(mode="json")
    market_llm = FakeLLM(
        [
            LLMError(ErrorCode.LLM_TIMEOUT, "Synthetic Market transport timeout")
            if fault == "market_timeout"
            else market.output()
        ]
    )
    technology_llm = FakeLLM(
        [
            LLMError(ErrorCode.LLM_TIMEOUT, "Synthetic Technology transport timeout")
            if fault == "technology_timeout"
            else technology_output
        ]
    )
    calls, reviews, resolutions = Counter(), [], []
    market_results, technology_receipts, raised = [], [], []
    sibling_terminals, review_registry = {}, {}
    lock, barrier = Lock(), Barrier(5, timeout=5)

    def arrive(branch, received):
        with lock:
            calls[branch] += 1
        barrier.wait()
        assert received.model_dump(mode="json") == frozen
        assert received is not snapshot

    def review(c, evidence):
        reviews.append(c.criterion_id)
        assert set(evidence) == set(c.evidence_ids)
        assert all(e == snapshot.evidence[eid] for eid, e in evidence.items())
        if fault == "review_exception":
            raise RuntimeError("SECRET synthetic reviewer exception")
        receipt = ReviewedTechnologyAnchor(
            review_reference="synthetic-caller-review:" + c.criterion_id,
            artifact_sha256=core_artifact_digest(rubric),
            snapshot_sha256=original_digest,
            criterion_id=c.criterion_id,
            rating=c.rating,
            evidence_ids=tuple(c.evidence_ids),
            anchor_facts_reviewed=True,
            minimum_evidence_reviewed=True,
            direct_negative_facts_reviewed=True,
            independent_corroboration_reviewed=True,
        )
        review_registry[receipt.review_reference] = receipt
        if fault == "wrong_evidence":
            return replace(receipt, evidence_ids=("ev-demand",))
        if fault == "wrong_reference":
            return replace(receipt, review_reference="synthetic-unregistered")
        return receipt

    def resolve(receipt):
        resolutions.append(receipt)
        return review_registry.get(receipt.review_reference) == receipt

    def actual_market(received):
        arrive("market", received)
        result = evaluate_market(
            received,
            target_market=MarketTarget(
                segment_id="kr-logistics-amr", geographies=("KR", "GLOBAL")
            ),
            market_links=market.links,
            rubric=rubric,
            llm=market_llm,
            policy=policy,
            clock=FakeClock(NOW),
            schema_version=received.schema_version,
        )
        market_results.append(result)
        return result

    def actual_technology(received):
        arrive("technology", received)
        try:
            receipt = evaluate_technology_approved_fixture(
                received,
                policy_path=ROOT / "configs/scoring.v3.json",
                approvals=registry.policy_approvals(),
                approval_verifier=registry.verify_policy,
                rubric=rubric,
                llm=technology_llm,
                clock=FakeClock(NOW),
                schema_version=received.schema_version,
                verify_observation=review,
                review_verifier=resolve,
                artifact_approval=registry.core_approval(),
                artifact_verifier=registry.verify_core,
            )
        except (ValueError, TechnologyReviewError) as exc:
            raised.append(exc)
            raise
        technology_receipts.append(receipt)
        return receipt.result  # Retain container locally; only RESULT enters binder.

    def synthetic_callback(branch):
        def callback(received):
            arrive(branch, received)
            terminal = synthetic_sibling_terminal(branch, received, policy)
            sibling_terminals[branch] = terminal
            return terminal

        return callback

    callbacks = {
        branch: bind_baseline_evaluator_v3(
            branch,
            actual_market if branch == "market" else actual_technology,
            criteria=policy.criteria,
            industry_evidence_dimensions={"market"},
        )
        if branch in {"market", "technology"}
        else synthetic_callback(branch)
        for branch in BRANCH_DIMENSIONS
    }
    state = dict(
        snapshot_v3=frozen,
        current_candidate_id=snapshot.candidate_id,
        evaluation_rounds={snapshot.candidate_id: snapshot.evaluation_round},
        evidence_revisions={snapshot.candidate_id: snapshot.evidence_revision},
        run_input={
            "execution_mode": "live" if fault == "live" else "fixture",
            "policy_version": policy.policy_version,
        },
        snapshots={snapshot.snapshot_id: deepcopy(frozen)},
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
    values = [v for mode, v in events if mode == "values"]
    updates = [v for mode, v in events if mode == "updates"]
    out = values[-1]
    assert state == before
    assert snapshot.model_dump(mode="json") == frozen
    assert out["snapshot_v3"] == frozen
    assert out["snapshots"] == before["snapshots"]
    assert calls == (
        Counter() if fault == "live" else Counter({b: 1 for b in BRANCH_DIMENSIONS})
    )
    assert len(market_llm.calls) == (0 if fault == "live" else 1)
    assert len(technology_llm.calls) == (
        0 if fault in {"live", "nested_generation"} else 1
    )
    assert sum("join_v3" in u for u in updates) == (0 if fault == "live" else 1)
    assert set(sibling_terminals) == (
        set() if fault == "live" else {"founder", "moat", "business_deal"}
    )
    stored = {r["branch_id"]: r for r in out.get("branch_results_v3", {}).values()}
    for branch, terminal in sibling_terminals.items():
        assert stored[branch] == terminal.model_dump(mode="json")
    assert all(len(v.get("evaluations_v3", {})) in {0, 6} for v in values)
    if fault is None:
        assert out["evaluation_status_v3"] == "success"
        assert len(out["evaluations_v3"]) == 6
        assert len(market_results) == len(technology_receipts) == 1
        receipt = technology_receipts[0]
        assert len(reviews) == len(resolutions) == 4
        assert set(reviews) == {c.criterion_id for c in technology_output.criteria}
        assert all(type(r) is ReviewedTechnologyAnchor for r in resolutions)
        assert all(r.snapshot_sha256 == original_digest for r in resolutions)
        assert receipt.prompt_version and receipt.allowed_evidence_ids and receipt.trace
        assert set(receipt.allowed_evidence_ids) == {
            eid for c in technology_output.criteria for eid in c.evidence_ids
        }
        assert {(t.criterion_id, t.evidence_id) for t in receipt.trace} == {
            (c.criterion_id, eid)
            for c in technology_output.criteria
            for eid in c.evidence_ids
        }
        for t in receipt.trace:
            e = snapshot.evidence[t.evidence_id]
            record = snapshot.retrieval_records[t.retrieval_id]
            chunk = snapshot.chunks[t.chunk_id]
            assert t.snapshot_id == snapshot.snapshot_id
            assert (
                t.chunk_id in record.chunk_ids and t.evidence_id in record.evidence_ids
            )
            assert chunk.source_id == e.source_id and e.source_id in record.source_ids
            assert e.excerpt in chunk.text
        for branch, result in (
            ("market", market_results[0]),
            ("technology", receipt.result),
        ):
            assert stored[branch]["evaluations"][branch] == V3Evaluation.model_validate(
                result.evaluation.model_dump()
            ).model_dump(mode="json")
            for field in (
                "schema_version",
                "run_id",
                "candidate_id",
                "evaluation_round",
                "snapshot_id",
                "evidence_revision",
                "policy_version",
            ):
                assert (
                    stored[branch][field]
                    == getattr(result, field)
                    == getattr(snapshot, field)
                )
        assert out["candidate_index"] == 0
        assert not any("archive_advance_v3" in u for u in updates)
    else:
        assert out["evaluation_status_v3"] == "failure"
        assert out["evaluations_v3"] == {}
        assert out["candidate_index"] == 1 and out["current_candidate_id"] is None
        assert sum("archive_advance_v3" in u for u in updates) == 1
        assert archive_advance_failure_v3(out) == {}
        assert "score_summaries" not in out and "investment_decisions" not in out
        assert "SECRET" not in str(out)
        assert out["candidate_outcomes"][snapshot.candidate_id]["status"] == "failed"
        assert (
            out["candidate_outcomes"][snapshot.candidate_id]["failure_ids"]
            == out["evaluation_failure_ids_v3"]
        )
        if fault in {"market_timeout", "technology_timeout"}:
            result = (
                market_results[0]
                if fault == "market_timeout"
                else technology_receipts[0].result
            )
            assert result.status == "failure" and result.evaluation is None
            assert out["errors"] == [e.model_dump(mode="json") for e in result.errors]
            assert out["evaluation_failure_ids_v3"] == [
                e.error_id for e in result.errors
            ]
            assert raised == []
            assert len(reviews) == (4 if fault == "market_timeout" else 0)
            assert len(resolutions) == len(reviews)
        elif fault != "live":
            assert len(raised) == 1 and technology_receipts == []
            assert isinstance(
                raised[0],
                ValueError if fault == "nested_generation" else TechnologyReviewError,
            )
            assert len(reviews) == (0 if fault == "nested_generation" else 1)
            assert len(resolutions) == (1 if fault == "wrong_reference" else 0)
            error = WorkflowError.model_validate(out["errors"][0])
            assert error.node == "technology" and error.error_code == "UPSTREAM_INVALID"
            assert error.retryable is False and error.attempt == 1
            assert stored["technology"]["evaluations"] is None
        else:
            assert reviews == resolutions == technology_receipts == market_results == []


def test_combined_technology_actual_runtime_gate_before_bad_path_and_callbacks(
    approved_market_technology_case,
):
    from skala_rag.agents.technology import evaluate_technology_approved_fixture

    snapshot, _, rubric, _, output = approved_market_technology_case
    registry = pinned_approval_registry(ROOT)
    llm, touched = FakeLLM([output]), []

    def forbidden(*args):
        touched.append(True)
        raise AssertionError("Actual runtime must deny before path or verifier")

    with pytest.raises(ValueError, match="actual runtime unavailable"):
        evaluate_technology_approved_fixture(
            snapshot,
            actual_runtime=True,
            policy_path=ROOT / "synthetic-nonexistent-policy.json",
            approvals=registry.policy_approvals(),
            approval_verifier=forbidden,
            rubric=rubric,
            llm=llm,
            clock=FakeClock(NOW),
            schema_version=snapshot.schema_version,
            verify_observation=forbidden,
            review_verifier=forbidden,
            artifact_approval=registry.core_approval(),
            artifact_verifier=forbidden,
        )
    assert llm.calls == touched == []


@pytest.fixture
def five_implementation_case(approved_market_technology_case):
    """Full synthetic observations/financial receipts prepared before execution."""
    from skala_rag.agents.finance_verification import ReviewedFinancialFact
    from skala_rag.agents.technology_verification import checked_snapshot
    from skala_rag.scoring.finance import parse_period

    snapshot, policy, rubric, market, technology = approved_market_technology_case
    snapshot = snapshot.model_copy(deep=True)
    fixtures = load_common_fixtures(
        load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
    )
    outputs = dict(market=market.output(), technology=technology)
    for branch in ("founder", "moat"):
        outputs[branch] = output_from_evaluation(
            fixtures.evaluations[
                f"{snapshot.candidate_id}:{snapshot.evaluation_round}:{branch}"
            ]
        )
    finance = yaml.safe_load((ROOT / "configs/rubrics/finance.yaml").read_text())
    assert finance["status"] == "approved"
    assert finance["rubric_version"] == policy.approvals.finance.version
    # Existing approved Finance unit example: 100 revenue / 50 cost => 50%, rating 5.
    template = snapshot.evidence["ev-fixture-eligible-traction-gross_margin"]
    observations = []
    for role, value in (("revenue", 100), ("cost_of_revenue", 50)):
        e = template.model_copy(
            update=dict(
                evidence_id="synthetic-finance:" + role,
                locator="https://example.com/offline-finance-fixture",
                evidence_kind="reported",
                value=value,
                unit="one",
                currency="KRW",
                period="2025-01-01/2025-12-31",
                value_as_of=snapshot.as_of,
                excerpt=f"Synthetic FY2025 {role}: KRW {value}",
                claim=f"Synthetic FY2025 {role}: KRW {value}",
                supporting_evidence_ids=[],
                conflicts_with=[],
            ),
            deep=True,
        )
        snapshot.evidence[e.evidence_id] = e
        snapshot.evidence_ids.append(e.evidence_id)
        observations.append((role, e))
        for provenance in e.provenance:
            snapshot.chunks[provenance.chunk_id].text += "\n" + e.excerpt
            snapshot.retrieval_records[provenance.retrieval_id].evidence_ids.append(
                e.evidence_id
            )
    snapshot = checked_snapshot(snapshot)
    facts = tuple(
        ReviewedFinancialFact(
            **{
                k: getattr(snapshot, k)
                for k in (
                    "run_id",
                    "snapshot_id",
                    "candidate_id",
                    "evaluation_round",
                    "evidence_revision",
                    "policy_version",
                )
            },
            reviewer_reference="synthetic-caller-finance:" + role,
            accounting_entity=snapshot.candidate_id,
            metric_role=role,
            funding_round=None,
            valuation_basis=None,
            period=parse_period(e.period),
            evidence=e.model_copy(deep=True),
        )
        for role, e in observations
    )
    outputs["business_deal"] = {
        d: dict(
            criteria=[
                dict(
                    schema_version=snapshot.schema_version,
                    criterion_id=c.criterion_id,
                    status="missing",
                    rating=None,
                    evidence_ids=[],
                    rationale="Synthetic undisclosed financial fact",
                    missing_reason="not_disclosed",
                )
                for c in policy.criteria
                if c.dimension == d
            ],
            research_gaps=[],
            caveats=[],
        )
        for d in ("traction", "deal_terms")
    }
    c = next(
        c
        for c in outputs["business_deal"]["traction"]["criteria"]
        if c["criterion_id"] == "traction.gross_margin"
    )
    c.update(
        status="observed",
        rating=5,
        missing_reason=None,
        evidence_ids=[f.evidence.evidence_id for f in facts],
        rationale="Synthetic reviewed 50% gross margin",
    )
    return snapshot, policy, rubric, market, outputs, finance, facts


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "founder_timeout",
        "market_timeout",
        "technology_timeout",
        "moat_timeout",
        "business_deal_timeout",
        "founder_stale",
        "technology_stale",
        "moat_stale",
        "bd_half",
        "market_schema",
        "live",
    ],
)
def test_five_real_implementations_one_pinned_frozen_graph(
    five_implementation_case, fault
):
    import importlib

    from skala_rag.agents.business_deal import ApprovedVerifiers, evaluate_business_deal
    from skala_rag.agents.founder import evaluate_founder_approved_fixture
    from skala_rag.agents.founder_verification import ReviewedFounderAnchor
    from skala_rag.agents.moat import evaluate_moat_approved_fixture
    from skala_rag.agents.moat_verification import (
        ReviewedMoatAnchor,
        core_artifact_digest,
        frozen_snapshot_digest,
    )
    from skala_rag.agents.technology import evaluate_technology_approved_fixture
    from skala_rag.agents.technology_verification import ReviewedTechnologyAnchor

    snapshot, policy, rubric, market, outputs, finance, facts = five_implementation_case
    registry = pinned_approval_registry(ROOT)
    digest = frozen_snapshot_digest(snapshot)
    if fault and fault.endswith("_stale"):
        snapshot.evidence["ev-demand"].claim += " synthetic unrelated change"
        assert frozen_snapshot_digest(snapshot) != digest
    if fault == "bd_half":
        outputs["business_deal"]["deal_terms"]["criteria"][0]["rating"] = 9
    frozen = snapshot.model_dump(mode="json")
    llms = {
        b: FakeLLM(
            [
                LLMError(ErrorCode.LLM_TIMEOUT, "Synthetic redacted transport timeout")
                if fault == b + "_timeout"
                else output
            ]
        )
        for b, output in outputs.items()
    }
    people = ("synthetic-founder-1",)
    links = {
        eid: people[0]
        for eid, e in snapshot.evidence.items()
        if e.scope == "company"
        and any(c.startswith("founder.") for c in e.criterion_ids)
    }
    calls, terminals, containers, raised = Counter(), {}, [], {}
    reviews, resolutions, receipt_registry = [], [], {}
    lock, barrier = Lock(), Barrier(5, timeout=5)

    def review_for(branch):
        def review(c, evidence):
            assert set(evidence) == set(c.evidence_ids)
            assert all(e == snapshot.evidence[eid] for eid, e in evidence.items())
            common = dict(
                review_reference="synthetic-caller:" + c.criterion_id,
                artifact_sha256=core_artifact_digest(rubric),
                snapshot_sha256=digest
                if fault == branch + "_stale"
                else frozen_snapshot_digest(snapshot),
                criterion_id=c.criterion_id,
                rating=c.rating,
                evidence_ids=tuple(c.evidence_ids),
            )
            if branch == "founder":
                r = ReviewedFounderAnchor(
                    **common,
                    founder_person_ids=people,
                    person_by_evidence_id=tuple(
                        (eid, links[eid]) for eid in c.evidence_ids
                    ),
                    anchor_facts_reviewed=True,
                    minimum_evidence_reviewed=True,
                    person_identity_reviewed=True,
                    employment_identity_reviewed=True,
                    independent_corroboration_reviewed=True,
                )
            elif branch == "technology":
                r = ReviewedTechnologyAnchor(
                    **common,
                    anchor_facts_reviewed=True,
                    minimum_evidence_reviewed=True,
                    direct_negative_facts_reviewed=True,
                    independent_corroboration_reviewed=True,
                )
            else:
                r = ReviewedMoatAnchor(**common)
            with lock:
                reviews.append((branch, r))
                receipt_registry[r.review_reference] = r
            return r

        return review

    def resolve(r):
        with lock:
            resolutions.append(r)
        return receipt_registry.get(r.review_reference) == r

    def finance_review(c, received):
        expected = snapshot.model_copy(
            update={
                "evidence": {
                    eid: e
                    for eid, e in snapshot.evidence.items()
                    if e.scope == "company"
                    and any(
                        cid.startswith(("traction.", "deal_terms."))
                        for cid in e.criterion_ids
                    )
                },
                "evidence_ids": sorted(
                    eid
                    for eid, e in snapshot.evidence.items()
                    if e.scope == "company"
                    and any(
                        cid.startswith(("traction.", "deal_terms."))
                        for cid in e.criterion_ids
                    )
                ),
            },
            deep=True,
        )
        assert (
            received == expected
        )  # Existing BD supplies its explicit Finance projection.
        assert snapshot.model_dump(mode="json") == frozen
        assert c.criterion_id == "traction.gross_margin" and c.rating == 5
        assert set(c.evidence_ids) == {f.evidence.evidence_id for f in facts}
        with lock:
            reviews.append(("business_deal", c.model_copy(deep=True)))
        return True  # Caller-owned synthetic authority, never authenticated review.

    implementations = dict(
        founder=evaluate_founder_approved_fixture,
        market=evaluate_market,
        technology=evaluate_technology_approved_fixture,
        moat=evaluate_moat_approved_fixture,
        business_deal=evaluate_business_deal,
    )
    for b in implementations:
        assert (
            Path(importlib.import_module("skala_rag.agents." + b).__file__).resolve()
            == (ROOT / "src/skala_rag/agents" / (b + ".py")).resolve()
        )

    def callback_for(b):
        def callback(received):
            with lock:
                calls[b] += 1
            barrier.wait()
            assert (
                received is not snapshot and received.model_dump(mode="json") == frozen
            )
            common = dict(
                llm=llms[b],
                clock=FakeClock(NOW),
                schema_version=received.schema_version,
            )
            approved = dict(
                policy_path=ROOT / "configs/scoring.v3.json",
                approvals=registry.policy_approvals(),
                approval_verifier=registry.verify_policy,
                rubric=rubric,
                artifact_approval=registry.core_approval(),
                artifact_verifier=registry.verify_core,
                verify_observation=review_for(b),
            )
            try:
                if b == "market":
                    if fault == "market_schema":
                        common["schema_version"] = "synthetic-wrong-requested-schema"
                    result = implementations[b](
                        received,
                        **common,
                        rubric=rubric,
                        policy=policy,
                        target_market=MarketTarget(
                            segment_id="kr-logistics-amr", geographies=("KR", "GLOBAL")
                        ),
                        market_links=market.links,
                    )
                elif b == "business_deal":
                    result = implementations[b](
                        received,
                        **common,
                        rubric=finance,
                        policy=policy,
                        financial_facts=facts,
                        execution_mode="fixture",
                        verifiers=ApprovedVerifiers(
                            finance["rubric_version"],
                            finance["rubric_version"],
                            finance["rubric_version"],
                            finance_review,
                            finance_review,
                            lambda c, s: False,
                        ),
                    )
                else:
                    extra = {} if b == "moat" else dict(review_verifier=resolve)
                    if b == "founder":
                        extra.update(
                            founder_person_ids=people,
                            verified_person_by_evidence_id=links,
                        )
                    result = implementations[b](received, **common, **approved, **extra)
                    if b == "technology":
                        containers.append(result)
                        result = result.result
            except Exception as exc:
                raised[b] = exc
                raise  # Current graph catches ordinary technical review failures.
            terminals[b] = result
            return result

        return (
            bind_baseline_evaluator_v3(
                b,
                callback,
                criteria=policy.criteria,
                industry_evidence_dimensions={"market"},
            )
            if b in {"founder", "market", "technology"}
            else callback
        )

    state = dict(
        snapshot_v3=frozen,
        current_candidate_id=snapshot.candidate_id,
        evaluation_rounds={snapshot.candidate_id: snapshot.evaluation_round},
        evidence_revisions={snapshot.candidate_id: snapshot.evidence_revision},
        run_input=dict(
            execution_mode="live" if fault == "live" else "fixture",
            policy_version=policy.policy_version,
        ),
        snapshots={snapshot.snapshot_id: deepcopy(frozen)},
        candidates=[{"candidate_id": snapshot.candidate_id}],
        candidate_index=0,
        candidate_outcomes={},
        candidate_status={},
        errors=[],
    )
    before = deepcopy(state)
    events = list(
        build_evaluation_graph_v3(
            {b: callback_for(b) for b in BRANCH_DIMENSIONS},
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
    values = [v for mode, v in events if mode == "values"]
    updates = [v for mode, v in events if mode == "updates"]
    out = values[-1]
    assert state == before and snapshot.model_dump(mode="json") == frozen
    assert out["snapshot_v3"] == frozen and out["snapshots"] == before["snapshots"]
    assert calls == (
        Counter() if fault == "live" else Counter({b: 1 for b in BRANCH_DIMENSIONS})
    )
    assert all(len(llm.calls) == (0 if fault == "live" else 1) for llm in llms.values())
    assert sum("join_v3" in u for u in updates) == (0 if fault == "live" else 1)
    assert all(len(v.get("evaluations_v3", {})) in {0, 6} for v in values)
    stored = {r["branch_id"]: r for r in out.get("branch_results_v3", {}).values()}
    for b, result in terminals.items():
        for field in (
            "schema_version",
            "run_id",
            "candidate_id",
            "evaluation_round",
            "snapshot_id",
            "evidence_revision",
            "policy_version",
        ):
            if fault == "market_schema" and b == "market" and field == "schema_version":
                assert stored[b][field] == snapshot.schema_version
                continue  # Preserve the original wrong-schema result locally.
            assert (
                stored[b][field] == getattr(result, field) == getattr(snapshot, field)
            )
        if b in {"moat", "business_deal"}:
            assert stored[b] == result.model_dump(mode="json")
        elif result.status == "success" and not (
            fault == "market_schema" and b == "market"
        ):
            assert stored[b]["evaluations"][b] == V3Evaluation.model_validate(
                result.evaluation.model_dump()
            ).model_dump(mode="json")
    if fault is None:
        assert out["evaluation_status_v3"] == "success", (
            out["errors"],
            raised,
            {b: r.status for b, r in terminals.items()},
        )
        assert len(out["evaluations_v3"]) == 6
        for b, result in terminals.items():
            dimensions = (
                result.evaluations
                if b in {"moat", "business_deal"}
                else {b: result.evaluation}
            )
            for dimension, evaluation in dimensions.items():
                expected = (
                    outputs[b][dimension]["criteria"]
                    if b == "business_deal"
                    else outputs[b]["criteria"]
                    if isinstance(outputs[b], dict)
                    else [c.model_dump() for c in outputs[b].criteria]
                )
                assert {c.criterion_id for c in evaluation.criteria} == {
                    c["criterion_id"] for c in expected
                }
                for actual in evaluation.criteria:
                    original = next(
                        c for c in expected if c["criterion_id"] == actual.criterion_id
                    )
                    for field in (
                        "status",
                        "rating",
                        "evidence_ids",
                        "rationale",
                        "missing_reason",
                    ):
                        assert getattr(actual, field) == original.get(field)
                    for eid in actual.evidence_ids:
                        assert (
                            actual.criterion_id in snapshot.evidence[eid].criterion_ids
                        )
        assert set(terminals) == set(implementations) and raised == {}
        assert Counter(b for b, r in reviews) == Counter(
            founder=3, technology=4, moat=4, business_deal=2
        )
        assert len(resolutions) == 7 and all(
            r.snapshot_sha256 == digest for r in resolutions
        )
        assert all(r.snapshot_sha256 == digest for b, r in reviews if b == "moat")
        assert len(containers) == 1 and containers[0].trace
        assert containers[0].prompt_version and containers[0].allowed_evidence_ids
        assert {(t.criterion_id, t.evidence_id) for t in containers[0].trace} == {
            (c.criterion_id, eid)
            for c in outputs["technology"].criteria
            for eid in c.evidence_ids
        }
        for t in containers[0].trace:
            e = snapshot.evidence[t.evidence_id]
            record = snapshot.retrieval_records[t.retrieval_id]
            assert (
                t.snapshot_id == snapshot.snapshot_id
                and t.evidence_id in record.evidence_ids
            )
            assert t.chunk_id in record.chunk_ids and e.source_id in record.source_ids
            assert e.excerpt in snapshot.chunks[t.chunk_id].text
        observed = next(
            c
            for c in terminals["business_deal"].evaluations["traction"].criteria
            if c.criterion_id == "traction.gross_margin"
        )
        assert observed.status == "observed" and observed.rating == 5
        assert set(observed.evidence_ids) == {f.evidence.evidence_id for f in facts}
        assert all(
            f.evidence == snapshot.evidence[f.evidence.evidence_id] for f in facts
        )
        assert out["candidate_index"] == 0 and not any(
            "archive_advance_v3" in u for u in updates
        )
    else:
        assert out["evaluation_status_v3"] == "failure" and out["evaluations_v3"] == {}
        assert out["candidate_index"] == 1 and out["current_candidate_id"] is None
        assert (
            sum("archive_advance_v3" in u for u in updates) == 1
            and archive_advance_failure_v3(out) == {}
        )
        assert (
            "score_summaries" not in out
            and "investment_decisions" not in out
            and "SECRET" not in str(out)
        )
        assert (
            out["candidate_outcomes"][snapshot.candidate_id]["failure_ids"]
            == out["evaluation_failure_ids_v3"]
        )
        if fault.endswith("_timeout") or fault == "bd_half":
            b = (
                fault.removesuffix("_timeout")
                if fault != "bd_half"
                else "business_deal"
            )
            result = terminals[b]
            assert result.status == "failure"
            assert (
                result.evaluations
                if b in {"moat", "business_deal"}
                else result.evaluation
            ) is None
            assert out["errors"] == [e.model_dump(mode="json") for e in result.errors]
            assert (
                out["evaluation_failure_ids_v3"] == [e.error_id for e in result.errors]
                and raised == {}
            )
        elif fault.endswith("_stale"):
            b = fault.removesuffix("_stale")
            if b == "moat":
                assert (
                    terminals[b].status == "failure"
                    and terminals[b].evaluations is None
                )
                assert out["errors"] == [
                    e.model_dump(mode="json") for e in terminals[b].errors
                ]
            else:
                assert set(raised) == {b} and b not in terminals
                assert stored[b]["evaluations"] is None
                assert (
                    out["errors"][0]["error_code"] == "UPSTREAM_INVALID"
                    and out["errors"][0]["retryable"] is False
                )
        elif fault == "market_schema":
            assert terminals["market"].status == "success" and raised == {}
            assert (
                terminals["market"].schema_version == "synthetic-wrong-requested-schema"
            )
            assert out["errors"][0]["error_code"] == "UPSTREAM_INVALID"
        else:
            assert (
                terminals == raised == {} and reviews == resolutions == containers == []
            )


@pytest.mark.parametrize("branch", ["founder", "moat", "business_deal"])
def test_five_implementation_actual_entry_gates(five_implementation_case, branch):
    import importlib

    from skala_rag.agents.business_deal import ApprovedVerifiers

    snapshot, policy, rubric, _, outputs, finance, _ = five_implementation_case
    registry = pinned_approval_registry(ROOT)
    llm, touched = FakeLLM([outputs[branch]]), []

    def forbidden(*args):
        touched.append(True)
        raise AssertionError("Runtime gate must precede path/review/model")

    module = importlib.import_module("skala_rag.agents." + branch)
    common = dict(llm=llm, clock=FakeClock(NOW), schema_version=snapshot.schema_version)
    if branch == "business_deal":
        evaluator = module.evaluate_business_deal
        kwargs = dict(
            rubric=finance,
            policy=policy,
            execution_mode="real",
            verifiers=ApprovedVerifiers(
                finance["rubric_version"],
                finance["rubric_version"],
                finance["rubric_version"],
                forbidden,
                forbidden,
                forbidden,
            ),
        )
        message = "actual evaluation blocked"
    else:
        evaluator = getattr(module, "evaluate_" + branch + "_approved_fixture")
        kwargs = dict(
            actual_runtime=True,
            policy_path=ROOT / "synthetic-nonexistent-policy.json",
            approvals=registry.policy_approvals(),
            approval_verifier=forbidden,
            rubric=rubric,
            artifact_approval=registry.core_approval(),
            artifact_verifier=forbidden,
            verify_observation=forbidden,
        )
        if branch == "founder":
            kwargs.update(
                founder_person_ids=("synthetic-person",),
                verified_person_by_evidence_id={},
                review_verifier=forbidden,
            )
        message = "actual runtime unavailable"
    with pytest.raises(ValueError, match=message):
        evaluator(snapshot, **common, **kwargs)
    assert llm.calls == touched == []
