"""Offline synthetic approval consumers; never actual registry/runtime evidence."""

import importlib
from decimal import Decimal
from pathlib import Path

import pytest
from tests.unit.test_approved_policy import approval_payload
from tests.unit.test_scoring_v3 import evaluations

from skala_rag.contracts import EvaluationSnapshot
from skala_rag.scoring.aggregate_v3 import aggregate_scores_v3
from skala_rag.scoring.approved_policy import PolicyApprovals
from skala_rag.scoring.decision_v3 import decide_v3
from skala_rag.scoring.v3_policy import load_v3_policy

PATH = Path("configs/scoring.v3.json")


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    import socket

    def denied(*args, **kwargs):
        raise AssertionError("approved consumer tests must stay offline")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def consumer_module():
    return importlib.import_module("skala_rag.scoring.approved_consumers")


def source(**overrides):
    inputs = dict(
        path=PATH,
        approvals=PolicyApprovals.model_validate(approval_payload()),
        approval_verifier=lambda evidence, policy: (
            evidence.model_dump() == approval_payload()[evidence.scope]
        ),
        execution_mode="fixture",
    )
    return consumer_module().ApprovedPolicySource(**{**inputs, **overrides})


def snapshot():
    # Schema-valid synthetic frozen input. Provenance/semantic join is upstream.
    return EvaluationSnapshot(
        schema_version="v3-test",
        snapshot_id="snap",
        run_id="run",
        candidate_id="co",
        evaluation_round=1,
        evidence_revision=1,
        policy_version="v3-operational-1.0.0",
        corpus_version="synthetic-corpus",
        index_version="synthetic-index",
        as_of="2026-09-30",
        evidence_ids=[],
        evidence={},
        sources={},
        chunks={},
        retrieval_records={},
    )


def test_loader_backed_approved_aggregate_matches_fixture_numeric_core():
    policy = load_v3_policy(PATH, execution_mode="fixture")
    items = evaluations(policy)
    frozen = snapshot()
    result = consumer_module().aggregate_scores_approved(
        items, source(), snapshot=frozen, applicability_verifier=None
    )
    assert result == aggregate_scores_v3(
        items, policy, snapshot=frozen, applicability_verifier=None
    )
    assert len(result.criterion_points) == 23
    assert result.normalized_score == 100


@pytest.mark.parametrize(
    "value,label",
    [
        ("59.99", "PASS"),
        ("60", "WATCHLIST"),
        ("69.99", "WATCHLIST"),
        ("70", "RECOMMEND"),
        ("79.996", "RECOMMEND"),
        ("80", "RECOMMEND_PRIORITY"),
    ],
)
def test_approved_decision_reuses_unrounded_thresholds(value, label):
    policy = load_v3_policy(PATH, execution_mode="fixture")
    summary = aggregate_scores_v3(
        evaluations(policy), policy, applicability_verifier=None
    )
    summary.normalized_score = Decimal(value)
    actual = consumer_module().decide_approved(summary, source())
    assert actual == decide_v3(summary, policy)
    assert actual.label == label


def test_approved_selector_reuses_raw_id_ties_and_permutation_order():
    from itertools import permutations

    from tests.unit.test_select_v3 import entry

    from skala_rag.scoring.selector_v3 import select_best_v3

    policy = load_v3_policy(PATH, execution_mode="fixture")
    rows = [
        entry("z", "RECOMMEND", "100"),
        entry("a", "RECOMMEND_PRIORITY", "80"),
        entry(" A", "RECOMMEND_PRIORITY", "80"),
        entry("A", "RECOMMEND_PRIORITY", "80"),
    ]
    for order in permutations(rows):
        actual = consumer_module().select_best_approved(
            order, source(), run_id="run", schema_version="v3-test"
        )
        assert actual == select_best_v3(
            order, policy, run_id="run", schema_version="v3-test"
        )
        assert actual.selected_candidate_id == " A"


def invoke_consumer(name, inputs, payload=None):
    module = consumer_module()
    if name == "aggregate":
        return module.aggregate_scores_approved(
            payload, inputs, snapshot=snapshot(), applicability_verifier=None
        )
    if name == "decision":
        return module.decide_approved(payload, inputs)
    return module.select_best_approved(
        payload, inputs, run_id="run", schema_version="v3-test"
    )


