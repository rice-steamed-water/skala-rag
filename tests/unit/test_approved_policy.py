"""Synthetic approval registry only; never readiness or paid-call evidence."""

import importlib
import json
from pathlib import Path

import pytest

from skala_rag.scoring.v3_policy import load_v3_policy

PATH = Path("configs/scoring.v3.json")


def approval_payload():
    return {
        "contract_reference": "rice-steamed-water/skala-rag#168",
        "operational": {
            "scope": "operational",
            "version": "v3-operational-1.0.0",
            "reference": "rice-steamed-water/skala-rag#82",
        },
        "core": {
            "scope": "core",
            "version": "core-0.1.0",
            "reference": "synthetic-registry:core",
        },
        "finance": {
            "scope": "finance",
            "version": "finance-0.1.0",
            "reference": "synthetic-registry:finance",
        },
    }


def test_separate_approved_contract_preserves_fixture_policy():
    module = importlib.import_module("skala_rag.scoring.approved_policy")
    seen = []

    def verify(evidence, operational):
        seen.append(evidence.scope)
        return evidence.model_dump() == approval_payload()[evidence.scope]

    policy = module.load_approved_policy(
        PATH,
        approvals=module.PolicyApprovals.model_validate(approval_payload()),
        approval_verifier=verify,
    )
    baseline = load_v3_policy(PATH, execution_mode="fixture")
    assert policy.execution_mode == "fixture"
    assert policy.operational == baseline
    assert policy.criteria == baseline.criteria
    assert policy.policy_version == baseline.policy_version
    assert seen == ["operational", "core", "finance"]
    assert policy.operational.approval.live_budget == "not_approved"
    assert not hasattr(policy, "thresholds")  # Not a legacy fixed-100 scorer policy.


def gates_payload():
    # Synthetic admission observations, not current run authorization.
    return {
        "run_id": "synthetic-run",
        "policy_version": "v3-operational-1.0.0",
        "provider": "synthetic-provider",
        "open_decisions": [],
        "readiness": {
            "schema_version": "test",
            "required": True,
            "configured": True,
            "credential_required": True,
            "credential_present": True,
            "model_required": True,
            "model_available": True,
            "index_required": True,
            "index_available": True,
        },
        "limits": {
            "schema_version": "test",
            "max_calls": 2,
            "tool_max_calls": {"synthetic-provider": 2},
            "max_input_tokens": 100,
            "max_output_tokens": 100,
            "max_cost_usd": "0.20",
        },
        "allowance": {
            "schema_version": "test",
            "input_tokens": 10,
            "output_tokens": 10,
            "max_cost_usd": "0.10",
        },
        "runtime_readiness_reference": "synthetic:readiness",
        "call_budget_reference": "synthetic:calls",
        "cost_budget_reference": "synthetic:cost",
    }


def test_live_contract_requires_independent_verified_gates():
    module = importlib.import_module("skala_rag.scoring.approved_policy")
    gates = module.LiveScoringGates.model_validate(gates_payload())
    seen = []

    def verify_gate(gate, actual):
        seen.append(gate)
        return actual == gates

    policy = module.load_approved_policy(
        PATH,
        approvals=module.PolicyApprovals.model_validate(approval_payload()),
        approval_verifier=lambda evidence, policy: True,
        execution_mode="live",
        live_gates=gates,
        live_gate_verifier=verify_gate,
    )
    assert seen == ["runtime_readiness", "call_budget", "cost_budget"]
    assert policy.execution_mode == "live"
    assert policy.live_gates == gates
    assert policy.operational.execution_mode == "fixture"


@pytest.mark.parametrize("scope", ["operational", "core", "finance"])
@pytest.mark.parametrize("answer", [False, None, 1, "approved"])
def test_each_policy_approval_requires_exact_true(scope, answer):
    module = importlib.import_module("skala_rag.scoring.approved_policy")
    with pytest.raises(ValueError, match=scope):
        module.load_approved_policy(
            PATH,
            approvals=module.PolicyApprovals.model_validate(approval_payload()),
            approval_verifier=lambda evidence, policy: (
                answer if evidence.scope == scope else True
            ),
        )


@pytest.mark.parametrize("scope", ["operational", "core", "finance"])
def test_rubric_and_operational_version_mismatch_rejected(scope):
    module = importlib.import_module("skala_rag.scoring.approved_policy")
    payload = approval_payload()
    payload[scope]["version"] = "unapproved-2.0.0"
    with pytest.raises(ValueError, match="mismatch"):
        module.PolicyApprovals.model_validate(payload)


