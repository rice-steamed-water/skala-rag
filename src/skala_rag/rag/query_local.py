"""Explicit local query bridge; model is already loaded by an opt-in caller."""

import time

from skala_rag.rag.dense import QueryVector
from skala_rag.rag.index_v3 import IndexSettings
from skala_rag.rag.local_bge_validation import MODEL, REVISION, LocalEncoder


class LocalQueryEncoder:
    retry_owner = "runtime"

    def __init__(self, encoder: LocalEncoder, *, settings: IndexSettings) -> None:
        self._encoder = encoder
        self._settings = settings.snapshot()
        if settings.model_id != MODEL or settings.model_revision != REVISION:
            raise ValueError("local query model revision mismatch")

    def encode_once(
        self, query: str, *, settings: IndexSettings, timeout_seconds: float
    ) -> QueryVector:
        if settings.snapshot() != self._settings:
            raise ValueError("query/index settings mismatch")
        started = time.monotonic()
        row = self._encoder.embed_texts((query,))[0]
        # Like #45 synchronous callbacks, this rejects late work but cannot cancel it.
        if time.monotonic() - started >= timeout_seconds:
            raise TimeoutError("local query exceeded attempt bound")
        return QueryVector(MODEL, REVISION, row)
