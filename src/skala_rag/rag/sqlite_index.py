"""Optional local SQLite sink; explicit path, immutable versions, exact cosine."""

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from skala_rag.contracts.sources import Chunk, Source
from skala_rag.rag.index_v3 import (
    EmbeddingVector,
    IndexMetadata,
    IndexPlan,
    _index_version,
)


@dataclass(frozen=True)
class DenseHit:
    chunk: Chunk
    source: Source
    similarity: float


class SQLiteIndexStore:
    def __init__(self, path: Path, *, plan: IndexPlan | None = None):
        self.path = path
        self.plan = plan

    def _load(self, version):
        if not self.path.exists():
            return None
        with sqlite3.connect(
            f"{self.path.resolve().as_uri()}?mode=ro", uri=True
        ) as conn:
            try:
                row = conn.execute(
                    "SELECT payload, digest FROM indexes WHERE version = ?", (version,)
                ).fetchone()
            except sqlite3.OperationalError:
                raise ValueError("invalid SQLite index schema") from None
        if row is None:
            return None
        if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
            raise ValueError("stored index integrity mismatch")
        payload = json.loads(row[0])
        metadata = payload["metadata"]
        metadata["document_ids"] = tuple(metadata["document_ids"])
        metadata["chunk_ids"] = tuple(metadata["chunk_ids"])
        meta = IndexMetadata(**metadata)
        if (
            meta.index_version != version
            or _index_version(
                corpus_version=meta.corpus_version,
                corpus_hash=meta.corpus_hash,
                settings_snapshot=meta.settings_snapshot,
                source_snapshots=tuple(payload["sources"]),
                chunk_snapshots=tuple(payload["chunks"]),
            )
            != version
        ):
            raise ValueError("stored plan integrity mismatch")
        return meta, payload

    def read_metadata(self, index_version):
        loaded = self._load(index_version)
        return loaded[0] if loaded else None

    def write_new(self, metadata, vectors):
        if self.plan is None or self.plan.metadata != metadata:
            raise ValueError("matching bound index plan required")
        if tuple(v.chunk_id for v in vectors) != metadata.chunk_ids or any(
            v.model_id != metadata.model_id
            or v.model_revision != metadata.model_revision
            or len(v.values) != metadata.dimension
            or any(
                type(x) not in (int, float) or not math.isfinite(x) for x in v.values
            )
            or not math.hypot(*v.values)
            for v in vectors
        ):
            raise ValueError("invalid stored vectors")
        if (
            _index_version(
                corpus_version=metadata.corpus_version,
                corpus_hash=metadata.corpus_hash,
                settings_snapshot=metadata.settings_snapshot,
                source_snapshots=self.plan.source_snapshots,
                chunk_snapshots=self.plan.chunk_snapshots,
            )
            != metadata.index_version
        ):
            raise ValueError("bound plan integrity mismatch")
        payload = json.dumps(
            {
                "metadata": asdict(metadata),
                "sources": self.plan.source_snapshots,
                "chunks": self.plan.chunk_snapshots,
                "vectors": [asdict(v) for v in vectors],
            },
            sort_keys=True,
            allow_nan=False,
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()
        # One transaction persists vectors and all version-bearing DTO snapshots.
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS indexes "
                "(version TEXT PRIMARY KEY, payload TEXT NOT NULL, "
                "digest TEXT NOT NULL)"
            )
            try:
                conn.execute(
                    "INSERT INTO indexes VALUES (?, ?, ?)",
                    (metadata.index_version, payload, digest),
                )
            except sqlite3.IntegrityError:
                raise ValueError(
                    "index version already exists; refusing overwrite"
                ) from None

    def search(self, query: EmbeddingVector, *, expected: IndexMetadata, top_k: int):
        if type(top_k) is not int or top_k <= 0:
            raise ValueError("positive top_k required")
        loaded = self._load(expected.index_version)
        if loaded is None or loaded[0] != expected:
            raise ValueError("index/corpus/settings identity mismatch")
        if (
            query.model_id != expected.model_id
            or query.model_revision != expected.model_revision
            or len(query.values) != expected.dimension
            or any(
                type(x) not in (int, float) or not math.isfinite(x)
                for x in query.values
            )
            or not math.hypot(*query.values)
        ):
            raise ValueError("query embedding space mismatch")
        payload = loaded[1]
        sources = {
            s.source_id: s
            for s in (Source.model_validate_json(raw) for raw in payload["sources"])
        }
        chunks = {
            c.chunk_id: c
            for c in (Chunk.model_validate_json(raw) for raw in payload["chunks"])
        }
        qnorm = math.hypot(*query.values)
        hits = []
        for vector in payload["vectors"]:
            values = vector["values"]
            score = sum(
                (a / qnorm) * (b / math.hypot(*values))
                for a, b in zip(query.values, values, strict=True)
            )
            chunk = chunks[vector["chunk_id"]]
            hits.append(DenseHit(chunk, sources[chunk.source_id], score))
        return tuple(
            sorted(hits, key=lambda h: (-h.similarity, h.chunk.chunk_id))[:top_k]
        )
