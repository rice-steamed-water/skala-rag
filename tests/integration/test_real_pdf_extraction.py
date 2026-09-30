"""승인·hash 확인된 로컬 원문이 있을 때만 실제 PDF를 대조한다. 네트워크 없음."""

import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from skala_rag.agents.evidence_extraction import rag_segment
from skala_rag.contracts import RetrievalBundle, RetrievalRecord, Source
from skala_rag.rag.corpus import CorpusManifest, check_corpus
from skala_rag.rag.extraction import PageChunkSettings, extract_local_document

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/manifests/issue49-real-validation-v1.json"
SOURCES = ROOT / "data/manifests/issue49-source-snapshots.json"


@pytest.mark.parametrize(
    "document_id,pages", [("2410.24164v4", 17), ("2504.16054v1", 19)]
)
def test_real_approved_pdf_pages_and_provenance(document_id, pages):
    manifest = CorpusManifest.model_validate_json(MANIFEST.read_text())
    doc = manifest.document(document_id)
    if not (ROOT / doc.local_path).is_file():
        pytest.skip("승인 원문은 git에서 제외되어 로컬 준비 시만 검증합니다")
    source = Source.model_validate(json.loads(SOURCES.read_text())[doc.source_id])
    arguments = dict(
        root=ROOT,
        schema_version="extraction-v1",
        settings=PageChunkSettings(
            max_characters=12000,
            overlap=0,
            tokenizer="none-page-atomic",
            document_kind="technical_whitepaper",
            version="issue49-real-v2",
            section_mode="pdf-outline",
        ),
        sections_by_page={},
        embedding_model="not-embedded",
        embedding_revision="not-embedded",
    )
    result = extract_local_document(manifest, document_id, source, **arguments)
    again = extract_local_document(manifest, document_id, source, **arguments)
    assert result.status == "partial"
    assert result.page_count == pages and len(result.chunks) == pages
    assert result == again
    reader = PdfReader(ROOT / doc.local_path)
    for number, (chunk, page) in enumerate(zip(result.chunks, reader.pages), 1):
        assert chunk.page_start == chunk.page_end == number
        assert chunk.locator == source.url + f"#page={number}"
        assert chunk.text == page.extract_text(
            extraction_mode="layout", layout_mode_strip_rotated=False
        )
        bundle = RetrievalBundle(
            schema_version="extraction-v1",
            sources={source.source_id: source},
            chunks=[chunk],
        )
        record = RetrievalRecord(
            schema_version="extraction-v1",
            retrieval_id=f"real-check-{number}",
            run_id="issue49-validation",
            candidate_id="co-physical-intelligence",
            tool_name="local-extraction-validation",
            arguments_without_secrets={},
            started_at=source.retrieved_at,
            finished_at=source.retrieved_at,
            status="ok",
            source_ids=[source.source_id],
            chunk_ids=[chunk.chunk_id],
            evidence_ids=[],
            cache_hit=False,
        )
        assert (
            rag_segment(chunk, bundle, record, schema_version="extraction-v1").text
            == chunk.text
        )
    assert check_corpus(manifest).passed is False
    if document_id == "2410.24164v4":
        text = result.chunks[15].text
        for phrase in [
            "TABLE I",
            "inference time",
            "14 ms",
            "32 ms",
            "27 ms",
            "13 ms",
            "73 ms",
            "86 ms",
        ]:
            assert phrase in text


@pytest.mark.parametrize("document_id", ["2410.24164v4", "2504.16054v1"])
def test_reviewed_text_scope_matches_real_pdf_and_preserves_full_partial(document_id):
    from skala_rag.rag.reviewed_extraction import verify_text_review

    path = ROOT / "data/manifests/issue52-reviewed-text-v1.json"
    if not path.exists():
        pytest.skip("#52 text scope metadata not present")
    manifest = CorpusManifest.model_validate_json(path.read_text())
    doc = manifest.document(document_id)
    if not (ROOT / doc.local_path).is_file():
        pytest.skip("승인 원문은 git 제외; 로컬 준비 시 검증")
    source = Source.model_validate(json.loads(SOURCES.read_text())[doc.source_id])
    review = doc.text_index_review
    result = extract_local_document(
        manifest,
        document_id,
        source,
        root=ROOT,
        schema_version=manifest.schema_version,
        settings=PageChunkSettings(**review.extraction_settings),
        sections_by_page={},
        embedding_model="not-embedded",
        embedding_revision="not-embedded",
    )
    verify_text_review(result, review)
    assert doc.extraction_status == result.status == "partial"
    assert check_corpus(manifest).passed
    assert not check_corpus(
        CorpusManifest.model_validate_json(MANIFEST.read_text())
    ).passed
