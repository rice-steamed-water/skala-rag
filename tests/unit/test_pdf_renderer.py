"""T17: 실제 가상 PDF의 분량·인용·실패 경계. 실모델 검증 아님."""

from pathlib import Path

import pytest
from pypdf import PdfReader

from skala_rag.contracts import ReportDraft, ReportJudgement, ValidationResult
from skala_rag.reporting.pdf import PDFRenderer, load_pdf_profile
from skala_rag.reporting.validator import artifact_hash

ROOT = Path(__file__).resolve().parents[2]


def make_draft(
    summary="가상 fixture의 HOLD 판정과 예시 점수 65.00. [@evidence:ev-fixture]",
    body="가상 조사 범위이며 실측이 아닙니다.",
):
    return ReportDraft(
        schema_version="fixture-pdf-v1",
        report_id="report-fixture",
        context_id="ctx-fixture",
        revision=0,
        markdown=f"""# SUMMARY
{summary}
# COMPANY & TEAM
가상 로봇 기업과 가상 팀입니다.
# TECHNOLOGY
{body}
# MARKET
가상 시장 자료이며 실제 시장 규모·수요는 검증하지 않았다.
# INVESTMENT ASSESSMENT & RISKS
| 항목 | 값 |
| --- | --- |
| 점수 | 65.00 |
| 판정 | HOLD |
| 한계 | 근거 부족은 Missing으로 보존 |
# REFERENCE
[@source:src-fixture] 가상 문서. https://example.invalid/robotics/technical-document/fixture-validation?version=fixture-v1&source=src-fixture&corpus=corpus-fixture-v1
""",
        cited_evidence_ids=["ev-fixture"],
        reference_source_ids=["src-fixture"],
        limitations=["실측 아님"],
    )


def proof(draft):
    digest = artifact_hash(draft)
    validation = ValidationResult(
        schema_version=draft.schema_version,
        valid=True,
        context_id=draft.context_id,
        checks={"fixture": True},
        errors=[],
        artifact_hash=digest,
    )
    judge = ReportJudgement(
        schema_version=draft.schema_version,
        verdict="pass",
        context_id=draft.context_id,
        findings=[],
        revision_instructions=[],
        judged_artifact_hash=digest,
    )
    return validation, judge


def renderer(tmp_path, proofs=proof, mode="fixture"):
    return PDFRenderer(
        profile=load_pdf_profile(ROOT / "configs/pdf.layout.v1.json"),
        output_dir=tmp_path,
        proof=proofs,
        execution_mode=mode,
    )


def test_fixture_real_pdf_measured_and_citations_preserved(tmp_path):
    draft = make_draft()
    result = renderer(tmp_path)(draft, "pdf-layout-v1")
    assert result.errors == []
    assert result.page_count == 1
    assert result.layout_measurements["pdf_verified"] is True
    assert result.layout_measurements["final_allowed"] is False
    assert result.layout_measurements["summary_fraction"] <= 0.5
    assert result.layout_measurements["context_id"] == draft.context_id
    text = PdfReader(result.artifact_path).pages[0].extract_text()
    for content in [
        "가상 로봇",
        "65.00",
        "HOLD",
        "[@evidence:ev-fixture]",
        "[@source:src-fixture]",
    ]:
        assert content in text


def test_summary_exceed_half_page_is_revise_and_not_final(tmp_path):
    draft = make_draft(
        summary=("가상 SUMMARY 문장입니다. " * 6 + "\n") * 25 + "[@evidence:ev-fixture]"
    )
    result = renderer(tmp_path)(draft, "pdf-layout-v1")
    assert result.artifact_path is not None
    assert "PDF_SUMMARY" in [e.code for e in result.errors]
    assert result.layout_measurements["action"] == "revise"
    assert result.layout_measurements["final_allowed"] is False


def test_more_than_five_pages_is_rejected(tmp_path):
    draft = make_draft(body=("가상 긴 본문입니다. " * 15 + "\n") * 200)
    result = renderer(tmp_path)(draft, "pdf-layout-v1")
    assert result.page_count > 5
    assert "PDF_PAGE_COUNT" in [e.code for e in result.errors]
    assert result.layout_measurements["final_allowed"] is False


def test_stale_upstream_pass_rejected_before_pdf_created(tmp_path):
    def stale(draft):
        validation, judge = proof(draft)
        judge.judged_artifact_hash = "wrong"
        return validation, judge

    result = renderer(tmp_path, stale)(make_draft(), "pdf-layout-v1")
    assert result.errors[0].code == "PDF_UPSTREAM_NOT_VALIDATED"
    assert list(tmp_path.iterdir()) == []


