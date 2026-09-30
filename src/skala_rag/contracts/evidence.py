"""Evidence/provenance payload shape; no merge or factual/policy inference."""

from typing import Literal, Self

from pydantic import model_validator

from .common import Confidence, Contract, ISODate, Locator, Number, Text


class EvidenceProvenance(Contract):
    retrieval_id: Text
    method: Literal["web", "api", "rag", "manual"]
    chunk_id: Text | None = None

    @model_validator(mode="after")
    def require_rag_chunk(self) -> Self:
        if self.method == "rag" and self.chunk_id is None:
            raise ValueError("rag provenance requires chunk_id")
        return self


class Evidence(Contract):
    evidence_id: Text
    candidate_id: Text | None = None
    scope: Literal["company", "industry"]
    criterion_ids: list[Text]
    claim: Text
    value: Number | None = None
    unit: Text | None = None
    currency: Text | None = None
    value_as_of: ISODate | None = None
    period: Text | None = None
    geography: Text | None = None
    event_date: ISODate | None = None
    source_id: Text
    locator: Locator
    excerpt: Text
    provenance: list[EvidenceProvenance]
    evidence_kind: Literal["reported", "derived", "estimated"]
    confidence: Confidence
    limitations: list[Text]
    supporting_evidence_ids: list[Text]
    derivation: Text | None = None
    conflicts_with: list[Text]
    supersedes: Text | None = None

    @model_validator(mode="after")
    def require_money_context(self) -> Self:
        if self.currency is not None and (
            self.value is None or self.unit is None or self.value_as_of is None
        ):
            raise ValueError(
                "monetary evidence requires value/currency/unit/value_as_of"
            )
        if self.value_as_of is not None and self.currency is None:
            raise ValueError("monetary basis date requires explicit currency")
        return self
