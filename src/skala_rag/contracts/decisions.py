"""Score and decision observations; no arithmetic or policy thresholds."""

import re
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from .common import Contract, Count, Text
from .evaluation import Dimension


def score_number(value: object) -> object:
    """Accept exact decimal JSON strings without boolean/object coercion."""
    if type(value) not in (int, float, Decimal, str):
        raise ValueError("score requires a finite number or decimal string")
    if isinstance(value, str) and not re.fullmatch(
        r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value
    ):
        raise ValueError("score string must contain an explicit decimal number")
    return value


ScoreNumber = Annotated[
    Decimal, BeforeValidator(score_number), Field(allow_inf_nan=False)
]
ScorePoints = Annotated[ScoreNumber, Field(ge=0)]
ScorePercentage = Annotated[ScoreNumber, Field(ge=0, le=100)]
DimensionRating = Annotated[ScoreNumber, Field(ge=1, le=5)]


class ScoreSummary(Contract):
    score_summary_id: Text
    run_id: Text
    candidate_id: Text
    evaluation_round: Count
    snapshot_id: Text
    evidence_revision: Count
    policy_version: Text
    criterion_points: dict[Text, ScorePoints | None]
    dimension_ratings: dict[Dimension, DimensionRating | None]
    observed_score: ScorePoints
    missing_weight: ScorePoints
    coverage_pct: ScorePercentage
    low_score_dimensions: list[Dimension]
    hold_reasons: list[Text]


class InvestmentDecision(Contract):
    decision_id: Text
    run_id: Text
    candidate_id: Text
    label: Literal["RECOMMEND", "WATCHLIST", "PASS"]
    report_grade: Text
    score_summary_id: Text
    reason_codes: list[Text]
    evidence_ids: list[Text]
    rationale: Text
    risks: list[Text]
    limitations: list[Text]
