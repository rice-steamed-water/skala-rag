"""Offline, injected index input boundary; not a BGE runtime or a store selection.

No model loader, tokenizer, filesystem index, network call, or default provider is
provided. The caller must supply approved inputs and its own encoder/sink.
"""

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from skala_rag.contracts.sources import Chunk, Source
from skala_rag.rag.corpus import (
    CorpusManifest,
    check_corpus,
    compare_index_inputs,
    manifest_hash,
    require_indexable,
)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _required(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")


def _validate_json_mapping_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError("settings keys must be strings")
            _validate_json_mapping_keys(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _validate_json_mapping_keys(nested)


@dataclass(frozen=True)
class IndexSettings:
    """Every version-bearing decision is supplied; none is a product default."""

    model_id: str
    model_revision: str
    tokenizer_id: str
    tokenizer_revision: str
    tokenizer_settings: Mapping[str, Any]
    preprocessing_settings: Mapping[str, Any]
    chunk_settings: Mapping[str, Any]
    embedding_settings: Mapping[str, Any]
    dimension: int
    store_schema_version: str
    _payload: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        for name in (
            "model_id",
            "model_revision",
            "tokenizer_id",
            "tokenizer_revision",
            "store_schema_version",
        ):
            _required(getattr(self, name), name)
        if type(self.dimension) is not int or self.dimension <= 0:
            raise ValueError("dimension must be a positive integer")
        payload = {
            name: getattr(self, name)
            for name in (
                "model_id",
                "model_revision",
                "tokenizer_id",
                "tokenizer_revision",
                "tokenizer_settings",
                "preprocessing_settings",
                "chunk_settings",
                "embedding_settings",
                "dimension",
                "store_schema_version",
            )
        }
        for name in (
            "tokenizer_settings",
            "preprocessing_settings",
            "chunk_settings",
            "embedding_settings",
        ):
            if not isinstance(payload[name], Mapping) or not payload[name]:
                raise ValueError(f"{name} must be a nonempty JSON mapping")
            _validate_json_mapping_keys(payload[name])
        try:
            encoded = _canonical(payload)
            decoded = json.loads(encoded)
        except (TypeError, ValueError) as exc:
            raise ValueError("settings must be finite JSON data") from exc
        # Snapshot protects identity from later mutations of supplied mappings.
        object.__setattr__(self, "_payload", _canonical(decoded))

    def snapshot(self) -> dict[str, Any]:
        return json.loads(self._payload)


@dataclass(frozen=True)
class IndexMetadata:
    index_version: str
    corpus_version: str
    corpus_hash: str
    model_id: str
    model_revision: str
    tokenizer_id: str
    tokenizer_revision: str
    settings_snapshot: str
    dimension: int
    document_ids: tuple[str, ...]
    chunk_ids: tuple[str, ...]


@dataclass(frozen=True)
class IndexPlan:
    metadata: IndexMetadata
    # Serialized copies make later mutations of the caller's DTOs irrelevant.
    source_snapshots: tuple[str, ...]
    chunk_snapshots: tuple[str, ...]


@dataclass(frozen=True)
class EmbeddingVector:
    chunk_id: str
    model_id: str
    model_revision: str
    values: tuple[float, ...]


class Encoder(Protocol):
    def encode(
        self, chunks: tuple[Chunk, ...], *, settings: IndexSettings
    ) -> Sequence[EmbeddingVector]: ...


class IndexSink(Protocol):
    def read_metadata(self, index_version: str) -> IndexMetadata | None: ...
    def write_new(
        self, metadata: IndexMetadata, vectors: tuple[EmbeddingVector, ...]
    ) -> None: ...


def _index_version(
    *,
    corpus_version: str,
    corpus_hash: str,
    settings_snapshot: str,
    source_snapshots: tuple[str, ...],
    chunk_snapshots: tuple[str, ...],
) -> str:
    version_input = {
        "boundary": "skala-index-v3-fixture-1",
        "corpus_version": corpus_version,
        "corpus_hash": corpus_hash,
        "settings": json.loads(settings_snapshot),
        "sources": [json.loads(snapshot) for snapshot in source_snapshots],
        "chunks": [json.loads(snapshot) for snapshot in chunk_snapshots],
    }
    digest = hashlib.sha256(_canonical(version_input).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_index_plan(
    *,
    manifest: CorpusManifest,
    expected_corpus_hash: str,
    sources: Mapping[str, Source],
    chunks: Sequence[Chunk],
    settings: IndexSettings,
) -> IndexPlan:
    """Require full manifest closure; no partial indexing of a failed corpus."""
    if not isinstance(settings, IndexSettings):
        raise ValueError("explicit IndexSettings required")
    if expected_corpus_hash != manifest_hash(manifest):
        raise ValueError("corpus hash mismatch")
    gate = check_corpus(manifest)
    doc_ids = require_indexable(gate)
    if not doc_ids:
        raise ValueError("empty corpus is not indexable")
    documents = {doc.source_id: doc for doc in manifest.documents}
    if set(sources) != set(documents):
        raise ValueError("source set differs from approved manifest")
    for source_id, source in sources.items():
        doc = documents[source_id]
        if source.source_id != source_id or any(
            (
                source.content_hash != doc.content_hash,
                source.local_path != doc.local_path,
                source.title != doc.title,
                source.language != doc.language,
            )
        ):
            raise ValueError(f"source mismatch: {source_id}")
    if not chunks:
        raise ValueError("no chunks to index")
    ids: set[str] = set()
    observed: set[str] = set()
    ordered = sorted(chunks, key=lambda item: item.chunk_id)
    for item in ordered:
        if item.chunk_id in ids:
            raise ValueError("duplicate chunk ID")
        ids.add(item.chunk_id)
        doc = documents.get(item.source_id)
        if doc is None or any(
            (
                item.corpus_version != manifest.corpus_version,
                item.embedding_model != settings.model_id,
                item.embedding_revision != settings.model_revision,
                item.scope != doc.scope,
                set(item.candidate_ids) != set(doc.candidate_ids),
                len(item.candidate_ids) != len(doc.candidate_ids),
                item.language != doc.language,
            )
        ):
            raise ValueError(f"chunk provenance/model mismatch: {item.chunk_id}")
        observed.add(doc.document_id)
    indexed = {doc_id: manifest.document(doc_id).content_hash for doc_id in observed}
    if not compare_index_inputs(manifest, gate, indexed).matches:
        raise ValueError("chunk documents differ from approved corpus")
    chunk_snapshots = tuple(
        _canonical(item.model_dump(mode="json")) for item in ordered
    )
    source_snapshots = tuple(
        _canonical(sources[source_id].model_dump(mode="json"))
        for source_id in sorted(sources)
    )
    index_version = _index_version(
        corpus_version=manifest.corpus_version,
        corpus_hash=gate.manifest_hash,
        settings_snapshot=settings._payload,
        source_snapshots=source_snapshots,
        chunk_snapshots=chunk_snapshots,
    )
    metadata = IndexMetadata(
        index_version=index_version,
        corpus_version=manifest.corpus_version,
        corpus_hash=gate.manifest_hash,
        model_id=settings.model_id,
        model_revision=settings.model_revision,
        tokenizer_id=settings.tokenizer_id,
        tokenizer_revision=settings.tokenizer_revision,
        settings_snapshot=settings._payload,
        dimension=settings.dimension,
        document_ids=tuple(sorted(doc_ids)),
        chunk_ids=tuple(sorted(ids)),
    )
    return IndexPlan(metadata, source_snapshots, chunk_snapshots)


def write_index(plan: IndexPlan, *, embedder: Encoder, store: IndexSink) -> None:
    """Validate every vector before a single injected sink write; never overwrite."""
    metadata = plan.metadata
    try:
        frozen_settings = IndexSettings(**json.loads(metadata.settings_snapshot))
        chunks = tuple(
            Chunk.model_validate_json(snapshot) for snapshot in plan.chunk_snapshots
        )
        expected_version = _index_version(
            corpus_version=metadata.corpus_version,
            corpus_hash=metadata.corpus_hash,
            settings_snapshot=metadata.settings_snapshot,
            source_snapshots=plan.source_snapshots,
            chunk_snapshots=plan.chunk_snapshots,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("index plan integrity mismatch") from exc
    if (
        metadata.index_version != expected_version
        or metadata.model_id != frozen_settings.model_id
        or metadata.model_revision != frozen_settings.model_revision
        or metadata.tokenizer_id != frozen_settings.tokenizer_id
        or metadata.tokenizer_revision != frozen_settings.tokenizer_revision
        or metadata.dimension != frozen_settings.dimension
        or tuple(c.chunk_id for c in chunks) != metadata.chunk_ids
    ):
        raise ValueError("index plan integrity mismatch")
    if store.read_metadata(metadata.index_version) is not None:
        raise ValueError("index version already exists; refusing overwrite")
    vectors = tuple(embedder.encode(chunks, settings=frozen_settings))
    if len(vectors) != len(chunks):
        raise ValueError("embedding count mismatch")
    by_id: dict[str, EmbeddingVector] = {}
    for vector in vectors:
        if not isinstance(vector, EmbeddingVector):
            raise ValueError("embedding must include explicit model provenance")
        if vector.chunk_id in by_id or vector.chunk_id not in metadata.chunk_ids:
            raise ValueError("embedding chunk ID mismatch")
        if (
            vector.model_id != metadata.model_id
            or vector.model_revision != metadata.model_revision
        ):
            raise ValueError("mixed embedding model or revision")
        if len(vector.values) != metadata.dimension or any(
            type(value) not in (float, int) or not math.isfinite(value)
            for value in vector.values
        ):
            raise ValueError("embedding dimension or finite-value mismatch")
        by_id[vector.chunk_id] = vector
    if set(by_id) != set(metadata.chunk_ids):
        raise ValueError("embedding set differs from chunk set")
    store.write_new(metadata, tuple(by_id[chunk_id] for chunk_id in metadata.chunk_ids))