def test_missing_citation_in_pdf_blocks_verification(tmp_path):
    draft = make_draft(summary="가상 fixture입니다.")
    result = renderer(tmp_path)(draft, "pdf-layout-v1")
    assert "PDF_CITATIONS" in [e.code for e in result.errors]
    assert result.layout_measurements["pdf_verified"] is False


def test_html_and_remote_image_are_not_executed(tmp_path):
    draft = make_draft(
        body='<img src="https://example.invalid/private"> ![그림](https://example.invalid/image.png)'
    )
    result = renderer(tmp_path)(draft, "pdf-layout-v1")
    assert result.errors == []
    assert "<img" in PdfReader(result.artifact_path).pages[0].extract_text()


def test_render_exception_is_fail_without_retry(tmp_path, monkeypatch):
    calls = []

    def broken(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("private error")

    monkeypatch.setattr("skala_rag.reporting.pdf.SimpleDocTemplate.build", broken)
    result = renderer(tmp_path)(make_draft(), "pdf-layout-v1")
    assert result.errors[0].code == "PDF_RENDER_FAILED"
    assert result.layout_measurements["action"] == "fail"
    assert calls == [1]
    assert "private error" not in result.errors[0].message


def test_wrong_template_and_no_overwrite(tmp_path):
    render = renderer(tmp_path)
    assert render(make_draft(), "wrong").errors[0].code == "PDF_PROFILE_NOT_APPROVED"
    first = render(make_draft(), "pdf-layout-v1")
    before = Path(first.artifact_path).read_bytes()
    assert (
        render(make_draft(), "pdf-layout-v1").errors[0].code
        == "PDF_ARTIFACT_ALREADY_EXISTS"
    )
    assert Path(first.artifact_path).read_bytes() == before


def test_saved_pdf_layout_validation_and_tamper_rejection(tmp_path):
    from types import SimpleNamespace

    from skala_rag.reporting.pdf import PDFArtifactError, PDFLayoutValidator

    draft = make_draft()
    result = renderer(tmp_path)(draft, "pdf-layout-v1")
    context = SimpleNamespace(context_id=draft.context_id)
    validator = PDFLayoutValidator()
    verified = validator(draft, context, result)
    assert verified.valid and verified.checks["pdf_verified"]
    path = Path(result.artifact_path)
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(PDFArtifactError, match="hash"):
        validator(draft, context, result)


def _physical_pdf(path, headings, *, reverse_stream=False):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas

    from skala_rag.reporting.pdf import SECTIONS

    canvas = Canvas(str(path), pagesize=A4)
    canvas.setFont("Helvetica", 10.5)
    for index, mention in enumerate((*SECTIONS, "LOW_MARKET; LOW_TECHNOLOGY")):
        canvas.drawString(60, 800 - index * 12, mention)
    canvas.drawString(60, 700, "[@evidence:ev-fixture] [@source:src-fixture]")
    canvas.setFont("Helvetica-Bold", 14)
    positions = list(enumerate(headings))
    for index, heading in reversed(positions) if reverse_stream else positions:
        canvas.drawString(60, 670 - index * 25, heading)
    canvas.save()


def _physical_headings(shape):
    from skala_rag.reporting.pdf import SECTIONS

    headings: list[str] = list(SECTIONS)
    if shape == "missing":
        del headings[2]
    elif shape == "wrong":
        headings[2] = "TECHNOLOGIES"
    elif shape == "duplicate":
        headings.insert(3, headings[2])
    elif shape == "combined":
        headings[2] = "TECHNOLOGY & MARKET"
    elif shape == "combined-extra":
        headings.insert(3, "TECHNOLOGY & MARKET")
    elif shape == "order":
        headings[2], headings[3] = headings[3], headings[2]
    elif shape == "reference-not-last":
        headings[-2], headings[-1] = headings[-1], headings[-2]
    return headings


@pytest.mark.parametrize(
    "shape",
    [
        "missing",
        "wrong",
        "duplicate",
        "combined",
        "combined-extra",
        "order",
        "reference-not-last",
        "valid",
    ],
)
def test_saved_pdf_sections_require_physical_headings(tmp_path, shape):
    import hashlib
    from types import SimpleNamespace

    from skala_rag.reporting.pdf import PDFLayoutValidator

    draft = make_draft()
    render = renderer(tmp_path)(draft, "pdf-layout-v1")
    assert render.artifact_path is not None
    path = Path(render.artifact_path)
    _physical_pdf(path, _physical_headings(shape), reverse_stream=True)
    # Match actual saved bytes: this exercises sections, not hash rejection.
    render.layout_measurements["artifact_hash"] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    result = PDFLayoutValidator()(
        draft, SimpleNamespace(context_id=draft.context_id), render
    )
    assert result.checks["sections"] is (shape == "valid")
    assert result.valid is (shape == "valid")
    assert all(
        result.checks[name]
        for name in ("summary", "bounds", "page_size", "citations", "page_count")
    )
    if shape != "valid":
        assert [error.code for error in result.errors] == ["PDF_SECTIONS"]
        assert result.checks["action"] == "revise"
        assert not result.checks["pdf_verified"]


def test_renderer_checks_actual_pdf_headings(tmp_path, monkeypatch):
    def build(document, *args, **kwargs):
        _physical_pdf(document.filename, _physical_headings("combined-extra"))

    monkeypatch.setattr("skala_rag.reporting.pdf.SimpleDocTemplate.build", build)
    result = renderer(tmp_path)(make_draft(), "pdf-layout-v1")
    assert not result.layout_measurements["checks"]["sections"]
    assert "PDF_SECTIONS" in [error.code for error in result.errors]
    assert not result.layout_measurements["pdf_verified"]
    assert not result.layout_measurements["final_allowed"]


def test_explicit_document_title_remains_supported(tmp_path):
    from types import SimpleNamespace

    from skala_rag.reporting.pdf import PDFLayoutValidator

    draft = make_draft()
    draft = draft.model_copy(
        update={"markdown": "# Fixture report\n\n" + draft.markdown}
    )
    render = renderer(tmp_path)(draft, "pdf-layout-v1")
    assert render.errors == []
    validation = PDFLayoutValidator()(
        draft, SimpleNamespace(context_id=draft.context_id), render
    )
    assert validation.valid and validation.checks["sections"]


def test_layout_draft_revision_mismatch_is_fatal(tmp_path):
    from types import SimpleNamespace

    from skala_rag.reporting.pdf import PDFArtifactError, PDFLayoutValidator

    draft = make_draft()
    result = renderer(tmp_path)(draft, "pdf-layout-v1")
    draft.revision = 1
    with pytest.raises(PDFArtifactError):
        PDFLayoutValidator()(
            draft, SimpleNamespace(context_id=draft.context_id), result
        )


def test_font_hash_mismatch_remains_fatal_without_artifact(tmp_path):
    render = renderer(tmp_path)
    render.profile = render.profile.model_copy(update={"regular_sha256": "0" * 64})
    result = render(make_draft(), "pdf-layout-v1")
    assert result.errors[0].code == "PDF_FONT_HASH_MISMATCH"
    assert result.layout_measurements["final_allowed"] is False
    assert list(tmp_path.iterdir()) == []


def test_missing_font_glyph_remains_fatal_without_artifact(tmp_path):
    result = renderer(tmp_path)(make_draft(body="가상 관측 😀"), "pdf-layout-v1")
    assert result.errors[0].code == "PDF_FONT_GLYPH_MISSING"
    assert result.layout_measurements["final_allowed"] is False
    assert list(tmp_path.iterdir()) == []


def test_pdf_parser_accepts_only_approved_six_section_order():
    from tests.unit.test_v3_report_pipeline import Stub, context

    from skala_rag.reporting.pdf import _blocks
    from skala_rag.reporting.v3_pipeline import ReportGeneratorV3

    draft = ReportGeneratorV3(Stub())(context(), [])
    headings = [
        section for section, kind, _ in _blocks(draft.markdown) if kind == "heading"
    ]
    assert headings == [
        "SUMMARY",
        "COMPANY & TEAM",
        "TECHNOLOGY",
        "MARKET",
        "INVESTMENT ASSESSMENT & RISKS",
        "REFERENCE",
    ]


def test_pdf_parser_rejects_combined_heading_even_with_all_six_sections():
    from tests.unit.test_v3_report_pipeline import Stub, context

    from skala_rag.reporting.pdf import _blocks
    from skala_rag.reporting.v3_pipeline import ReportGeneratorV3

    draft = ReportGeneratorV3(Stub())(context(), [])
    marked = draft.markdown.replace(
        "## TECHNOLOGY", "## TECHNOLOGY & MARKET\n\nCombined body\n\n## TECHNOLOGY"
    )
    with pytest.raises(ValueError):
        _blocks(marked)
