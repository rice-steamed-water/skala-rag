"""Source and chunk payloads, without fetching or model selection."""

from typing import Literal, Self

from pydantic import model_validator

from .common import Contract, Count, ISODate, JSONMap, Locator, Text, Timestamp


class Source(Contract):
    source_id: Text
    title: Text
    publisher: Text | None = None
    author: Text | None = None
    source_kind: Literal["web", "report", "paper", "filing", "patent"]
    url: Locator | None = None
    local_path: Locator | None = None
    published_at: ISODate | Timestamp | None = None
    retrieved_at: Timestamp
    content_hash: Text
    language: Text
    access_notes: Text
    bibliographic_metadata: JSONMap

    @model_validator(mode="after")
    def require_location(self) -> Self:
        if self.url is None and self.local_path is None:
            raise ValueError("Source requires url or local_path")
        return self


class Chunk(Contract):
    chunk_id: Text
    source_id: Text
    corpus_version: Text
    text: Text
    page_start: Count | None = None
    page_end: Count | None = None
    section: Text | None = None
    locator: Locator
    candidate_ids: list[Text]
    scope: Literal["company", "industry"]
    language: Text
    embedding_model: Text
    embedding_revision: Text
