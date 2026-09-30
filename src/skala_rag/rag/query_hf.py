"""Single HF query request using #52 encoder; #45 owns retry/error observation."""

from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.rag.dense import QueryVector
from skala_rag.rag.hf_embedding import HFEmbeddingEncoder
from skala_rag.rag.index_v3 import IndexSettings
from skala_rag.tools.runtime import TransportFailure


class HFQueryEncoder:
    retry_owner = "runtime"

    def __init__(self, encoder: HFEmbeddingEncoder) -> None:
        self._encoder = encoder

    def encode_once(
        self, query: str, *, settings: IndexSettings, timeout_seconds: float
    ) -> QueryVector:
        try:
            rows = self._encoder.embed_texts(
                (query,),
                settings=settings,
                timeout_seconds=timeout_seconds,
                runtime_errors=True,
            )
        except ValueError:
            raise TransportFailure(ErrorCode.TOOL_RESPONSE_INVALID) from None
        return QueryVector(settings.model_id, settings.model_revision, rows[0])
