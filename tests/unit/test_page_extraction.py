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


def test_text_form_is_not_falsely_reported_as_missing_image():
    from skala_rag.rag.extraction import _contains_visual

    resources = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject(
                {
                    NameObject("/Fm"): DictionaryObject(
                        {NameObject("/Subtype"): NameObject("/Form")}
                    )
                }
            )
        }
    )
    assert _contains_visual(resources) is False
    resources["/XObject"]["/Fm"][NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject(
                {
                    NameObject("/Im"): DictionaryObject(
                        {NameObject("/Subtype"): NameObject("/Image")}
                    )
                }
            )
        }
    )
    assert _contains_visual(resources) is True


def test_outline_section_hierarchy_and_inheritance(context):
    from pypdf import PdfReader

    content, document, source = context
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(BytesIO(content)))
    parent = writer.add_outline_item("Integration", 0)
    writer.add_outline_item("Control loop", 1, parent=parent)
    buffer = BytesIO()
    writer.write(buffer)
    content = buffer.getvalue()
    document = document.model_copy(update={"content_hash": content_hash(content)})
    source = source.model_copy(update={"content_hash": content_hash(content)})
    result = extract(
        (content, document, source),
        settings=settings(section_mode="pdf-outline"),
        sections_by_page={},
    )
    assert result.status == "ok"
    assert result.chunks[0].section == "Integration"
    assert result.chunks[1].section == "Integration / Control loop"
    override = extract(
        (content, document, source),
        settings=settings(section_mode="pdf-outline"),
        sections_by_page={2: "검토자가 지정한 제목"},
    )
    assert override.chunks[1].section == "검토자가 지정한 제목"


def test_approved_local_manifest_and_path_boundary(context, tmp_path):
    from skala_rag.rag.corpus import CorpusManifest
    from skala_rag.rag.extraction import extract_local_document

    content, document, source = context
    path = tmp_path / "data/local/manual.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    document = ManifestDocument.model_validate(
        dict(document.model_dump(mode="json"), local_path="data/local/manual.pdf")
    )
    source = Source.model_validate(
        dict(source.model_dump(mode="json"), local_path="data/local/manual.pdf")
    )
    manifest = CorpusManifest(
        schema_version="fixture-1",
        corpus_version="corpus-fixture",
        documents=(document,),
    )
    arguments = dict(
        root=tmp_path,
        schema_version="fixture-1",
        settings=settings(),
        sections_by_page={},
        embedding_model="not-embedded",
        embedding_revision="not-embedded",
    )
    result = extract_local_document(manifest, document.document_id, source, **arguments)
    assert result.status == "ok"
    assert result.chunks[0].locator == "data/local/manual.pdf#page=1"
    external = tmp_path / "outside.pdf"
    external.write_bytes(content)
    # 생성한 임시 PDF도 data/local 밖으로 연결된 symlink로 읽지 못한다.
    link = tmp_path / "data/local/link.pdf"
    link.symlink_to(external)
    changed = document.model_copy(update={"local_path": "data/local/link.pdf"})
    manifest = CorpusManifest(
        schema_version="fixture-1",
        corpus_version="corpus-fixture",
        documents=(changed,),
    )
    with pytest.raises(ValueError, match="data/local"):
        extract_local_document(manifest, document.document_id, source, **arguments)


def test_extraction_chunk_consumed_by_real_rag_segment(context):
    from skala_rag.agents.evidence_extraction import rag_segment
    from skala_rag.contracts import RetrievalBundle, RetrievalRecord

    result = extract(context)
    chunk = result.chunks[0]
    bundle = RetrievalBundle.model_validate(
        dict(
            schema_version="fixture-1",
            chunks=[chunk],
            sources={context[2].source_id: context[2]},
        ),
        context={"execution_mode": "fixture"},
    )
    record = RetrievalRecord(
        schema_version="fixture-1",
        retrieval_id="retrieval-fixture",
        run_id="run-fixture",
        candidate_id="co-fixture",
        tool_name="fixture-search",
        arguments_without_secrets={},
        started_at="2026-09-30T00:00:00Z",
        finished_at="2026-09-30T00:00:01Z",
        status="ok",
        source_ids=[chunk.source_id],
        chunk_ids=[chunk.chunk_id],
        evidence_ids=[],
        cache_hit=False,
    )
    segment = rag_segment(chunk, bundle, record, schema_version="fixture-1")
    assert segment.locator == chunk.locator
    assert segment.text == chunk.text
    assert segment.provenance.chunk_id == chunk.chunk_id


def test_runner_writes_only_local_outputs_and_retains_metadata(context, tmp_path):
    import json
    from dataclasses import asdict

    from skala_rag.rag.corpus import CorpusManifest
    from skala_rag.rag.extraction_runner import run_extraction

    content, document, source = context
    path = tmp_path / "data/local/manual.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    document = ManifestDocument.model_validate(
        dict(document.model_dump(mode="json"), local_path="data/local/manual.pdf")
    )
    source = Source.model_validate(
        dict(source.model_dump(mode="json"), local_path=document.local_path)
    )
    manifest = CorpusManifest(
        schema_version="fixture-1",
        corpus_version="corpus-fixture",
        documents=(document,),
    )
    files = {}
    for name, payload in [
        ("manifest", manifest.model_dump(mode="json")),
        ("source", source.model_dump(mode="json")),
        ("settings", asdict(settings(section_mode="pdf-outline"))),
        ("sections", {}),
    ]:
        files[name] = tmp_path / f"{name}.json"
        files[name].write_text(json.dumps(payload))
    arguments = dict(
        root=tmp_path,
        manifest_path=files["manifest"],
        source_path=files["source"],
        settings_path=files["settings"],
        sections_path=files["sections"],
        document_id=document.document_id,
        embedding_model="not-embedded",
        embedding_revision="not-embedded",
    )
    output = tmp_path / "outputs/fixture/extraction.json"
    result = run_extraction(**arguments, output_path=output)
    payload = json.loads(output.read_text())
    assert payload["status"] == result.status == "ok"
    assert payload["content_hash"] == document.content_hash
    assert payload["chunks"][0]["page_start"] == 1
    assert payload["settings"]["section_mode"] == "pdf-outline"
    with pytest.raises(FileExistsError):
        run_extraction(**arguments, output_path=output)
    with pytest.raises(ValueError, match="outputs"):
        run_extraction(**arguments, output_path=tmp_path / "wrong.json")


def test_rotated_text_warning_is_recorded_in_result(context):
    from pypdf import PdfReader

    content, document, source = context
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(BytesIO(content)))
    page = writer.pages[0]
    stream = DecodedStreamObject()
    stream.set_data(
        page["/Contents"].get_object().get_data()
        + b"\nBT /F1 8 Tf 0 1 -1 0 25 700 Tm (SIDE HEADER) Tj ET\n"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = BytesIO()
    writer.write(buffer)
    content = buffer.getvalue()
    document = document.model_copy(update={"content_hash": content_hash(content)})
    source = source.model_copy(update={"content_hash": content_hash(content)})
    result = extract((content, document, source))
    assert result.status == "partial"
    assert any(
        issue.code == "TEXT_LAYOUT_WARNING" and issue.page == 1
        for issue in result.issues
    )
    assert "SIDE HEADER" in result.chunks[0].text
