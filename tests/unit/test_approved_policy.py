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


def runtime_binding(*, run_id="synthetic-run", schema_version="test", payload=None):
    from datetime import UTC, datetime, timedelta

    from skala_rag.contracts.tools import ToolBudget
    from skala_rag.fakes import FakeClock
    from skala_rag.scoring.approved_policy import (
        LiveScoringGates,
        ScoringRuntimeBinding,
    )
    from skala_rag.tools.runtime import (
        AdapterRuntime,
        BudgetLedger,
        CallContext,
        RuntimePolicy,
    )

    payload = gates_payload() if payload is None else payload
    payload["run_id"] = run_id
    for name in ("readiness", "limits", "allowance"):
        payload[name]["schema_version"] = schema_version
    gates = LiveScoringGates.model_validate(payload)
    policy = RuntimePolicy(
        schema_version=schema_version,
        execution_mode="live",
        retry_delays_seconds=(),
        live_approval_reference="synthetic:scope",
        timing_approval_reference="synthetic:timing",
    )
    clock = FakeClock(datetime(2026, 9, 30, tzinfo=UTC))

    def forbidden_sleep(seconds):
        pytest.fail("read-only preflight slept")

    runtime = AdapterRuntime(
        policy=policy,
        ledger=BudgetLedger(gates.limits),
        clock=clock,
        sleep=forbidden_sleep,
    )
    return ScoringRuntimeBinding(
        runtime=runtime,
        gates=gates,
        policy=policy,
        call=CallContext(
            schema_version=schema_version,
            call_id="synthetic-call",
            run_id=run_id,
            candidate_id=None,
            tool_name=gates.provider,
            node="synthetic-preflight",
        ),
        budget=ToolBudget(
            schema_version=schema_version,
            max_calls=2,
            max_retries=0,
            timeout_seconds=2,
            deadline=clock.now() + timedelta(seconds=30),
        ),
        readiness=gates.readiness,
        allowance=gates.allowance,
        provider=gates.provider,
        tool_name=gates.provider,
    )


def observe_binding(binding, **overrides):
    from skala_rag.scoring.approved_policy import observe_scoring_runtime

    return observe_scoring_runtime(
        binding,
        **{
            "run_id": binding.gates.run_id,
            "policy_version": binding.gates.policy_version,
            "schema_version": binding.call.schema_version,
            **overrides,
        },
    )


def forbid_runtime_mutation(monkeypatch, binding):
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append(True)
        pytest.fail("observation attempted runtime mutation/transport")

    monkeypatch.setattr(binding.runtime.ledger, "reserve", forbidden)
    monkeypatch.setattr(binding.runtime.ledger, "settle", forbidden)
    monkeypatch.setattr(binding.runtime, "execute", forbidden)
    return attempts


def test_runtime_observation_is_fresh_detached_and_read_only(monkeypatch):
    from skala_rag.tools.runtime import Usage

    binding = runtime_binding()
    first = observe_binding(binding)
    assert first["compatibility"]
    assert not first["semantic_authority"] and not first["live_admission"]
    assert binding.runtime.ledger.reserve(
        binding.tool_name, binding.allowance, live=True
    )
    assert binding.runtime.ledger.settle(
        binding.allowance,
        Usage(schema_version="test", input_tokens=2, output_tokens=3, cost_usd="0.01"),
    )
    before = binding.runtime.ledger.snapshot()
    attempts = forbid_runtime_mutation(monkeypatch, binding)
    second = observe_binding(binding)
    assert first["ledger"]["calls"] == 0 and second["ledger"]["calls"] == 1
    assert second["ledger"]["input_tokens_accounted"] == 2
    assert second["ledger"]["output_tokens_accounted"] == 3
    assert second["ledger"]["cost_usd_accounted"] == "0.01"
    second["ledger"]["tool_calls"].clear()
    assert binding.runtime.ledger.snapshot() == before
    assert binding.runtime.readiness_history == binding.runtime.error_history == {}
    assert attempts == []


