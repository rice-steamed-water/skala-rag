"""한글 HTML→PDF 보고서 직접 호출 helper (#175). CLI/서버 없이 Python에서 호출한다."""

from pathlib import Path

from skala_rag.contracts import ReportDraft, ReportJudgement, ValidationResult
from skala_rag.contracts.tools import RenderResult
from skala_rag.reporting.html_pdf import HTMLPDFLayoutValidator, HTMLPDFRenderer
from skala_rag.reporting.v3_context import ReportContextV3

TEMPLATE = "html-v1"


def build_korean_report_pdf(
    context: ReportContextV3,
    draft: ReportDraft,
    structural: ValidationResult,
    judgement: ReportJudgement,
    output_dir: Path,
    execution_mode: str,
) -> tuple[RenderResult, ValidationResult]:
    """render 실패(artifact_path=None)면 layout 검증 없이 RenderResult만 의미가 있다."""
    render = HTMLPDFRenderer(
        output_dir=output_dir,
        proof=lambda _: (structural, judgement),
        execution_mode=execution_mode,
        context=context,
    )(draft, TEMPLATE)
    if render.artifact_path is None:
        validation = ValidationResult(
            schema_version=draft.schema_version,
            valid=False,
            context_id=draft.context_id,
            checks={"action": "fail"},
            errors=render.errors,
            artifact_hash=render.layout_measurements["draft_hash"],
        )
        return render, validation
    return render, HTMLPDFLayoutValidator()(draft, context, render)
