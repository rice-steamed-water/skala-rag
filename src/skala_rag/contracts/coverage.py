"""Research/coverage observations. No thresholds, budgets, or calculations."""

from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from .common import Contract, Count, Number, Text

Nonnegative = Annotated[Number, Field(ge=0)]
Percentage = Annotated[Number, Field(ge=0, le=100)]


class ResearchGap(Contract):
    gap_id: Text
    candidate_id: Text
    criterion_id: Text | None = None
    eligibility_field: Text | None = None
    missing_fields: list[Text]
    reason: Text
    priority_weight: Nonnegative | None = None
    suggested_queries: list[Text]
    attempted_retrieval_ids: list[Text]
    status: Literal["open", "resolved", "exhausted"]

    @model_validator(mode="after")
    def require_one_target(self) -> Self:
        if (self.criterion_id is None) == (self.eligibility_field is None):
            raise ValueError(
                "gap requires exactly one criterion_id or eligibility_field"
            )
        return self


class CoverageResult(Contract):
    candidate_id: Text
    evidence_revision: Count
    policy_version: Text
    covered_criterion_ids: list[Text]
    missing_criterion_ids: list[Text]
    missing_weight: Nonnegative | None = None
    coverage_pct: Percentage | None = None
    research_ready: StrictBool | None = None
    unresolved_conflicts: list[Text]
