"""Injected dense search over reopened vectors. No filesystem or model defaults."""

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from skala_rag.contracts import Chunk, RetrievalBundle, RetrievalRequest, Source
from skala_rag.contracts.common import JSONMap
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.rag.adapter import IndexSnapshot, index_identity
from skala_rag.rag.corpus import CorpusManifest
from skala_rag.rag.extraction import ExtractionResult
from skala_rag.rag.index_v3 import (
    EmbeddingVector,
    IndexMetadata,
    IndexPlan,
    IndexSettings,
    build_index_plan,
)
from skala_rag.rag.retrieval import source_date
from skala_rag.tools.runtime import TransportFailure


def snapshot_from_plan(
    plan: IndexPlan,
    *,
    manifest: CorpusManifest,
    reopened_metadata: IndexMetadata,
    search_settings: JSONMap,
    execution_mode: str,
    extraction_results: Mapping[str, ExtractionResult] | None = None,
    source_inputs: Mapping[str, Source] | None = None,
) -> IndexSnapshot:
    """Revalidate #52 plan, manifest and store read-back before constructing #54 input.

    A caller-supplied read-back is not proof of an actual store/file/model execution.
    No source bytes are fetched, partial manifest waived, or live permission granted.
    """
    if execution_mode not in ("fixture", "live"):
        raise ValueError("explicit execution mode required")
    context = {"execution_mode": execution_mode}
    sources = [
        Source.model_validate_json(s, context=context) for s in plan.source_snapshots
    ]
    chunks = [
        Chunk.model_validate_json(c, context=context) for c in plan.chunk_snapshots
    ]
    if len({s.source_id for s in sources}) != len(sources):
        raise ValueError("duplicate plan Source")
    settings = IndexSettings(**json.loads(plan.metadata.settings_snapshot))
    reconstructed = build_index_plan(
        manifest=manifest,
        expected_corpus_hash=plan.metadata.corpus_hash,
        sources=source_inputs
        if source_inputs is not None
        else {s.source_id: s for s in sources},
        chunks=chunks,
        settings=settings,
        extraction_results=extraction_results,
    )
    if reconstructed != plan or reopened_metadata != plan.metadata:
        raise ValueError("index plan or read-back identity mismatch")
    return IndexSnapshot.model_validate(
        dict(
            schema_version=manifest.schema_version,
            corpus_version=plan.metadata.corpus_version,
            corpus_hash=plan.metadata.corpus_hash,
            index_version=plan.metadata.index_version,
            embedding_model=settings.model_id,
            embedding_revision=settings.model_revision,
            search_settings={
                "index_settings": settings.snapshot(),
                "search": search_settings,
            },
            bundle=dict(
                schema_version=manifest.schema_version,
                chunks=chunks,
                sources={s.source_id: s for s in sources},
            ),
        ),
        context=context,
    )


@dataclass(frozen=True)
class QueryVector:
    model_id: str
    model_revision: str
    values: tuple[float, ...]


class QueryEncoder(Protocol):
    retry_owner: str

    def encode_once(
        self, query: str, *, settings: IndexSettings, timeout_seconds: float
    ) -> QueryVector: ...


def _unit(values: Sequence[float], dimension: int) -> tuple[float, ...]:
    if len(values) != dimension or any(
        type(v) not in (int, float) or not math.isfinite(v) for v in values
    ):
        raise ValueError("invalid dense vector dimension or values")
    # Scaling prevents overflow/underflow for finite nonzero vector components.
    scale = max(abs(v) for v in values)
    if scale == 0:
        raise ValueError("zero dense vector")
    scaled = tuple(v / scale for v in values)
    norm = math.sqrt(math.fsum(v * v for v in scaled))
    return tuple(v / norm for v in scaled)


