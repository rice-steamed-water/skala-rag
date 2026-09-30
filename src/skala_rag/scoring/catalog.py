"""명시적으로 선택한 draft 정책 로더. 정책 승인을 대신하지 않는다."""

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Dimension = Literal["founder", "market", "technology", "moat", "traction", "deal_terms"]


class Criterion(BaseModel):
    """평가 항목의 식별자와 원문 비중."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    criterion_id: str = Field(min_length=1)
    dimension: Dimension
    display_name: str = Field(min_length=1)
    weight: int = Field(strict=True, gt=0)


class Thresholds(BaseModel):
    """반올림 전 비교에 사용할 명시적 임계값."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    priority_score: Decimal = Field(ge=0, le=100, allow_inf_nan=False)
    recommend_score: Decimal = Field(ge=0, le=100, allow_inf_nan=False)
    watchlist_score: Decimal = Field(ge=0, le=100, allow_inf_nan=False)
    missing_weight: Decimal = Field(ge=0, le=100, allow_inf_nan=False)
    low_dimension_rating: Decimal = Field(ge=1, le=5, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_order(self) -> "Thresholds":
        if not self.watchlist_score < self.recommend_score < self.priority_score:
            raise ValueError("점수 임계값 순서가 올바르지 않습니다")
        return self


class Budgets(BaseModel):
    """D08 승인 전 fixture용 예산. 암묵적 기본값은 없다."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    max_candidates: int = Field(strict=True, gt=0)
    max_research_retries_per_candidate: int = Field(strict=True, ge=0)
    max_report_revisions: int = Field(strict=True, ge=0)


class ScoringPolicy(BaseModel):
    """현재 main 설계의 draft catalog. live 승인은 별도 작업이다."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    policy_version: str = Field(min_length=1)
    status: Literal["draft"]
    basis: str = Field(min_length=1)
    open_decisions: tuple[str, ...] = Field(min_length=1)
    criteria: tuple[Criterion, ...] = Field(min_length=23, max_length=23)
    dimension_weights: dict[Dimension, int]
    thresholds: Thresholds
    budgets: Budgets

    @model_validator(mode="after")
    def validate_catalog(self) -> "ScoringPolicy":
        expected = dict(
            founder=5, market=30, technology=25, moat=20, traction=10, deal_terms=10
        )
        if self.dimension_weights != expected:
            raise ValueError("영역 비중은 main draft의 5/30/25/20/10/10이어야 합니다")
        identifiers = [item.criterion_id for item in self.criteria]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("criterion ID가 중복되었습니다")
        totals = dict.fromkeys(expected, 0)
        for item in self.criteria:
            if not item.criterion_id.startswith(item.dimension + "."):
                raise ValueError("criterion ID와 영역이 다릅니다")
            totals[item.dimension] += item.weight
        if sum(totals.values()) != 100 or totals != expected:
            raise ValueError("전체 또는 영역별 비중 합이 일치하지 않습니다")
        return self


def load_policy(
    path: str | Path, *, execution_mode: Literal["fixture", "live"]
) -> ScoringPolicy:
    """지정 경로만 읽으며 draft 정책의 live 사용을 거절한다."""
    if execution_mode != "fixture":
        raise ValueError("draft 정책은 fixture 모드에서만 사용할 수 있습니다")
    return ScoringPolicy.model_validate_json(Path(path).read_text(encoding="utf-8"))
