"""D09 승인 fixed PDF renderer. 사실·점수·판정 변경과 외부 fetch를 하지 않는다."""

import hashlib
import html
import re
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

import reportlab
from pydantic import BaseModel, ConfigDict
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table

from skala_rag.contracts import (
    ReportDraft,
    ReportJudgement,
    ValidationErrorDetail,
    ValidationResult,
)
from skala_rag.contracts.tools import RenderResult
from skala_rag.reporting.company_context import CompanyWriterContext
from skala_rag.reporting.format import finalize_narrative
from skala_rag.reporting.narrative import (
    NARRATIVE_HEADINGS,
    NarrativeCompositionResult,
    SemanticJudgeReply,
    composition_hash,
)
from skala_rag.reporting.pdf_presentation import (
    INK,
    NAVY,
    TEAL,
    VERSION,
    page_decoration,
    table_style,
)
from skala_rag.reporting.validator import artifact_hash

SECTIONS = (
    "SUMMARY",
    "COMPANY & TEAM",
    "TECHNOLOGY",
    "MARKET",
    "INVESTMENT ASSESSMENT & RISKS",
    "REFERENCE",
)
FONT_ROOT = Path(__file__).with_name("fonts")
NARRATIVE_SECTIONS = (*NARRATIVE_HEADINGS, "REFERENCE")


def narrative_pdf_draft(
    result: NarrativeCompositionResult, context: CompanyWriterContext
) -> ReportDraft:
    """Resolve references only after checking contextual semantic acceptance."""
    context.verify_binding()
    judgment = SemanticJudgeReply.model_validate(
        result.judgment, context={"writer_context": context.writer_context}
    )
    digest = composition_hash(result.composition)
    if (
        judgment.verdict != "pass"
        or judgment.artifact_hash != digest
        or result.artifact_hash != digest
    ):
        raise PDFArtifactError("NARRATIVE_PDF_UPSTREAM_NOT_VALIDATED")
    finalized = finalize_narrative(result.composition, context)
    return ReportDraft(
        schema_version="narrative-report-1",
        report_id="narrative-" + digest,
        context_id=finalized.context_id,
        revision=0,
        markdown=finalized.markdown,
        cited_evidence_ids=list(finalized.cited_evidence_ids),
        reference_source_ids=list(finalized.reference_source_ids),
        limitations=[],
    )


class PdfProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: str
    status: str
    renderer_version: str
    regular_sha256: str
    bold_sha256: str


def load_pdf_profile(path: Path | None = None) -> PdfProfile:
    if path is None:
        module = Path(__file__).resolve()
        if module.parts[-4:] == ("src", "skala_rag", "reporting", "pdf.py"):
            content = (module.parents[3] / "configs/pdf.layout.v1.json").read_bytes()
        else:
            content = (
                files("skala_rag").joinpath("_config/pdf.layout.v1.json").read_bytes()
            )
    else:
        content = path.read_bytes()
    return PdfProfile.model_validate_json(content)


def _plain(text):
    return re.sub(r"\*\*(.*?)\*\*", r"\1", text)


def _heading(text):
    return re.sub(r"^\d+\.\s*", "", text).strip()


def _blocks(markdown, sections_expected=SECTIONS):
    lines = markdown.splitlines()
    section = None
    sections = []
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        heading = re.match(r"^#{1,3}\s+(.+)$", line)
        if heading:
            title = _heading(heading.group(1))
            if title == "TECHNOLOGY & MARKET":
                raise ValueError("v3 기술·시장 통합 절은 허용하지 않습니다")
            if title in sections_expected:
                section = title
                sections.append(title)
                blocks.append((section, "heading", title))
            elif section is None:
                blocks.append(("title", "heading", heading.group(1)))
            else:
                blocks.append((section, "subheading", heading.group(1)))
            i += 1
            continue
        if section is None:
            raise ValueError("SUMMARY 전에 본문이 있습니다")
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = [cell.strip() for cell in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in row):
                    rows.append(row)
                i += 1
            if not rows or any(len(row) != len(rows[0]) for row in rows):
                raise ValueError("표 열 개수가 일치하지 않습니다")
            blocks.append((section, "table", rows))
            continue
        blocks.append((section, "paragraph", line))
        i += 1
    if sections != list(sections_expected):
        raise ValueError("본문과 REFERENCE 순서/중복/누락 오류")
    return blocks