@pytest.mark.parametrize("name", ["aggregate", "decision", "selector"])
@pytest.mark.parametrize("scope", ["operational", "core", "finance"])
@pytest.mark.parametrize("answer", [False, 1, "exception"])
def test_each_consumer_rejects_unverified_approval_before_consumption(
    monkeypatch, name, scope, answer
):
    module = consumer_module()
    consumed = []

    def forbidden(*args, **kwargs):
        consumed.append(True)
        raise AssertionError("numeric core must not run")

    for helper in ("_aggregate_scores_v3", "_decide_v3", "_select_best_v3"):
        monkeypatch.setattr(module, helper, forbidden)

    def verify(evidence, policy):
        if evidence.scope != scope:
            return True
        if answer == "exception":
            raise RuntimeError("synthetic registry offline")
        return answer

    with pytest.raises((ValueError, RuntimeError)):
        invoke_consumer(name, source(approval_verifier=verify), object())
    assert consumed == []


@pytest.mark.parametrize("name", ["aggregate", "decision", "selector"])
@pytest.mark.parametrize("mutation", ["scope", "version", "artifact"])
def test_nested_approval_input_is_revalidated_not_cached(name, mutation):
    from dataclasses import replace

    inputs = source()
    approvals = inputs.approvals
    changed = approvals.core.model_copy(
        update={
            "scope": "finance" if mutation == "scope" else "core",
            "version": "stale" if mutation == "version" else "core-0.1.0",
            "reference": "unapproved-content"
            if mutation == "artifact"
            else approvals.core.reference,
        }
    )
    stale = replace(inputs, approvals=approvals.model_copy(update={"core": changed}))
    with pytest.raises(ValueError):
        invoke_consumer(name, stale, object())


@pytest.mark.parametrize("name", ["aggregate", "decision", "selector"])
def test_arbitrary_constructed_outer_dto_is_not_authority(name):
    from skala_rag.scoring.approved_policy import ApprovedScoringPolicy

    constructed = ApprovedScoringPolicy.model_construct(execution_mode="fixture")
    with pytest.raises(ValueError, match="loader-backed"):
        invoke_consumer(name, constructed, object())


@pytest.mark.parametrize("name", ["aggregate", "decision", "selector"])
def test_actual_runtime_absence_blocks_even_true_callbacks_before_any_io(
    monkeypatch, name
):
    module = consumer_module()
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("no file/network/verifier/consumption before denial")

    monkeypatch.setattr(module, "load_approved_policy", forbidden)
    for helper in ("_aggregate_scores_v3", "_decide_v3", "_select_best_v3"):
        monkeypatch.setattr(module, helper, forbidden)
    inputs = source(
        path="does-not-exist",
        execution_mode="live",
        approval_verifier=forbidden,
        live_gate_verifier=forbidden,
    )
    with pytest.raises(ValueError, match="registry/runtime unavailable"):
        invoke_consumer(name, inputs, object())
    assert calls == []


@pytest.mark.parametrize(
    "case", ["missing", "na", "all_missing", "market40", "market_missing"]
)
def test_missing_na_and_hold_guard_equivalence(case):
    policy = load_v3_policy(PATH, execution_mode="fixture")
    missing = (
        {
            "technology.maturity",
            "technology.reliability",
            "technology.integration",
            "technology.commercialization",
            "founder.expertise",
            "founder.industry",
        }
        if case == "missing"
        else ()
    )
    na = (
        {"traction.revenue_growth", "traction.gross_margin", "traction.rule_of_40"}
        if case == "na"
        else ()
    )
    if case == "all_missing":
        missing = {c.criterion_id for c in policy.criteria}
    if case == "market_missing":
        missing = {c.criterion_id for c in policy.criteria if c.dimension == "market"}
    items = evaluations(policy, missing=missing, na=na)
    if case == "market40":
        for item in items:
            if item.dimension == "market":
                for assessment in item.criteria:
                    assessment.rating = 2
    seen = []

    def verify(assessment, frozen):
        seen.append(frozen)
        return assessment.applicability_rule_id == "external-rule"

    frozen = snapshot()
    actual = consumer_module().aggregate_scores_approved(
        items, source(), snapshot=frozen, applicability_verifier=verify
    )
    expected = aggregate_scores_v3(
        items, policy, snapshot=frozen, applicability_verifier=verify
    )
    assert actual == expected
    assert consumer_module().decide_approved(actual, source()) == decide_v3(
        expected, policy
    )
    if case == "na":
        assert actual.not_applicable_weight == 6
        assert actual.applicable_weight == 94
        assert all(s == frozen and s is not frozen for s in seen[:3])
    if case == "all_missing":
        assert actual.observed_score == 0
        assert actual.applicable_weight == 100
    if case == "market_missing":
        assert actual.weighted_missing_pct == 30
        assert actual.hold_reasons == ["WEIGHTED_MISSING", "LOW_MARKET"]
    if case == "market40":
        assert actual.dimension_scores["market"].dimension_score_pct == 40
        assert "LOW_MARKET" in actual.hold_reasons


