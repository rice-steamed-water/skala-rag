"""T11: 미승인·추출 미완료·교체 문서 제외와 버전 고정. 가상 manifest만 쓴다."""

from datetime import date

import pytest
from pydantic import ValidationError

from skala_rag.contracts.retrieval import RetrievalRequest
from skala_rag.contracts.sources import Chunk, Source
from skala_rag.rag.corpus import (
    CorpusGateError,
    CorpusManifest,
    ManifestDocument,
    ManifestStore,
    Rejection,
    allowed_source_ids,
    check_corpus,
    compare_index_inputs,
    manifest_hash,
    next_corpus_version,
    require_indexable,
)
from skala_rag.tools.fixture_retrieve import fixture_search

SCHEMA = "synthetic-1"


def doc(document_id, **changes):
    data = dict(
        schema_version=SCHEMA,
        document_id=document_id,
        source_id=f"src-{document_id}",
        local_path=f"data/local/corpus/{document_id}.pdf",
        content_hash=f"hash-{document_id}",
        title=f"Synthetic {document_id}",
        language="ko",
        permission_note="Synthetic only",
        candidate_ids=[],
        scope="industry",
        extraction_status="ok",
        reviewer="synthetic-reviewer",
        approved=True,
    )
    data.update(changes)
    return ManifestDocument.model_validate(data)


def manifest(*documents, version="corpus-v1", **changes):
    return CorpusManifest(
        schema_version=SCHEMA,
        corpus_version=version,
        documents=documents,
        **changes,
    )


def codes(result):
    return {(issue.code, issue.document_id) for issue in result.issues}


def test_approved_extracted_documents_pass():
    result = check_corpus(manifest(doc("a"), doc("b")))
    assert result.passed
    assert require_indexable(result) == ("a", "b")


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        (dict(approved=False, reviewer=None), Rejection.DOCUMENT_NOT_APPROVED),
        (dict(extraction_status="partial"), Rejection.EXTRACTION_NOT_OK),
        (dict(extraction_status="pending"), Rejection.EXTRACTION_NOT_OK),
    ],
)
def test_document_level_rejections(changes, code):
    result = check_corpus(manifest(doc("ok"), doc("x", **changes)))
    assert codes(result) == {(code, "x")}
    assert result.indexable_document_ids == ("ok",)
    with pytest.raises(CorpusGateError):
        require_indexable(result)


@pytest.mark.parametrize(
    "changes",
    [
        dict(approved=True, reviewer=None),
        dict(scope="company", candidate_ids=[]),
        dict(scope="industry", candidate_ids=["co-a"]),
        dict(local_path="/abs/path.pdf"),
        dict(local_path="data/local/../secret.pdf"),
        dict(local_path="data/manifests/a.pdf"),
        dict(local_path="https://example.invalid/a.pdf"),
    ],
)
def test_manifest_document_shape_is_rejected(changes):
    with pytest.raises(ValidationError):
        doc("x", **changes)


def test_manifest_is_immutable_and_rejects_duplicates():
    corpus = manifest(doc("a"))
    with pytest.raises(ValidationError):
        corpus.corpus_version = "changed"
    with pytest.raises(ValidationError):
        manifest(doc("a"), doc("a", source_id="src-other"))


def test_replacement_creates_new_version_and_excludes_old_document():
    v1 = manifest(doc("old"), doc("keep"))
    v2 = next_corpus_version(
        v1,
        corpus_version="corpus-v2",
        remove=["old"],
        add=[doc("new"), doc("pending", approved=False, reviewer=None)],
    )
    assert v1.corpus_version == "corpus-v1" and len(v1.documents) == 2
    assert v2.previous_corpus_version == "corpus-v1"
    assert manifest_hash(v1) != manifest_hash(v2)

    result = check_corpus(v2)
    assert result.indexable_document_ids == ("keep", "new")
    assert codes(result) == {(Rejection.DOCUMENT_NOT_APPROVED, "pending")}

    with pytest.raises(ValueError):
        next_corpus_version(v1, corpus_version="corpus-v1")
    with pytest.raises(ValueError):
        next_corpus_version(v1, corpus_version="corpus-v3", remove=["missing"])


