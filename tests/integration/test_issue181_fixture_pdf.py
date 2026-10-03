"""#181 real offline fixture PDFs; fixture verification never permits publication."""

import hashlib
import json
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from skala_rag.cli import run
from skala_rag.contracts import ReportDraft
from skala_rag.contracts.manifest import RunManifest
from skala_rag.reporting.pdf import SECTIONS
from skala_rag.reporting.validator import artifact_hash


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
    positions = [text.index(re.sub(r"\s+", "", section)) for section in SECTIONS]
    assert positions == sorted(positions)
    for token in draft.cited_evidence_ids:
        assert f"[@evidence:{token}]" in text
    for token in draft.reference_source_ids:
        assert f"[@source:{token}]" in text