@pytest.mark.parametrize("answer", [False, None, 1, "approved"])
def test_live_gate_prose_and_truthy_values_are_not_evidence(answer):
    module = importlib.import_module("skala_rag.scoring.approved_policy")
    with pytest.raises(ValueError, match="runtime_readiness"):
        module.load_approved_policy(
            PATH,
            approvals=module.PolicyApprovals.model_validate(approval_payload()),
            approval_verifier=lambda evidence, policy: True,
            execution_mode="live",
            live_gates=module.LiveScoringGates.model_validate(gates_payload()),
            live_gate_verifier=lambda gate, gates: answer,
        )


def test_no_implicit_live_gates_or_approval_verifier():
    module = importlib.import_module("skala_rag.scoring.approved_policy")
    approvals = module.PolicyApprovals.model_validate(approval_payload())
    with pytest.raises(TypeError):
        module.load_approved_policy(PATH)
    with pytest.raises(ValueError, match="verifier"):
        module.load_approved_policy(PATH, approvals=approvals, approval_verifier=None)
    with pytest.raises(ValueError, match="independent"):
        module.load_approved_policy(
            PATH,
            approvals=approvals,
            approval_verifier=lambda a, p: True,
            execution_mode="live",
        )
    with pytest.raises(ValueError, match="independent"):
        module.load_approved_policy(
            PATH,
            approvals=approvals,
            approval_verifier=lambda a, p: True,
            execution_mode="live",
            live_gates=module.LiveScoringGates.model_validate(gates_payload()),
        )


@pytest.mark.parametrize("mode", ["real", "LIVE", None, True])
def test_unknown_execution_mode_rejected(mode):
    module = importlib.import_module("skala_rag.scoring.approved_policy")
    with pytest.raises(ValueError, match="execution_mode"):
        module.load_approved_policy(
            PATH,
            approvals=module.PolicyApprovals.model_validate(approval_payload()),
            approval_verifier=lambda a, p: True,
            execution_mode=mode,
        )


@pytest.mark.parametrize("mutation", ["version", "numeric", "catalog", "draft", "nan"])
def test_invalid_source_policy_cannot_be_approved(tmp_path, mutation):
    module = importlib.import_module("skala_rag.scoring.approved_policy")
    payload = json.loads(PATH.read_text())
    if mutation == "version":
        payload["policy_version"] = "unapproved-2.0.0"
    elif mutation == "numeric":
        payload["numeric"]["recommend_score"] = "71"
    elif mutation == "catalog":
        payload["criteria"][0]["criterion_id"] = "founder.invented"
    elif mutation == "draft":
        payload = json.loads(Path("configs/scoring.draft.json").read_text())
    else:
        payload["numeric"]["priority_score"] = "NaN"
    source = tmp_path / "unapproved.json"
    source.write_text(json.dumps(payload))
    seen = []
    with pytest.raises(ValueError):
        module.load_approved_policy(
            source,
            approvals=module.PolicyApprovals.model_validate(approval_payload()),
            approval_verifier=lambda a, p: seen.append(a) or True,
            execution_mode="live",
            live_gates=module.LiveScoringGates.model_validate(gates_payload()),
            live_gate_verifier=lambda g, i: True,
        )
    assert seen == []


def test_shared_evaluator_catalog_and_fixture_scoring_consumers():
    from tests.fixtures.loader import load_common_fixtures
    from tests.unit.test_scoring_v3 import evaluations

    from skala_rag.agents.evaluation import (
        DimensionAssessmentOutput,
        assemble_evaluation,
        build_user_prompt,
    )
    from skala_rag.scoring.aggregate_v3 import aggregate_scores_v3
    from skala_rag.scoring.catalog import load_policy
    from skala_rag.scoring.decision_v3 import decide_v3

    module = importlib.import_module("skala_rag.scoring.approved_policy")
    approved = module.load_approved_policy(
        PATH,
        approvals=module.PolicyApprovals.model_validate(approval_payload()),
        approval_verifier=lambda a, p: True,
    )
    draft = load_policy("configs/scoring.draft.json", execution_mode="fixture")
    snapshot = next(iter(load_common_fixtures(draft).snapshots.values()))
    snapshot.policy_version = approved.policy_version
    rubric = {"rubric_version": "core-0.1.0", "dimensions": {"founder": {}}}
    output = DimensionAssessmentOutput.model_validate(
        {
            "criteria": [
                dict(
                    criterion_id=c.criterion_id,
                    status="missing",
                    rating=None,
                    evidence_ids=[],
                    rationale="Synthetic missing",
                    missing_reason="not_disclosed",
                )
                for c in approved.criteria
                if c.dimension == "founder"
            ],
            "research_gaps": [],
            "caveats": [],
        }
    )
    assert "founder.expertise" in build_user_prompt(
        "founder", snapshot, rubric, approved
    )
    result = assemble_evaluation(
        output,
        dimension="founder",
        snapshot=snapshot,
        policy=approved,
        rubric=rubric,
        schema_version="synthetic",
    )
    assert result.policy_version == approved.policy_version
    snapshot.policy_version = "wrong-generation"
    with pytest.raises(ValueError, match="POLICY_MISMATCH"):
        assemble_evaluation(
            output,
            dimension="founder",
            snapshot=snapshot,
            policy=approved,
            rubric=rubric,
            schema_version="synthetic",
        )
    original = load_v3_policy(PATH, execution_mode="fixture")
    for policy in (original, approved.operational):
        summary = aggregate_scores_v3(
            evaluations(policy), policy, applicability_verifier=None
        )
        assert summary.normalized_score == 100
        assert decide_v3(summary, policy).label == "RECOMMEND_PRIORITY"
    # Separate approved contract does not silently widen the fixture-only scorer.
    with pytest.raises(ValueError, match="fixture"):
        aggregate_scores_v3(
            evaluations(original), approved, applicability_verifier=None
        )


