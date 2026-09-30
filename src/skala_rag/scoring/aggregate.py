"""점수 집계 — scoring.md §3, D01·D02·D04·D05(APPROVED).

LLM이 아니라 순수 함수가 점수를 계산한다. 반올림하지 않고, 결측을 제외한
재정규화도 하지 않는다(분모는 전체 비중 100 고정).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Protocol

from skala_rag.scoring.catalog import Dimension, ScoringPolicy

DIMENSIONS: tuple[Dimension, ...] = (
    "founder",
    "market",
    "technology",
    "moat",
    "traction",
    "deal_terms",
)


class ScoringError(ValueError):
    """집계를 거절한 이유. code는 테스트·로그용 고정 문자열이다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class AssessmentLike(Protocol):
    """#6 CriterionAssessment와 호환되는 최소 속성."""

    criterion_id: str
    status: str
    rating: int | None


class EvaluationLike(Protocol):
    """#6 Evaluation과 호환되는 최소 속성."""

    candidate_id: str
    evaluation_round: int
    snapshot_id: str
    evidence_revision: int
    policy_version: str
    dimension: str
    criteria: Sequence[AssessmentLike]


@dataclass(frozen=True)
class ScoreBreakdown:
    """ScoreSummary(#6) 필드 중 산술 결과. ID·세대 정보는 호출 측이 붙인다."""

    policy_version: str
    criterion_points: dict[str, Decimal | None]
    dimension_ratings: dict[str, Decimal | None]
    observed_score: Decimal
    missing_weight: Decimal
    coverage_pct: Decimal


def _validate_rating(criterion_id: str, rating: object) -> int | None:
    if rating is None:
        return None
    # bool은 int의 하위 타입이므로 명시적으로 거절한다.
    if type(rating) is not int or not 1 <= rating <= 5:
        raise ScoringError(
            "INVALID_RATING", f"{criterion_id}: rating은 정수 1..5 또는 null"
        )
    return rating


def compute_scores(
    ratings: Mapping[str, int | None], policy: ScoringPolicy
) -> ScoreBreakdown:
    """23개 criterion rating으로 §3 공식을 계산한다.

    ratings의 key는 catalog의 criterion ID와 정확히 같아야 한다. 누락된 key를
    missing으로 추측하지 않는다 — missing은 명시적인 None이다.
    """
    expected = {c.criterion_id for c in policy.criteria}
    got = set(ratings)
    if got != expected:
        missing = sorted(expected - got)
        extra = sorted(got - expected)
        raise ScoringError(
            "CRITERIA_MISMATCH", f"누락 {missing}, 알 수 없는 ID {extra}"
        )

    criterion_points: dict[str, Decimal | None] = {}
    dim_weighted: dict[str, int] = dict.fromkeys(DIMENSIONS, 0)
    dim_observed_weight: dict[str, int] = dict.fromkeys(DIMENSIONS, 0)
    observed_score = Decimal(0)
    missing_weight = 0

    for criterion in policy.criteria:
        rating = _validate_rating(
            criterion.criterion_id, ratings[criterion.criterion_id]
        )
        if rating is None:
            criterion_points[criterion.criterion_id] = None
            missing_weight += criterion.weight
            continue
        # w × r / 5 는 항상 유한 소수이므로 정확하다.
        points = Decimal(criterion.weight * rating) / Decimal(5)
        criterion_points[criterion.criterion_id] = points
        observed_score += points
        dim_weighted[criterion.dimension] += criterion.weight * rating
        dim_observed_weight[criterion.dimension] += criterion.weight

    dimension_ratings: dict[str, Decimal | None] = {}
    # 영역 비중 합은 30 이하라 가중평균이 임계값 2와 같지 않으면 최소 1/30
    # 떨어져 있다. 28자리 정밀도에서 비교 결과가 바뀌지 않는다.
    with localcontext() as ctx:
        ctx.prec = 28
        for dim in DIMENSIONS:
            weight = dim_observed_weight[dim]
            dimension_ratings[dim] = (
                None if weight == 0 else Decimal(dim_weighted[dim]) / Decimal(weight)
            )

    missing = Decimal(missing_weight)
    return ScoreBreakdown(
        policy_version=policy.policy_version,
        criterion_points=criterion_points,
        dimension_ratings=dimension_ratings,
        observed_score=observed_score,
        missing_weight=missing,
        coverage_pct=Decimal(100) - missing,
    )


def aggregate_scores(
    evaluations: Sequence[EvaluationLike], policy: ScoringPolicy
) -> ScoreBreakdown:
    """여섯 영역 평가를 검증한 뒤 집계한다 (scoring §6, contracts §4).

    거절: 여섯 영역이 정확히 하나씩 있지 않음(투자조건 누락 포함), 서로 다른
    후보·평가 세대·snapshot·evidence_revision·policy 혼합, policy 불일치,
    영역별 criterion 집합 불일치, status와 rating 모순.
    """
    by_dim: dict[str, EvaluationLike] = {}
    for ev in evaluations:
        if ev.dimension not in DIMENSIONS:
            raise ScoringError("UNKNOWN_DIMENSION", str(ev.dimension))
        if ev.dimension in by_dim:
            raise ScoringError("DUPLICATE_DIMENSION", str(ev.dimension))
        by_dim[ev.dimension] = ev
    absent = [d for d in DIMENSIONS if d not in by_dim]
    if absent:
        raise ScoringError("INCOMPLETE_EVALUATIONS", f"결과 없음: {absent}")

    keys = {
        (
            ev.candidate_id,
            ev.evaluation_round,
            ev.snapshot_id,
            ev.evidence_revision,
            ev.policy_version,
        )
        for ev in by_dim.values()
    }
    if len(keys) != 1:
        raise ScoringError(
            "MIXED_GENERATION",
            "후보·평가 세대·snapshot·evidence_revision·policy가 모두 같아야 한다",
        )
    if next(iter(keys))[4] != policy.policy_version:
        raise ScoringError("POLICY_MISMATCH", "평가와 정책의 policy_version이 다르다")

    catalog_by_dim: dict[str, set[str]] = {d: set() for d in DIMENSIONS}
    for c in policy.criteria:
        catalog_by_dim[c.dimension].add(c.criterion_id)

    ratings: dict[str, int | None] = {}
    for dim, ev in by_dim.items():
        ids = [a.criterion_id for a in ev.criteria]
        if len(ids) != len(set(ids)) or set(ids) != catalog_by_dim[dim]:
            raise ScoringError(
                "CRITERIA_MISMATCH", f"{dim}: 영역 criterion 집합이 catalog와 다르다"
            )
        for a in ev.criteria:
            if a.status == "observed" and a.rating is None:
                raise ScoringError(
                    "INVALID_RATING", f"{a.criterion_id}: observed인데 null"
                )
            if a.status == "missing" and a.rating is not None:
                raise ScoringError(
                    "INVALID_RATING", f"{a.criterion_id}: missing인데 rating"
                )
            if a.status not in ("observed", "missing"):
                raise ScoringError("INVALID_STATUS", f"{a.criterion_id}: {a.status}")
            ratings[a.criterion_id] = a.rating
    return compute_scores(ratings, policy)
