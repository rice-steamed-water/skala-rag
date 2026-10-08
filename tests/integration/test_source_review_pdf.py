"""Real offline Korean renderer exercised with synthetic FakeLLM content only."""

from pathlib import Path

import pytest
from pypdf import PdfReader
from tests.unit.test_source_review_report import content, context, draft, judgement, run

from skala_rag.fakes import FakeLLM
from skala_rag.reporting.korean_report import build_korean_report_pdf


@pytest.mark.browser
def test_source_review_uses_existing_korean_pdf_and_layout(tmp_path):
    ctx = context()
    generator = FakeLLM([content()])
    judge = FakeLLM([judgement(ctx, draft(ctx))])
    rendered = []

    def check_pdf(report, fixed, structural, judged):
        render, validation = build_korean_report_pdf(
            fixed, report, structural, judged, tmp_path, "fixture"
        )
        rendered.append(render)
        return validation

    result = run(ctx, generator, judge, check_pdf=check_pdf)
    assert result.status == "completed" and not result.warning
    assert result.validation.valid and result.pdf_validation.valid
    assert not result.final_allowed
    render = rendered[-1]
    assert render.artifact_path is not None
    assert not render.layout_measurements["final_allowed"]
    assert 1 <= render.page_count <= 5
    assert render.layout_measurements["summary_fraction"] <= 0.5
    reader = PdfReader(render.artifact_path)
    text = "\n".join(p.extract_text() for p in reader.pages)
    assert "Dexory" in text
    assert "평가 미실행" in text and "투자 추천 없음" in text
    assert "unscored / unrated" in text
    assert "synthetic-dexory" in text
    assert "fixture://dexory/announcement" in text
    assert "normalized_score" not in text
    assert "기업·팀" in text and "기술" in text and "시장" in text
    html = Path(render.layout_measurements["html_path"]).read_text()
    assert 'id="evidence-1"' in html and 'href="#evidence-1"' in html
    assert "[@source:synthetic-dexory]" in html
    assert 'id="score-overview"' not in html


@pytest.mark.browser
def test_real_pdf_layout_revisions_exhaust_original_budget(tmp_path):
    ctx = context()
    body = content()
    body["summary"] *= 60
    base = draft(ctx, body)
    generator = FakeLLM([body] * 3)
    judge = FakeLLM(
        [judgement(ctx, base.model_copy(update={"revision": i})) for i in range(3)]
    )
    renders = []

    def check_pdf(report, fixed, structural, judged):
        render, validation = build_korean_report_pdf(
            fixed, report, structural, judged, tmp_path, "fixture"
        )
        renders.append(render)
        return validation

    result = run(ctx, generator, judge, check_pdf=check_pdf)
    assert result.status == "completed" and result.warning
    assert result.revisions == 2 and result.error_code == "BUDGET_EXHAUSTED"
    assert len(generator.calls) == len(judge.calls) == len(renders) == 3
    assert not result.final_allowed and not result.pdf_validation.valid
    assert all(r.artifact_path is None for r in renders)
    assert any("SUMMARY" in e.code for e in result.pdf_validation.errors)
