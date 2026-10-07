"""HTML → PDF (Playwright/Chromium) renderer와 layout validator (#175).

저장한 HTML bytes를 그대로 Chromium에 넣는다. browser 부재·예외는 PDF_RENDER_FAILED로
실패하며 ReportLab 등 다른 renderer로 대체하지 않는다. 사실·점수·판정을 바꾸지 않는다.
"""

import hashlib
import re
from collections.abc import Callable
from pathlib import Path

from pypdf import PdfReader

from skala_rag.contracts import (
    ReportDraft,
    ReportJudgement,
    ValidationErrorDetail,
    ValidationResult,
)
from skala_rag.contracts.tools import RenderResult
from skala_rag.reporting.html_report import TITLES, render_report_html
from skala_rag.reporting.pdf import PDFArtifactError, _failure
from skala_rag.reporting.v3_context import ReportContextV3
from skala_rag.reporting.validator import artifact_hash

A4 = (595.2756, 841.8898)
RENDERER = "playwright-chromium"


def _squash(text):
    return re.sub(r"\s+", "", text)


def _heading_ys(reader):
    """페이지별 제목 줄의 baseline y. 같은 y의 text run을 이어 붙여 제목과 비교한다."""
    found = {}
    for number, page in enumerate(reader.pages):
        lines = {}

        def visit(text, cm, tm, font, size, lines=lines):
            # h2 is fixed at 14pt. Whole-line table labels and role h3 titles
            # also say "기술"/"시장"; they are not section headings. Chromium's
            # text size is transformed by cm into actual PDF points.
            rendered_size = size * abs(cm[3])
            if text.strip() and abs(rendered_size - 14) < 0.1:
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                lines.setdefault(round(y), []).append(text)

        page.extract_text(visitor_text=visit)
        for y, parts in lines.items():
            line = _squash("".join(parts))
            for name, title in TITLES.items():
                if line == _squash(title):
                    found.setdefault(name, []).append((number, y))
    # A repeated title is not an exact six-section report. Leave it unresolved
    # so existing section/summary gates refuse it rather than using the first.
    return {
        name: positions[0] for name, positions in found.items() if len(positions) == 1
    }


def measure_summary(reader):
    """(fraction, ok). 요약 제목과 다음 제목의 baseline 간격을 A4 높이로 나눈다."""
    found = _heading_ys(reader)
    start, nxt = found.get("SUMMARY"), found.get("COMPANY & TEAM")
    if start is None or nxt is None or start[0] != nxt[0]:
        return None, False
    fraction = abs(start[1] - nxt[1]) / A4[1]
    return fraction, fraction <= 0.5


def verify_pdf(path, draft):
    reader = PdfReader(path)
    text = _squash("\n".join(page.extract_text() for page in reader.pages))
    tokens = [f"[@evidence:{e}]" for e in draft.cited_evidence_ids]
    tokens += [f"[@source:{s}]" for s in draft.reference_source_ids]
    fraction, summary_ok = measure_summary(reader)
    found = _heading_ys(reader)
    order = [(found[n][0], -found[n][1]) for n in TITLES if n in found]
    sections_ok = len(order) == len(TITLES) and order == sorted(order)
    checks = dict(
        page_count=len(reader.pages) <= 5,
        page_size=all(
            abs(float(p.mediabox.width) - A4[0]) < 0.5
            and abs(float(p.mediabox.height) - A4[1]) < 0.5
            for p in reader.pages
        ),
        citations=all(_squash(t) in text for t in tokens),
        sections=sections_ok,
        summary=summary_ok,
    )
    return checks, len(reader.pages), fraction


def _errors(draft, checks):
    return [
        ValidationErrorDetail(
            schema_version=draft.schema_version,
            code="PDF_" + name.upper(),
            location="pdf",
            message="PDF 분량·배치·인용 검증 실패",
        )
        for name, ok in checks.items()
        if not ok
    ]