@pytest.mark.parametrize("dimension", [None, "market", "technology"])
def test_zero_denominator_preserves_candidate_error(dimension):
    from skala_rag.scoring.aggregate_v3 import ZeroDenominatorV3

    policy = load_v3_policy(PATH, execution_mode="fixture")
    na = {
        c.criterion_id
        for c in policy.criteria
        if dimension is None or c.dimension == dimension
    }
    with pytest.raises(ZeroDenominatorV3) as exc:
        consumer_module().aggregate_scores_approved(
            evaluations(policy, na=na),
            source(),
            snapshot=snapshot(),
            applicability_verifier=lambda a, s: True,
        )
    assert exc.value.candidate_id == "co"


@pytest.mark.parametrize("answer", [None, False, 1, "exception"])
def test_unapproved_na_never_becomes_missing_or_zero(answer):
    policy = load_v3_policy(PATH, execution_mode="fixture")

    def verify(a, s):
        if answer == "exception":
            raise RuntimeError("synthetic N/A registry offline")
        return answer

    with pytest.raises((ValueError, RuntimeError)):
        consumer_module().aggregate_scores_approved(
            evaluations(policy, na={"traction.revenue_growth"}),
            source(),
            snapshot=snapshot(),
            applicability_verifier=verify if answer is not None else None,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "other"),
        ("candidate_id", "other"),
        ("evaluation_round", 2),
        ("evidence_revision", 2),
        ("snapshot_id", "stale"),
        ("policy_version", "stale"),
        ("schema_version", "stale"),
    ],
)
@pytest.mark.parametrize("target", ["evaluation", "snapshot"])
def test_stale_generation_rejected(field, value, target):
    policy = load_v3_policy(PATH, execution_mode="fixture")
    items = evaluations(policy)
    frozen = snapshot()
    if target == "evaluation":
        setattr(items[0], field, value)
    else:
        setattr(frozen, field, value)
    with pytest.raises(ValueError, match="generation"):
        consumer_module().aggregate_scores_approved(
            items, source(), snapshot=frozen, applicability_verifier=None
        )


@pytest.mark.parametrize("mutation", ["partial", "duplicate", "catalog"])
def test_partial_or_noncomplete_evaluations_rejected(mutation):
    policy = load_v3_policy(PATH, execution_mode="fixture")
    items = evaluations(policy)
    if mutation == "partial":
        items.pop()
    elif mutation == "duplicate":
        items[-1] = items[0]
    else:
        items[0].criteria.pop()
    with pytest.raises(ValueError, match="six|dimension"):
        consumer_module().aggregate_scores_approved(
            items, source(), snapshot=snapshot(), applicability_verifier=None
        )


@pytest.mark.parametrize("missing_snapshot", [None, object()])
def test_snapshot_is_required(missing_snapshot):
    policy = load_v3_policy(PATH, execution_mode="fixture")
    with pytest.raises(ValueError, match="frozen"):
        consumer_module().aggregate_scores_approved(
            evaluations(policy),
            source(),
            snapshot=missing_snapshot,
            applicability_verifier=None,
        )


