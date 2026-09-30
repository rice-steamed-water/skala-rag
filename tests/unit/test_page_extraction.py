"""네트워크 없는 가상 PDF bytes로 페이지·표 맥락·승인 경계를 검증한다."""

from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from skala_rag.contracts import Source
from skala_rag.rag.corpus import ManifestDocument
from skala_rag.rag.extraction import PageChunkSettings, extract_pdf
from skala_rag.tools.source_fetch import content_hash


def pdf_bytes(pages):
    writer = PdfWriter()
    for lines in pages:
        page = writer.add_blank_page(width=600, height=800)
        if lines is not None:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/F1"): writer._add_object(font)}
                    )
                }
            )
            commands = ["BT /F1 12 Tf 50 740 Td"]
            for line in lines:
                escaped = (
                    line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                )
                commands.append(f"({escaped}) Tj 0 -20 Td")
            commands.append("ET")
            stream = DecodedStreamObject()
            stream.set_data("\n".join(commands).encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture
def context():
    content = pdf_bytes(
        [
            [
                "SYNTHETIC PRODUCT MANUAL",
                "Table 1: Bench test",
                "Unit: ms",
                "Mode   Latency",
                "A      10",
                "Note: synthetic measurement, not real.",
            ],
            ["SYNTHETIC INTEGRATION", "Sensor -> controller -> robot."],
        ]
    )
    digest = content_hash(content)
    document = ManifestDocument.model_validate(
        dict(
            schema_version="fixture-1",
            document_id="doc-fixture",
            source_id="src-fixture",
            local_path="fixture://manual.pdf",
            content_hash=digest,
            title="가상 제품 문서",
            language="en",
            permission_note="직접 만든 가상 PDF",
            candidate_ids=["co-fixture"],
            scope="company",
            extraction_status="pending",
            approved=True,
            reviewer="fixture-reviewer",
        ),
        context={"execution_mode": "fixture"},
    )
    source = Source.model_validate(
        dict(
            schema_version="fixture-1",
            source_id="src-fixture",
            title="가상 제품 문서",
            source_kind="report",
            local_path="fixture://manual.pdf",
            content_hash=digest,
            language="en",
            retrieved_at="2026-09-30T00:00:00Z",
            access_notes="실제 자료 아님",
            bibliographic_metadata={},
        ),
        context={"execution_mode": "fixture"},
    )
    return content, document, source


def settings(**updates):
    payload = dict(
        max_characters=10000,
        overlap=0,
        tokenizer="none-page-atomic",
        document_kind="product_document",
        version="fixture-v1",
    )
    payload.update(updates)
    return PageChunkSettings(**payload)


def extract(context, **updates):
    content, document, source = context
    payload = dict(
        corpus_version="corpus-fixture",
        schema_version="fixture-1",
        settings=settings(),
        sections_by_page={1: "가상 벤치 표", 2: "가상 통합"},
        embedding_model="not-embedded",
        embedding_revision="not-embedded",
        execution_mode="fixture",
    )
    payload.update(updates)
    return extract_pdf(content, document, source, **payload)


def test_page_locator_and_table_context_preserved(context):
    result = extract(context)
    assert result.status == "ok"
    assert result.page_count == 2
    assert len(result.chunks) == 2
    first = result.chunks[0]
    assert first.page_start == first.page_end == 1
    assert first.section == "가상 벤치 표"
    assert first.locator == "fixture://manual.pdf#page=1"
    for phrase in ["Table 1", "Unit: ms", "Mode", "Latency", "10", "Note: synthetic"]:
        assert phrase in first.text
    assert first.candidate_ids == ["co-fixture"]
    assert first.source_id == context[1].source_id
    assert first.corpus_version == "corpus-fixture"
    assert first.language == "en"


def test_ids_are_deterministic_and_configuration_sensitive(context):
    first = extract(context)
    assert first == extract(context)
    changed = extract(context, settings=settings(version="fixture-v2"))
    assert first.chunks[0].chunk_id != changed.chunks[0].chunk_id
    assert first.chunks[0].chunk_id != first.chunks[1].chunk_id


def test_oversized_page_kept_atomic_and_marked_partial(context):
    result = extract(context, settings=settings(max_characters=5))
    assert result.status == "partial"
    assert len(result.chunks) == 2
    assert any(issue.code == "PAGE_EXCEEDS_CHARACTER_LIMIT" for issue in result.issues)
    assert "Note: synthetic" in result.chunks[0].text


def test_blank_page_not_success(context):
    content = pdf_bytes([None])
    _, document, source = context
    document = document.model_copy(update={"content_hash": content_hash(content)})
    source = source.model_copy(update={"content_hash": content_hash(content)})
    result = extract((content, document, source), sections_by_page={})
    assert result.status == "failed"
    assert result.chunks == ()
    assert result.issues[0].code == "NO_EXTRACTABLE_TEXT"


@pytest.mark.parametrize("problem", ["unapproved", "hash", "source", "path", "live"])
def test_unapproved_or_mismatched_snapshot_rejected(context, problem):
    content, document, source = context
    updates = {}
    if problem == "unapproved":
        document = document.model_copy(update={"approved": False})
    elif problem == "hash":
        content += b"changed"
    elif problem == "source":
        source = source.model_copy(update={"source_id": "other"})
    elif problem == "path":
        source = source.model_copy(update={"local_path": "fixture://other.pdf"})
    else:
        updates["execution_mode"] = "live"
    with pytest.raises(ValueError):
        extract((content, document, source), **updates)


def test_corrupt_pdf_not_success(context):
    content, document, source = context
    content = b"not-pdf"
    document = document.model_copy(update={"content_hash": content_hash(content)})
    source = source.model_copy(update={"content_hash": content_hash(content)})
    result = extract((content, document, source))
    assert result.status == "failed"
    assert result.chunks == ()
    assert result.issues[0].code == "PDF_UNREADABLE"


@pytest.mark.parametrize(
    "updates",
    [
        {"overlap": 1},
        {"max_characters": 0},
        {"tokenizer": "unknown"},
        {"document_kind": "pptx"},
    ],
)
def test_unsupported_configuration_rejected(updates):
    with pytest.raises(ValueError):
        settings(**updates)


def test_partial_pdf_preserves_original_page_number(context):
    content = pdf_bytes([None, ["SYNTHETIC PAGE TWO"]])
    _, document, source = context
    document = document.model_copy(update={"content_hash": content_hash(content)})
    source = source.model_copy(update={"content_hash": content_hash(content)})
    result = extract(
        (content, document, source), sections_by_page={2: "가상 두번째 페이지"}
    )
    assert result.status == "partial"
    assert len(result.chunks) == 1
    assert result.chunks[0].page_start == 2
    assert result.chunks[0].locator.endswith("#page=2")
    assert result.issues[0].page == 1
