"""Python-direct narrative examples; importing this module performs no work.

Artifact checks are not evidence of real model quality. QA entry points require
operator-owned local model assets; controlled transports remain test-only.
"""

import hashlib
import json
import os
from datetime import date
from pathlib import Path

from pypdf import PdfReader

from skala_rag.company_report import run_company_report
from skala_rag.contracts.company_report import (
    CompanyReportConfigError,
    NarrativeRunResult,
    ResearchLimits,
    load_company_report_config,
)


def scenario_stored_report(
    company_name: str, *, config_path: str | Path, output_dir: str | Path
) -> Path:
    """Run retained evidence with collection explicitly disabled."""
    return run_company_report(
        company_name,
        config_path=Path(config_path),
        output_dir=Path(output_dir),
        research=False,
    )


def scenario_research_target_report(
    company_name: str,
    *,
    config_path: str | Path,
    output_dir: str | Path,
    research_limits: ResearchLimits,
) -> Path:
    """Request only the configured target's bounded optional research."""
    return run_company_report(
        company_name,
        config_path=Path(config_path),
        output_dir=Path(output_dir),
        research=True,
        research_limits={
            "max_calls": research_limits.max_calls,
            "max_cost_usd": research_limits.max_cost_usd,
            "deadline_seconds": research_limits.deadline_seconds,
        },
    )


def _check_report(out: Path, *, research: bool) -> NarrativeRunResult:
    """Check saved artifacts and bound acceptance without claiming actual QA."""
    result = NarrativeRunResult.model_validate_json(
        (out / "run-result.json").read_bytes()
    )
    assert result.outcome in ("completed", "partial")
    assert result.validation == "passed"
    assert result.research_requested is research
    assert result.report_md_path == str(out / "report.md")
    assert result.pdf_path == str(out / "report.pdf")
    assert PdfReader(out / "report.pdf").pages
    assert result.validation_receipt_hashes == [
        hashlib.sha256((out / name).read_bytes()).hexdigest()
        for name in ("judgment.json", "pdf-validation.json", "references.json")
    ]
    judgment = json.loads((out / "judgment.json").read_bytes())
    assert judgment["verdict"] == "pass" and not judgment["issues"]
    return result


def _require_model_assets(config_path: str | Path) -> None:
    config = load_company_report_config(Path(config_path))
    if not config.model_path.is_dir() or not config.model_receipt_path.is_file():
        raise CompanyReportConfigError("ACTUAL_QA_SCOPE_REQUIRED")


def qa_stored(
    company_name: str, *, config_path: str | Path, output_dir: str | Path
) -> Path:
    """Refuse missing local-model prerequisites before any public dispatch."""
    _require_model_assets(config_path)
    out = scenario_stored_report(
        company_name, config_path=config_path, output_dir=output_dir
    )
    _check_report(out, research=False)
    return out


def qa_research(
    company_name: str,
    *,
    config_path: str | Path,
    output_dir: str | Path,
    research_limits: ResearchLimits,
) -> Path:
    """Require local-model prerequisites and explicit research limits."""
    _require_model_assets(config_path)
    out = scenario_research_target_report(
        company_name,
        config_path=config_path,
        output_dir=output_dir,
        research_limits=research_limits,
    )
    _check_report(out, research=True)
    return out


if __name__ == "__main__":
    output = run_company_report(
        os.environ["SKALA_COMPANY_NAME"],
        config_path=Path(os.environ["SKALA_COMPANY_REPORT_CONFIG"]),
        output_dir=Path(os.environ["SKALA_COMPANY_REPORT_OUTPUT"]),
        research=False,
        as_of=date(2026, 10, 10),
    )
    result = _check_report(output, research=False)
    print(result.model_dump_json())
