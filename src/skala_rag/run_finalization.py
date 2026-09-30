"""Fixture-only runner terminal interface; does not implement #94 reporting.

Outcome is supplied explicitly by the reporting adapter. No v3 mode/outcome policy
is inferred here. Fixture validation can never authorize submission publication.
"""

from dataclasses import dataclass, field

from skala_rag.contracts import ReportDraft, ReportJudgement, ValidationResult
from skala_rag.contracts.state import RunOutcome
from skala_rag.reporting.validator import artifact_hash


@dataclass(frozen=True)
class ReportCompletion:
    outcome: RunOutcome
    draft: ReportDraft
    structural: ValidationResult | None = None
    semantic: ReportJudgement | None = None
    revisions: int = 0
    fatal: bool = False
    pdf: ValidationResult | None = None


@dataclass(frozen=True)
class TerminalResult:
    workflow_status: str
    run_outcome: RunOutcome
    exit_code: int
    acceptance: str
    warnings: tuple[str, ...] = ()
    reason: str | None = None
    publication_allowed: bool = False
    validations: dict = field(default_factory=dict)


def finalize_fixture(report: ReportCompletion | None) -> TerminalResult:
    """Validate terminal receipts, preserving current draft findings on Warning.

    The reporting controller owns revision requests. This function only checks its
    terminal receipt and cannot perform or silently reset any retries.
    """

    def failed(reason):
        return TerminalResult(
            "failed", RunOutcome.TECHNICAL_FAILURE, 1, "rejected", reason=reason
        )

    if report is None:
        return failed("REPORT_ADAPTER_UNAVAILABLE")
    if not isinstance(report, ReportCompletion):
        return failed("INVALID_REPORT_COMPLETION")
    if (
        type(report.revisions) is not int
        or not 0 <= report.revisions <= 2
        or type(report.fatal) is not bool
    ):
        return failed("INVALID_REPORT_REVISION_COUNT")
    try:
        outcome = RunOutcome(report.outcome)
        draft = ReportDraft.model_validate(report.draft)
        structural = (
            ValidationResult.model_validate(report.structural)
            if report.structural is not None
            else None
        )
        pdf = (
            ValidationResult.model_validate(report.pdf)
            if report.pdf is not None
            else None
        )
        semantic = (
            ReportJudgement.model_validate(report.semantic)
            if report.semantic is not None
            else None
        )
    except (ValueError, TypeError):
        return failed("INVALID_REPORT_COMPLETION")
    if draft.revision != report.revisions:
        return failed("REPORT_REVISION_MISMATCH")
    expected_hash = artifact_hash(draft)
    for validation, value in (
        (structural, structural.artifact_hash if structural else None),
        (semantic, semantic.judged_artifact_hash if semantic else None),
        (pdf, pdf.artifact_hash if pdf else None),
    ):
        if validation and (
            validation.context_id != draft.context_id
            or value != expected_hash
            or validation.schema_version != draft.schema_version
        ):
            return failed("STALE_REPORT_VALIDATION")
    validations = {}
    if structural:
        validations["structural"] = structural
    if semantic:
        validations["semantic"] = semantic
    if pdf:
        validations["pdf"] = pdf
    if (
        report.fatal
        or outcome == RunOutcome.TECHNICAL_FAILURE
        or (semantic and semantic.verdict == "fail")
        or (structural and structural.checks.get("action") == "fail")
        or (pdf and pdf.checks.get("action") == "fail")
    ):
        terminal = failed("REPORT_FATAL")
        return TerminalResult(**{**terminal.__dict__, "validations": validations})
    # Judge may only run on the current structurally valid draft.
    if semantic and (structural is None or not structural.valid):
        return failed("INVALID_REPORT_STAGE_ORDER")
    if pdf and (
        not structural
        or not structural.valid
        or not semantic
        or semantic.verdict != "pass"
    ):
        return failed("INVALID_REPORT_STAGE_ORDER")
    revise = (
        (
            structural is not None
            and not structural.valid
            and structural.checks.get("action") == "revise"
        )
        or (semantic is not None and semantic.verdict == "revise")
        or (pdf is not None and not pdf.valid and pdf.checks.get("action") == "revise")
    )
    if revise and report.revisions == 2:
        return TerminalResult(
            "completed",
            outcome,
            2,
            "warning",
            warnings=("Warning: report revisions exhausted",),
            validations=validations,
        )
    if (
        structural
        and structural.valid
        and semantic
        and semantic.verdict == "pass"
        and (pdf is None or pdf.valid)
    ):
        return TerminalResult(
            "completed",
            outcome,
            0,
            "fixture_only",
            reason="FIXTURE_PDF_VERIFIED"
            if pdf and pdf.checks.get("pdf_verified") is True
            else "PDF_NOT_VERIFIED",
            validations=validations,
        )
    terminal = failed("INCOMPLETE_REPORT_VALIDATION")
    return TerminalResult(**{**terminal.__dict__, "validations": validations})
