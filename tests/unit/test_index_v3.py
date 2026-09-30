"""#52: synthetic offline vectors only; no model or document is downloaded."""

from dataclasses import replace

import pytest

from skala_rag.contracts.sources import Chunk, Source
from skala_rag.rag.corpus import (
    CorpusGateError,
    CorpusManifest,
    ManifestDocument,
    manifest_hash,
)
from skala_rag.rag.index_v3 import (
    EmbeddingVector,
    IndexSettings,
    build_index_plan,
    write_index,
)

SCHEMA = "synthetic-1"


def document(name="a", **changes):
    fields = dict(
        schema_version=SCHEMA,
        document_id=name,
        source_id=f"src-{name}",
        local_path=f"data/local/{name}.pdf",
        content_hash=f"synthetic-hash-{name}",
        title=f"Synthetic {name}",
        language="ko",
        permission_note="synthetic only",
        candidate_ids=("co-a",),
        scope="company",
        extraction_status="ok",
        reviewer="fixture-reviewer",
        approved=True,
    )
    return ManifestDocument(**(fields | changes))


def corpus(*documents):
    return CorpusManifest(
        schema_version=SCHEMA, corpus_version="synthetic-v1", documents=documents
    )


def source(doc):
    return Source(
        schema_version=SCHEMA,
        source_id=doc.source_id,
        title=doc.title,
        source_kind="report",
        local_path=doc.local_path,
        retrieved_at="2026-09-01T12:00:00+09:00",
        content_hash=doc.content_hash,
        language=doc.language,
        access_notes="synthetic only",
        bibliographic_metadata={},
    )


def chunk(doc, **changes):
    fields = dict(
        schema_version=SCHEMA,
        chunk_id=f"chunk-{doc.document_id}",
        source_id=doc.source_id,
        corpus_version="synthetic-v1",
        text="Synthetic robot actuator description.",
        locator=f"https://fixture.invalid/{doc.document_id}#page=1",
        page_start=1,
        page_end=1,
        candidate_ids=list(doc.candidate_ids),
        scope=doc.scope,
        language=doc.language,
        embedding_model="BAAI/bge-m3",
        embedding_revision="synthetic-revision-not-approved",
    )
    return Chunk(**(fields | changes))


def settings(**changes):
    fields = dict(
        model_id="BAAI/bge-m3",
        model_revision="synthetic-revision-not-approved",
        tokenizer_id="BAAI/bge-m3",
        tokenizer_revision="synthetic-tokenizer-not-approved",
        tokenizer_settings={"max_tokens": 8, "overflow": "reject"},
        preprocessing_settings={"prefix": "", "version": "synthetic-1"},
        chunk_settings={"page_atomic": True, "version": "synthetic-1"},
        embedding_settings={"normalization": "l2", "dtype": "float32", "mode": "dense"},
        dimension=2,
        store_schema_version="synthetic-store-1",
    )
    return IndexSettings(**(fields | changes))


def plan(doc=None, **options):
    doc = doc or document()
    manifest = corpus(doc)
    inputs = dict(
        manifest=manifest,
        expected_corpus_hash=manifest_hash(manifest),
        sources={doc.source_id: source(doc)},
        chunks=[chunk(doc)],
        settings=settings(),
    )
    return build_index_plan(**(inputs | options))


class FakeEmbedder:
    def __init__(self, vectors=None):
        self.vectors = vectors
        self.calls = 0

    def encode(self, chunks, *, settings):
        self.calls += 1
        if self.vectors is not None:
            return self.vectors
        return [
            EmbeddingVector(
                c.chunk_id, settings.model_id, settings.model_revision, (0.6, 0.8)
            )
            for c in chunks
        ]


class FakeStore:
    def __init__(self, existing=None):
        self.existing = existing
        self.writes = []

    def read_metadata(self, index_version):
        return self.existing

    def write_new(self, metadata, vectors):
        self.writes.append((metadata, vectors))


def test_offline_plan_and_atomic_fake_write():
    result = plan()
    assert result.metadata.corpus_hash == manifest_hash(corpus(document()))
    assert result.metadata.model_id == "BAAI/bge-m3"
    assert result.metadata.index_version.startswith("sha256:")
    embedder, store = FakeEmbedder(), FakeStore()
    write_index(result, embedder=embedder, store=store)
    assert embedder.calls == 1
    assert len(store.writes) == 1
    assert store.writes[0][1][0].chunk_id == "chunk-a"


@pytest.mark.parametrize(
    "changes",
    [
        {"approved": False, "reviewer": None},
        {"extraction_status": "partial"},
        {"extraction_status": "pending"},
        {"extraction_status": "failed"},
    ],
)
def test_rejected_document_blocks_whole_plan(changes):
    good, rejected = document(), document("b", **changes)
    manifest = corpus(good, rejected)
    with pytest.raises(CorpusGateError, match="rejected"):
        build_index_plan(
            manifest=manifest,
            expected_corpus_hash=manifest_hash(manifest),
            sources={good.source_id: source(good)},
            chunks=[chunk(good)],
            settings=settings(),
        )


@pytest.mark.parametrize(
    "bad",
    [
        lambda d: {"expected_corpus_hash": "sha256:wrong"},
        lambda d: {"sources": {}},
        lambda d: {
            "sources": {
                d.source_id: source(d).model_copy(update={"content_hash": "wrong"})
            }
        },
        lambda d: {"chunks": [chunk(d, source_id="src-other")]},
        lambda d: {"chunks": [chunk(d, corpus_version="other")]},
        lambda d: {"chunks": [chunk(d, embedding_revision="other")]},
        lambda d: {"chunks": [chunk(d, candidate_ids=["co-other"])]},
        lambda d: {"chunks": []},
    ],
)
def test_wrong_corpus_source_or_chunk_is_rejected(bad):
    doc = document()
    with pytest.raises(ValueError):
        plan(doc, **bad(doc))


