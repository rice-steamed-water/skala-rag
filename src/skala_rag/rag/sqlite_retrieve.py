"""Connect opt-in #52 SQLite store to Retrieve without globally truncating hits."""

from skala_rag.contracts import RetrievalBundle, RetrievalRequest
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.rag.adapter import IndexSnapshot, index_identity
from skala_rag.rag.dense import QueryEncoder
from skala_rag.rag.index_v3 import EmbeddingVector, IndexMetadata, IndexSettings
from skala_rag.rag.sqlite_index import SQLiteIndexStore
from skala_rag.tools.runtime import TransportFailure


class SQLiteDenseSearch:
    retry_owner = "runtime"

    def __init__(
        self,
        *,
        store: SQLiteIndexStore,
        metadata: IndexMetadata,
        snapshot: IndexSnapshot,
        encoder: QueryEncoder,
    ) -> None:
        self._store, self._metadata, self._encoder = store, metadata, encoder
        self._identity = index_identity(snapshot)
        self._snapshot = snapshot.model_copy(deep=True)
        self._settings = IndexSettings(**snapshot.search_settings["index_settings"])
        if (
            metadata.index_version != snapshot.index_version
            or metadata.corpus_hash != snapshot.corpus_hash
            or metadata.settings_snapshot != self._settings._payload
            or snapshot.search_settings["search"] != {"metric": "cosine"}
            or getattr(encoder, "retry_owner", None) != "runtime"
        ):
            raise ValueError("SQLite/query/snapshot identity mismatch")

    def preflight(self) -> None:
        """Local read-back only, before cache/ledger or any query HTTP request."""
        try:
            observed = self._store.read_metadata(self._metadata.index_version)
        except (ValueError, OSError):
            raise TransportFailure(ErrorCode.TOOL_RESPONSE_INVALID) from None
        if observed is None:
            raise TransportFailure(ErrorCode.TOOL_NOT_CONFIGURED)
        if observed != self._metadata:
            raise TransportFailure(ErrorCode.TOOL_RESPONSE_INVALID)

    def search_once(
        self,
        request: RetrievalRequest,
        *,
        snapshot: IndexSnapshot,
        allowed_chunk_ids: tuple[str, ...],
        timeout_seconds: float,
    ) -> RetrievalBundle:
        if index_identity(snapshot) != self._identity:
            raise TransportFailure(ErrorCode.TOOL_RESPONSE_INVALID)
        self.preflight()
        query = self._encoder.encode_once(
            request.query,
            settings=IndexSettings(**self._settings.snapshot()),
            timeout_seconds=timeout_seconds,
        )
        try:
            # Store's API lacks pre-filter support. Read its complete ranked set,
            # then apply permitted IDs BEFORE request.top_k to preserve recall.
            hits = self._store.search(
                EmbeddingVector(
                    "query", query.model_id, query.model_revision, query.values
                ),
                expected=self._metadata,
                top_k=len(self._metadata.chunk_ids),
            )
        except (ValueError, OSError):
            raise TransportFailure(ErrorCode.TOOL_RESPONSE_INVALID) from None
        allowed = set(allowed_chunk_ids)
        chosen = [hit for hit in hits if hit.chunk.chunk_id in allowed][: request.top_k]
        return RetrievalBundle(
            schema_version=snapshot.schema_version,
            chunks=[hit.chunk for hit in chosen],
            sources={hit.source.source_id: hit.source for hit in chosen},
        )