@pytest.mark.parametrize(
    "damage",
    [
        "run",
        "policy_version",
        "schema",
        "provider",
        "tool",
        "call_run",
        "runtime_policy",
        "limits",
        "readiness",
        "allowance",
        "mode",
        "scope",
        "timing",
        "retries",
        "deadline",
        "expired",
        "request_calls",
        "invalid_counts",
        "missing_readiness",
        "unknown_tool",
    ],
)
def test_runtime_binding_rejects_mismatch_and_invalid_inputs(monkeypatch, damage):
    from dataclasses import replace

    binding = runtime_binding()
    overrides = {}
    if damage in ("run", "policy_version", "schema"):
        overrides[{"run": "run_id", "schema": "schema_version"}.get(damage, damage)] = (
            "wrong"
        )
    elif damage in ("provider", "tool"):
        binding = replace(
            binding, **{"tool_name" if damage == "tool" else damage: "wrong"}
        )
    elif damage == "call_run":
        binding = replace(
            binding, call=binding.call.model_copy(update={"run_id": "wrong"})
        )
    elif damage == "runtime_policy":
        binding.runtime.policy = binding.policy.model_copy(
            update={"timing_approval_reference": "changed"}
        )
    elif damage == "limits":
        binding.runtime.ledger.limits.tool_max_calls["another-tool"] = 1
    elif damage in ("readiness", "missing_readiness"):
        binding = replace(
            binding,
            readiness=binding.readiness.model_copy(
                update={"credential_present": False}
            ),
        )
        if damage == "missing_readiness":
            binding = replace(
                binding,
                gates=binding.gates.model_copy(update={"readiness": binding.readiness}),
            )
    elif damage == "allowance":
        binding = replace(
            binding,
            allowance=binding.allowance.model_copy(update={"input_tokens": 11}),
        )
    elif damage in ("mode", "scope", "timing"):
        key, value = {
            "mode": ("execution_mode", "fixture"),
            "scope": ("live_approval_reference", None),
            "timing": ("timing_approval_reference", None),
        }[damage]
        policy = binding.policy.model_copy(update={key: value})
        binding.runtime.policy = policy
        binding = replace(binding, policy=policy)
    elif damage == "unknown_tool":
        binding = replace(
            binding,
            tool_name="unknown",
            call=binding.call.model_copy(update={"tool_name": "unknown"}),
        )
    else:
        key, value = {
            "retries": ("max_retries", 1),
            "deadline": ("deadline", None),
            "expired": ("deadline", binding.runtime.clock.now()),
            "request_calls": ("max_calls", 0),
            "invalid_counts": ("max_calls", True),
        }[damage]
        binding = replace(
            binding, budget=binding.budget.model_copy(update={key: value})
        )
    before = binding.runtime.ledger.snapshot()
    attempts = forbid_runtime_mutation(monkeypatch, binding)
    with pytest.raises(ValueError):
        observe_binding(binding, **overrides)
    assert binding.runtime.ledger.snapshot() == before and attempts == []


@pytest.mark.parametrize(
    "exhaustion", ["global", "tool", "input", "output", "cost", "invalid"]
)
def test_runtime_observation_rechecks_shared_ledger_headroom(monkeypatch, exhaustion):
    from skala_rag.tools.runtime import Usage

    payload = gates_payload()
    payload["limits"]["max_calls"] = 3
    payload["limits"]["max_cost_usd"] = "1"
    payload["limits"]["tool_max_calls"]["other"] = 3
    if exhaustion == "input":
        payload["limits"]["max_input_tokens"] = 10
    if exhaustion == "output":
        payload["limits"]["max_output_tokens"] = 10
    if exhaustion == "cost":
        payload["limits"]["max_cost_usd"] = "0.1"
    binding = runtime_binding(payload=payload)
    assert observe_binding(binding)["ledger"]["calls"] == 0
    tool = binding.tool_name if exhaustion == "tool" else "other"
    count = 3 if exhaustion == "global" else 2 if exhaustion == "tool" else 1
    for _ in range(count):
        assert binding.runtime.ledger.reserve(tool, binding.allowance, live=True)
    if exhaustion == "invalid":
        assert not binding.runtime.ledger.settle(
            binding.allowance,
            Usage(
                schema_version="test",
                input_tokens=11,
                output_tokens=None,
                cost_usd=None,
            ),
        )
    before = binding.runtime.ledger.snapshot()
    attempts = forbid_runtime_mutation(monkeypatch, binding)
    with pytest.raises(ValueError, match="exhausted|invalid"):
        observe_binding(binding)
    assert binding.runtime.ledger.snapshot() == before and attempts == []


