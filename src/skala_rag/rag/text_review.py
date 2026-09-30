"""Explicit approval receipts for a text-only view of a partial PDF."""

import hashlib
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from skala_rag.contracts.common import Contract, ISODate, JSONMap, Text

PageNumber = Annotated[int, Field(strict=True, gt=0)]


def text_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class ReviewedOmission(Contract):
    model_config = ConfigDict(frozen=True)
    code: Literal["VISUAL_CONTENT_NOT_EXTRACTED", "TEXT_LAYOUT_WARNING"]
    page: PageNumber
    explanation: Text


class TextIndexReview(Contract):
    model_config = ConfigDict(frozen=True)
    indexing_scope: Literal["text_only"]
    source_content_hash: Text
    approved_by: Text
    approved_on: ISODate
    approval_record: Text
    page_count: PageNumber
    page_text_hashes: dict[Text, Text]
    extraction_settings: JSONMap
    omissions: tuple[ReviewedOmission, ...]
    limitations: tuple[Text, ...]

    @model_validator(mode="after")
    def validate_pages(self) -> Self:
        if set(self.page_text_hashes) != {
            str(n) for n in range(1, self.page_count + 1)
        }:
            raise ValueError("review requires every PDF page exactly once")
        if any(
            not value.startswith("sha256:")
            or len(value) != 71
            or any(c not in "0123456789abcdef" for c in value[7:])
            for value in self.page_text_hashes.values()
        ):
            raise ValueError("review requires SHA-256 text hashes")
        if not self.extraction_settings or not self.limitations:
            raise ValueError("explicit extraction settings and limitations required")
        keys = [(item.code, item.page) for item in self.omissions]
        if len(keys) != len(set(keys)) or any(
            page > self.page_count for _, page in keys
        ):
            raise ValueError("invalid or duplicate omission page")
        return self
