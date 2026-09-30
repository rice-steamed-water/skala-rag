"""retrieve 공통 wrapper — #19, contracts §7, data-rag §4·§6, T12.

검색 backend(fixture 또는 M2의 실제 index)는 ``SearchBackend``로 주입한다. wrapper는

- query·기업·corpus/index_version·top_k·allowed_source_ids·as_of로 cache key를 만들고
- backend가 돌려준 Chunk가 요청 범위(기업·출처·corpus·기준일·top_k)를 벗어나면
  반환 전체를 거절하며(``TOOL_RESPONSE_INVALID``)
- 호출마다 ``RetrievalRecord``를 남긴다. evidence_ids는 비워 두고 collector가 채운다.

similarity 점수는 다루지 않는다. 실패 결과는 cache에 넣지 않는다.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime
from typing import Literal

from skala_rag.contracts.bundles import RetrievalBundle
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode, is_retryable
from skala_rag.contracts.interfaces import Clock
from skala_rag.contracts.retrieval import RetrievalRequest
from skala_rag.contracts.sources import Source
from skala_rag.contracts.tools import ToolResult

SearchBackend = Callable[[RetrievalRequest], RetrievalBundle]


class SearchError(Exception):
    """backend 실패. 메시지에 key·원문을 넣지 않는다."""

    def __init__(self, error_code: ErrorCode, message_redacted: str) -> None:
        super().__init__(message_redacted)
        self.error_code = ErrorCode(error_code)
        self.message_redacted = message_redacted


def retrieval_cache_key(request: RetrievalRequest) -> str:
    """같은 질의라도 기업·corpus/index·허용 출처·as_of·top_k가 다르면 다른 key."""
    payload = json.dumps(
        [
            "skala-rag-retrieval-cache-v1",
            request.query,
            request.candidate_id,
            request.corpus_version,
            request.index_version,
            request.as_of.isoformat(),
            request.top_k,
            sorted(set(request.allowed_source_ids)),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"retrieval-cache-v1-{hashlib.sha256(payload).hexdigest()}"


def source_date(source: Source) -> date:
    """발행일과 확보일 모두 지난 뒤에만 source snapshot을 허용한다.

    기존 date 계약대로 timestamp 자체의 UTC offset에서 calendar date를 취한다.
    UTC/실행기 timezone으로 변환하지 않으며 기준일 당일은 포함한다.
    발행일 미상일 때는 필수 retrieved_at의 확보일만 사용한다.
    """
    acquired = source.retrieved_at.date()
    published = source.published_at
    if published is None:
        return acquired
    published_date = published.date() if isinstance(published, datetime) else published
    return max(published_date, acquired)


def bundle_violations(request: RetrievalRequest, bundle: RetrievalBundle) -> list[str]:
    """요청 범위를 벗어난 반환 내용. 빈 목록이면 통과."""
    violations: list[str] = []
    allowed = set(request.allowed_source_ids)
    if len(bundle.chunks) > request.top_k:
        violations.append(f"TOP_K_EXCEEDED: {len(bundle.chunks)} > {request.top_k}")
    if len({chunk.chunk_id for chunk in bundle.chunks}) != len(bundle.chunks):
        violations.append("DUPLICATE_CHUNK")
    for chunk in bundle.chunks:
        if chunk.corpus_version != request.corpus_version:
            violations.append(f"CORPUS_MISMATCH: {chunk.chunk_id}")
        if chunk.source_id not in allowed:
            violations.append(f"SOURCE_NOT_ALLOWED: {chunk.chunk_id}")
        if chunk.scope == "company" and request.candidate_id not in chunk.candidate_ids:
            violations.append(f"OTHER_COMPANY_CHUNK: {chunk.chunk_id}")
        source = bundle.sources[chunk.source_id]
        if source_date(source) > request.as_of:
            violations.append(f"AFTER_AS_OF: {chunk.chunk_id}")
    return violations


class GuardedRetriever:
    """``Retrieve`` Protocol 구현. cache·범위 검증·RetrievalRecord를 담당한다."""

    def __init__(
        self,
        search: SearchBackend,
        *,
        run_id: str,
        tool_name: str,
        clock: Clock,
        schema_version: str,
        execution_mode: Literal["fixture", "live"],
    ) -> None:
        self._search = search
        self._run_id = run_id
        self._tool_name = tool_name
        self._clock = clock
        self._schema_version = schema_version
        self._context = {"execution_mode": execution_mode}
        self._execution_mode = execution_mode
        self._cache: dict[str, RetrievalBundle] = {}
        self._count = 0

    @property
    def cache_keys(self) -> list[str]:
        return list(self._cache)

    def __call__(self, request: RetrievalRequest) -> ToolResult[RetrievalBundle]:
        started_at = self._clock.now()
        self._count += 1
        retrieval_id = f"retrieval:{self._run_id}:{self._count}"
        key = retrieval_cache_key(request)
        cache_hit = key in self._cache
        bundle: RetrievalBundle | None = self._cache.get(key)
        error: tuple[ErrorCode, str] | None = None
        if bundle is None:
            try:
                bundle = RetrievalBundle.model_validate(
                    self._search(request).model_dump(mode="json"),
                    context=self._context,
                )
            except SearchError as exc:
                error = (exc.error_code, exc.message_redacted)
            else:
                violations = bundle_violations(request, bundle)
                if violations:
                    bundle = None
                    error = (
                        ErrorCode.TOOL_RESPONSE_INVALID,
                        "; ".join(violations),
                    )
                else:
                    self._cache[key] = bundle
        finished_at = self._clock.now()

        errors: list[dict] = []
        if error is not None:
            code, message = error
            errors.append(
                {
                    "schema_version": self._schema_version,
                    "error_id": f"error:{retrieval_id}",
                    "run_id": self._run_id,
                    "candidate_id": request.candidate_id,
                    "node": self._tool_name,
                    "error_code": code,
                    "message_redacted": message,
                    "retryable": is_retryable(code),
                    "attempt": 1,
                    "timestamp": finished_at,
                }
            )
            status = ERROR_SPECS[code].tool_status
        else:
            status = "ok" if bundle.chunks else "empty"

        chunks = bundle.chunks if bundle else []
        record = {
            "schema_version": self._schema_version,
            "retrieval_id": retrieval_id,
            "run_id": self._run_id,
            "candidate_id": request.candidate_id,
            "tool_name": self._tool_name,
            "query": request.query,
            "arguments_without_secrets": {
                "execution_mode": self._execution_mode,
                "cache_key": key,
                **request.model_dump(mode="json", exclude={"schema_version"}),
            },
            "started_at": started_at,
            "finished_at": finished_at,
            "status": status,
            "source_ids": sorted({c.source_id for c in chunks}),
            "chunk_ids": [c.chunk_id for c in chunks],
            "evidence_ids": [],
            "error_id": errors[0]["error_id"] if errors else None,
            "cost": None,
            "cache_hit": cache_hit,
        }
        return ToolResult[RetrievalBundle].model_validate(
            {
                "schema_version": self._schema_version,
                "status": status,
                "data": bundle.model_dump(mode="json") if bundle else None,
                "retrieval_records": [record],
                "errors": errors,
            },
            context=self._context,
        )
