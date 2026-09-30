"""Fixture terminal receipts distinguish Warning, fatal and stale validation."""

from dataclasses import replace

import pytest

from skala_rag.contracts import ReportDraft, ReportJudgement, ValidationResult
from skala_rag.reporting.validator import artifact_hash
from skala_rag.run_finalization import ReportCompletion, finalize_fixture


def completion(*, revisions=0, verdict="pass", structural_valid=True):
    draft = ReportDraft(
        schema_version="fixture-1",
        report_id="report",
        context_id="ctx",
        revision=revisions,
        markdown="Fixture draft",
        cited_evidence_ids=[],
        reference_source_ids=[],
        limitations=["synthetic"],
    )
    structural = ValidationResult(
        schema_version="fixture-1",
        context_id="ctx",
        valid=structural_valid,
        artifact_hash=artifact_hash(draft),
        checks={"action": "pass" if structural_valid else "revise"},
        errors=[]
        if structural_valid
        else [
            dict(
                schema_version="fixture-1",
                code="SV03",
                location="report",
                message="Missing heading",
            )
        ],
    )
    semantic = (
        ReportJudgement(
            schema_version="fixture-1",
            context_id="ctx",
            verdict=verdict,
            findings=[],
            revision_instructions=[],
            judged_artifact_hash=artifact_hash(draft),
        )
        if structural_valid
        else None
    )
    return ReportCompletion("recommended", draft, structural, semantic, revisions)


@pytest.mark.parametrize("structural_valid", [True, False])
def test_exhausted_structural_or_semantic_revision_is_completed_warning(
    structural_valid,
):
    terminal = finalize_fixture(
        completion(revisions=2, verdict="revise", structural_valid=structural_valid)
    )
    assert (terminal.workflow_status, terminal.exit_code, terminal.acceptance) == (
        "completed",
        2,
        "warning",
    )
    assert terminal.warnings and not terminal.publication_allowed
    assert terminal.validations["structural"].valid == structural_valid


@pytest.mark.parametrize("mutation", ["hash", "context", "revision", "schema"])
def test_stale_receipt_cannot_be_warning_or_accepted(mutation):
    report = completion(revisions=2, verdict="revise")
    if mutation == "revision":
        report = replace(report, revisions=1)
    else:
        field = {
            "hash": "judged_artifact_hash",
            "context": "context_id",
            "schema": "schema_version",
        }[mutation]
        report = replace(
            report, semantic=report.semantic.model_copy(update={field: "wrong"})
        )
    terminal = finalize_fixture(report)
    assert terminal.workflow_status == "failed" and terminal.exit_code == 1
    assert not terminal.publication_allowed


def test_fixture_pass_never_claims_real_pdf_publication():
    terminal = finalize_fixture(completion())
    assert terminal.workflow_status == "completed" and terminal.exit_code == 0
    assert terminal.acceptance == "fixture_only"
    assert terminal.reason == "PDF_NOT_VERIFIED"
    assert not terminal.publication_allowed


@pytest.mark.parametrize(
    "report",
    [None, completion(verdict="fail"), completion(revisions=1, verdict="revise")],
)
def test_unavailable_fatal_or_premature_stop_fails(report):
    terminal = finalize_fixture(report)
    assert terminal.workflow_status == "failed"
    assert terminal.run_outcome == "technical_failure"
    assert terminal.exit_code == 1


def test_judge_cannot_bypass_structural_failure():
    report = completion(structural_valid=False, revisions=2)
    semantic = completion(revisions=2, verdict="revise").semantic
    terminal = finalize_fixture(replace(report, semantic=semantic))
    assert terminal.reason == "INVALID_REPORT_STAGE_ORDER"


def test_pdf_revision_exhaustion_and_stale_proof_rejected():
    from skala_rag.contracts import ValidationErrorDetail

    report = completion(revisions=2)
    pdf = ValidationResult(
        schema_version=report.draft.schema_version,
        context_id=report.draft.context_id,
        artifact_hash=artifact_hash(report.draft),
        valid=False,
        checks={"action": "revise"},
        errors=[
            ValidationErrorDetail(
                schema_version=report.draft.schema_version,
                code="PDF_SUMMARY",
                location="pdf",
                message="synthetic",
            )
        ],
    )
    result = finalize_fixture(replace(report, pdf=pdf))
    assert result.workflow_status == "completed" and result.exit_code == 2
    assert "pdf" in result.validations and not result.publication_allowed
    stale = pdf.model_copy(update={"artifact_hash": "other"})
    assert (
        finalize_fixture(replace(report, pdf=stale)).reason == "STALE_REPORT_VALIDATION"
    )
