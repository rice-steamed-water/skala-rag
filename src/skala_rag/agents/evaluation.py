"""영역 평가 wrapper의 검증·조립 부분 — #22, contracts §4, T01·T22.

LLM은 ``DimensionAssessmentOutput``(criterion 판단·gap·caveat)만 만든다. run_id·
snapshot_id·policy_version 같은 envelope 필드, gap_id, 비중·점수는 wrapper가
snapshot과 catalog에서 채운다 — 모델 출력의 점수·ID는 받지 않는다(schema가
extra 필드를 거절). 점수는 이후 scoring.aggregate_scores가 rating으로만 계산한다.

LLM 호출·구조 수정 1회·failure envelope는 #8(StructuredLLM·LLMError) 병합 후
이 모듈 위에 연결한다.
"""

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from skala_rag.contracts import (
    CriterionAssessment,
    Evaluation,
    EvaluationSnapshot,
    ResearchGap,
    evaluation_key,
)
from skala_rag.contracts.evaluation import Dimension
from skala_rag.scoring.catalog import ScoringPolicy


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CriterionOutput(_Output):
    """LLM이 criterion마다 내는 판단. 점수·비중 필드는 없다."""

    criterion_id: str = Field(min_length=1)
    status: Literal["observed", "missing"]
    rating: int | None = Field(default=None, ge=1, le=5)
    evidence_ids: list[str]
    rationale: str = Field(min_length=1)
    missing_reason: str | None = None
    applicability_note: str | None = None


class GapOutput(_Output):
    """LLM이 제안하는 추가 조사. gap_id·우선순위는 wrapper가 정한다."""

    criterion_id: str = Field(min_length=1)
    missing_fields: list[str]
    reason: str = Field(min_length=1)
    suggested_queries: list[str]


class DimensionAssessmentOutput(_Output):
    """StructuredLLM.generate(output_schema=...)에 넘길 LLM 출력 schema."""

    criteria: list[CriterionOutput]
    research_gaps: list[GapOutput] = []
    caveats: list[str] = []


class EvaluationValidationError(ValueError):
    """LLM 출력이 계약을 어김. wrapper가 구조 수정 1회 후 failure로 옮긴다."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


def _industry_dimensions(rubric: Mapping[str, object]) -> set[str] | None:
    """rubric의 산업 근거 허용 영역. 지정이 없으면 None(제한 없음)."""
    rules = rubric.get("common_rules")
    if isinstance(rules, Mapping) and "industry_evidence_dimensions" in rules:
        return set(rules["industry_evidence_dimensions"])  # type: ignore[arg-type]
    return None


def validate_output(
    output: DimensionAssessmentOutput,
    *,
    dimension: Dimension,
    snapshot: EvaluationSnapshot,
    policy: ScoringPolicy,
    rubric: Mapping[str, object],
) -> list[str]:
    """계약 위반 목록을 돌려준다. 빈 목록이면 통과.

    - 영역 criterion이 정확히 한 번씩
    - observed는 rating·근거 필수, missing은 rating 없음·사유 필수
    - 근거는 snapshot 안에 있고, 같은 후보 또는 허용된 산업 scope
    - gap은 이 영역 criterion만
    """
    violations: list[str] = []
    expected = [c.criterion_id for c in policy.criteria if c.dimension == dimension]
    got = [c.criterion_id for c in output.criteria]
    if sorted(got) != sorted(expected) or len(set(got)) != len(got):
        violations.append(f"CRITERIA_SET: 기대 {sorted(expected)}, 출력 {sorted(got)}")

    industry_dims = _industry_dimensions(rubric)
    industry_ok = industry_dims is None or dimension in industry_dims
    for c in output.criteria:
        if c.status == "observed":
            if c.rating is None:
                violations.append(f"RATING_REQUIRED: {c.criterion_id}")
            if not c.evidence_ids:
                violations.append(
                    f"EVIDENCE_REQUIRED: {c.criterion_id} 근거 없는 rating"
                )
            if c.missing_reason is not None:
                violations.append(f"STATUS_CONFLICT: {c.criterion_id}")
        else:
            if c.rating is not None:
                violations.append(
                    f"STATUS_CONFLICT: {c.criterion_id} missing인데 rating"
                )
            if not c.missing_reason:
                violations.append(f"MISSING_REASON_REQUIRED: {c.criterion_id}")
        for eid in c.evidence_ids:
            ev = snapshot.evidence.get(eid)
            if ev is None:
                violations.append(f"EVIDENCE_NOT_IN_SNAPSHOT: {c.criterion_id} → {eid}")
            elif ev.scope == "company" and ev.candidate_id != snapshot.candidate_id:
                violations.append(f"OTHER_COMPANY_EVIDENCE: {c.criterion_id} → {eid}")
            elif ev.scope == "industry" and not industry_ok:
                violations.append(
                    f"INDUSTRY_EVIDENCE_NOT_ALLOWED: {c.criterion_id} → {eid}"
                )

    for gap in output.research_gaps:
        if gap.criterion_id not in expected:
            violations.append(f"GAP_OUTSIDE_DIMENSION: {gap.criterion_id}")
    return violations


def assemble_evaluation(
    output: DimensionAssessmentOutput,
    *,
    dimension: Dimension,
    snapshot: EvaluationSnapshot,
    policy: ScoringPolicy,
    rubric: Mapping[str, object],
    schema_version: str,
) -> Evaluation:
    """검증을 통과한 출력으로 Evaluation을 만든다. 위반이 있으면 예외.

    envelope는 snapshot에서, gap 우선순위는 catalog 비중에서 채운다.
    """
    if snapshot.policy_version != policy.policy_version:
        raise EvaluationValidationError(["POLICY_MISMATCH: snapshot과 정책이 다르다"])
    rubric_version = rubric.get("rubric_version")
    if not isinstance(rubric_version, str) or not rubric_version:
        raise EvaluationValidationError(["RUBRIC_VERSION_REQUIRED"])
    violations = validate_output(
        output, dimension=dimension, snapshot=snapshot, policy=policy, rubric=rubric
    )
    if violations:
        raise EvaluationValidationError(violations)

    weights = {c.criterion_id: c.weight for c in policy.criteria}
    key = evaluation_key(snapshot.candidate_id, snapshot.evaluation_round, dimension)
    return Evaluation(
        schema_version=schema_version,
        run_id=snapshot.run_id,
        candidate_id=snapshot.candidate_id,
        dimension=dimension,
        evaluation_round=snapshot.evaluation_round,
        snapshot_id=snapshot.snapshot_id,
        evidence_revision=snapshot.evidence_revision,
        policy_version=snapshot.policy_version,
        rubric_version=rubric_version,
        criteria=[
            CriterionAssessment(
                schema_version=schema_version,
                **c.model_dump(),
            )
            for c in output.criteria
        ],
        research_gaps=[
            ResearchGap(
                schema_version=schema_version,
                gap_id=f"gap:{key}:{g.criterion_id}",
                candidate_id=snapshot.candidate_id,
                criterion_id=g.criterion_id,
                missing_fields=g.missing_fields,
                reason=g.reason,
                priority_weight=weights[g.criterion_id],
                suggested_queries=g.suggested_queries,
                attempted_retrieval_ids=[],
                status="open",
            )
            for g in output.research_gaps
        ],
        caveats=output.caveats,
    )
