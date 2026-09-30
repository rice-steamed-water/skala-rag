"""Score and decision observations; no arithmetic or policy thresholds."""

from typing import Annotated, Literal

from pydantic import Field

from .common import Contract, Count, Number, Text
from .coverage import Nonnegative, Percentage
from .evaluation import Dimension

DimensionRating = Annotated[Number, Field(ge=1, le=5)]


class ScoreSummary(Contract):
    score_summary_id: Text
    run_id: Text
    candidate_id: Text
    evaluation_round: Count
    snapshot_id: Text
    evidence_revision: Count
    policy_version: Text
    criterion_points: dict[Text, Nonnegative | None]
    dimension_ratings: dict[Dimension, DimensionRating | None]
    observed_score: Nonnegative
    missing_weight: Nonnegative
    coverage_pct: Percentage
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
