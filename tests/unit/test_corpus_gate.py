"""T11: 전체 200페이지 gate, 미상·미승인·교체 문서 제외. 가상 manifest만 쓴다."""

from dataclasses import dataclass
from datetime import date

import pytest
from pydantic import ValidationError

from skala_rag.contracts.retrieval import RetrievalRequest
from skala_rag.contracts.sources import Chunk, Source
from skala_rag.rag.corpus import (
    PROJECT_PAGE_LIMIT,
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
RULE_VERSION = "fixture-d13-proposal"


@dataclass(frozen=True)
class FixtureRule:
    """D13 제안안을 흉내 낸 가상 규칙. 승인된 산정 규칙이 아니다."""

    rule_version: str = RULE_VERSION
    approved: bool = False
    allow_partial: bool = False
    kinds: frozenset[str] = frozenset({"pdf", "pitch_deck", "html_snapshot"})

    def counted_page_ids(self, document):
        if document.document_kind not in self.kinds:
            return None
        if document.is_partial() and not self.allow_partial:
            return None
        return document.included_pages()


def doc(document_id, pages, **changes):
    data = dict(
        schema_version=SCHEMA,
        document_id=document_id,
        source_id=f"src-{document_id}",
        document_kind="pdf",
        local_path=f"data/local/corpus/{document_id}.pdf",
        content_hash=f"hash-{document_id}",
        title=f"Synthetic {document_id}",
        language="ko",
        original_page_count=pages,
        included_page_ranges=[{"schema_version": SCHEMA, "start": 1, "end": pages}],
        counted_pages=pages,
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
        counting_rule_version=RULE_VERSION,
        documents=documents,
        **changes,
    )


def codes(result):
    return {(issue.code, issue.document_id) for issue in result.issues}


def test_project_total_200_passes_and_201_rejects():
    ok = check_corpus(
        manifest(doc("a", 120), doc("b", 80)), FixtureRule(), execution_mode="fixture"
    )
    assert ok.passed and ok.total_counted_pages == PROJECT_PAGE_LIMIT

    over = check_corpus(
        manifest(doc("a", 120), doc("b", 81)), FixtureRule(), execution_mode="fixture"
    )
    assert not over.passed
    assert codes(over) == {(Rejection.PAGE_LIMIT_EXCEEDED, None)}
    with pytest.raises(CorpusGateError):
        require_indexable(over)


def test_limit_is_project_wide_not_per_company():
    a = doc("a", 150, scope="company", candidate_ids=["co-a"])
    b = doc("b", 60, scope="company", candidate_ids=["co-b"])
    result = check_corpus(manifest(a, b), FixtureRule(), execution_mode="fixture")
    assert codes(result) == {(Rejection.PAGE_LIMIT_EXCEEDED, None)}


def test_unknown_page_count_is_rejected_not_counted_as_one():
    unknown = doc(
        "u",
        1,
        original_page_count=None,
        counted_pages=None,
    )
    result = check_corpus(
        manifest(doc("a", 200), unknown), FixtureRule(), execution_mode="fixture"
    )
    assert codes(result) == {(Rejection.PAGE_COUNT_UNKNOWN, "u")}
    assert result.total_counted_pages == 200
    assert result.indexable_document_ids == ("a",)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        (dict(approved=False, reviewer=None), Rejection.DOCUMENT_NOT_APPROVED),
        (dict(extraction_status="partial"), Rejection.EXTRACTION_NOT_OK),
        (dict(document_kind="html_raw"), Rejection.COUNT_NOT_ALLOWED),
        (dict(counted_pages=1), Rejection.COUNTED_PAGES_MISMATCH),
    ],
)
def test_document_level_rejections(changes, code):
    result = check_corpus(
        manifest(doc("x", 10, **changes)), FixtureRule(), execution_mode="fixture"
    )
    assert codes(result) == {(code, "x")}
    assert result.indexable_document_ids == ()


def test_partial_extraction_requires_rule_permission():
    partial = doc(
        "p",
        5,
        original_page_count=30,
        included_page_ranges=[
            {"schema_version": SCHEMA, "start": 3, "end": 5},
            {"schema_version": SCHEMA, "start": 10, "end": 11},
        ],
    )
    denied = check_corpus(manifest(partial), FixtureRule(), execution_mode="fixture")
    assert codes(denied) == {(Rejection.COUNT_NOT_ALLOWED, "p")}

    allowed = check_corpus(
        manifest(partial), FixtureRule(allow_partial=True), execution_mode="fixture"
    )
    assert allowed.passed and allowed.total_counted_pages == 5


def test_same_original_page_is_counted_once():
    first = doc("a", 150, content_hash="hash-shared")
    copy = doc("b", 150, content_hash="hash-shared")
    result = check_corpus(
        manifest(first, copy), FixtureRule(), execution_mode="fixture"
    )
    assert result.passed and result.total_counted_pages == 150


