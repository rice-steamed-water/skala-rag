"""한글 HTML→PDF fixture 예제. `uv run python examples/korean_report_fixture.py`

가상 fixture이며 실제 LLM/API/기업 조사를 호출하지 않는다. 결과는 실측이 아니다.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # tests의 fixture helper 재사용

from tests.unit.test_v3_report_pipeline import context  # noqa: E402

from skala_rag.fixture_reporting import FixtureReportLLM  # noqa: E402
from skala_rag.reporting.korean_report import build_korean_report_pdf  # noqa: E402
from skala_rag.reporting.v3_pipeline import (  # noqa: E402
    ReportGeneratorV3,
    SemanticJudgeV3,
    run_report_v3,
)


def main():
    ctx = context()
    out = ROOT / "outputs" / "issue-175-demo" / datetime.now().strftime("%Y%m%dT%H%M%S")
    llm = FixtureReportLLM()
    renders = []

    def check_pdf(draft, context, structural, judged):
        render, validation = build_korean_report_pdf(
            context, draft, structural, judged, out, "fixture"
        )
        renders.append(render)
        return validation

    run = run_report_v3(
        ctx,
        generate=ReportGeneratorV3(llm),
        judge=SemanticJudgeV3(llm),
        check_pdf=check_pdf,
    )
    render = renders[-1] if renders else None
    m = render.layout_measurements if render else {}
    print(
        json.dumps(
            dict(
                status=run.status,
                warning=run.warning,
                error_code=run.error_code,
                pdf_valid=run.pdf_validation.valid if run.pdf_validation else None,
                final_allowed=run.final_allowed,
                execution_mode="fixture",
                html_path=m.get("html_path"),
                pdf_path=render.artifact_path if render else None,
                html_hash=m.get("html_hash"),
                pdf_hash=m.get("artifact_hash"),
                page_count=render.page_count if render else None,
                summary_fraction=m.get("summary_fraction"),
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if run.status == "completed" and not run.warning else 1


if __name__ == "__main__":
    raise SystemExit(main())
