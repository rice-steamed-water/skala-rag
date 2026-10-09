"""Moat atomic branch with separate fixture and controller-admitted entries."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import assert_never

from skala_rag.agents.evaluation import (
    CriterionOutput,
    EvaluationValidationError,
    evaluate_dimension,
)
from skala_rag.agents.moat_verification import (
    MOAT_CRITERIA,
    CoreArtifactApproval,
    IndependentComparison,
    ReviewedMoatAnchor,
    VerifiedPatent,
    frozen_moat_evidence,
    legacy_moat_violation,
    validate_core_artifact,
    validate_moat_catalog,
    validate_reviewed_anchor,
)
from skala_rag.contracts.evaluation import EvaluationSnapshot
from skala_rag.contracts.evidence import Evidence
from skala_rag.contracts.interfaces import Clock, StructuredLLM
from skala_rag.contracts.v3 import Evaluation, EvaluationBranchResult
from skala_rag.prompt.moat_evaluation import SYSTEM_PROMPT, build_user_prompt
from skala_rag.scoring.approved_consumers import ActualAdmissionV3
from skala_rag.scoring.approved_policy import (
    ApprovalVerifier,
    ApprovedScoringPolicy,
    PolicyApprovals,
    load_approved_policy,
)
from skala_rag.scoring.catalog import ScoringPolicy

MoatObservationVerifier = Callable[
    [CriterionOutput, Mapping[str, Evidence]], bool | ReviewedMoatAnchor
]


def _evaluate_moat(
    snapshot: EvaluationSnapshot,
    *,
    rubric: Mapping[str, object],
    policy: ScoringPolicy | ApprovedScoringPolicy,
    llm: StructuredLLM,
    clock: Clock,
    schema_version: str,
    verify_observation: MoatObservationVerifier,
    verified_patents: Mapping[str, VerifiedPatent],
    independent_comparisons: Mapping[str, IndependentComparison],
    approved_actual: bool = False,
) -> EvaluationBranchResult:
    """Shared observed/missing atomic body; no search, defaults, repairs or N/A."""
    approved_core = (
        rubric.get("status") == "approved" or approved_actual
    ) and rubric.get("rubric_version") == "core-0.1.0"
    if (not approved_core and rubric.get("status") != "proposed") or (
        not isinstance(policy, ApprovedScoringPolicy) and policy.status != "draft"
    ):
        raise ValueError("Moat offline fixture only; unsupported rubric or policy")
    if isinstance(policy, ApprovedScoringPolicy):
        if (
            policy.execution_mode != ("live" if approved_actual else "fixture")
            or not approved_core
        ):
            raise ValueError("Approved Moat fixture requires approved Core rubric")
    snapshot = EvaluationSnapshot.model_validate(
        snapshot.model_dump(),
        context={"execution_mode": "live" if approved_actual else "fixture"},
    )
    ids = {c.criterion_id for c in policy.criteria if c.dimension == "moat"}
    validate_moat_catalog(rubric, ids)
    if snapshot.policy_version != policy.policy_version:
        raise ValueError("Snapshot policy mismatch")
    allowed = frozen_moat_evidence(snapshot, MOAT_CRITERIA)
    scoped = snapshot.model_copy(
        update={"evidence_ids": sorted(allowed), "evidence": allowed}, deep=True
    )

    class CheckedLLM:
        def generate(self, **kwargs):
            output = llm.generate(**kwargs)
            for criterion in output.criteria:
                if criterion.status != "observed":
                    continue
                cited = set(criterion.evidence_ids)
                if not cited or not cited <= set(allowed):
                    raise EvaluationValidationError(["MOAT_EVIDENCE_INVALID"])
                if not approved_core and (
                    violations := legacy_moat_violation(
                        snapshot.candidate_id,
                        criterion_id=criterion.criterion_id,
                        cited=cited,
                        verified_patents=verified_patents,
                        independent_comparisons=independent_comparisons,
                    )
                ):
                    raise EvaluationValidationError(violations)
                review = verify_observation(
                    criterion, {eid: allowed[eid] for eid in cited}
                )
                if approved_core:
                    verified = validate_reviewed_anchor(
                        review,
                        rubric=rubric,
                        snapshot=snapshot,
                        criterion=criterion,
                        verifier=lambda receipt: receipt is review,
                    )
                else:
                    verified = review is True
                if not verified:
                    raise EvaluationValidationError(["MOAT_RUBRIC_UNVERIFIED"])
            return output

    result = evaluate_dimension(
        "moat",
        scoped,
        rubric,
        llm=CheckedLLM(),
        policy=policy,
        clock=clock,
        schema_version=schema_version,
        max_repairs=0,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=build_user_prompt(scoped, rubric, policy),
    )
    payload = result.model_dump(exclude={"dimension", "evaluation"})
    payload.update(branch_id="moat", evaluations=None)
    if result.evaluation is not None:
        payload["evaluations"] = {
            "moat": Evaluation.model_validate(result.evaluation.model_dump())
        }
    return EvaluationBranchResult.model_validate(payload)


def evaluate_moat_approved(
    snapshot: EvaluationSnapshot,
    *,
    actual_admission: ActualAdmissionV3,
    llm: StructuredLLM,
    review_request: bytes,
    review_subject: str,
    reviewed_anchors: Mapping[str, ReviewedMoatAnchor],
) -> EvaluationBranchResult:
    """Admit reviewed anchors; unknown facts support Missing, never low/N/A."""
    if type(actual_admission) is not ActualAdmissionV3:
        raise ValueError("Exact ActualAdmissionV3 required")
    rubric = actual_admission.registry.rubric("core-0.1.0")
    policy = actual_admission.verify_evaluator(snapshot, rubric, llm, "moat")

    def verify(
        criterion: CriterionOutput, _evidence: Mapping[str, Evidence]
    ) -> ReviewedMoatAnchor | bool:
        receipt = reviewed_anchors.get(criterion.criterion_id)
        if receipt is None:
            return False
        review = actual_admission.resolve_review(
            snapshot, rubric, review_request, receipt, subject=review_subject
        )
        if review is None:
            return False
        match review.decision:
            case "accepted":
                return receipt
            case "rejected" | "unresolved":
                return False
            case unreachable:
                assert_never(unreachable)

    return _evaluate_moat(
        snapshot,
        rubric=rubric,
        policy=policy,
        llm=llm,
        clock=actual_admission.runtime_binding.runtime.clock,
        schema_version=snapshot.schema_version,
        verify_observation=verify,
        verified_patents={},
        independent_comparisons={},
        approved_actual=True,
    )


def evaluate_moat(
    snapshot: EvaluationSnapshot,
    *,
    rubric: Mapping[str, object],
    policy: ScoringPolicy,
    llm: StructuredLLM,
    clock: Clock,
    schema_version: str,
    verify_observation: MoatObservationVerifier,
    verified_patents: Mapping[str, VerifiedPatent],
    independent_comparisons: Mapping[str, IndependentComparison],
    artifact_approval: CoreArtifactApproval | None = None,
    artifact_verifier: Callable[[CoreArtifactApproval], bool] | None = None,
) -> EvaluationBranchResult:
    """Legacy fixture entry; constructed approved contracts are not receipts."""
    if isinstance(policy, ApprovedScoringPolicy):
        raise ValueError("#168: approved contract has no trusted-loading receipt")
    if rubric.get("status") == "approved":
        if artifact_approval is None or artifact_verifier is None:
            raise ValueError("Explicit authoritative Core artifact approval required")
        validate_core_artifact(rubric, artifact_approval, artifact_verifier)
    return _evaluate_moat(
        snapshot,
        rubric=rubric,
        policy=policy,
        llm=llm,
        clock=clock,
        schema_version=schema_version,
        verify_observation=verify_observation,
        verified_patents=verified_patents,
        independent_comparisons=independent_comparisons,
    )


def evaluate_moat_approved_fixture(
    snapshot: EvaluationSnapshot,
    *,
    policy_path: str | Path,
    approvals: PolicyApprovals,
    approval_verifier: ApprovalVerifier,
    rubric: Mapping[str, object],
    llm: StructuredLLM,
    clock: Clock,
    schema_version: str,
    verify_observation: MoatObservationVerifier,
    artifact_approval: CoreArtifactApproval,
    artifact_verifier: Callable[[CoreArtifactApproval], bool],
    actual_runtime: bool = False,
) -> EvaluationBranchResult:
    """Fixture-only loader; verify exact artifact contents, never promote live."""
    if actual_runtime is not False:
        raise ValueError(
            "#168: actual runtime unavailable: no trusted-loading receipt; "
            "authoritative Core artifact and matched runtime admission required"
        )
    from skala_rag.fakes import FakeLLM

    if not isinstance(llm, FakeLLM):
        raise ValueError("Approved fixture requires FakeLLM; actual runtime is blocked")
    validate_core_artifact(rubric, artifact_approval, artifact_verifier)
    if artifact_approval.reference != approvals.core.reference:
        raise ValueError("Core artifact and policy approval reference mismatch")
    policy = load_approved_policy(
        policy_path,
        approvals=approvals,
        approval_verifier=approval_verifier,
        execution_mode="fixture",
    )
    return _evaluate_moat(
        snapshot,
        rubric=rubric,
        policy=policy,
        llm=llm,
        clock=clock,
        schema_version=schema_version,
        verify_observation=verify_observation,
        verified_patents={},
        independent_comparisons={},
    )
