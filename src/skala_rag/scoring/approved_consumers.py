"""Loader-backed offline approved consumers; actual registry/runtime is absent.

An input source is configuration, never an authenticated capability. Every
consumer resolves fresh approval inputs. Live execution is denied before file
reads, callbacks, arithmetic or candidate consumption, even with truthy gates.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from skala_rag.contracts import EvaluationSnapshot
from skala_rag.contracts.v3 import Evaluation, InvestmentDecision, ScoreSummary
from skala_rag.scoring.aggregate_v3 import (
    ApplicabilityVerifier,
    _aggregate_scores_v3,
)
from skala_rag.scoring.approved_policy import (
    ApprovalVerifier,
    ApprovedScoringPolicy,
    LiveGateVerifier,
    LiveScoringGates,
    PolicyApprovals,
    load_approved_policy,
)
from skala_rag.scoring.decision_v3 import _decide_v3
from skala_rag.scoring.selector_v3 import SelectionResultV3, _select_best_v3


@dataclass(frozen=True, kw_only=True)
class ApprovedPolicySource:
    """Explicit loader inputs, not approval or runtime authority."""

    path: str | Path
    approvals: PolicyApprovals
    approval_verifier: ApprovalVerifier
    execution_mode: Literal["fixture", "live"]
    live_gates: LiveScoringGates | None = None
    live_gate_verifier: LiveGateVerifier | None = None


def _load_fixture(source: ApprovedPolicySource) -> ApprovedScoringPolicy:
    if not isinstance(source, ApprovedPolicySource):
        raise ValueError("loader-backed ApprovedPolicySource required")
    if source.execution_mode == "live":
        raise ValueError("actual approved registry/runtime unavailable; live denied")
    return load_approved_policy(
        source.path,
        approvals=source.approvals,
        approval_verifier=source.approval_verifier,
        execution_mode=source.execution_mode,
        live_gates=source.live_gates,
        live_gate_verifier=source.live_gate_verifier,
    )


def aggregate_scores_approved(
    evaluations: Sequence[Evaluation],
    source: ApprovedPolicySource,
    *,
    snapshot: EvaluationSnapshot,
    applicability_verifier: ApplicabilityVerifier | None,
) -> ScoreSummary:
    """Score promoted six-dimension input; upstream join owns semantic closure."""
    approved = _load_fixture(source)
    if not isinstance(snapshot, EvaluationSnapshot):
        raise ValueError("frozen EvaluationSnapshot required")
    frozen = EvaluationSnapshot.model_validate(
        snapshot.model_dump(), context={"execution_mode": "fixture"}
    )
    return _aggregate_scores_v3(
        evaluations,
        criteria=approved.criteria,
        numeric=approved.operational.numeric,
        policy_version=approved.policy_version,
        applicability_verifier=applicability_verifier,
        snapshot=frozen,
    )


def decide_approved(
    summary: ScoreSummary,
    source: ApprovedPolicySource,
    *,
    evidence_ids: tuple[str, ...] = (),
    rationale: str = "Deterministic v3 policy decision",
    risks: tuple[str, ...] = (),
    limitations: tuple[str, ...] = (),
) -> InvestmentDecision:
    """Resolve fresh approvals before consuming a same-policy summary."""
    approved = _load_fixture(source)
    validated = ScoreSummary.model_validate(summary.model_dump())
    return _decide_v3(
        validated,
        numeric=approved.operational.numeric,
        policy_version=approved.policy_version,
        evidence_ids=evidence_ids,
        rationale=rationale,
        risks=risks,
        limitations=limitations,
    )


def select_best_approved(
    candidates: Sequence[Mapping[str, object]],
    source: ApprovedPolicySource,
    *,
    run_id: str,
    schema_version: str,
) -> SelectionResultV3:
    """Resolve fresh approvals before ordering eligible terminal results."""
    approved = _load_fixture(source)
    return _select_best_v3(
        candidates,
        numeric=approved.operational.numeric,
        policy_version=approved.policy_version,
        run_id=run_id,
        schema_version=schema_version,
    )
