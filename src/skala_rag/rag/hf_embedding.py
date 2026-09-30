"""Explicit HTTP feature-extraction adapter; no model/tokenizer downloads.

Deployment identity is operator-supplied, not inferred from a Hub repository.
The caller owns the HTTP client and explicitly authorizes each execution.
"""

import math
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from skala_rag.contracts.sources import Chunk
from skala_rag.rag.index_v3 import EmbeddingVector, IndexSettings


@dataclass(frozen=True)
class HFDeployment:
    endpoint: str
    model_id: str
    model_revision: str
    tokenizer_revision: str
    deployment_record: str

    def __post_init__(self):
        url = urlsplit(self.endpoint)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "endpoint must be an explicit HTTPS URL without credentials"
            )
        for value in (
            self.model_id,
            self.model_revision,
            self.tokenizer_revision,
            self.deployment_record,
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("explicit deployment identity and record required")


class HFEmbeddingEncoder:
    def __init__(
        self,
        *,
        deployment: HFDeployment,
        token: str,
        client: httpx.Client,
        timeout_seconds: float,
    ):
        if not isinstance(token, str) or not token.strip():
            raise ValueError("HF token required")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("positive finite timeout required")
        self.deployment = deployment
        self._token = token
        self._client = client
        self._timeout = timeout_seconds

    def embed_texts(self, texts: tuple[str, ...], *, settings: IndexSettings):
        snapshot = settings.snapshot()
        deployment = self.deployment
        if (
            settings.model_id != deployment.model_id
            or settings.model_revision != deployment.model_revision
            or settings.tokenizer_id != deployment.model_id
            or settings.tokenizer_revision != deployment.tokenizer_revision
            or snapshot["embedding_settings"]
            != {
                "mode": "dense",
                "normalization": "l2",
                "runtime": "hf-http",
                "endpoint": deployment.endpoint,
                "deployment_record": deployment.deployment_record,
            }
            or snapshot["preprocessing_settings"] != {"prefix": ""}
            or snapshot["tokenizer_settings"] != {"truncate": False}
        ):
            raise ValueError("HF deployment/settings identity mismatch")
        if not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ValueError("nonempty texts required")
        # One bounded request, no retries, redirects, hidden prefix or truncation.
        try:
            response = self._client.post(
                deployment.endpoint,
                headers={"Authorization": f"Bearer {self._token}"},
                json={"inputs": list(texts), "normalize": True, "truncate": False},
                timeout=self._timeout,
                follow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise ValueError("HF embedding request failed") from None
        if not isinstance(payload, list) or len(payload) != len(texts):
            raise ValueError("HF embedding count mismatch")
        result = []
        for row in payload:
            if (
                not isinstance(row, list)
                or len(row) != settings.dimension
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in row)
            ):
                raise ValueError("HF embedding dimension or finite-value mismatch")
            norm = math.hypot(*row)
            if not norm or not math.isfinite(norm):
                raise ValueError("HF embedding invalid norm")
            result.append(tuple(v / norm for v in row))
        return tuple(result)

    def encode(self, chunks: tuple[Chunk, ...], *, settings: IndexSettings):
        if any(
            c.embedding_model != settings.model_id
            or c.embedding_revision != settings.model_revision
            for c in chunks
        ):
            raise ValueError("chunk embedding provenance mismatch")
        rows = self.embed_texts(tuple(c.text for c in chunks), settings=settings)
        return tuple(
            EmbeddingVector(c.chunk_id, settings.model_id, settings.model_revision, row)
            for c, row in zip(chunks, rows, strict=True)
        )
