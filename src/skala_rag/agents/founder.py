"""Founder 평가의 인물 귀속 경계 (#58).

기존 fixture 경계는 유지하며 actual 진입점은 별도 shared admission을 요구한다.
인물 동일성은 Evidence 텍스트나 동명이인 추측으로 판정하지 않고, 상위 조사
단계가 검증해 전달한 evidence_id → person_id 매핑과 독립 review를 요구한다.
"""

from collections.abc import Callable, Collection, Mapping
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

from skala_rag.agents.evaluation import evaluate_dimension
from skala_rag.agents.founder_verification import (
    FOUNDER_CRITERIA,
    FounderReviewError,
    ReviewedFounderAnchor,
    _founder_attribution,
    _run_founder_approved,
)
from skala_rag.agents.moat_verification import (
    CoreArtifactApproval,
    validate_core_artifact,
)
from skala_rag.agents.technology_verification import (
    _exact_tree,
    checked_snapshot,
)
from skala_rag.contracts.assessment import CriterionAssessment
from skala_rag.contracts.evaluation import EvaluationResult, EvaluationSnapshot
from skala_rag.contracts.evidence import Evidence
from skala_rag.contracts.interfaces import Clock, StructuredLLM
from skala_rag.scoring.approved_policy import (
    ApprovalVerifier,
    PolicyApprovals,
    load_approved_policy,
)
from skala_rag.scoring.catalog import ScoringPolicy

if TYPE_CHECKING:
    from skala_rag.scoring.approved_consumers import ActualAdmissionV3
    from skala_rag.tools.runtime_llm import RuntimeStructuredLLM


def evaluate_founder_fixture(
    snapshot: EvaluationSnapshot,
    *,
    founder_person_ids: Collection[str],
    verified_person_by_evidence_id: Mapping[str, str],
    rubric: Mapping[str, object],
    llm: StructuredLLM,
    policy: ScoringPolicy,
    clock: Clock,
    schema_version: str,
) -> EvaluationResult:
    """검증된 창업자 귀속 근거만 #22 공통 평가 wrapper에 전달한다.

    ``verified_person_by_evidence_id``는 상위 조사 경계에서 인물·소속을
    확인한 결과여야 한다. 이 함수는 그 판단을 텍스트에서 재추론하지 않는다.
    매핑 밖 근거는 프롬프트와 출력 검증 양쪽에서 제외한다.
    """
    if rubric.get("status") != "proposed" or policy.status != "draft":
        raise ValueError("Founder fixture requires proposed rubric and draft policy")
    people = set(founder_person_ids)
    if not people or any(not isinstance(pid, str) or not pid.strip() for pid in people):
        raise ValueError("Founder person IDs must be explicit nonblank IDs")
    if not set(verified_person_by_evidence_id) <= set(snapshot.evidence):
        raise ValueError("Founder attribution references evidence outside snapshot")
    if any(
        not isinstance(pid, str) or not pid.strip()
        for pid in verified_person_by_evidence_id.values()
    ):
        raise ValueError("Founder attribution requires nonblank person IDs")

    allowed = {
        eid: evidence
        for eid, evidence in snapshot.evidence.items()
        if verified_person_by_evidence_id.get(eid) in people
        and evidence.scope == "company"
        and evidence.candidate_id == snapshot.candidate_id
        and any(cid.startswith("founder.") for cid in evidence.criterion_ids)
    }
    scoped = snapshot.model_copy(
        update={"evidence_ids": list(allowed), "evidence": allowed}, deep=True
    )
    return evaluate_dimension(
        "founder",
        scoped,
        rubric,
        llm=llm,
        policy=policy,
        clock=clock,
        schema_version=schema_version,
    )


