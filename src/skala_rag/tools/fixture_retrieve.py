"""fixture 검색 backend — #19. embedding·index 없이 주어진 가상 Chunk만 고른다.

실제 검색(M2)과 같은 요청 필터를 적용한다: corpus_version, 허용 출처, 기업 귀속
(company Chunk), 기준일(Source 발행일과 snapshot 확보일 모두).
순위는 similarity가 아니라 chunk_id 순서다.
결과는 ``GuardedRetriever``가 다시 검증·cache·기록한다.
"""

from collections.abc import Mapping

from skala_rag.contracts.bundles import RetrievalBundle
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.retrieval import RetrievalRequest
from skala_rag.contracts.sources import Chunk, Source
from skala_rag.rag.retrieval import SearchBackend, SearchError, source_date

TOOL_NAME = "fixture-retrieve"


def fixture_search(
    chunks: Mapping[str, Chunk],
    sources: Mapping[str, Source],
    *,
    index_version: str,
    schema_version: str,
) -> SearchBackend:
    """고정 Chunk 집합을 검색하는 backend. 다른 index_version 요청은 미설정 처리."""

    def search(request: RetrievalRequest) -> RetrievalBundle:
        if request.index_version != index_version:
            raise SearchError(
                ErrorCode.TOOL_NOT_CONFIGURED,
                f"fixture index {request.index_version} not configured",
            )
        allowed = set(request.allowed_source_ids)
        hits = [
            chunk
            for _, chunk in sorted(chunks.items())
            if chunk.corpus_version == request.corpus_version
            and chunk.source_id in allowed
            and (
                chunk.scope == "industry" or request.candidate_id in chunk.candidate_ids
            )
            and source_date(sources[chunk.source_id]) <= request.as_of
        ][: request.top_k]
        return RetrievalBundle.model_construct(
            schema_version=schema_version,
            chunks=hits,
            sources={c.source_id: sources[c.source_id] for c in hits},
        )

    return search