def _chromium_pdf(html_text, pdf_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(chromium_sandbox=True)
        try:
            context = browser.new_context(
                java_script_enabled=False,
                service_workers="block",
                accept_downloads=False,
            )
            # data: URL은 inline이라 route를 거치지 않는다. 그 외 모든 요청은 차단한다.
            context.route("**/*", lambda route: route.abort())
            page = context.new_page()
            page.set_content(html_text, wait_until="load")
            page.pdf(
                path=str(pdf_path),
                format="A4",
                prefer_css_page_size=True,
                print_background=True,
                scale=1,
            )
        finally:
            browser.close()


class HTMLPDFRenderer:
    """RenderPdf Protocol과 같은 (draft, template) 호출. proof는 호출자가 제공한다."""

    def __init__(
        self,
        *,
        output_dir: Path,
        proof: Callable[[ReportDraft], tuple[ValidationResult, ReportJudgement]],
        execution_mode: str,
        context: ReportContextV3,
    ):
        if execution_mode not in ("fixture", "live"):
            raise ValueError("명시적 fixture/live 모드가 필요합니다")
        self.output_dir = Path(output_dir)
        self.proof = proof
        self.execution_mode = execution_mode
        self.context = context

    def __call__(self, draft: ReportDraft, template: str) -> RenderResult:
        temps = []
        published = []
        try:
            structural, judged = self.proof(draft)
            digest = artifact_hash(draft)
            if (
                self.execution_mode != self.context.snapshot()["execution_mode"]
                or not structural.valid
                or judged.verdict != "pass"
                or structural.context_id != draft.context_id
                or judged.context_id != draft.context_id
                or self.context.context_id != draft.context_id
                or structural.artifact_hash != digest
                or judged.judged_artifact_hash != digest
            ):
                return _failure(draft, "PDF_UPSTREAM_NOT_VALIDATED")
            if tuple(re.findall(r"^## (.+)$", draft.markdown, re.M)) != tuple(TITLES):
                return _failure(draft, "PDF_SECTIONS")
            self.output_dir.mkdir(parents=True, exist_ok=True)
            stem = f"report-{draft.revision}-{digest.removeprefix('sha256:')[:8]}"
            html_path = self.output_dir / f"{stem}.html"
            pdf_path = self.output_dir / f"{stem}.pdf"
            if html_path.exists() or pdf_path.exists():
                return _failure(draft, "PDF_ARTIFACT_ALREADY_EXISTS")
            # 임시 이름으로 만들고 검증 뒤에만 최종 이름으로 바꾼다.
            tmp_html = self.output_dir / f".{stem}.html.tmp"
            tmp_pdf = self.output_dir / f".{stem}.pdf.tmp"
            temps = [tmp_html, tmp_pdf]
            html_bytes = render_report_html(draft, self.context).encode("utf-8")
            with open(tmp_html, "wb") as f:
                f.write(html_bytes)
            _chromium_pdf(html_bytes.decode("utf-8"), tmp_pdf)
            checks, pages, fraction = verify_pdf(tmp_pdf, draft)
            valid = all(checks.values())
            stub = any(f.severity == "stub" for f in judged.findings)
            measures = dict(
                context_id=draft.context_id,
                report_id=draft.report_id,
                draft_revision=draft.revision,
                draft_hash=digest,
                html_hash=hashlib.sha256(html_bytes).hexdigest(),
                artifact_hash=hashlib.sha256(tmp_pdf.read_bytes()).hexdigest(),
                renderer=RENDERER,
                template_version=template,
                checks=checks,
                summary_fraction=fraction,
                pdf_verified=valid,
                final_allowed=valid and not stub and self.execution_mode == "live",
                action="pass" if valid else "revise",
            )
            if not valid:
                for tmp in temps:
                    tmp.unlink(missing_ok=True)
                return RenderResult(
                    schema_version=draft.schema_version,
                    layout_measurements=measures,
                    errors=_errors(draft, checks),
                )
            tmp_html.rename(html_path)
            published.append(html_path)
            tmp_pdf.rename(pdf_path)
            published.append(pdf_path)
            measures["html_path"] = str(html_path)
            return RenderResult(
                schema_version=draft.schema_version,
                artifact_path=str(pdf_path),
                page_count=pages,
                layout_measurements=measures,
                errors=_errors(draft, checks),
            )
        except Exception:
            for tmp in temps + published:
                tmp.unlink(missing_ok=True)
            return _failure(draft, "PDF_RENDER_FAILED")


class HTMLPDFLayoutValidator:
    """ReportNodes.layout 경계. 저장된 HTML/PDF를 다시 hash·검증한다."""

    def __call__(self, draft, context, render):
        m = render.layout_measurements
        digest = artifact_hash(draft)
        if (
            context.context_id != draft.context_id
            or m.get("context_id") != draft.context_id
            or m.get("report_id") != draft.report_id
            or m.get("draft_revision") != draft.revision
            or m.get("draft_hash") != digest
            or m.get("renderer") != RENDERER
            or render.artifact_path is None
            or not m.get("html_path")
        ):
            raise PDFArtifactError("PDF와 draft/context 참조가 일치하지 않습니다")
        html_bytes = Path(m["html_path"]).read_bytes()
        if hashlib.sha256(html_bytes).hexdigest() != m.get("html_hash"):
            raise PDFArtifactError("저장된 HTML hash가 바뀌었습니다")
        if html_bytes != render_report_html(draft, context).encode("utf-8"):
            raise PDFArtifactError("저장된 HTML이 draft/context와 다릅니다")
        path = Path(render.artifact_path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != m.get("artifact_hash"):
            raise PDFArtifactError("저장된 PDF hash가 바뀌었습니다")
        checks, pages, fraction = verify_pdf(path, draft)
        if pages != render.page_count:
            raise PDFArtifactError("PDF page_count가 변경되었습니다")
        valid = all(checks.values())
        checks.update(
            pdf_verified=valid,
            action="pass" if valid else "revise",
            summary_fraction=fraction,
            artifact_hash=m["artifact_hash"],
            html_hash=m["html_hash"],
        )
        return ValidationResult(
            schema_version=draft.schema_version,
            valid=valid,
            context_id=draft.context_id,
            checks=checks,
            errors=_errors(draft, {k: v for k, v in checks.items() if k in _NAMES}),
            artifact_hash=digest,
        )


_NAMES = ("page_count", "page_size", "citations", "sections", "summary")