def evaluate_founder_approved_fixture(
    snapshot: EvaluationSnapshot,
    *,
    founder_person_ids: Collection[str],
    verified_person_by_evidence_id: Mapping[str, str],
    policy_path: str | Path,
    approvals: PolicyApprovals,
    approval_verifier: ApprovalVerifier,
    rubric: Mapping[str, object],
    llm: StructuredLLM,
    clock: Clock,
    schema_version: str,
    verify_observation: Callable[[object, Mapping[str, Evidence]], object],
    review_verifier: Callable[[ReviewedFounderAnchor], bool],
    artifact_approval: CoreArtifactApproval,
    artifact_verifier: Callable[[CoreArtifactApproval], bool],
    actual_runtime: bool = False,
) -> EvaluationResult:
    """Additive loader-backed fixture consumer; never runtime/person authority.

    Legacy draft behavior is separate. Review authenticates claims only through
    caller-owned external resolution, after the common wrapper (no repairs).
    """
    from skala_rag.fakes import FakeLLM

    if actual_runtime is not False:
        raise ValueError("#168: actual runtime unavailable")
    if type(llm) is not FakeLLM:
        raise ValueError("Approved fixture requires exact FakeLLM")
    try:
        rubric = deepcopy(dict(rubric))
        if rubric.get("status") != "approved":
            raise ValueError("Approved Founder fixture requires approved Core")
        snapshot = checked_snapshot(snapshot)
        if schema_version != snapshot.schema_version:
            raise ValueError("Snapshot schema mismatch")
        if not callable(verify_observation) or not callable(review_verifier):
            raise ValueError("External Founder review callbacks required")
        validate_core_artifact(rubric, artifact_approval, artifact_verifier)
        validated_approvals = PolicyApprovals.model_validate(approvals.model_dump())
        _exact_tree(approvals, validated_approvals, "")
        approvals = validated_approvals
        if artifact_approval.reference != approvals.core.reference:
            raise ValueError("Core artifact and policy reference mismatch")
        policy = load_approved_policy(
            policy_path,
            approvals=approvals,
            approval_verifier=approval_verifier,
            execution_mode="fixture",
        )
        if snapshot.policy_version != policy.policy_version:
            raise ValueError("Snapshot policy mismatch")
        dimensions = rubric.get("dimensions")
        founder = dimensions.get("founder") if isinstance(dimensions, Mapping) else None
        criteria = founder.get("criteria") if isinstance(founder, Mapping) else None
        if (
            not isinstance(criteria, Mapping)
            or set(criteria) != FOUNDER_CRITERIA
            or {c.criterion_id for c in policy.criteria if c.dimension == "founder"}
            != FOUNDER_CRITERIA
        ):
            raise ValueError("Founder criteria incomplete")
        people, attribution, scoped = _founder_attribution(
            snapshot, founder_person_ids, verified_person_by_evidence_id
        )
        if not people:
            raise ValueError("Invalid Founder person attribution")
    except Exception:
        raise ValueError("Founder approved fixture preflight rejected") from None

    return _run_founder_approved(
        snapshot,
        scoped=scoped,
        people=people,
        attribution=attribution,
        rubric=rubric,
        llm=llm,
        policy=policy,
        clock=clock,
        schema_version=schema_version,
        verify_observation=verify_observation,
        review_verifier=review_verifier,
    )


def evaluate_founder_approved(
    snapshot: EvaluationSnapshot,
    *,
    actual_admission: "ActualAdmissionV3",
    founder_person_ids: Collection[str],
    verified_person_by_evidence_id: Mapping[str, str],
    llm: "RuntimeStructuredLLM",
    review_request: bytes,
    review_subject: str,
    reviewed_anchors: Mapping[str, ReviewedFounderAnchor],
) -> EvaluationResult:
    """Evaluate with controller admission and independently accepted person receipts.

    Empty founder identity is permitted only as missing, never invented people.
    Controller-selected request/subject/receipts are not model approval inputs.
    """
    from skala_rag.scoring.approved_consumers import ActualAdmissionV3

    if type(actual_admission) is not ActualAdmissionV3:
        raise FounderReviewError("FOUNDER_ADMISSION_REJECTED")
    rubric = actual_admission.registry.rubric("core-0.1.0")
    snapshot = checked_snapshot(snapshot)
    policy = actual_admission.verify_evaluator(snapshot, rubric, llm, "founder")
    validate_core_artifact(
        rubric,
        actual_admission.registry.core_approval(),
        actual_admission.registry.verify_core,
    )
    people, attribution, scoped = _founder_attribution(
        snapshot, founder_person_ids, verified_person_by_evidence_id
    )
    anchors = deepcopy(dict(reviewed_anchors))

    def observation(
        criterion: CriterionAssessment, _evidence: Mapping[str, Evidence]
    ) -> ReviewedFounderAnchor | None:
        return anchors.get(criterion.criterion_id)

    def accepted(receipt: ReviewedFounderAnchor) -> bool:
        resolved = actual_admission.resolve_review(
            snapshot, rubric, review_request, receipt, subject=review_subject
        )
        return resolved is not None and resolved.decision == "accepted"

    return _run_founder_approved(
        snapshot,
        scoped=scoped,
        people=people,
        attribution=attribution,
        rubric=rubric,
        llm=llm,
        policy=policy,
        clock=actual_admission.runtime_binding.runtime.clock,
        schema_version=snapshot.schema_version,
        verify_observation=observation,
        review_verifier=accepted,
    )