def _physical_sections(reader, draft):
    """Check real 14pt heading lines in physical page order, not body substrings."""
    lines = {}
    for number, page in enumerate(reader.pages):

        def visit(text, cm, tm, font, size, number=number):
            if not text.strip() or abs(size * abs(cm[3]) - 14) >= 0.1:
                return
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            bold = font is not None and str(font.get("/BaseFont", "")).endswith("Bold")
            lines.setdefault((number, -round(y, 1)), []).append((x, text, bold))

        page.extract_text(visitor_text=visit)
    headings = []
    for _, parts in sorted(lines.items()):
        parts.sort(key=lambda part: part[0])
        if not all(part[2] for part in parts):
            return False
        headings.append(re.sub(r"\s+", " ", "".join(part[1] for part in parts)).strip())
    # The public parser also permits an explicit document title before SUMMARY.
    expected = [
        value
        for _, kind, value in _blocks(
            draft.markdown,
            NARRATIVE_SECTIONS
            if draft.schema_version == "narrative-report-1"
            else SECTIONS,
        )
        if kind == "heading"
    ]
    return headings == expected


def _reference_page(reader: PdfReader) -> int:
    """Measure the reference boundary from actual bold 14pt PDF headings."""
    for number, page in enumerate(reader.pages, 1):
        found = []

        def visit(text, cm, tm, font, size):
            if (
                abs(size * abs(cm[3]) - 14) < 0.1
                and font is not None
                and str(font.get("/BaseFont", "")).endswith("Bold")
                and text.strip() == "REFERENCE"
            ):
                found.append(True)

        page.extract_text(visitor_text=visit)
        if found:
            return number
    raise PDFArtifactError("PDF_REFERENCE_BOUNDARY_MISSING")


def _failure(draft, code):
    return RenderResult(
        schema_version=draft.schema_version,
        layout_measurements={
            "context_id": draft.context_id,
            "report_id": draft.report_id,
            "draft_hash": artifact_hash(draft),
            "pdf_verified": False,
            "final_allowed": False,
            "action": "fail",
        },
        errors=[
            ValidationErrorDetail(
                schema_version=draft.schema_version,
                code=code,
                location="pdf",
                message="PDF 생성 또는 검증이 실패했습니다",
            )
        ],
    )