def test_explicit_zero_cost_remains_valid_for_a_zero_cost_allowance():
    # Existing AdapterRuntime supports zero; do not invent a positive-cost policy.
    module = importlib.import_module("skala_rag.scoring.approved_policy")
    payload = gates_payload()
    payload["limits"]["max_cost_usd"] = "0"
    payload["allowance"]["max_cost_usd"] = "0"
    assert module.LiveScoringGates.model_validate(payload).limits.max_cost_usd == 0


def test_loader_revalidates_constructed_nested_inputs():
    module = importlib.import_module("skala_rag.scoring.approved_policy")
    approvals = module.PolicyApprovals.model_validate(approval_payload())
    approvals = approvals.model_copy(
        update={"core": approvals.core.model_copy(update={"version": "draft"})}
    )
    with pytest.raises(ValueError):
        module.load_approved_policy(
            PATH, approvals=approvals, approval_verifier=lambda a, p: True
        )
    gates = module.LiveScoringGates.model_validate(gates_payload())
    gates.limits.tool_max_calls.clear()
    with pytest.raises(ValueError):
        module.load_approved_policy(
            PATH,
            approvals=module.PolicyApprovals.model_validate(approval_payload()),
            approval_verifier=lambda a, p: True,
            execution_mode="live",
            live_gates=gates,
            live_gate_verifier=lambda g, i: True,
        )


@pytest.mark.parametrize("denied", ["runtime_readiness", "call_budget", "cost_budget"])
def test_each_live_gate_can_independently_refuse(denied):
    module = importlib.import_module("skala_rag.scoring.approved_policy")
    with pytest.raises(ValueError, match=denied):
        module.load_approved_policy(
            PATH,
            approvals=module.PolicyApprovals.model_validate(approval_payload()),
            approval_verifier=lambda evidence, policy: True,
            execution_mode="live",
            live_gates=module.LiveScoringGates.model_validate(gates_payload()),
            live_gate_verifier=lambda gate, gates: gate != denied,
        )


@pytest.mark.parametrize(
    "section,key,value",
    [
        (None, "provider", "kipris"),
        (None, "provider", "krx"),
        (None, "provider", "중기부"),
        (None, "provider", "tavily"),
        (None, "open_decisions", ["D06-minimum-evidence"]),
        (None, "open_decisions", ["D08-new-RNG"]),
        (None, "policy_version", "draft-1"),
        ("readiness", "configured", False),
        ("readiness", "credential_present", False),
        ("readiness", "model_available", False),
        ("readiness", "index_available", False),
        ("limits", "max_calls", 0),
        ("limits", "max_input_tokens", None),
        ("limits", "max_output_tokens", None),
        ("limits", "tool_max_calls", {}),
        ("limits", "tool_max_calls", {"synthetic-provider": 0}),
        ("limits", "max_cost_usd", None),
        ("limits", "max_cost_usd", "0"),
        ("allowance", "max_cost_usd", None),
        ("allowance", "max_cost_usd", "0.21"),
        ("allowance", "input_tokens", 101),
        ("allowance", "output_tokens", 101),
    ],
)
def test_live_inconsistent_observations_rejected_even_with_true_verifier(
    section, key, value
):
    module = importlib.import_module("skala_rag.scoring.approved_policy")
    payload = gates_payload()
    (payload if section is None else payload[section])[key] = value
    with pytest.raises(ValueError):
        module.load_approved_policy(
            PATH,
            approvals=module.PolicyApprovals.model_validate(approval_payload()),
            approval_verifier=lambda evidence, policy: True,
            execution_mode="live",
            live_gates=module.LiveScoringGates.model_validate(payload),
            live_gate_verifier=lambda gate, gates: True,
        )
