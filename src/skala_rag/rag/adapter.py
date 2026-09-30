"""Pinned-index retrieval boundary. No model loading, store selection, or retries."""

import hashlib
import json
from typing import Protocol
from uuid import uuid4

from skala_rag.contracts import (
    RetrievalBundle,
    RetrievalRecord,
    RetrievalRequest,
    ToolBudget,
    ToolResult,
)
from skala_rag.contracts.common import Contract, JSONMap, Text
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode
from skala_rag.contracts.errors import WorkflowError
from skala_rag.rag.retrieval import bundle_violations, retrieval_cache_key, source_date
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    AttemptResponse,
    CallContext,
    Readiness,
    TransportFailure,
    Usage,
)


class IndexSnapshot(Contract):
    """External owner supplies a verified active index; this is metadata, not approval.

    Complete active Chunk/Source payloads pin replacement and model identity.
    corpus_hash and search_settings identify the externally built index/search path.
    """

    corpus_version: Text
    corpus_hash: Text
    index_version: Text
    embedding_model: Text
    embedding_revision: Text
    search_settings: JSONMap
    bundle: RetrievalBundle


class DenseSearch(Protocol):
    """One request only. Filter BEFORE ranking and limit, not after global Top-K.

    Return ranked chunks and their Source payloads from this exact pinned index.
    Query encoder must use the snapshot's model/revision and search settings.
    Honor the whole-attempt timeout; no hidden retry or automatic downloads.
    """

    retry_owner: str

    def search_once(
        self,
        request: RetrievalRequest,
        *,
        snapshot: IndexSnapshot,
        allowed_chunk_ids: tuple[str, ...],
        timeout_seconds: float,
    ) -> RetrievalBundle: ...


def index_identity(snapshot: IndexSnapshot) -> str:
    payload = snapshot.model_dump(mode="json")
    payload["bundle"]["chunks"].sort(key=lambda item: item["chunk_id"])
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