class PDFRenderer:
    """RenderPdf Protocol. 최신 structural/Judge proof를 호출자가 제공한다."""

    def __init__(
        self,
        *,
        profile: PdfProfile,
        output_dir: Path,
        proof: Callable[[ReportDraft], tuple[ValidationResult, ReportJudgement]],
        execution_mode: str,
    ):
        if execution_mode not in ("fixture", "live"):
            raise ValueError("명시적 fixture/live 모드가 필요합니다")
        self.profile = profile
        self.output_dir = output_dir
        self.proof = proof
        self.execution_mode = execution_mode

    def render_narrative(
        self,
        result: NarrativeCompositionResult,
        context: CompanyWriterContext,
        template: str,
    ) -> RenderResult:
        """Production narrative boundary; no model dispatch or repair counter."""
        draft = narrative_pdf_draft(result, context)
        digest = artifact_hash(draft)
        structural = ValidationResult(
            schema_version=draft.schema_version,
            valid=True,
            context_id=draft.context_id,
            artifact_hash=digest,
            checks={
                "narrative_evidence": {
                    eid: context.writer_context.evidence[eid].model_dump(mode="json")
                    for eid in draft.cited_evidence_ids
                }
            },
            errors=[],
        )
        judged = ReportJudgement(
            schema_version=draft.schema_version,
            verdict="pass",
            context_id=draft.context_id,
            judged_artifact_hash=digest,
            findings=[],
            revision_instructions=[],
        )
        renderer = PDFRenderer(
            profile=self.profile,
            output_dir=self.output_dir,
            proof=lambda _: (structural, judged),
            execution_mode=self.execution_mode,
        )
        return renderer(draft, template)

    def __call__(self, draft: ReportDraft, template: str) -> RenderResult:
        try:
            structural, judged = self.proof(draft)
            digest = artifact_hash(draft)
            if (
                not structural.valid
                or judged.verdict != "pass"
                or structural.context_id != draft.context_id
                or judged.context_id != draft.context_id
                or structural.artifact_hash != digest
                or judged.judged_artifact_hash != digest
            ):
                return _failure(draft, "PDF_UPSTREAM_NOT_VALIDATED")
            if self.profile.status != "approved" or template != self.profile.version:
                return _failure(draft, "PDF_PROFILE_NOT_APPROVED")
            if self.profile.renderer_version != reportlab.Version:
                return _failure(draft, "PDF_RENDERER_VERSION_MISMATCH")
            if structural.checks.get("pdf_presentation") is not None:
                return _failure(draft, "PDF_SCORED_PRESENTATION_RETIRED")
            fonts = []
            for filename, expected in [
                ("NanumGothic-Regular.ttf", self.profile.regular_sha256),
                ("NanumGothic-Bold.ttf", self.profile.bold_sha256),
            ]:
                path = FONT_ROOT / filename
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    return _failure(draft, "PDF_FONT_HASH_MISMATCH")
                name = "PDF-" + expected[:16]
                if name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(name, str(path)))
                fonts.append(name)
            narrative = draft.schema_version == "narrative-report-1"
            visible_markdown = draft.markdown
            citation_labels = {
                eid: f"[{number}]"
                for number, eid in enumerate(sorted(draft.cited_evidence_ids), 1)
            }
            if narrative:
                prose, boundary, references = visible_markdown.partition("## REFERENCE")
                for eid, label in citation_labels.items():
                    prose = prose.replace(f"[@evidence:{eid}]", label)
                    references = references.replace(
                        f"evidence={eid};", f"{label} [@evidence:{eid}];"
                    )
                visible_markdown = prose + boundary + references
            blocks = _blocks(
                visible_markdown, NARRATIVE_SECTIONS if narrative else SECTIONS
            )
            glyphs = pdfmetrics.getFont(fonts[0]).face.charToGlyph
            text = _plain(draft.markdown)
            if any(ord(c) not in glyphs for c in text if not c.isspace()):
                return _failure(draft, "PDF_FONT_GLYPH_MISSING")
            body = ParagraphStyle(
                "body",
                fontName=fonts[0],
                fontSize=10.5,
                leading=15,
                wordWrap="CJK",
                spaceAfter=6,
                textColor=INK,
            )
            head = ParagraphStyle(
                "head",
                parent=body,
                fontName=fonts[1],
                fontSize=14,
                leading=19,
                spaceBefore=12,
                spaceAfter=7,
                textColor=NAVY,
                keepWithNext=True,
            )
            subhead = ParagraphStyle(
                "subhead",
                parent=head,
                fontSize=10.5,
                leading=15,
                textColor=TEAL,
                spaceBefore=8,
                spaceAfter=5,
            )
            table_header = ParagraphStyle(
                "table-header", parent=body, fontName=fonts[1], textColor=colors.white
            )
            positions = []

            class MeasuredParagraph(Paragraph):
                def __init__(self, value, style, section=None, **kwargs):
                    rendered = html.escape(_plain(value)) if value is not None else None
                    super().__init__(rendered, style, **kwargs)
                    self.section = section

                def drawOn(self, canvas, x, y, _sW=0):
                    positions.append(
                        dict(
                            section=self.section,
                            page=canvas.getPageNumber(),
                            x=x,
                            y=y,
                            width=self.width,
                            height=self.height,
                        )
                    )
                    return super().drawOn(canvas, x, y, _sW)

                def split(self, width, height):
                    # 실제 split fragment의 좌표도 기록해 SUMMARY page 초과를 잡는다.
                    split = super().split(width, height)
                    for part in split:
                        part.section = self.section
                    return split

            class MeasuredTable(Table):
                def drawOn(self, canvas, x, y, _sW=0):
                    positions.append(
                        dict(
                            section=self.section,
                            page=canvas.getPageNumber(),
                            x=x,
                            y=y,
                            width=self._width,
                            height=self._height,
                        )
                    )
                    return super().drawOn(canvas, x, y, _sW)

                def split(self, width, height):
                    split = super().split(width, height)
                    for part in split:
                        part.section = self.section
                    return split

            self.output_dir.mkdir(parents=True, exist_ok=True)
            destination = self.output_dir / f"{digest.removeprefix('sha256:')}.pdf"
            if destination.exists():
                return _failure(draft, "PDF_ARTIFACT_ALREADY_EXISTS")
            margin = 18 * mm
            width = A4[0] - 2 * margin - 12
            story = []
            visualizations = {
                "score_cards": 0,
                "dimension_bars": 0,
                "candidate_rows": 0,
            }
            for section, kind, value in blocks:
                if narrative and kind == "heading" and section == "REFERENCE":
                    story.append(PageBreak())
                if kind == "table":
                    rows = [
                        [
                            Paragraph(
                                html.escape(_plain(cell)),
                                table_header if i == 0 else body,
                            )
                            for cell in row
                        ]
                        for i, row in enumerate(value)
                    ]
                    table = MeasuredTable(
                        rows,
                        colWidths=[width / len(rows[0])] * len(rows[0]),
                        repeatRows=1,
                        hAlign="LEFT",
                    )
                    table.section = section
                    table.setStyle(table_style())
                    story.append(table)
                else:
                    story.append(
                        MeasuredParagraph(
                            value,
                            head
                            if kind == "heading"
                            else subhead
                            if kind == "subheading"
                            else body,
                            section,
                        )
                    )
                if (
                    narrative
                    and kind == "heading"
                    and section == "재무·투자유치·거래조건"
                ):
                    from skala_rag.reporting.pdf_presentation import narrative_timeline

                    additions, event_count = narrative_timeline(
                        structural.checks["narrative_evidence"],
                        width,
                        body,
                        subhead,
                        MeasuredTable,
                        MeasuredParagraph,
                        section,
                        citation_labels,
                    )
                    story.extend(additions)
                    visualizations["timeline_events"] = event_count
            document = SimpleDocTemplate(
                str(destination),
                pagesize=A4,
                leftMargin=margin,
                rightMargin=margin,
                topMargin=margin,
                bottomMargin=margin,
                title="투자 검토 보고서",
            )

            def decorate(canvas, doc):
                page_decoration(
                    canvas,
                    doc,
                    font=fonts[0],
                    mode=self.execution_mode,
                    narrative=narrative,
                )

            document.build(story, onFirstPage=decorate, onLaterPages=decorate)
            reader = PdfReader(destination)
            content = "\n".join(page.extract_text() for page in reader.pages)
            summary = [box for box in positions if box["section"] == "SUMMARY"]
            pages = sorted({box["page"] for box in summary})
            summary_height = (
                (
                    max(box["y"] + box["height"] for box in summary)
                    - min(box["y"] for box in summary)
                )
                if summary
                else A4[1]
            )
            fraction = summary_height / A4[1]
            reference_pages = sorted(
                {box["page"] for box in positions if box["section"] == "REFERENCE"}
            )
            body_pages = _reference_page(reader) - 1 if narrative else len(reader.pages)
            checks = dict(
                page_count=body_pages <= (6 if narrative else 5),
                summary=len(pages) == 1 and fraction <= 0.5,
            )
            normalized = re.sub(r"\s+", "", content)
            required = [f"[@evidence:{eid}]" for eid in draft.cited_evidence_ids]
            required += [f"[@source:{sid}]" for sid in draft.reference_source_ids]
            checks["citations"] = all(token in normalized for token in required)
            checks["sections"] = _physical_sections(reader, draft)
            # 저장 PDF의 실제 용지 크기를 확인한다.
            checks["page_size"] = all(
                abs(float(p.mediabox.width) - A4[0]) < 0.1
                and abs(float(p.mediabox.height) - A4[1]) < 0.1
                for p in reader.pages
            )
            valid = all(checks.values())
            errors = [
                ValidationErrorDetail(
                    schema_version=draft.schema_version,
                    code="PDF_" + name.upper(),
                    location="pdf",
                    message="PDF 분량 또는 인용 검증이 실패했습니다",
                )
                for name, ok in checks.items()
                if not ok
            ]
            stub = any(f.severity == "stub" for f in judged.findings)
            measures = dict(
                context_id=draft.context_id,
                report_id=draft.report_id,
                draft_revision=draft.revision,
                draft_hash=digest,
                artifact_hash=hashlib.sha256(destination.read_bytes()).hexdigest(),
                renderer_version=reportlab.Version,
                template_version=self.profile.version,
                presentation_version=VERSION,
                visualizations=visualizations,
                body_page_count=body_pages,
                reference_pages=reference_pages,
                summary_height_pt=summary_height,
                summary_fraction=fraction,
                summary_pages=pages,
                checks=checks,
                pdf_verified=valid,
                final_allowed=valid and not stub and self.execution_mode == "live",
                action="pass" if valid else "revise",
                measurements=positions,
            )
            return RenderResult(
                schema_version=draft.schema_version,
                artifact_path=str(destination),
                page_count=len(reader.pages),
                layout_measurements=measures,
                errors=errors,
            )
        except Exception:
            return _failure(draft, "PDF_RENDER_FAILED")


