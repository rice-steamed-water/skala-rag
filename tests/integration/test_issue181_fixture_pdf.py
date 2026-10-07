"""#181 real offline fixture PDFs; fixture verification never permits publication."""

import hashlib
import json
import re
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from skala_rag.cli import run
from skala_rag.contracts import ReportDraft
from skala_rag.contracts.manifest import RunManifest
from skala_rag.reporting.pdf import SECTIONS
from skala_rag.reporting.validator import artifact_hash


def _assert_pdf_section_headings(reader):
    headings = []
    for page_number, page in enumerate(reader.pages):

        def visit(text, cm, tm, font, size):
            # PDFRenderer's head style is bold 14 pt; body/table/subhead
            # mentions (including LOW_MARKET/LOW_TECHNOLOGY) are 10.5 pt.
            if size != 14 or not text.strip():
                return
            assert font is not None and str(font["/BaseFont"]).endswith("Bold")
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            headings.append((page_number, -y, x, text.strip()))

        page.extract_text(visitor_text=visit)
    # Read physical page order, top-to-bottom, not substring/content-stream
    # order. Exact sequence also rejects missing/duplicate/renamed headings
    # and requires REFERENCE to be the last of exactly six headings.
    expected = (
        "SUMMARY",
        "COMPANY & TEAM",
        "TECHNOLOGY",
        "MARKET",
        "INVESTMENT ASSESSMENT & RISKS",
        "REFERENCE",
    )
    assert SECTIONS == expected
    assert tuple(item[3] for item in sorted(headings)) == expected


@pytest.mark.parametrize(
    "headings",
    [
        SECTIONS[:2] + SECTIONS[3:],
        SECTIONS[:2] + (SECTIONS[3], SECTIONS[2]) + SECTIONS[4:],
        SECTIONS[:3] + (SECTIONS[2],) + SECTIONS[3:],
        SECTIONS[:2] + ("TECHNOLOGY & MARKET",) + SECTIONS[3:],
        SECTIONS[:-2] + (SECTIONS[-1], SECTIONS[-2]),
    ],
    ids=["missing", "reordered", "duplicate", "renamed", "reference-not-last"],
)
def test_summary_mentions_cannot_mask_invalid_pdf_headings(headings):
    with pytest.raises(AssertionError):
        _assert_pdf_section_headings(_heading_control_pdf(headings))


def test_summary_mentions_are_not_pdf_headings():
    _assert_pdf_section_headings(_heading_control_pdf(SECTIONS))


def _heading_control_pdf(headings):
    buffer = BytesIO()
    canvas = Canvas(buffer)
    canvas.setFont("Helvetica", 10.5)
    y = 800
    # Exact standalone mentions as well as embedded decision codes must not
    # substitute for missing headings or determine their physical order.
    for mention in (*SECTIONS, "LOW_MARKET; LOW_TECHNOLOGY"):
        canvas.drawString(50, y, mention)
        y -= 15
    canvas.setFont("Helvetica-Bold", 14)
    for heading in headings:
        canvas.drawString(50, y, heading)
        y -= 25
    canvas.save()
    buffer.seek(0)
    return PdfReader(buffer)


@pytest.mark.parametrize(
    "ratings,outcome", [((5, 4), "recommended"), ((1, 1), "no_recommendation")]
)
def test_two_candidate_fixture_pdf_receipt_and_current_hashes(
    tmp_path, ratings, outcome
):
    destination = run(
        "robotics",
        output_dir=tmp_path,
        policy_path="configs/scoring.v3.json",
        catalog_path="configs/scoring.draft.json",
        config_path=Path(__file__).parents[1] / "fixtures/cli-input.json",
        fixture_ratings=ratings,
    )
    receipt = json.loads((destination / "run-result.json").read_text())
    assert receipt["workflow_status"] == "completed"
    assert receipt["run_outcome"] == outcome
    assert receipt["exit_code"] == 0
    assert receipt["acceptance"] == "fixture_only"
    assert receipt["publication_allowed"] is False
    assert receipt["warnings"] == []
    assert not (destination / "report.md").exists()
    manifest = RunManifest.model_validate_json(
        (destination / "manifest.json").read_text()
    )
    assert manifest.usage["external_requests"] == 0
    assert manifest.usage["fixture_evaluation_branches"] == 10
    for artifact in manifest.artifacts.values():
        assert (
            hashlib.sha256(Path(artifact.artifact_path).read_bytes()).hexdigest()
            == artifact.artifact_hash
        )
    draft = ReportDraft.model_validate_json(
        (destination / "report-draft.json").read_text()
    )
    digest = artifact_hash(draft)
    assert draft.revision == 0
    assert manifest.validation_results["structural"].artifact_hash == digest
    assert manifest.validation_results["semantic"].judged_artifact_hash == digest
    validation = manifest.validation_results["pdf"]
    assert validation.artifact_hash == digest
    assert validation.valid
    assert validation.checks["summary_fraction"] <= 0.5
    pdfs = list(destination.glob("*.pdf"))
    assert len(pdfs) == 1
    reader = PdfReader(pdfs[0])
    assert len(reader.pages) <= 5
    assert (
        hashlib.sha256(pdfs[0].read_bytes()).hexdigest()
        == validation.checks["artifact_hash"]
    )
    text = re.sub(r"\s+", "", "\n".join(p.extract_text() for p in reader.pages))
    _assert_pdf_section_headings(reader)
    for token in draft.cited_evidence_ids:
        assert f"[@evidence:{token}]" in text
    for token in draft.reference_source_ids:
        assert f"[@source:{token}]" in text
