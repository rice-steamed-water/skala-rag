"""#175: 실제 Chromium HTML→PDF. fixture 검증이며 실측 아님."""

import hashlib
import http.server
import threading
from pathlib import Path

import pytest
from pypdf import PdfReader
from tests.unit.test_v3_report_pipeline import context

from skala_rag.fixture_reporting import FixtureReportLLM
from skala_rag.reporting import html_pdf
from skala_rag.reporting.html_pdf import HTMLPDFLayoutValidator, HTMLPDFRenderer
from skala_rag.reporting.pdf import PDFArtifactError
from skala_rag.reporting.v3_pipeline import (
    ReportGeneratorV3,
    SemanticJudgeV3,
    validate_report_v3,
)


def _chromium_missing():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        return not Path(p.chromium.executable_path).exists()


pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        _chromium_missing(),
        reason="Playwright Chromium 미설치: `uv run playwright install chromium`",
    ),
]


@pytest.fixture(scope="module")
def ctx():
    return context()


def make(ctx, llm=None):
    draft = ReportGeneratorV3(llm or FixtureReportLLM())(ctx, [])
    structural = validate_report_v3(draft, ctx)
    judged = SemanticJudgeV3(FixtureReportLLM())(draft, ctx)
    return draft, structural, judged


def renderer(tmp_path, ctx, structural, judged, mode="fixture"):
    return HTMLPDFRenderer(
        output_dir=tmp_path,
        proof=lambda _: (structural, judged),
        execution_mode=mode,
        context=ctx,
    )


def test_fixture_report_renders_and_validates(tmp_path, ctx):
    draft, structural, judged = make(ctx)
    render = renderer(tmp_path, ctx, structural, judged)(draft, "html-v1")
    assert render.artifact_path, render.errors
    m = render.layout_measurements
    assert m["renderer"] == "playwright-chromium"
    assert m["action"] == "pass" and m["pdf_verified"] and not m["final_allowed"]
    assert render.page_count <= 5 and 0 < m["summary_fraction"] <= 0.5
    reader = PdfReader(render.artifact_path)
    assert all(abs(float(p.mediabox.width) - 595.28) < 0.5 for p in reader.pages)
    text = "".join(p.extract_text() for p in reader.pages).replace(" ", "")
    assert "투자평가·위험" in text and "가상데이터" in text
    html_bytes = Path(m["html_path"]).read_bytes()
    assert m["html_hash"] == hashlib.sha256(html_bytes).hexdigest()
    result = HTMLPDFLayoutValidator()(draft, ctx, render)
    assert result.valid and result.checks["action"] == "pass"


def test_stale_proof_refused_before_render(tmp_path, ctx):
    draft, structural, judged = make(ctx)
    stale = structural.model_copy(update={"artifact_hash": "old"})
    render = renderer(tmp_path, ctx, stale, judged)(draft, "html-v1")
    assert render.artifact_path is None
    assert render.errors[0].code == "PDF_UPSTREAM_NOT_VALIDATED"
    assert list(tmp_path.iterdir()) == []


def test_existing_file_not_overwritten(tmp_path, ctx):
    draft, structural, judged = make(ctx)
    r = renderer(tmp_path, ctx, structural, judged)
    first = r(draft, "html-v1")
    before = Path(first.artifact_path).read_bytes()
    second = r(draft, "html-v1")
    assert second.artifact_path is None
    assert second.errors[0].code == "PDF_ARTIFACT_ALREADY_EXISTS"
    assert Path(first.artifact_path).read_bytes() == before


@pytest.mark.parametrize("target", ["html_path", "artifact_path"])
def test_tamper_raises(tmp_path, ctx, target):
    draft, structural, judged = make(ctx)
    render = renderer(tmp_path, ctx, structural, judged)(draft, "html-v1")
    path = (
        render.artifact_path
        if target == "artifact_path"
        else render.layout_measurements["html_path"]
    )
    with open(path, "ab") as f:
        f.write(b"\n")
    with pytest.raises(PDFArtifactError):
        HTMLPDFLayoutValidator()(draft, ctx, render)


def test_overflow_requests_revision(tmp_path, ctx):
    class Long(FixtureReportLLM):
        def generate(self, *, system, user, output_schema):
            out = super().generate(
                system=system, user=user, output_schema=output_schema
            )
            if hasattr(out, "summary"):
                out = out.model_copy(update={"summary": "긴 요약 문장입니다. " * 400})
            return out

    draft, structural, judged = make(ctx, Long())
    render = renderer(tmp_path, ctx, structural, judged)(draft, "html-v1")
    assert render.artifact_path is None
    m = render.layout_measurements
    assert m["action"] == "revise" and not m["pdf_verified"]
    assert not m["checks"]["summary"] and not m["final_allowed"]
    assert not m.get("html_path")
    assert "PDF_SUMMARY" in [error.code for error in render.errors]
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(PDFArtifactError):
        HTMLPDFLayoutValidator()(draft, ctx, render)