class IndexedRetriever:
    """Retrieve Protocol with runtime admission and isolated, defensive cache copies.

    Industry relevance is supplied by the caller's allowed_source_ids. This adapter
    does not infer industry relevance or introduce unapproved doc_type/year rules.
    """

    def __init__(
        self,
        *,
        snapshot: IndexSnapshot,
        backend: DenseSearch,
        runtime: AdapterRuntime,
        readiness: Readiness,
        budget: ToolBudget,
        allowance: Allowance,
        run_id: str,
        schema_version: str,
        tool_name: str,
    ) -> None:
        self._context = {"execution_mode": runtime.policy.execution_mode}
        self._snapshot = IndexSnapshot.model_validate(
            snapshot.model_dump(mode="python"), context=self._context
        ).model_copy(deep=True)
        self._chunks = {c.chunk_id: c for c in self._snapshot.bundle.chunks}
        if len(self._chunks) != len(self._snapshot.bundle.chunks):
            raise ValueError("duplicate index Chunk")
        if set(self._snapshot.bundle.sources) != {
            c.source_id for c in self._chunks.values()
        }:
            raise ValueError("index Source closure mismatch")
        for chunk in self._chunks.values():
            if (
                chunk.corpus_version != self._snapshot.corpus_version
                or chunk.embedding_model != self._snapshot.embedding_model
                or chunk.embedding_revision != self._snapshot.embedding_revision
                or (
                    chunk.page_start is not None
                    and chunk.page_end is not None
                    and chunk.page_start > chunk.page_end
                )
            ):
                raise ValueError("index payload identity mismatch")
        self._identity = index_identity(self._snapshot)
        self._backend = backend
        self._runtime = runtime
        self._readiness = Readiness.model_validate(readiness).model_copy(deep=True)
        if not self._readiness.index_required or not self._readiness.model_required:
            raise ValueError("indexed retrieval requires index and model readiness")
        self._budget = ToolBudget.model_validate(budget).model_copy(deep=True)
        self._allowance = Allowance.model_validate(allowance).model_copy(deep=True)
        self._run_id, self._schema_version, self._tool_name = (
            run_id,
            schema_version,
            tool_name,
        )
        self._cache: dict[str, RetrievalBundle] = {}

    @property
    def cache_keys(self) -> tuple[str, ...]:
        return tuple(self._cache)

    def __call__(self, request: RetrievalRequest) -> ToolResult[RetrievalBundle]:
        request = RetrievalRequest.model_validate(request.model_dump(mode="python"))
        runtime = self._runtime
        call = CallContext(
            schema_version=self._schema_version,
            call_id=uuid4().hex,
            run_id=self._run_id,
            candidate_id=request.candidate_id,
            tool_name=self._tool_name,
            node=self._tool_name,
        )
        key = hashlib.sha256(
            f"{self._identity}:{retrieval_cache_key(request)}".encode()
        ).hexdigest()
        version_matches = (
            request.index_version == self._snapshot.index_version
            and request.corpus_version == self._snapshot.corpus_version
        )
        preflight_code = None
        if (
            not version_matches
            or getattr(self._backend, "retry_owner", None) != "runtime"
        ):
            preflight_code = ErrorCode.TOOL_RESPONSE_INVALID
        elif callable(getattr(self._backend, "preflight", None)):
            try:
                self._backend.preflight()
            except TransportFailure as exc:
                preflight_code = exc.code
            except Exception:
                preflight_code = ErrorCode.TOOL_FAILED
        if preflight_code is not None:
            error = WorkflowError(
                schema_version=self._schema_version,
                error_id=f"index-error-{uuid4().hex}",
                run_id=self._run_id,
                candidate_id=request.candidate_id,
                node=self._tool_name,
                error_code=preflight_code,
                message_redacted=f"index preflight rejected: {preflight_code.value}",
                retryable=ERROR_SPECS[preflight_code].retryable,
                attempt=0,
                timestamp=runtime.clock.now(),
            )
            runtime.error_history[error.error_id] = error
            return ToolResult(
                schema_version=self._schema_version,
                status=ERROR_SPECS[preflight_code].tool_status,
                data=None,
                retrieval_records=[],
                errors=[error],
            )
        allowed = tuple(
            sorted(
                c.chunk_id
                for c in self._chunks.values()
                if c.source_id in request.allowed_source_ids
                and (c.scope == "industry" or request.candidate_id in c.candidate_ids)
                and source_date(self._snapshot.bundle.sources[c.source_id])
                <= request.as_of
            )
        )
        owner = self

        class Attempt:
            retry_owner = "runtime"

            def __call__(self, *, timeout_seconds: float) -> AttemptResponse:
                raw = owner._backend.search_once(
                    request.model_copy(deep=True),
                    snapshot=owner._snapshot.model_copy(deep=True),
                    allowed_chunk_ids=allowed,
                    timeout_seconds=timeout_seconds,
                )
                if not isinstance(raw, RetrievalBundle):
                    raise TransportFailure(ErrorCode.TOOL_RESPONSE_INVALID)
                bundle = RetrievalBundle.model_validate(
                    raw.model_dump(mode="python"), context=owner._context
                )
                if (
                    bundle_violations(request, bundle)
                    or set(bundle.sources) != {c.source_id for c in bundle.chunks}
                    or any(
                        c.chunk_id not in allowed or c != owner._chunks[c.chunk_id]
                        for c in bundle.chunks
                    )
                    or any(
                        s != owner._snapshot.bundle.sources[sid]
                        for sid, s in bundle.sources.items()
                    )
                ):
                    raise TransportFailure(ErrorCode.TOOL_RESPONSE_INVALID)
                return AttemptResponse(
                    schema_version=owner._schema_version,
                    status="ok" if bundle.chunks else "empty",
                    data=bundle,
                    source_ids=sorted(bundle.sources),
                    chunk_ids=[c.chunk_id for c in bundle.chunks],
                    evidence_ids=[],
                    usage=Usage(
                        schema_version=owner._schema_version,
                        input_tokens=None,
                        output_tokens=None,
                        cost_usd=None,
                    ),
                )

        # Cache is local work, not a physical provider request; never consumes ledger.
        # Do not bypass readiness/live approval/timing gates even for cached results.
        cache_allowed = (
            not self._readiness.missing
            and len(runtime.policy.retry_delays_seconds) == self._budget.max_retries
            and (
                runtime.policy.execution_mode == "fixture"
                or (
                    runtime.policy.live_approval_reference is not None
                    and runtime.policy.timing_approval_reference is not None
                    and self._budget.deadline is not None
                )
            )
            and (
                self._budget.deadline is None
                or runtime.clock.now() < self._budget.deadline
            )
        )
        cache_hit = key in self._cache
        if cache_allowed and not cache_hit and (not allowed or request.top_k == 0):
            self._cache[key] = RetrievalBundle(
                schema_version=self._schema_version, chunks=[], sources={}
            )
        if cache_allowed and key in self._cache:
            bundle = self._cache[key].model_copy(deep=True)
            now = runtime.clock.now()
            status = "ok" if bundle.chunks else "empty"
            record = RetrievalRecord(
                schema_version=self._schema_version,
                retrieval_id=f"index-cache-{uuid4().hex}",
                run_id=self._run_id,
                candidate_id=request.candidate_id,
                tool_name=self._tool_name,
                query=None,
                arguments_without_secrets={
                    "execution_mode": runtime.policy.execution_mode,
                    "cache_key": key,
                    "index_identity": self._identity,
                },
                started_at=now,
                finished_at=now,
                status=status,
                source_ids=sorted(bundle.sources),
                chunk_ids=[c.chunk_id for c in bundle.chunks],
                evidence_ids=[],
                cache_hit=cache_hit,
            )
            return ToolResult(
                schema_version=self._schema_version,
                status=status,
                data=bundle,
                retrieval_records=[record],
                errors=[],
            )
        result = runtime.execute(
            call,
            budget=self._budget,
            readiness=self._readiness,
            allowance=self._allowance,
            transport=Attempt(),
        )
        if result.status in ("ok", "empty"):
            self._cache[key] = result.data.model_copy(deep=True)
        for record in result.retrieval_records:
            record.arguments_without_secrets.update(
                cache_key=key, index_identity=self._identity
            )
        return result
