"""Approved-value contract, not permission to execute an evaluator or provider.

The old draft and v3 fixture loaders stay unchanged. Approval references are
untrusted data until a trusted controller's external verifier resolves them.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, model_validator

from skala_rag.contracts.common import Text
from skala_rag.scoring.v3_policy import (
    FrozenPolicy,
    V3Criterion,
    V3Policy,
    load_v3_policy,
)
from skala_rag.tools.provider_scope import EXCLUDED_PROVIDERS
from skala_rag.tools.runtime import Allowance, Readiness, RuntimeLimits


class ApprovalEvidence(FrozenPolicy):
    scope: Literal["operational", "core", "finance"]
    version: Text
    reference: Text


class PolicyApprovals(FrozenPolicy):
    contract_reference: Literal["rice-steamed-water/skala-rag#168"]
    operational: ApprovalEvidence
    core: ApprovalEvidence
    finance: ApprovalEvidence

    @model_validator(mode="after")
    def approved_versions(self) -> "PolicyApprovals":
        expected = {
            "operational": "v3-operational-1.0.0",
            "core": "core-0.1.0",
            "finance": "finance-0.1.0",
        }
        for scope, version in expected.items():
            evidence = getattr(self, scope)
            if (evidence.scope, evidence.version) != (scope, version):
                raise ValueError(f"approval scope/version mismatch: {scope}")
        if self.operational.reference != "rice-steamed-water/skala-rag#82":
            raise ValueError("operational approval must refer to issue #82")
        return self


ApprovalVerifier = Callable[[ApprovalEvidence, V3Policy], bool]
LiveGate = Literal["runtime_readiness", "call_budget", "cost_budget"]


class LiveScoringGates(FrozenPolicy):
    """Caller observations for one provider in one run, never an execution token.

    The external verifier must resolve references against these exact inputs and
    current ledger state. AdapterRuntime must still reserve/settle each request.
    No live limit, readiness flag or approval reference has a default.
    """

    run_id: Text
    policy_version: Literal["v3-operational-1.0.0"]
    provider: Text
    open_decisions: tuple[Text, ...]
    readiness: Readiness
    limits: RuntimeLimits
    allowance: Allowance
    runtime_readiness_reference: Text
    call_budget_reference: Text
    cost_budget_reference: Text

    @model_validator(mode="after")
    def independent_constraints(self) -> "LiveScoringGates":
        if self.provider in EXCLUDED_PROVIDERS:
            raise ValueError("provider excluded from the current run")
        if self.open_decisions:
            raise ValueError("unapproved decisions block live scoring")
        if self.readiness.missing:
            raise ValueError("runtime_readiness is incomplete")
        if (
            self.limits.max_calls < 1
            or self.limits.tool_max_calls.get(self.provider, 0) < 1
        ):
            raise ValueError("call_budget does not admit this provider")
        for bound, requested in (
            (self.limits.max_input_tokens, self.allowance.input_tokens),
            (self.limits.max_output_tokens, self.allowance.output_tokens),
        ):
            if bound is None or requested > bound:
                raise ValueError("call_budget token allowance exceeds limit")
        if (
            self.limits.max_cost_usd is None
            or self.allowance.max_cost_usd is None
            or self.allowance.max_cost_usd > self.limits.max_cost_usd
        ):
            raise ValueError("cost_budget requires explicit sufficient limits")
        return self


LiveGateVerifier = Callable[[LiveGate, LiveScoringGates], bool]


class ApprovedScoringPolicy(FrozenPolicy):
    """Catalog view for evaluator consumers; never a legacy scoring policy.

    Construction/schema validation alone is NOT approval. Use the loader with a
    trusted registry verifier. Rubric version approval is not anchor/evidence
    semantic validation. ``operational`` retains its historical fixture metadata.
    """

    model_config = ConfigDict(revalidate_instances="always")
    execution_mode: Literal["fixture", "live"]
    operational: V3Policy
    approvals: PolicyApprovals
    live_gates: LiveScoringGates | None

    @property
    def policy_version(self) -> str:
        return self.operational.policy_version

    @property
    def criteria(self) -> tuple[V3Criterion, ...]:
        return self.operational.criteria


def load_approved_policy(
    path: str | Path,
    *,
    approvals: PolicyApprovals,
    approval_verifier: ApprovalVerifier,
    execution_mode: Literal["fixture", "live"] = "fixture",
    live_gates: LiveScoringGates | None = None,
    live_gate_verifier: LiveGateVerifier | None = None,
) -> ApprovedScoringPolicy:
    """Resolve external approvals and independent admission inputs without I/O.

    Only ``path`` is read. Verifiers are trusted controller code, not callbacks
    derived from model/provider responses. Their exceptions fail closed.
    """
    if execution_mode not in ("fixture", "live"):
        raise ValueError("execution_mode must be fixture or live")
    operational = load_v3_policy(path, execution_mode="fixture")
    approvals = PolicyApprovals.model_validate(approvals.model_dump())
    if not callable(approval_verifier):
        raise ValueError("trusted external approval verifier required")
    for evidence in (approvals.operational, approvals.core, approvals.finance):
        if approval_verifier(evidence, operational) is not True:
            raise ValueError(f"external approval rejected: {evidence.scope}")
    if execution_mode == "live":
        if live_gates is None or not callable(live_gate_verifier):
            raise ValueError("live requires independent readiness and budget gates")
        live_gates = LiveScoringGates.model_validate(live_gates.model_dump())
        for gate in ("runtime_readiness", "call_budget", "cost_budget"):
            if live_gate_verifier(gate, live_gates) is not True:
                raise ValueError(f"external live gate rejected: {gate}")
    elif live_gates is not None or live_gate_verifier is not None:
        raise ValueError("fixture must not carry live admission inputs")
    return ApprovedScoringPolicy(
        execution_mode=execution_mode,
        operational=operational,
        approvals=approvals,
        live_gates=live_gates,
    )
