"""Founder 평가의 인물 귀속 경계 (#58).

rubric은 D14 core 승인(core-0.1.0)을 요구하고, catalog는 draft이므로 fixture
실행만 허용한다. 인물 동일성은
Evidence 텍스트나 동명이인 추측으로 판정하지 않고, 상위 조사 단계가 검증해
전달한 evidence_id → person_id 매핑을 요구한다.
"""

from collections.abc import Collection, Mapping

from skala_rag.agents.evaluation import evaluate_dimension
from skala_rag.contracts.evaluation import EvaluationResult, EvaluationSnapshot
from skala_rag.contracts.interfaces import Clock, StructuredLLM
from skala_rag.scoring.catalog import ScoringPolicy


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
    if rubric.get("status") != "approved" or policy.status != "draft":
        raise ValueError("Founder fixture requires approved rubric and draft policy")
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
