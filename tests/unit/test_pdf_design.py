"""Automated fixture PDF checks only; no visual/live review."""

import re
from copy import deepcopy
from decimal import Decimal

import pytest
from pypdf import PdfReader
from reportlab.lib.styles import ParagraphStyle
from tests.integration.test_v3_candidates import scenario
from tests.unit.test_pdf_renderer import make_draft, renderer
from tests.unit.test_v3_report_pipeline import Stub, context

from skala_rag.reporting.pdf import PDFLayoutValidator
from skala_rag.reporting.pdf_presentation import ScoreBar, validated_presentation
from skala_rag.reporting.v3_context import build_report_context_v3
from skala_rag.reporting.v3_pipeline import (
    ReportGeneratorV3,
    SemanticJudgeV3,
    validate_report_v3,
)


def test_v3_validated_scores_reach_real_pdf_design(tmp_path):
    ctx = context()
    draft = ReportGeneratorV3(Stub())(ctx, [])
    original = draft.model_dump_json()
    structural = validate_report_v3(draft, ctx)
    judged = SemanticJudgeV3(Stub())(draft, ctx)
    result = renderer(tmp_path, lambda _: (structural, judged))(draft, "pdf-layout-v1")
    assert result.errors == []
    assert PDFLayoutValidator()(draft, ctx, result).valid
    assert result.layout_measurements["presentation_version"] == "investment-report-v1"
    assert result.layout_measurements["visualizations"] == {
        "score_cards": 1,
        "dimension_bars": 6,
        "candidate_rows": 1,
    }
    text = "\n".join(p.extract_text() for p in PdfReader(result.artifact_path).pages)
    compact = re.sub(r"\s+", "", text)
    for expected in [
        "후보 비교",
        "영역별 점수",
        "normalized_score",
        "RECOMMEND_PRIORITY",
    ]:
        assert re.sub(r"\s+", "", expected) in compact
    assert "FIXTURE" in text and "PAGE 1" in text
    assert result.page_count <= 5
    assert result.layout_measurements["summary_fraction"] <= 0.5
    assert not result.layout_measurements["final_allowed"]
    assert draft.model_dump_json() == original
    for number, page in enumerate(PdfReader(result.artifact_path).pages, 1):
        assert f"PAGE {number}" in page.extract_text()
        assert "INVESTMENT REVIEW" in page.extract_text()


def test_presentation_values_cannot_diverge_from_validated_draft(tmp_path):
    ctx = context()
    draft = ReportGeneratorV3(Stub())(ctx, [])
    structural = validate_report_v3(draft, ctx)
    judged = SemanticJudgeV3(Stub())(draft, ctx)
    structural.checks["pdf_presentation"]["scores"]["company-0"]["normalized_score"] = (
        "99.125"
    )
    result = renderer(tmp_path, lambda _: (structural, judged))(draft, "pdf-layout-v1")
    assert result.artifact_path is None
    assert result.layout_measurements["final_allowed"] is False


@pytest.mark.parametrize("rating,statuses", [(1, ("eligible", "unknown")), (5, ())])
def test_no_selection_and_unscored_candidates_stay_unscored(tmp_path, rating, statuses):
    ctx = context(rating=rating, statuses=statuses)
    draft = ReportGeneratorV3(Stub())(ctx, [])
    structural = validate_report_v3(draft, ctx)
    judged = SemanticJudgeV3(Stub())(draft, ctx)
    result = renderer(tmp_path, lambda _: (structural, judged))(draft, "pdf-layout-v1")
    assert result.errors == []
    assert PDFLayoutValidator()(draft, ctx, result).valid
    metrics = result.layout_measurements["visualizations"]
    assert metrics["candidate_rows"] == len(statuses)
    assert metrics["score_cards"] == (1 if statuses else 0)
    assert not result.layout_measurements["final_allowed"]
    text = "\n".join(p.extract_text() for p in PdfReader(result.artifact_path).pages)
    assert "선택 결과: 없음" in text
    if statuses:
        assert "company-1/eligibility_unknown" in re.sub(r"\s+", "", text)
        assert "미상" in text
        assert "RECOMMEND_PRIORITY" not in text
    else:
        assert "성공 평가 후보 없음" in text
        assert "normalized_score" not in text


