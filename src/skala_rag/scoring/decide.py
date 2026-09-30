"""투자 판단 — scoring.md §5 우선순위 표, D02·D03·D05(APPROVED).

label·report_grade는 계산 결과로만 정한다. LLM 설명은 이를 바꿀 수 없다.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from skala_rag.scoring.aggregate import DIMENSIONS, ScoreBreakdown, ScoringError
from skala_rag.scoring.catalog import Thresholds

Label = Literal["RECOMMEND", "WATCHLIST", "PASS"]

GRADE_INSUFFICIENT = "보류 (정보 부족)"
GRADE_LOW_SCORE = "보류 (저점수)"
GRADE_PRIORITY = "투자 우선 검토"
GRADE_RECOMMEND = "투자 검토"
GRADE_WATCHLIST = "보류"
GRADE_PASS = "투자비추천"


@dataclass(frozen=True)
class Decision:
    """대표 label·grade와 해당하는 모든 reason_code."""

    label: Label
    report_grade: str
    reason_codes: tuple[str, ...]
    low_score_dimensions: tuple[str, ...]


def _as_decimal(name: str, value: Decimal | str | int) -> Decimal:
    d = Decimal(value)
    if not d.is_finite():
        raise ScoringError("INVALID_NUMBER", f"{name}: 유한한 값이어야 한다")
    return d


def decide(
    observed_score: Decimal | str | int,
    missing_weight: Decimal | str | int,
    dimension_ratings: Mapping[str, Decimal | str | int | None],
    thresholds: Thresholds,
) -> Decision:
    """§5 표를 위에서부터 적용한다. 반올림 전 값으로 비교한다.

    해당하는 reason은 모두 기록하고, 대표 label·grade는 가장 위 행을 따른다.
    한 영역 전체가 결측(None)이면 저점수 조건을 적용하지 않는다(D02).
    """
    score = _as_decimal("observed_score", observed_score)
    missing = _as_decimal("missing_weight", missing_weight)
    if not (0 <= score <= 100 and 0 <= missing <= 100):
        raise ScoringError("INVALID_NUMBER", "점수와 결측 비중은 0..100")
    if set(dimension_ratings) != set(DIMENSIONS):
        raise ScoringError("INCOMPLETE_EVALUATIONS", "여섯 영역 rating이 모두 필요하다")

    low = tuple(
        dim
        for dim in DIMENSIONS
        if dimension_ratings[dim] is not None
        and _as_decimal(dim, dimension_ratings[dim]) <= thresholds.low_dimension_rating
    )

    reasons: list[str] = []
    rows: list[tuple[Label, str]] = []
    if missing >= thresholds.missing_weight:
        reasons.append("INSUFFICIENT_EVIDENCE")
        rows.append(("WATCHLIST", GRADE_INSUFFICIENT))
    if low:
        reasons.extend(f"LOW_DIMENSION:{dim}" for dim in low)
        rows.append(("WATCHLIST", GRADE_LOW_SCORE))
    if score >= thresholds.priority_score:
        reasons.append("SCORE_PRIORITY")
        rows.append(("RECOMMEND", GRADE_PRIORITY))
    elif score >= thresholds.recommend_score:
        reasons.append("SCORE_RECOMMEND")
        rows.append(("RECOMMEND", GRADE_RECOMMEND))
    elif score >= thresholds.watchlist_score:
        reasons.append("SCORE_WATCHLIST")
        rows.append(("WATCHLIST", GRADE_WATCHLIST))
    else:
        reasons.append("SCORE_BELOW_WATCHLIST")
        rows.append(("PASS", GRADE_PASS))

    label, grade = rows[0]
    return Decision(
        label=label,
        report_grade=grade,
        reason_codes=tuple(reasons),
        low_score_dimensions=low,
    )


def decide_breakdown(breakdown: ScoreBreakdown, thresholds: Thresholds) -> Decision:
    """compute_scores/aggregate_scores 결과로 판단한다."""
    return decide(
        breakdown.observed_score,
        breakdown.missing_weight,
        breakdown.dimension_ratings,
        thresholds,
    )
