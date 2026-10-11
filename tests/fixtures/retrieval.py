"""Deterministic in-memory backend for retained retrieval boundary tests."""

from collections.abc import Mapping

from skala_rag.contracts import Chunk, RetrievalBundle, RetrievalRequest, Source
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.rag.retrieval import SearchBackend, SearchError, source_date

TOOL_NAME = "fixture-retrieve"


def request(data, **updates):
    payload = dict(
        schema_version="synthetic-1",
        query="가상 로봇 기술",
        candidate_id=data.cases["eligible"],
        corpus_version="corpus-fixture-v1",
        index_version="index-fixture-v1",
        as_of="2026-09-30",
        top_k=4,
        allowed_source_ids=list(data.sources),
    )
    payload.update(updates)
    return RetrievalRequest.model_validate(payload)


def fixture_search(
    chunks: Mapping[str, Chunk],
    sources: Mapping[str, Source],
    *,
    index_version: str,
    schema_version: str,
) -> SearchBackend:
    """Filter a synthetic bundle; the production guard still verifies returns."""

    def search(req: RetrievalRequest) -> RetrievalBundle:
        if req.index_version != index_version:
            raise SearchError(ErrorCode.TOOL_NOT_CONFIGURED, "INDEX_NOT_CONFIGURED")
        selected = [
            chunk.model_copy(deep=True)
            for chunk in chunks.values()
            if chunk.corpus_version == req.corpus_version
            and chunk.source_id in req.allowed_source_ids
            and (chunk.scope == "industry" or req.candidate_id in chunk.candidate_ids)
            and source_date(sources[chunk.source_id]) <= req.as_of
        ][: req.top_k]
        return RetrievalBundle.model_validate(
            dict(
                schema_version=schema_version,
                chunks=selected,
                sources={
                    chunk.source_id: sources[chunk.source_id].model_copy(deep=True)
                    for chunk in selected
                },
            ),
            context={"execution_mode": "fixture"},
        )

    return search
