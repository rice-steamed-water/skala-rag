"""Candidate DTO observations. No stage normalization or eligibility decisions."""

from typing import Literal, Self

from pydantic import StrictBool, model_validator

from .common import (
    Confidence,
    Contract,
    Count,
    ISODate,
    JSONMap,
    Locator,
    MonetaryObservation,
    Text,
)


class Candidate(Contract):
    candidate_id: Text
    canonical_name: Text
    aliases: list[Text]
    country: Text
    homepage_url: Locator | None = None
    legal_identifiers: dict[Text, Text]
    discovery_source_ids: list[Text]


class StageInfo(Contract):
    raw_label: Text | None = None
    normalized_round: Literal[
        "seed", "series_a", "series_b", "series_c", "out_of_scope", "unknown"
    ]
    bucket: Literal["early", "late", "unknown"]
    last_round_date: ISODate | None = None
    cumulative_funding_krw: MonetaryObservation | None = None
    method: Literal["explicit", "estimated", "unknown"]
    source_ids: list[Text]
    confidence: Confidence
    rationale: Text

    @model_validator(mode="after")
    def require_krw_context(self) -> Self:
        if (
            self.cumulative_funding_krw is not None
            and self.cumulative_funding_krw.currency != "KRW"
        ):
            raise ValueError("cumulative_funding_krw requires caller-supplied KRW")
        return self


class CompanyProfile(Contract):
    candidate_id: Text
    domain_match: StrictBool | None = None
    is_listed: StrictBool | None = None
    exit_completed: StrictBool | None = None
    stage: StageInfo
    as_of: ISODate
    field_evidence_ids: dict[Text, list[Text]]


class EligibilityResult(Contract):
    eligibility_result_id: Text
    run_id: Text
    candidate_id: Text
    evidence_revision: Count
    policy_version: Text
    as_of: ISODate
    status: Literal["eligible", "ineligible", "unknown"]
    checks: JSONMap
    reason_codes: list[Text]
    evidence_ids: list[Text]
