"""Private PDF presentation: supplied v3 observations, never prose-derived scores."""

import html
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Flowable, Paragraph, TableStyle

from skala_rag.contracts.reports import CandidateOutcome
from skala_rag.contracts.v3 import InvestmentDecision, ScoreSummary
from skala_rag.reporting.validator import artifact_hash

VERSION = "investment-report-v1"
NAVY = colors.HexColor("#17324D")
TEAL = colors.HexColor("#167D8D")
INK = colors.HexColor("#243746")
MUTED = colors.HexColor("#526575")
PALE = colors.HexColor("#F0F6F7")
LINE = colors.HexColor("#CCDADF")


def presentation_payload(data, draft):
    """Called only by the structural validator after exact score-block checks."""
    return {
        "version": VERSION,
        "context_id": draft.context_id,
        "draft_hash": artifact_hash(draft),
        "execution_mode": data["execution_mode"],
        "scores": data["scores"],
        "decisions": data["decisions"],
        "outcomes": data["outcomes"],
        "selection": data["selection"],
    }


def validated_presentation(raw, draft, execution_mode):
    """Recheck the trusted proof's optional observations and identity closure."""
    if raw is None:
        return None
    # Import at render time: the validator imports presentation_payload above.
    from skala_rag.reporting.v3_pipeline import assessment_block, outcome_block

    if (
        raw["version"] != VERSION
        or raw["context_id"] != draft.context_id
        or raw["draft_hash"] != artifact_hash(draft)
        or raw["execution_mode"] != execution_mode
    ):
        raise ValueError("stale PDF presentation")
    if (
        assessment_block(raw) not in draft.markdown
        or outcome_block(raw) not in draft.markdown
    ):
        raise ValueError("PDF presentation differs from exact draft")
    scores = {cid: ScoreSummary.model_validate(s) for cid, s in raw["scores"].items()}
    decisions = {
        cid: InvestmentDecision.model_validate(d) for cid, d in raw["decisions"].items()
    }
    outcomes = {
        cid: CandidateOutcome.model_validate(o) for cid, o in raw["outcomes"].items()
    }
    if set(scores) != set(decisions) or not set(scores) <= set(outcomes):
        raise ValueError("PDF candidate closure mismatch")
    for cid, score in scores.items():
        decision = decisions[cid]
        if (
            score.candidate_id != cid
            or decision.candidate_id != cid
            or score.run_id != decision.run_id
            or score.score_summary_id != decision.score_summary_id
            or outcomes[cid].decision_id != decision.decision_id
        ):
            raise ValueError("PDF score identity mismatch")
    if any(cid != outcome.candidate_id for cid, outcome in outcomes.items()):
        raise ValueError("PDF outcome identity mismatch")
    selected = raw["selection"]["selected_candidate_id"]
    recommended = {
        cid
        for cid, decision in decisions.items()
        if decision.label in ("RECOMMEND", "RECOMMEND_PRIORITY")
    }
    if (selected is not None and selected not in recommended) or (
        selected is None and recommended
    ):
        raise ValueError("PDF selection mismatch")
    return scores, decisions, outcomes, selected


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


class ScoreBar(Flowable):
    """Exact percent text plus a fixed 0..100 track; None has no track or fill."""

    def __init__(self, value, style):
        super().__init__()
        if value is not None and (
            not isinstance(value, Decimal)
            or not value.is_finite()
            or not 0 <= value <= 100
        ):
            raise ValueError("invalid percentage")
        self.value = value
        self.ratio = None if value is None else value / Decimal(100)
        self.label = Paragraph(html.escape(value_text(value)), style)

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        _, label_height = self.label.wrap(availWidth, availHeight)
        self.height = label_height + (10 if self.value is not None else 0)
        return self.width, self.height

    def draw(self):
        self.label.drawOn(self.canv, 0, 10 if self.value is not None else 0)
        if self.ratio is not None:
            self.canv.setFillColor(LINE)
            self.canv.rect(0, 1, self.width, 4, fill=1, stroke=0)
            # Float conversion affects geometry only, never the printed observation.
            if self.ratio > 0:
                self.canv.setFillColor(TEAL)
                self.canv.rect(
                    0, 1, self.width * float(self.ratio), 4, fill=1, stroke=0
                )


def presentation_flowables(data, width, body, heading, table_class, paragraph_class):
    """Supplement (do not replace) the exact upstream assessment/citation tables."""
    scores, decisions, outcomes, selected = data
    section = "INVESTMENT ASSESSMENT & RISKS"
    header = ParagraphStyle("data-header", parent=body, textColor=colors.white)
    flows = []

    def paragraph(text, style=body):
        return Paragraph(html.escape(value_text(text)), style)

    def title(text):
        flows.append(paragraph_class(text, heading, section))

    def table(rows, fractions, *, has_header=True):
        cells = [
            [
                cell
                if isinstance(cell, Flowable)
                else paragraph(cell, header if has_header and i == 0 else body)
                for cell in row
            ]
            for i, row in enumerate(rows)
        ]
        item = table_class(
            cells,
            colWidths=[width * f for f in fractions],
            repeatRows=1 if has_header else 0,
            hAlign="LEFT",
            spaceAfter=8,
        )
        item.section = section
        item.setStyle(table_style(has_header))
        flows.append(item)

    if outcomes:
        title("후보 비교")
        # Candidate-ID order is presentation only, not a new ranking or selection.
        rows = [["candidate_id / status", "normalized_score", "label"]]
        for cid, outcome in sorted(outcomes.items()):
            score, decision = scores.get(cid), decisions.get(cid)
            rows.append(
                [
                    f"{cid} / {outcome.status}",
                    score.normalized_score if score else None,
                    decision.label if decision else None,
                ]
            )
        table(rows, (0.42, 0.27, 0.31))
    bars = 0
    for cid, score in sorted(scores.items()):
        title(f"점수 요약 — {cid}")
        table(
            [
                ["normalized_score", "label"],
                [score.normalized_score, decisions[cid].label],
                ["coverage_pct", "weighted_missing_pct"],
                [score.coverage_pct, score.weighted_missing_pct],
            ],
            (0.5, 0.5),
            has_header=False,
        )
        if score.dimension_scores:
            title("영역별 점수 — dimension_score_pct (0 - 100%)")
            rows = [["dimension", "dimension_score_pct"]]
            for dimension, item in sorted(score.dimension_scores.items()):
                rows.append([dimension, ScoreBar(item.dimension_score_pct, body)])
                bars += item.dimension_score_pct is not None
            table(rows, (0.4, 0.6))
    return flows, {
        "score_cards": len(scores),
        "dimension_bars": bars,
        "candidate_rows": len(outcomes),
    }


def page_decoration(canvas, document, *, font, mode):
    """Furniture uses reserved margins; it asserts mode, never publication status."""
    canvas.saveState()
    left = document.leftMargin
    right = document.pagesize[0] - document.rightMargin
    top = document.pagesize[1] - 28
    canvas.setFillColor(NAVY)
    canvas.setFont(font, 8)
    canvas.drawString(left, top, "투자 검토 보고서 | INVESTMENT REVIEW")
    canvas.setStrokeColor(TEAL)
    canvas.setLineWidth(0.7)
    canvas.line(left, top - 7, right, top - 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(
        left, 28, "가상 데이터 | FIXTURE" if mode == "fixture" else "LIVE"
    )
    canvas.drawRightString(right, 28, f"PAGE {canvas.getPageNumber()}")
    canvas.restoreState()