@pytest.mark.parametrize("rows", [[], ["WATCHLIST", "PASS"], ["unknown", "failed"]])
def test_selector_preserves_no_selection_states(rows):
    from tests.unit.test_select_v3 import entry

    from skala_rag.scoring.selector_v3 import select_best_v3

    policy = load_v3_policy(PATH, execution_mode="fixture")
    if rows == ["unknown", "failed"]:
        inputs = [
            {
                **entry("x", "RECOMMEND_PRIORITY", "100"),
                "eligibility_status": "unknown",
            },
            {**entry("y", "RECOMMEND_PRIORITY", "100"), "status": "failed"},
        ]
    else:
        inputs = [entry(str(i), label, "90") for i, label in enumerate(rows)]
    result = consumer_module().select_best_approved(
        inputs, source(), run_id="run", schema_version="v3-test"
    )
    assert result == select_best_v3(
        inputs, policy, run_id="run", schema_version="v3-test"
    )
    assert result.selected_candidate_id is None


def test_full_offline_consumer_chain_reverifies_each_stage():
    from tests.unit.test_select_v3 import entry

    policy = load_v3_policy(PATH, execution_mode="fixture")
    seen = []

    def verify(evidence, actual):
        seen.append(evidence.scope)
        return evidence.model_dump() == approval_payload()[evidence.scope]

    inputs = source(approval_verifier=verify)
    module = consumer_module()
    summary = module.aggregate_scores_approved(
        evaluations(policy), inputs, snapshot=snapshot(), applicability_verifier=None
    )
    decision = module.decide_approved(summary, inputs)
    row = entry(
        summary.candidate_id,
        decision.label,
        summary.normalized_score,
        summary.weighted_missing_pct,
        summary.applicable_weight,
    )
    row["score_summary_id"] = summary.score_summary_id
    selected = module.select_best_approved(
        [row], inputs, run_id=summary.run_id, schema_version=summary.schema_version
    )
    assert selected.selected_candidate_id == summary.candidate_id
    assert selected.compared_score_summary_ids == (summary.score_summary_id,)
    assert seen == ["operational", "core", "finance"] * 3


@pytest.mark.parametrize("name", ["aggregate", "decision", "selector"])
@pytest.mark.parametrize("gate", ["runtime_readiness", "call_budget", "cost_budget"])
@pytest.mark.parametrize("answer", [False, 1, "exception"])
def test_independent_live_gate_refusal_then_actual_consumer_denial(name, gate, answer):
    from tests.unit.test_approved_policy import gates_payload

    from skala_rag.scoring.approved_policy import LiveScoringGates, load_approved_policy

    gates = LiveScoringGates.model_validate(gates_payload())
    seen = []

    def verify(actual, inputs):
        seen.append(actual)
        if actual != gate:
            return True
        if answer == "exception":
            raise RuntimeError("synthetic live gate resolver unavailable")
        return answer

    inputs = source(execution_mode="live", live_gates=gates, live_gate_verifier=verify)
    with pytest.raises((ValueError, RuntimeError)):
        load_approved_policy(
            inputs.path,
            approvals=inputs.approvals,
            approval_verifier=inputs.approval_verifier,
            execution_mode="live",
            live_gates=gates,
            live_gate_verifier=verify,
        )
    assert gate in seen
    before = list(seen)
    with pytest.raises(ValueError, match="registry/runtime unavailable"):
        invoke_consumer(name, inputs, object())
    assert seen == before


@pytest.mark.parametrize("name", ["aggregate", "decision", "selector"])
@pytest.mark.parametrize("change", ["open", "kipris", "krx", "중기부", "tavily"])
def test_open_decisions_and_excluded_provider_cannot_reach_consumers(name, change):
    from tests.unit.test_approved_policy import gates_payload

    from skala_rag.scoring.approved_policy import LiveScoringGates, load_approved_policy

    gates = LiveScoringGates.model_validate(gates_payload())
    updates = (
        {"open_decisions": ("D06-minimum-evidence",)}
        if change == "open"
        else {
            "provider": change,
        }
    )
    gates = gates.model_copy(update=updates)
    seen = []

    def verify(gate, actual):
        seen.append(gate)
        return True

    inputs = source(execution_mode="live", live_gates=gates, live_gate_verifier=verify)
    with pytest.raises(ValueError):
        load_approved_policy(
            inputs.path,
            approvals=inputs.approvals,
            approval_verifier=inputs.approval_verifier,
            execution_mode="live",
            live_gates=gates,
            live_gate_verifier=verify,
        )
    with pytest.raises(ValueError, match="registry/runtime unavailable"):
        invoke_consumer(name, inputs, object())
    assert seen == []