def test_unapproved_rule_blocks_live_indexing():
    corpus = manifest(doc("a", 10))
    assert check_corpus(corpus, FixtureRule(), execution_mode="fixture").passed
    live = check_corpus(corpus, FixtureRule(), execution_mode="live")
    assert codes(live) == {(Rejection.RULE_NOT_APPROVED, None)}
    with pytest.raises(CorpusGateError):
        require_indexable(live)

    other = check_corpus(
        corpus, FixtureRule(rule_version="other"), execution_mode="fixture"
    )
    assert codes(other) == {(Rejection.RULE_VERSION_MISMATCH, None)}


@pytest.mark.parametrize(
    "changes",
    [
        dict(approved=True, reviewer=None),
        dict(scope="company", candidate_ids=[]),
        dict(local_path="/abs/path.pdf"),
        dict(local_path="data/local/../secret.pdf"),
        dict(local_path="data/manifests/a.pdf"),
        dict(local_path="https://example.invalid/a.pdf"),
        dict(
            included_page_ranges=[
                {"schema_version": SCHEMA, "start": 1, "end": 5},
                {"schema_version": SCHEMA, "start": 5, "end": 8},
            ]
        ),
        dict(included_page_ranges=[{"schema_version": SCHEMA, "start": 1, "end": 11}]),
        dict(included_page_ranges=[]),
    ],
)
def test_manifest_document_shape_is_rejected(changes):
    with pytest.raises(ValidationError):
        doc("x", 10, **changes)


def test_manifest_is_immutable_and_rejects_duplicates():
    corpus = manifest(doc("a", 10))
    with pytest.raises(ValidationError):
        corpus.corpus_version = "changed"
    with pytest.raises(ValidationError):
        manifest(doc("a", 10), doc("a", 5, source_id="src-other"))


def test_replacement_creates_new_version_and_excludes_old_document(tmp_path):
    v1 = manifest(doc("old", 50), doc("keep", 50))
    v2 = next_corpus_version(
        v1,
        corpus_version="corpus-v2",
        remove=["old"],
        add=[doc("new", 60), doc("pending", 10, approved=False, reviewer=None)],
    )
    assert v1.corpus_version == "corpus-v1" and len(v1.documents) == 2
    assert v2.previous_corpus_version == "corpus-v1"
    assert manifest_hash(v1) != manifest_hash(v2)

    result = check_corpus(v2, FixtureRule(), execution_mode="fixture")
    assert result.indexable_document_ids == ("keep", "new")
    assert codes(result) == {(Rejection.DOCUMENT_NOT_APPROVED, "pending")}

    with pytest.raises(ValueError):
        next_corpus_version(v1, corpus_version="corpus-v1")
    with pytest.raises(ValueError):
        next_corpus_version(v1, corpus_version="corpus-v3", remove=["missing"])


def test_store_freezes_version(tmp_path):
    store = ManifestStore(tmp_path / "data/manifests")
    v1 = manifest(doc("a", 10))
    digest = store.save(v1)
    assert store.save(v1) == digest
    assert store.load("corpus-v1", expected_hash=digest) == v1

    changed = manifest(doc("a", 11))
    with pytest.raises(FileExistsError):
        store.save(changed)
    with pytest.raises(ValueError):
        store.load("corpus-v1", expected_hash=manifest_hash(changed))
    with pytest.raises(ValueError):
        store.path("../escape")


def test_index_inputs_are_compared_with_approved_manifest():
    corpus = manifest(doc("a", 10), doc("b", 10))
    result = check_corpus(corpus, FixtureRule(), execution_mode="fixture")
    assert compare_index_inputs(corpus, result, {"a": "hash-a", "b": "hash-b"}).matches

    diff = compare_index_inputs(
        corpus, result, {"a": "hash-a-edited", "stale": "hash-stale"}
    )
    assert diff.missing == ("b",)
    assert diff.unexpected == ("stale",)
    assert diff.hash_mismatch == ("a",)

    with pytest.raises(ValueError):
        compare_index_inputs(manifest(doc("a", 10)), result, {})


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
    v1 = manifest(doc("old", 10), doc("keep", 10))
    v2 = next_corpus_version(
        v1, corpus_version="corpus-v2", remove=["old"], add=[doc("new", 10)]
    )
    result = check_corpus(v2, FixtureRule(), execution_mode="fixture")
    allowed = allowed_source_ids(v2, result, "co-a")
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
        doc("mine", 10, scope="company", candidate_ids=["co-a"]),
        doc("other", 10, scope="company", candidate_ids=["co-b"]),
        doc("industry", 10),
    )
    result = check_corpus(corpus, FixtureRule(), execution_mode="fixture")
    assert allowed_source_ids(corpus, result, "co-a") == ["src-industry", "src-mine"]

    failed = check_corpus(corpus, FixtureRule(), execution_mode="live")
    with pytest.raises(CorpusGateError):
        allowed_source_ids(corpus, failed, "co-a")
