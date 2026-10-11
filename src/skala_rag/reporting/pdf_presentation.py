"""Private PDF presentation: supplied v3 observations, never prose-derived scores."""

import html

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, TableStyle

VERSION = "investment-report-v1"
NAVY = colors.HexColor("#17324D")
TEAL = colors.HexColor("#167D8D")
INK = colors.HexColor("#243746")
MUTED = colors.HexColor("#526575")
PALE = colors.HexColor("#F0F6F7")
LINE = colors.HexColor("#CCDADF")


def value_text(value):
    """Keep Decimal precision, zero and unavailable distinct; no rounding."""
    return "미상" if value is None else str(value)


def table_style(header=True):
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -1), 0.35, LINE),
        ("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [colors.white, PALE]),
    ]
    if header:
        commands.append(("BACKGROUND", (0, 0), (-1, 0), NAVY))
    else:
        commands.append(("BACKGROUND", (0, 0), (-1, -1), PALE))
        commands.append(("LINEABOVE", (0, 0), (-1, 0), 2, TEAL))
    return TableStyle(commands)


def narrative_timeline(
    evidence,
    width,
    body,
    heading,
    table_class,
    paragraph_class,
    section,
    citation_labels,
):
    """Only explicitly dated admitted company observations become timeline rows."""
    events = sorted(
        (item["event_date"], eid, item["claim"])
        for eid, item in evidence.items()
        if item["scope"] == "company" and item["event_date"] is not None
    )
    if len(events) < 2:
        return [], 0
    header = ParagraphStyle("timeline-header", parent=body, textColor=colors.white)
    rows = [
        [
            Paragraph(html.escape(cell), header)
            for cell in ("사건일", "확인된 이력과 근거")
        ]
    ]
    rows.extend(
        [
            Paragraph(day, body),
            Paragraph(html.escape(f"{claim} {citation_labels[eid]}"), body),
        ]
        for day, eid, claim in events
    )
    table = table_class(
        rows, colWidths=[width * 0.22, width * 0.78], repeatRows=1, hAlign="LEFT"
    )
    table.section = section
    table.setStyle(table_style())
    return [
        paragraph_class("근거로 확인된 이력", heading, section),
        table,
    ], len(events)


def page_decoration(canvas, document, *, font, mode, narrative=False):
    """Furniture uses reserved margins; it asserts mode, never publication status."""
    canvas.saveState()
    left = document.leftMargin
    right = document.pagesize[0] - document.rightMargin
    top = document.pagesize[1] - 28
    canvas.setFillColor(NAVY)
    canvas.setFont(font, 8)
    canvas.drawString(
        left,
        top,
        "기업 분석 보고서 | COMPANY ANALYSIS"
        if narrative
        else "투자 검토 보고서 | INVESTMENT REVIEW",
    )
    canvas.setStrokeColor(TEAL)
    canvas.setLineWidth(0.7)
    canvas.line(left, top - 7, right, top - 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(
        left, 28, "가상 데이터 | FIXTURE" if mode == "fixture" else "LIVE"
    )
    canvas.drawRightString(right, 28, f"PAGE {canvas.getPageNumber()}")
    canvas.restoreState()