@pytest.mark.parametrize("name", ["aggregate", "decision", "selector"])
def test_verified_synthetic_live_contract_still_cannot_score(name):
    from tests.unit.test_approved_policy import gates_payload

    from skala_rag.scoring.approved_policy import LiveScoringGates, load_approved_policy

    gates = LiveScoringGates.model_validate(gates_payload())
    inputs = source(
        execution_mode="live",
        live_gates=gates,
        live_gate_verifier=lambda g, i: True,
    )
    contract = load_approved_policy(
        inputs.path,
        approvals=inputs.approvals,
        approval_verifier=inputs.approval_verifier,
        execution_mode="live",
        live_gates=gates,
        live_gate_verifier=inputs.live_gate_verifier,
    )
    assert contract.execution_mode == "live"
    assert contract.operational.execution_mode == "fixture"
    with pytest.raises(ValueError, match="registry/runtime unavailable"):
        invoke_consumer(name, inputs, object())
    with pytest.raises(ValueError, match="loader-backed"):
        invoke_consumer(name, contract, object())


@pytest.mark.parametrize("name", ["aggregate", "decision", "selector"])
@pytest.mark.parametrize("mutation", ["numeric", "version", "catalog"])
def test_policy_artifact_reloaded_and_denied_before_numeric_core(
    tmp_path, monkeypatch, name, mutation
):
    import json

    module = consumer_module()
    payload = json.loads(PATH.read_text())
    if mutation == "numeric":
        payload["numeric"]["recommend_score"] = "71"
    elif mutation == "version":
        payload["policy_version"] = "stale"
    else:
        payload["criteria"][0]["criterion_id"] = "founder.invented"
    path = tmp_path / "unapproved-policy.json"
    path.write_text(json.dumps(payload))
    calls = []

    def forbidden(*a, **k):
        calls.append(True)
        raise AssertionError("invalid artifact must not reach verifier/core")

    for helper in ("_aggregate_scores_v3", "_decide_v3", "_select_best_v3"):
        monkeypatch.setattr(module, helper, forbidden)
    with pytest.raises(ValueError):
        invoke_consumer(name, source(path=path, approval_verifier=forbidden), object())
    assert calls == []


def test_mutated_same_path_cannot_reuse_previous_approval(tmp_path):
    import json

    path = tmp_path / "synthetic-policy.json"
    path.write_text(PATH.read_text())
    inputs = source(path=path)
    policy = load_v3_policy(PATH, execution_mode="fixture")
    module = consumer_module()
    summary = module.aggregate_scores_approved(
        evaluations(policy), inputs, snapshot=snapshot(), applicability_verifier=None
    )
    payload = json.loads(path.read_text())
    payload["numeric"]["recommend_score"] = "71"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        module.decide_approved(summary, inputs)


@pytest.mark.parametrize("name", ["aggregate", "decision", "selector"])
def test_fixture_source_never_accepts_live_admission_inputs(name):
    from tests.unit.test_approved_policy import gates_payload

    from skala_rag.scoring.approved_policy import LiveScoringGates

    inputs = source(live_gates=LiveScoringGates.model_validate(gates_payload()))
    with pytest.raises(ValueError, match="fixture"):
        invoke_consumer(name, inputs, object())


def test_existing_fixture_apis_reject_outer_for_all_three_stages():
    from skala_rag.scoring.approved_policy import load_approved_policy
    from skala_rag.scoring.selector_v3 import select_best_v3

    policy = load_v3_policy(PATH, execution_mode="fixture")
    inputs = source()
    outer = load_approved_policy(
        inputs.path,
        approvals=inputs.approvals,
        approval_verifier=inputs.approval_verifier,
    )
    items = evaluations(policy)
    summary = aggregate_scores_v3(items, policy, applicability_verifier=None)
    with pytest.raises(ValueError, match="fixture"):
        aggregate_scores_v3(items, outer, applicability_verifier=None)
    with pytest.raises(ValueError, match="fixture"):
        decide_v3(summary, outer)
    with pytest.raises(ValueError, match="fixture"):
        select_best_v3([], outer, run_id="run", schema_version="v3-test")