@pytest.mark.parametrize("failure", ["summary", "sections"])
def test_failed_pdf_admission_does_not_publish(tmp_path, ctx, monkeypatch, failure):
    draft, structural, judged = make(ctx)
    r = renderer(tmp_path, ctx, structural, judged)
    real_verify = html_pdf.verify_pdf

    def verify(path, draft):
        assert Path(path).suffix == ".tmp"
        assert not list(tmp_path.glob("*.html"))
        assert not list(tmp_path.glob("*.pdf"))
        checks = dict.fromkeys(html_pdf._NAMES, True)
        checks[failure] = False
        return checks, 1, 0.6 if failure == "summary" else 0.2

    monkeypatch.setattr(html_pdf, "verify_pdf", verify)
    failed = r(draft, "html-v1")
    assert failed.artifact_path is None and failed.page_count is None
    m = failed.layout_measurements
    assert not m.get("html_path") and not m["pdf_verified"] and not m["final_allowed"]
    assert m["action"] == "revise" and m["checks"][failure] is False
    assert m["summary_fraction"] == (0.6 if failure == "summary" else 0.2)
    assert [error.code for error in failed.errors] == ["PDF_" + failure.upper()]
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(html_pdf, "verify_pdf", real_verify)
    retried = r(draft, "html-v1")
    assert retried.artifact_path is not None and retried.errors == []
    assert HTMLPDFLayoutValidator()(draft, ctx, retried).valid


@pytest.mark.parametrize("failure", ["summary", "sections"])
def test_direct_helper_preserves_unpublished_layout_revision(
    tmp_path, ctx, monkeypatch, failure
):
    from skala_rag.reporting.korean_report import build_korean_report_pdf

    draft, structural, judged = make(ctx)

    def verify(path, draft):
        checks = dict.fromkeys(html_pdf._NAMES, True)
        checks[failure] = False
        return checks, 1, 0.6 if failure == "summary" else 0.2

    monkeypatch.setattr(html_pdf, "verify_pdf", verify)
    render, validation = build_korean_report_pdf(
        ctx, draft, structural, judged, tmp_path, "fixture"
    )
    assert render.artifact_path is None and not validation.valid
    assert validation.checks["action"] == "revise"
    assert validation.checks[failure] is False
    assert not validation.checks["pdf_verified"]
    assert not validation.checks["final_allowed"]
    assert validation.errors == render.errors
    assert validation.artifact_hash == render.layout_measurements["draft_hash"]
    assert list(tmp_path.iterdir()) == []


def test_second_publish_rename_failure_rolls_back(tmp_path, ctx, monkeypatch):
    draft, structural, judged = make(ctx)
    rename = Path.rename

    def fail_pdf(path, target):
        if path.name.endswith(".pdf.tmp"):
            raise OSError("second rename failed")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_pdf)
    failed = renderer(tmp_path, ctx, structural, judged)(draft, "html-v1")
    assert failed.artifact_path is None
    assert failed.errors[0].code == "PDF_RENDER_FAILED"
    assert failed.layout_measurements["action"] == "fail"
    assert list(tmp_path.iterdir()) == []


def test_network_requests_blocked(tmp_path, ctx, monkeypatch):
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/x.css"
        original = html_pdf.render_report_html

        def hostile(draft, context):
            # CSP를 제거해 CSP가 아니라 route 차단이 요청을 막는지 검증한다.
            html = original(draft, context).replace(
                "Content-Security-Policy", "x-off", 1
            )
            return html.replace("<style>", f"<style>@import url({url});", 1)

        monkeypatch.setattr(html_pdf, "render_report_html", hostile)
        draft, structural, judged = make(ctx)
        render = renderer(tmp_path, ctx, structural, judged)(draft, "html-v1")
        assert render.artifact_path is not None, render.errors
        saved = Path(render.layout_measurements["html_path"]).read_text(
            encoding="utf-8"
        )
        assert f"@import url({url})" in saved and "x-off" in saved
        assert hits == []

        # positive control: route 차단 없이 같은 HTML을 열면 서버에 요청이 닿는다.
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_context().new_page()
                page.set_content(saved, wait_until="load")
            finally:
                browser.close()
        assert hits
    finally:
        server.shutdown()
        server.server_close()


def test_execution_mode_must_match_context(tmp_path, ctx):
    draft, structural, judged = make(ctx)
    other = "live" if ctx.snapshot()["execution_mode"] == "fixture" else "fixture"
    render = renderer(tmp_path, ctx, structural, judged, mode=other)(draft, "html-v1")
    assert render.artifact_path is None
    assert render.errors[0].code == "PDF_UPSTREAM_NOT_VALIDATED"
    assert list(tmp_path.iterdir()) == []