def test_na_weight_is_preserved_in_actual_pdf(tmp_path):
    from skala_rag.scoring.v3_policy import load_v3_policy

    policy = load_v3_policy("configs/scoring.v3.json", execution_mode="fixture")
    snapshots = {}
    result, _ = scenario(
        statuses=("eligible",),
        ratings=(5,),
        na=[c.criterion_id for c in policy.criteria if c.dimension == "deal_terms"][:1],
        freeze_mutation=lambda raw: snapshots.update(
            {raw["candidate_id"]: deepcopy(raw)}
        ),
    )
    from datetime import date

    snap = snapshots["company-0"]
    ctx = build_report_context_v3(
        result,
        snapshots,
        as_of=date.fromisoformat(snap["as_of"]),
        corpus_version=snap["corpus_version"],
        execution_mode="fixture",
    )
    draft = ReportGeneratorV3(Stub())(ctx, [])
    structural = validate_report_v3(draft, ctx)
    judged = SemanticJudgeV3(Stub())(draft, ctx)
    render = renderer(tmp_path, lambda _: (structural, judged))(draft, "pdf-layout-v1")
    assert render.errors == []
    assert PDFLayoutValidator()(draft, ctx, render).valid
    assert render.layout_measurements["visualizations"]["dimension_bars"] == 6
    projected = validated_presentation(
        structural.checks["pdf_presentation"], draft, "fixture"
    )
    dimension = projected[0]["company-0"].dimension_scores["deal_terms"]
    assert dimension.dimension_score_pct is not None
    assert dimension.not_applicable_weight > 0
    assert (
        dimension.not_applicable_weight
        == result.scores["company-0"]
        .dimension_scores["deal_terms"]
        .not_applicable_weight
    )
    text = "\n".join(p.extract_text() for p in PdfReader(render.artifact_path).pages)
    assert "not_applicable_weight" in re.sub(r"\s+", "", text)


def test_unstructured_prose_does_not_create_visual_scores(tmp_path):
    draft = make_draft(
        summary="normalized_score 99.99; technology 80 [@evidence:ev-fixture]"
    )
    result = renderer(tmp_path)(draft, "pdf-layout-v1")
    assert result.errors == []
    assert result.layout_measurements["visualizations"] == {
        "score_cards": 0,
        "dimension_bars": 0,
        "candidate_rows": 0,
    }


def test_bar_uses_exact_observation_and_distinguishes_none_from_zero():
    style = ParagraphStyle("test")
    precise = Decimal("33.333333333333333333333333333333")
    bar = ScoreBar(precise, style)
    assert bar.label.getPlainText() == str(precise)
    assert bar.ratio == precise / Decimal(100)
    missing, zero = ScoreBar(None, style), ScoreBar(Decimal("0.00"), style)
    assert missing.ratio is None and missing.label.getPlainText() == "미상"
    assert zero.ratio == 0 and zero.label.getPlainText() == "0.00"
    assert missing.wrap(150, 100)[1] < zero.wrap(150, 100)[1]
    for invalid in [Decimal("NaN"), Decimal("Infinity"), Decimal("-1"), Decimal("101")]:
        with pytest.raises(ValueError):
            ScoreBar(invalid, style)


@pytest.mark.parametrize(
    "field,value",
    [("context_id", "other"), ("draft_hash", "old"), ("execution_mode", "live")],
)
def test_stale_presentation_metadata_fails_closed(tmp_path, field, value):
    ctx = context()
    draft = ReportGeneratorV3(Stub())(ctx, [])
    structural = validate_report_v3(draft, ctx)
    judged = SemanticJudgeV3(Stub())(draft, ctx)
    structural.checks["pdf_presentation"][field] = value
    result = renderer(tmp_path, lambda _: (structural, judged))(draft, "pdf-layout-v1")
    assert result.artifact_path is None
    assert list(tmp_path.iterdir()) == []


def test_failed_structural_validation_never_exposes_visualization_payload():
    ctx = context()
    draft = ReportGeneratorV3(Stub())(ctx, [])
    draft.markdown = draft.markdown.replace("normalized_score", "invented_score")
    structural = validate_report_v3(draft, ctx)
    assert not structural.valid
    assert "pdf_presentation" not in structural.checks