def test_store_freezes_version(tmp_path):
    store = ManifestStore(tmp_path / "data/manifests")
    v1 = manifest(doc("a"))
    digest = store.save(v1)
    assert store.save(v1) == digest
    assert store.load("corpus-v1", expected_hash=digest) == v1

    changed = manifest(doc("a", content_hash="hash-a-edited"))
    with pytest.raises(FileExistsError):
        store.save(changed)
    with pytest.raises(ValueError):
        store.load("corpus-v1", expected_hash=manifest_hash(changed))
    with pytest.raises(ValueError):
        store.path("../escape")


def test_index_inputs_are_compared_with_approved_manifest():
    corpus = manifest(doc("a"), doc("b"))
    result = check_corpus(corpus)
    assert compare_index_inputs(corpus, result, {"a": "hash-a", "b": "hash-b"}).matches

    diff = compare_index_inputs(
        corpus, result, {"a": "hash-a-edited", "stale": "hash-stale"}
    )
    assert diff.missing == ("b",)
    assert diff.unexpected == ("stale",)
    assert diff.hash_mismatch == ("a",)

    with pytest.raises(ValueError):
        compare_index_inputs(manifest(doc("a")), result, {})


def _chunk(source_id, corpus_version):
    return Chunk(
        schema_version=SCHEMA,
        chunk_id=f"chunk-{source_id}",
        source_id=source_id,
        corpus_version=corpus_version,
        text="Synthetic text",
        locator=f"https://fixture.invalid/{source_id}#page=1",
        candidate_ids=[],
        scope="industry",
        language="ko",
        embedding_model="synthetic-not-a-selection",
        embedding_revision="synthetic-revision",
    )


def _source(source_id):
    return Source(
        schema_version=SCHEMA,
        source_id=source_id,
        title="Synthetic",
        source_kind="report",
        url=f"https://fixture.invalid/{source_id}",
        retrieved_at="2026-09-01T12:00:00+09:00",
        content_hash=f"hash-{source_id}",
        language="ko",
        access_notes="Synthetic only",
        bibliographic_metadata={},
    )


def test_replaced_document_is_not_retrieved():
    v1 = manifest(doc("old"), doc("keep"))
    v2 = next_corpus_version(
        v1, corpus_version="corpus-v2", remove=["old"], add=[doc("new")]
    )
    allowed = allowed_source_ids(v2, check_corpus(v2), "co-a")
    assert allowed == ["src-keep", "src-new"]

    ids = ["src-old", "src-keep", "src-new"]
    search = fixture_search(
        {f"chunk-{s}": _chunk(s, "corpus-v2") for s in ids},
        {s: _source(s) for s in ids},
        index_version="idx",
        schema_version=SCHEMA,
    )
    bundle = search(
        RetrievalRequest(
            schema_version=SCHEMA,
            query="synthetic",
            candidate_id="co-a",
            corpus_version="corpus-v2",
            index_version="idx",
            as_of=date(2026, 9, 30),
            top_k=10,
            allowed_source_ids=allowed,
        )
    )
    assert {c.source_id for c in bundle.chunks} == {"src-keep", "src-new"}


def test_allowed_sources_follow_company_scope_and_gate():
    corpus = manifest(
        doc("mine", scope="company", candidate_ids=["co-a"]),
        doc("other", scope="company", candidate_ids=["co-b"]),
        doc("industry"),
    )
    result = check_corpus(corpus)
    assert allowed_source_ids(corpus, result, "co-a") == ["src-industry", "src-mine"]

    failed = check_corpus(
        manifest(doc("mine"), doc("draft", approved=False, reviewer=None))
    )
    with pytest.raises(CorpusGateError):
        allowed_source_ids(corpus, failed, "co-a")