class PDFArtifactError(ValueError):
    """잘못되거나 변경된 artifact는 LLM 문장 수정으로 회복하지 않는다."""


class PDFLayoutValidator:
    """ReportNodes.layout 경계. 저장된 실제 PDF와 draft/measurement를 재검증한다."""

    def validate_narrative(
        self,
        result: NarrativeCompositionResult,
        context: CompanyWriterContext,
        render: RenderResult,
    ) -> ValidationResult:
        return self(narrative_pdf_draft(result, context), context, render)

    def __call__(self, draft, context, render):
        measurements = render.layout_measurements
        digest = artifact_hash(draft)
        if (
            context.context_id != draft.context_id
            or measurements.get("context_id") != draft.context_id
            or measurements.get("report_id") != draft.report_id
            or measurements.get("draft_revision") != draft.revision
            or measurements.get("draft_hash") != digest
            or render.artifact_path is None
        ):
            raise PDFArtifactError("PDF와 draft/context 참조가 일치하지 않습니다")
        path = Path(render.artifact_path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != measurements.get(
            "artifact_hash"
        ):
            raise PDFArtifactError("저장된 PDF hash가 바뀌었습니다")
        reader = PdfReader(path)
        boxes = measurements.get("measurements", [])
        summary = [box for box in boxes if box.get("section") == "SUMMARY"]
        pages = {box["page"] for box in summary}
        height = (
            max(box["y"] + box["height"] for box in summary)
            - min(box["y"] for box in summary)
            if summary
            else A4[1]
        )
        margin = 18 * mm
        narrative = draft.schema_version == "narrative-report-1"
        body_pages = _reference_page(reader) - 1 if narrative else len(reader.pages)
        checks = dict(
            page_count=body_pages <= (6 if narrative else 5),
            summary=len(pages) == 1 and height / A4[1] <= 0.5,
            page_size=all(
                abs(float(page.mediabox.width) - A4[0]) < 0.1
                and abs(float(page.mediabox.height) - A4[1]) < 0.1
                for page in reader.pages
            ),
            bounds=all(
                box["x"] >= margin - 0.1
                and box["y"] >= margin - 0.1
                and box["x"] + box["width"] <= A4[0] - margin + 0.1
                and box["y"] + box["height"] <= A4[1] - margin + 0.1
                for box in boxes
            ),
        )
        if len(reader.pages) != render.page_count:
            raise PDFArtifactError("PDF page_count가 변경되었습니다")
        text = re.sub(
            r"\s+", "", "\n".join(page.extract_text() for page in reader.pages)
        )
        tokens = [f"[@evidence:{eid}]" for eid in draft.cited_evidence_ids]
        tokens.extend(f"[@source:{sid}]" for sid in draft.reference_source_ids)
        checks["citations"] = all(token in text for token in tokens)
        checks["sections"] = _physical_sections(reader, draft)
        valid = all(checks.values())
        checks.update(
            pdf_verified=valid,
            action="pass" if valid else "revise",
            summary_fraction=height / A4[1],
            artifact_hash=measurements["artifact_hash"],
        )
        errors = [
            ValidationErrorDetail(
                schema_version=draft.schema_version,
                code="PDF_" + name.upper(),
                location="pdf",
                message="PDF 분량·배치·인용 검증 실패",
            )
            for name, ok in checks.items()
            if name
            in ("page_count", "summary", "page_size", "bounds", "citations", "sections")
            and not ok
        ]
        return ValidationResult(
            schema_version=draft.schema_version,
            valid=valid,
            context_id=draft.context_id,
            checks=checks,
            errors=errors,
            artifact_hash=digest,
        )