def test_version_deterministic_across_input_order_and_changes_on_identity():
    a, b = document(), document("b")
    manifest = corpus(a, b)
    sources = {d.source_id: source(d) for d in (a, b)}
    chunks = [chunk(a), chunk(b)]

    def version(manifest, sources, chunks, config=None):
        return build_index_plan(
            manifest=manifest,
            expected_corpus_hash=manifest_hash(manifest),
            sources=sources,
            chunks=chunks,
            settings=config or settings(),
        ).metadata.index_version

    baseline = version(manifest, sources, chunks)
    assert baseline == version(
        manifest, dict(reversed(list(sources.items()))), chunks[::-1]
    )
    changed_manifest = corpus(a, document("b", permission_note="new review"))
    assert baseline != version(changed_manifest, sources, chunks)
    assert baseline != version(
        manifest, sources, [chunk(a, text="Changed text"), chunk(b)]
    )
    assert baseline != version(
        manifest, sources, [chunk(a, chunk_id="new-id"), chunk(b)]
    )
    for field, new_value in [
        ("model_revision", "other-revision"),
        ("tokenizer_revision", "other-tokenizer"),
        ("tokenizer_settings", {"max_tokens": 9, "overflow": "reject"}),
        ("preprocessing_settings", {"prefix": "changed"}),
        ("chunk_settings", {"page_atomic": False}),
        ("embedding_settings", {"normalization": "none"}),
        ("store_schema_version", "other-store"),
    ]:
        config = settings(**{field: new_value})
        adjusted = [
            c.model_copy(update={"embedding_revision": config.model_revision})
            for c in chunks
        ]
        assert baseline != version(manifest, sources, adjusted, config), field


@pytest.mark.parametrize(
    "vectors",
    [
        [
            EmbeddingVector(
                "chunk-a", "other/model", "synthetic-revision-not-approved", (0.6, 0.8)
            )
        ],
        [EmbeddingVector("chunk-a", "BAAI/bge-m3", "wrong-revision", (0.6, 0.8))],
        [
            EmbeddingVector(
                "chunk-a", "BAAI/bge-m3", "synthetic-revision-not-approved", (0.6,)
            )
        ],
        [
            EmbeddingVector(
                "chunk-a",
                "BAAI/bge-m3",
                "synthetic-revision-not-approved",
                (float("nan"), 0.8),
            )
        ],
        [
            EmbeddingVector(
                "unknown", "BAAI/bge-m3", "synthetic-revision-not-approved", (0.6, 0.8)
            )
        ],
        [],
    ],
)
def test_bad_embedding_never_writes(vectors):
    store = FakeStore()
    with pytest.raises(ValueError):
        write_index(plan(), embedder=FakeEmbedder(vectors), store=store)
    assert not store.writes


def test_mixed_model_batch_fails_before_any_sink_write():
    a, b = document(), document("b")
    manifest = corpus(a, b)
    result = build_index_plan(
        manifest=manifest,
        expected_corpus_hash=manifest_hash(manifest),
        sources={d.source_id: source(d) for d in (a, b)},
        chunks=[chunk(a), chunk(b)],
        settings=settings(),
    )
    vectors = [
        EmbeddingVector(
            "chunk-a", "BAAI/bge-m3", settings().model_revision, (0.6, 0.8)
        ),
        EmbeddingVector(
            "chunk-b", "other/model", settings().model_revision, (0.6, 0.8)
        ),
    ]
    store = FakeStore()
    with pytest.raises(ValueError, match="mixed"):
        write_index(result, embedder=FakeEmbedder(vectors), store=store)
    assert not store.writes


def test_mutating_caller_settings_after_plan_does_not_change_encoder_settings():
    config = settings()
    result = plan(settings=config)
    config.chunk_settings["page_atomic"] = False

    class CheckingEmbedder(FakeEmbedder):
        def encode(self, chunks, *, settings):
            assert settings.chunk_settings["page_atomic"] is True
            return super().encode(chunks, settings=settings)

    write_index(result, embedder=CheckingEmbedder(), store=FakeStore())


def test_forged_plan_metadata_never_reaches_embedder_or_sink():
    result = plan()
    forged = replace(
        result,
        metadata=replace(result.metadata, index_version="sha256:forged"),
    )
    embedder, store = FakeEmbedder(), FakeStore()
    with pytest.raises(ValueError, match="integrity"):
        write_index(forged, embedder=embedder, store=store)
    assert embedder.calls == 0
    assert not store.writes


def test_existing_index_metadata_never_overwritten():
    result = plan()
    store = FakeStore(replace(result.metadata, model_revision="other"))
    embedder = FakeEmbedder()
    with pytest.raises(ValueError, match="exists"):
        write_index(result, embedder=embedder, store=store)
    assert embedder.calls == 0
    assert not store.writes


def test_missing_explicit_settings_or_non_json_configuration_rejected():
    with pytest.raises((TypeError, ValueError)):
        settings(tokenizer_revision="")
    with pytest.raises((TypeError, ValueError)):
        settings(chunk_settings={"threshold": float("nan")})
    with pytest.raises(ValueError, match="keys"):
        settings(chunk_settings={"nested": {1: "not-json"}})
