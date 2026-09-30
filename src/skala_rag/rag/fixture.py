"""메모리 fixture 검색. 실제 embedding·index·네트워크를 사용하지 않는다."""

import json
from collections.abc import Callable
from datetime import datetime

from skala_rag.contracts import (
    RetrievalBundle,
    RetrievalRecord,
    RetrievalRequest,
    ToolResult,
)
from skala_rag.contracts.interfaces import Clock


def _fixture(model, **payload):
    return model.model_validate(payload, context={"execution_mode": "fixture"})


def cache_key(request: RetrievalRequest) -> str:
    """검색 범위·기준일과 top_k까지 포함하는 결정적 key."""
    payload = request.model_dump(mode="json")
    payload["allowed_source_ids"] = sorted(set(request.allowed_source_ids))
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def validate_bundle(bundle: RetrievalBundle, request: RetrievalRequest) -> None:
    """주입된 검색 결과가 요청 범위를 벗어나면 거절한다."""
    ids = [chunk.chunk_id for chunk in bundle.chunks]
    if len(set(ids)) != len(ids) or len(ids) > request.top_k:
        raise ValueError("검색 Chunk 중복 또는 top_k 초과")
    if set(bundle.sources) != {chunk.source_id for chunk in bundle.chunks}:
        raise ValueError("검색 결과의 Source 집합 불일치")
    for chunk in bundle.chunks:
        source = bundle.sources[chunk.source_id]
        published = source.published_at
        day = published.date() if isinstance(published, datetime) else published
        if (
            chunk.source_id not in request.allowed_source_ids
            or chunk.corpus_version != request.corpus_version
            or (
                chunk.scope == "company"
                and request.candidate_id not in chunk.candidate_ids
            )
            or day is None
            or day > request.as_of
        ):
            raise ValueError("검색 Chunk가 기업·출처·버전·기준일 범위를 위반했습니다")


class FixtureRetriever:
    """고정 corpus를 순서대로 필터링한다. query는 cache 격리에만 사용한다."""

    def __init__(
        self,
        bundle: RetrievalBundle,
        *,
        index_version: str,
        run_id: str,
        schema_version: str,
        clock: Clock,
        retrieval_id_factory: Callable[[], str],
        execution_mode: str,
    ):
        if execution_mode != "fixture":
            raise ValueError("fixture 검색은 fixture 모드에서만 사용할 수 있습니다")
        self.bundle = bundle.model_copy(deep=True)
        self.index_version = index_version
        self.run_id = run_id
        self.schema_version = schema_version
        self.clock = clock
        self.retrieval_id_factory = retrieval_id_factory
        self.cache: dict[str, RetrievalBundle] = {}
        self._record_ids: set[str] = set()

    def __call__(self, request: RetrievalRequest) -> ToolResult[RetrievalBundle]:
        if request.index_version != self.index_version:
            raise ValueError("fixture index 버전 불일치")
        started = self.clock.now()
        key = cache_key(request)
        hit = key in self.cache
        if hit:
            bundle = self.cache[key].model_copy(deep=True)
        else:
            chunks = []
            for chunk in self.bundle.chunks:
                single = _fixture(
                    RetrievalBundle,
                    schema_version=self.schema_version,
                    chunks=[chunk],
                    sources={chunk.source_id: self.bundle.sources[chunk.source_id]},
                )
                try:
                    validate_bundle(single, request)
                except ValueError:
                    continue
                chunks.append(chunk.model_copy(deep=True))
            chunks = chunks[: request.top_k]
            bundle = _fixture(
                RetrievalBundle,
                schema_version=self.schema_version,
                chunks=chunks,
                sources={
                    c.source_id: self.bundle.sources[c.source_id].model_copy(deep=True)
                    for c in chunks
                },
            )
            validate_bundle(bundle, request)
            self.cache[key] = bundle.model_copy(deep=True)
        validate_bundle(bundle, request)
        retrieval_id = self.retrieval_id_factory()
        if retrieval_id in self._record_ids:
            raise ValueError("검색 이력 ID 중복")
        self._record_ids.add(retrieval_id)
        status = "ok" if bundle.chunks else "empty"
        record = RetrievalRecord(
            schema_version=self.schema_version,
            retrieval_id=retrieval_id,
            run_id=self.run_id,
            candidate_id=request.candidate_id,
            tool_name="fixture-retrieve",
            query=request.query,
            arguments_without_secrets=request.model_dump(mode="json"),
            started_at=started,
            finished_at=self.clock.now(),
            status=status,
            source_ids=list(bundle.sources),
            chunk_ids=[c.chunk_id for c in bundle.chunks],
            evidence_ids=[],
            cache_hit=hit,
        )
        return _fixture(
            ToolResult[RetrievalBundle],
            schema_version=self.schema_version,
            status=status,
            data=bundle,
            retrieval_records=[record],
            errors=[],
        )
