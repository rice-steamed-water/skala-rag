"""Retrieval request and recorded observations, not collector behavior."""

from typing import Literal

from pydantic import StrictBool

from .common import (
    Contract,
    Count,
    ISODate,
    JSONMap,
    MonetaryObservation,
    Text,
    Timestamp,
)


class RetrievalRequest(Contract):
    query: Text
    candidate_id: Text
    corpus_version: Text
    index_version: Text
    as_of: ISODate
    top_k: Count
    allowed_source_ids: list[Text]


class RetrievalRecord(Contract):
    retrieval_id: Text
    run_id: Text
    candidate_id: Text | None = None
    tool_name: Text
    query: Text | None = None
    arguments_without_secrets: JSONMap
    started_at: Timestamp
    finished_at: Timestamp
    status: Literal["ok", "empty", "unavailable", "failed"]
    source_ids: list[Text]
    chunk_ids: list[Text]
    evidence_ids: list[Text]
    error_id: Text | None = None
    cost: MonetaryObservation | None = None
    cache_hit: StrictBool