class DenseVectorSearch:
    """Exact cosine Top-K over injected vectors; metric must be explicitly selected.

    This is backend code, not an approved product store or real BGE-M3 loader.
    Query encoder owns whole-attempt timeout/cancellation; runtime owns retries.
    """

    retry_owner = "runtime"

    def __init__(
        self,
        *,
        snapshot: IndexSnapshot,
        vectors: Sequence[EmbeddingVector],
        encoder: QueryEncoder,
        execution_mode: str,
    ) -> None:
        if execution_mode not in ("fixture", "live"):
            raise ValueError("explicit execution mode required")
        self._context = {"execution_mode": execution_mode}
        self._snapshot = IndexSnapshot.model_validate(
            snapshot.model_dump(mode="python"), context=self._context
        ).model_copy(deep=True)
        self._identity = index_identity(self._snapshot)
        config = self._snapshot.search_settings
        self._settings = IndexSettings(**config["index_settings"])
        if config["search"] != {"metric": "cosine"}:
            raise ValueError("only explicitly selected cosine search is supported")
        if (
            self._settings.model_id != snapshot.embedding_model
            or self._settings.model_revision != snapshot.embedding_revision
        ):
            raise ValueError("query/document model identity mismatch")
        if getattr(encoder, "retry_owner", None) != "runtime":
            raise ValueError("query encoder must delegate retries to runtime")
        self._encoder = encoder
        self._chunks = {c.chunk_id: c for c in self._snapshot.bundle.chunks}
        if len(self._chunks) != len(self._snapshot.bundle.chunks):
            raise ValueError("duplicate Chunk")
        self._vectors = {}
        for v in vectors:
            if (
                not isinstance(v, EmbeddingVector)
                or v.chunk_id in self._vectors
                or v.model_id != self._settings.model_id
                or v.model_revision != self._settings.model_revision
            ):
                raise ValueError("vector model, revision or duplicate mismatch")
            self._vectors[v.chunk_id] = _unit(v.values, self._settings.dimension)
        if set(self._vectors) != set(self._chunks):
            raise ValueError("reopened vector/Chunk set mismatch")

    def search_once(
        self,
        request: RetrievalRequest,
        *,
        snapshot: IndexSnapshot,
        allowed_chunk_ids: tuple[str, ...],
        timeout_seconds: float,
    ) -> RetrievalBundle:
        allowed_sources = set(request.allowed_source_ids)
        eligible = {
            c.chunk_id
            for c in self._chunks.values()
            if c.source_id in allowed_sources
            and (c.scope == "industry" or request.candidate_id in c.candidate_ids)
            and source_date(self._snapshot.bundle.sources[c.source_id]) <= request.as_of
        }
        if (
            index_identity(snapshot) != self._identity
            or request.index_version != self._snapshot.index_version
            or request.corpus_version != self._snapshot.corpus_version
            or not set(allowed_chunk_ids) <= eligible
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise TransportFailure(ErrorCode.TOOL_RESPONSE_INVALID)
        ranked = []
        if allowed_chunk_ids and request.top_k:
            query = self._encoder.encode_once(
                request.query,
                settings=IndexSettings(**self._settings.snapshot()),
                timeout_seconds=timeout_seconds,
            )
            if (
                not isinstance(query, QueryVector)
                or query.model_id != self._settings.model_id
                or query.model_revision != self._settings.model_revision
            ):
                raise TransportFailure(ErrorCode.TOOL_RESPONSE_INVALID)
            try:
                unit = _unit(query.values, self._settings.dimension)
            except ValueError:
                raise TransportFailure(ErrorCode.TOOL_RESPONSE_INVALID) from None
            ranked = sorted(
                set(allowed_chunk_ids),
                key=lambda cid: (
                    -math.fsum(
                        a * b for a, b in zip(unit, self._vectors[cid], strict=True)
                    ),
                    cid,
                ),
            )
        chunks = [
            self._chunks[cid].model_copy(deep=True) for cid in ranked[: request.top_k]
        ]
        return RetrievalBundle.model_validate(
            dict(
                schema_version=snapshot.schema_version,
                chunks=chunks,
                sources={
                    c.source_id: self._snapshot.bundle.sources[c.source_id].model_copy(
                        deep=True
                    )
                    for c in chunks
                },
            ),
            context=self._context,
        )