def test_runtime_observation_uses_exact_cost_and_unknown_reservations(monkeypatch):
    from dataclasses import replace
    from decimal import Decimal, localcontext

    payload = gates_payload()
    payload["limits"]["max_cost_usd"] = "1"
    payload["allowance"]["max_cost_usd"] = "1"
    binding = runtime_binding(payload=payload)
    assert binding.runtime.ledger.reserve(
        binding.tool_name, binding.allowance, live=True
    )
    assert binding.runtime.ledger.settle(binding.allowance, None)
    allowance = binding.allowance.model_copy(update={"max_cost_usd": Decimal("1e-100")})
    gates = binding.gates.model_copy(update={"allowance": allowance})
    binding = replace(binding, gates=gates, allowance=allowance)
    attempts = forbid_runtime_mutation(monkeypatch, binding)
    with localcontext() as context:
        context.prec = 6
        with pytest.raises(ValueError, match="cost budget exhausted"):
            observe_binding(binding)
    assert binding.runtime.ledger.snapshot()["unknown_cost_requests"] == 1
    assert attempts == []


def test_runtime_observation_preserves_explicit_zero_cost(monkeypatch):
    payload = gates_payload()
    payload["limits"]["max_cost_usd"] = "0"
    payload["allowance"]["max_cost_usd"] = "0"
    binding = runtime_binding(payload=payload)
    attempts = forbid_runtime_mutation(monkeypatch, binding)
    assert observe_binding(binding)["compatibility"] and attempts == []


def test_runtime_observation_tracks_clock_and_separate_tool_identity(monkeypatch):
    from dataclasses import replace
    from datetime import timedelta

    payload = gates_payload()
    payload["limits"]["tool_max_calls"]["synthetic-tool"] = 1
    binding = runtime_binding(payload=payload)
    binding = replace(
        binding,
        tool_name="synthetic-tool",
        call=binding.call.model_copy(update={"tool_name": "synthetic-tool"}),
    )
    attempts = forbid_runtime_mutation(monkeypatch, binding)
    binding.runtime.clock.advance(timedelta(seconds=29))
    observation = observe_binding(binding)
    assert observation["provider"] == "synthetic-provider"
    assert observation["tool_name"] == "synthetic-tool"
    assert observation["timeout_seconds"] == 1
    binding.runtime.clock.advance(timedelta(seconds=1))
    with pytest.raises(ValueError, match="deadline"):
        observe_binding(binding)
    assert attempts == []


def test_runtime_observation_refuses_unbounded_unknown_ledger_cost(monkeypatch):
    from skala_rag.tools.runtime import Allowance, BudgetLedger

    binding = runtime_binding()
    ledger = BudgetLedger(
        binding.gates.limits.model_copy(update={"max_cost_usd": None})
    )
    allowance = Allowance(
        schema_version="test", input_tokens=0, output_tokens=0, max_cost_usd=None
    )
    assert ledger.reserve(binding.tool_name, allowance, live=False)
    assert ledger.settle(allowance, None)
    # A now-bounded limit cannot turn past unbounded unknown cost into zero.
    ledger.limits = binding.gates.limits.model_copy(deep=True)
    binding.runtime.ledger = ledger
    attempts = forbid_runtime_mutation(monkeypatch, binding)
    with pytest.raises(ValueError, match="unbounded"):
        observe_binding(binding)
    assert attempts == []


@pytest.mark.parametrize(
    "damage", ["limits", "policy", "allowance", "readiness", "dto"]
)
def test_runtime_binding_revalidates_bypassed_nested_dtos(monkeypatch, damage):
    from dataclasses import replace
    from decimal import Decimal

    binding = runtime_binding()
    if damage == "limits":
        binding.runtime.ledger.limits.tool_max_calls[binding.tool_name] = True
    elif damage == "policy":
        binding.runtime.policy = binding.policy.model_copy(
            update={"retry_delays_seconds": (float("nan"),)}
        )
    elif damage == "allowance":
        binding = replace(
            binding,
            allowance=binding.allowance.model_copy(
                update={"max_cost_usd": Decimal("NaN")}
            ),
        )
    elif damage == "readiness":
        binding = replace(
            binding, readiness=binding.readiness.model_copy(update={"configured": 1})
        )
    else:
        binding = replace(binding, budget=object())
    attempts = forbid_runtime_mutation(monkeypatch, binding)
    with pytest.raises(ValueError):
        observe_binding(binding)
    assert attempts == []
