"""#68: aggregate/decide 결과를 #6 ScoreSummary·InvestmentDecision으로 감싼다."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from skala_rag.contracts import (
    CriterionAssessment,
    Evaluation,
    EvaluationResult,
    InvestmentDecision,
    ScoreSummary,
    WorkflowError,
    decision_id,
    score_summary_id,
)
from skala_rag.scoring import (
    ScoringError,
    build_investment_decision,
    build_score_summary,
)
from skala_rag.scoring.catalog import load_policy

ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
FIXTURE = json.loads((ROOT / "tests/fixtures/scoring.draft.json").read_text())
SV = "synthetic-1"
DIMS = ("founder", "market", "technology", "moat", "traction", "deal_terms")


def _assessment(cid, rating):
    if rating is None:
        return CriterionAssessment(
            schema_version=SV,
            criterion_id=cid,
            status="missing",
            rating=None,
            evidence_ids=[],
            rationale="가상 근거 부족",
            missing_reason="not_disclosed",
        )
    return CriterionAssessment(
        schema_version=SV,
        criterion_id=cid,
        status="observed",
        rating=rating,
        evidence_ids=[f"ev-{cid}"],
        rationale="가상 근거",
    )


def _results(ratings, *, run_id="run-1", candidate="cand-1", round_=1):
    out = []
    for dim in DIMS:
        ev = Evaluation(
            schema_version=SV,
            run_id=run_id,
            candidate_id=candidate,
            dimension=dim,
            evaluation_round=round_,
            snapshot_id="snap-1",
            evidence_revision=2,
            policy_version=POLICY.policy_version,
            rubric_version="fixture",
            criteria=[
                _assessment(c.criterion_id, ratings[c.criterion_id])
                for c in POLICY.criteria
                if c.dimension == dim
            ],
            research_gaps=[],
            caveats=[],
        )
        out.append(
            EvaluationResult(
                schema_version=SV,
                run_id=run_id,
                candidate_id=candidate,
                dimension=dim,
                evaluation_round=round_,
                snapshot_id="snap-1",
                evidence_revision=2,
                policy_version=POLICY.policy_version,
                status="success",
                evaluation=ev,
                errors=[],
            )
        )
    return out


@pytest.mark.parametrize("case", FIXTURE["calculations"], ids=lambda c: c["case_id"])
def test_fixture_cases_through_dtos(case):
    summary = build_score_summary(_results(case["ratings"]), POLICY, schema_version=SV)
    exp = case["expected"]
    assert summary.observed_score == Decimal(exp["observed_score"])
    assert summary.missing_weight == Decimal(exp["missing_weight"])
    assert summary.coverage_pct == Decimal(exp["coverage_pct"])
    decision = build_investment_decision(
        summary, POLICY, schema_version=SV, evidence_ids=[], rationale="가상 설명"
    )
    assert decision.label == exp["label"]
    assert decision.report_grade == exp["report_grade"]


def test_ids_are_deterministic_and_linked():
    ratings = {c.criterion_id: 4 for c in POLICY.criteria}
    summary = build_score_summary(_results(ratings), POLICY, schema_version=SV)
    assert summary.score_summary_id == score_summary_id(
        "run-1", "cand-1", 1, POLICY.policy_version
    )
    decision = build_investment_decision(
        summary, POLICY, schema_version=SV, evidence_ids=["ev-a"], rationale="설명"
    )
    assert decision.decision_id == decision_id(summary.score_summary_id)
    assert decision.score_summary_id == summary.score_summary_id


def test_hold_reasons_and_low_dimensions():
    case = next(c for c in FIXTURE["calculations"] if c["case_id"] == "founder_one")
    summary = build_score_summary(_results(case["ratings"]), POLICY, schema_version=SV)
    assert summary.low_score_dimensions == ["founder"]
    assert summary.hold_reasons == ["LOW_DIMENSION:founder"]


def test_json_roundtrip_keeps_decimal_precision():
    ratings = {c.criterion_id: 5 for c in POLICY.criteria}
    ratings["traction.revenue_growth"] = 1  # traction 3.8
    ratings["founder.execution"] = 2  # founder (10+10+2)/5 = 4.4
    summary = build_score_summary(_results(ratings), POLICY, schema_version=SV)
    again = ScoreSummary.model_validate_json(summary.model_dump_json())
    assert again == summary
    assert again.dimension_ratings["traction"] == Decimal("3.8")
    assert again.dimension_ratings["founder"] == Decimal("4.4")


def test_rationale_cannot_change_label():
    ratings = {c.criterion_id: 2 for c in POLICY.criteria}
    summary = build_score_summary(_results(ratings), POLICY, schema_version=SV)
    decision = build_investment_decision(
        summary,
        POLICY,
        schema_version=SV,
        evidence_ids=[],
        rationale="매우 유망하므로 추천",  # 설명은 판단을 바꾸지 못한다
    )
    assert decision.label == "WATCHLIST"
    assert (
        InvestmentDecision.model_validate_json(decision.model_dump_json()) == decision
    )


def test_failed_evaluation_is_not_aggregated():
    ratings = {c.criterion_id: 5 for c in POLICY.criteria}
    results = _results(ratings)
    results[5] = EvaluationResult(
        schema_version=SV,
        run_id="run-1",
        candidate_id="cand-1",
        dimension="deal_terms",
        evaluation_round=1,
        snapshot_id="snap-1",
        evidence_revision=2,
        policy_version=POLICY.policy_version,
        status="failure",
        evaluation=None,
        errors=[
            WorkflowError(
                schema_version=SV,
                error_id="err-1",
                run_id="run-1",
                candidate_id="cand-1",
                node="deal_terms_evaluation",
                error_code="SCHEMA_INVALID",
                message_redacted="가상 오류",
                retryable=False,
                attempt=1,
                timestamp="2026-09-30T00:00:00+09:00",
            )
        ],
    )
    with pytest.raises(ScoringError) as err:
        build_score_summary(results, POLICY, schema_version=SV)
    assert err.value.code == "EVALUATION_FAILED"


def test_mixed_run_rejected():
    ratings = {c.criterion_id: 5 for c in POLICY.criteria}
    results = _results(ratings)
    results[0] = _results(ratings, run_id="run-2")[0]
    with pytest.raises(ScoringError) as err:
        build_score_summary(results, POLICY, schema_version=SV)
    assert err.value.code == "MIXED_GENERATION"


def test_missing_deal_terms_rejected():
    ratings = {c.criterion_id: 5 for c in POLICY.criteria}
    with pytest.raises(ScoringError) as err:
        build_score_summary(_results(ratings)[:5], POLICY, schema_version=SV)
    assert err.value.code == "INCOMPLETE_EVALUATIONS"


def test_decision_policy_mismatch_rejected():
    ratings = {c.criterion_id: 5 for c in POLICY.criteria}
    summary = build_score_summary(_results(ratings), POLICY, schema_version=SV)
    other = summary.model_copy(update={"policy_version": "other"})
    with pytest.raises(ScoringError):
        build_investment_decision(
            other, POLICY, schema_version=SV, evidence_ids=[], rationale="x"
        )