def test_failed_render_leaves_no_artifacts_and_retry_succeeds(
    tmp_path, ctx, monkeypatch
):
    draft, structural, judged = make(ctx)
    r = renderer(tmp_path, ctx, structural, judged)
    real = html_pdf._chromium_pdf

    def boom(*args, **kwargs):
        raise RuntimeError("chromium crashed")

    monkeypatch.setattr(html_pdf, "_chromium_pdf", boom)
    failed = r(draft, "html-v1")
    assert failed.artifact_path is None
    assert failed.errors[0].code == "PDF_RENDER_FAILED"
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(html_pdf, "_chromium_pdf", real)
    assert r(draft, "html-v1").artifact_path


def test_sections_require_whole_heading_lines_in_order(tmp_path, ctx):
    draft, structural, judged = make(ctx)
    render = renderer(tmp_path, ctx, structural, judged)(draft, "html-v1")
    checks, _, _ = html_pdf.verify_pdf(render.artifact_path, draft)
    assert checks["sections"]
    # 제목이 본문 속 부분 문자열로만 있으면 통과하면 안 된다.
    reader = PdfReader(render.artifact_path)
    found = html_pdf._heading_ys(reader)
    assert list(found) == list(html_pdf.TITLES)


def test_missing_browser_fails_without_reportlab_fallback(tmp_path, ctx, monkeypatch):
    import playwright.sync_api as api

    def boom(*args, **kwargs):
        raise RuntimeError("no browser")

    monkeypatch.setattr(api, "sync_playwright", boom)
    draft, structural, judged = make(ctx)
    render = renderer(tmp_path, ctx, structural, judged)(draft, "html-v1")
    assert render.artifact_path is None
    assert render.errors[0].code == "PDF_RENDER_FAILED"
    assert render.layout_measurements["action"] == "fail"
    assert not list(tmp_path.glob("*.pdf"))


def test_research_scoreboard_labels_are_not_section_headings(tmp_path):
    from tests.unit.test_demo_report_presentation import research_report
    from tests.unit.test_html_report import rehash

    from skala_rag.reporting.v3_context import canonical

    draft, data = research_report()
    ctx = rehash(canonical(data))
    path = tmp_path / "research.pdf"
    html_pdf._chromium_pdf(html_pdf.render_report_html(draft, ctx), path)
    found = html_pdf._heading_ys(PdfReader(path))
    assert list(found) == list(html_pdf.TITLES)
    checks, _, _ = html_pdf.verify_pdf(path, draft)
    assert checks["sections"]


def test_pdf_rejects_duplicate_section_heading(tmp_path, ctx):
    draft, _, _ = make(ctx)
    html_text = html_pdf.render_report_html(draft, ctx).replace(
        "<h2>시장</h2>", "<h2>시장</h2><h2>시장</h2>"
    )
    path = tmp_path / "duplicate.pdf"
    html_pdf._chromium_pdf(html_text, path)
    checks, _, _ = html_pdf.verify_pdf(path, draft)
    assert not checks["sections"]


@pytest.mark.parametrize(
    "shape", ["combined_extra", "missing", "duplicate", "order", "reference_not_last"]
)
def test_renderer_refuses_unapproved_draft_headings_before_chromium(
    tmp_path, ctx, monkeypatch, shape
):
    import re

    from skala_rag.reporting.validator import artifact_hash

    draft, structural, judged = make(ctx)
    sections = re.split(r"(?=^## )", draft.markdown, flags=re.M)[1:]
    if shape == "combined_extra":
        sections.insert(2, "## TECHNOLOGY & MARKET\n\nCombined body\n\n")
    elif shape == "missing":
        del sections[3]
    elif shape == "duplicate":
        sections.insert(3, sections[2])
    elif shape == "order":
        sections[2], sections[3] = sections[3], sections[2]
    else:
        sections[-1], sections[-2] = sections[-2], sections[-1]
    malformed = draft.model_copy(update={"markdown": "".join(sections)})
    digest = artifact_hash(malformed)
    # A shape error must not be hidden even by a caller-supplied matching proof.
    structural = structural.model_copy(update={"artifact_hash": digest})
    judged = judged.model_copy(update={"judged_artifact_hash": digest})
    monkeypatch.setattr(
        html_pdf, "_chromium_pdf", lambda *_: pytest.fail("must reject before render")
    )
    render = renderer(tmp_path, ctx, structural, judged)(malformed, "html-v1")
    assert render.artifact_path is None
    assert render.errors[0].code == "PDF_SECTIONS"
    assert not list(tmp_path.iterdir())
