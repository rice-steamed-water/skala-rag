"""Assessment structure, independent of pending evidence DTOs (#5)."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Rating = Annotated[int, Field(strict=True, ge=1, le=5)]


class CriterionAssessment(BaseModel):
    """Validate shape only; snapshot membership is checked by the wrapper."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Text
    criterion_id: Text
    status: Literal["observed", "missing"]
    rating: Rating | None
    evidence_ids: list[Text]
    rationale: Text
    missing_reason: Text | None = None
    applicability_note: Text | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must be unique")
        if self.status == "observed":
            if self.rating is None or not self.evidence_ids:
                raise ValueError("observed requires rating and evidence_ids")
            if self.missing_reason is not None:
                raise ValueError("observed must not have missing_reason")
        elif self.rating is not None or self.missing_reason is None:
            raise ValueError("missing requires rating=None and missing_reason")
        return self
