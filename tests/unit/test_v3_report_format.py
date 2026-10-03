"""Synthetic presentation fixtures, not calculated scores or live reports."""

import json
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError

from skala_rag import contracts as baseline
from skala_rag.contracts.v3 import InvestmentDecision, ScoreSummary
from skala_rag.reporting.v3_format import format_fixture_score_decision


@pytest.fixture
def summary():
    return ScoreSummary(
        schema_version="v3-fixture",
        score_summary_id="score-fixture",
        run_id="run-fixture",
        candidate_id="candidate-fixture",
        evaluation_round=1,
        snapshot_id="snapshot-fixture",
        evidence_revision=1,
        policy_version="synthetic-not-approved",
        criterion_points={
            "observed-fixture": Decimal("60.00000000000000000001"),
            "missing-fixture": None,
            "na-fixture": None,
        },
        dimension_scores={
            "market": {
                "schema_version": "v3-fixture",
                "observed_score": "12.00000000000000000001",
                "applicable_weight": "20.00",
                "not_applicable_weight": "10.00",
                "missing_weight": "5.00",
                "dimension_score_pct": "60.00000000000000000005",
            },
        },
        observed_score=Decimal("60.00000000000000000001"),
        applicable_weight=Decimal("80.00"),
        not_applicable_weight=Decimal("20.00"),
        missing_weight=Decimal("10.00"),
        normalized_score=Decimal("75.0000000000000000000125"),
        weighted_missing_pct=Decimal("12.50"),
        coverage_pct=Decimal("87.50"),
        low_score_dimensions=[],
        hold_reasons=["Synthetic observation; not a policy calculation"],
    )


@pytest.fixture
def decision(summary):
    return InvestmentDecision(
        schema_version="v3-fixture",
        decision_id="decision-fixture",
        run_id=summary.run_id,
        candidate_id=summary.candidate_id,
        score_summary_id=summary.score_summary_id,
        label="RECOMMEND",
        report_grade="synthetic-grade",
        reason_codes=["synthetic-reason"],
        evidence_ids=[],
        rationale="Synthetic observation, not an investment recommendation",
        risks=["synthetic-risk"],
        limitations=["Offline fixture only"],
    )


def test_lossless_na_score_presentation(summary, decision):
    result = format_fixture_score_decision(summary, decision)

    assert result["execution_mode"] == "fixture"
    assert result["score_basis"] == "normalized_score"
    score = result["score_summary"]
    assert score["normalized_score"] == "75.0000000000000000000125"
    assert score["observed_score"] == "60.00000000000000000001"
    assert score["applicable_weight"] == "80.00"
    assert score["not_applicable_weight"] == "20.00"
    assert score["missing_weight"] == "10.00"
    assert score["weighted_missing_pct"] == "12.50"
    assert score["coverage_pct"] == "87.50"
    assert score["dimension_scores"]["market"] == {
        "schema_version": "v3-fixture",
        "observed_score": "12.00000000000000000001",
        "applicable_weight": "20.00",
        "not_applicable_weight": "10.00",
        "missing_weight": "5.00",
        "dimension_score_pct": "60.00000000000000000005",
    }
    assert score["criterion_points"] == {
        "observed-fixture": "60.00000000000000000001",
        "missing-fixture": None,
        "na-fixture": None,
    }
    assert result["decision"]["label"] == "RECOMMEND"
    assert result["decision"]["report_grade"] == "synthetic-grade"
    assert json.loads(json.dumps(result)) == result
    assert ScoreSummary.model_validate(score) == summary
    assert InvestmentDecision.model_validate(result["decision"]) == decision
    assert "/100" not in json.dumps(result)
    assert "dimension_ratings" not in score


@pytest.mark.parametrize("field", ["score_summary_id", "candidate_id", "run_id"])
def test_rejects_decision_summary_identity_mismatch(summary, decision, field):
    mismatched = decision.model_copy(update={field: "other-fixture"})
    with pytest.raises(ValueError, match=field):
        format_fixture_score_decision(summary, mismatched)


@pytest.mark.parametrize("baseline_input", ["score_summary", "decision"])
def test_rejects_baseline_dtos(summary, decision, baseline_input):
    if baseline_input == "score_summary":
        payload = summary.model_dump()
        for field in (
            "dimension_scores",
            "applicable_weight",
            "not_applicable_weight",
            "normalized_score",
            "weighted_missing_pct",
        ):
            del payload[field]
        payload["dimension_ratings"] = {"market": Decimal("3")}
        summary = baseline.ScoreSummary.model_validate(payload)
    else:
        decision = baseline.InvestmentDecision.model_validate(decision.model_dump())
    with pytest.raises(ValidationError):
        format_fixture_score_decision(summary, decision)


@pytest.mark.parametrize("target", ["summary", "dimension", "decision"])
def test_revalidates_mutable_dtos(summary, decision, target):
    if target == "summary":
        summary.normalized_score = Decimal("NaN")
    elif target == "dimension":
        summary.dimension_scores["market"].dimension_score_pct = Decimal("Infinity")
    else:
        decision.label = "INVALID"
    with pytest.raises(ValidationError):
        format_fixture_score_decision(summary, decision)


@pytest.mark.parametrize(
    "label", ["RECOMMEND_PRIORITY", "RECOMMEND", "WATCHLIST", "PASS"]
)
def test_preserves_all_four_decision_labels(summary, decision, label):
    decision.label = label
    result = format_fixture_score_decision(summary, decision)
    assert result["decision"]["label"] == label
    assert InvestmentDecision.model_validate(result["decision"]) == decision


@pytest.mark.parametrize(
    "field",
    [
        "observed_score",
        "applicable_weight",
        "not_applicable_weight",
        "missing_weight",
        "normalized_score",
        "weighted_missing_pct",
        "coverage_pct",
    ],
)
@pytest.mark.parametrize("value", [None, Decimal("0.00")])
def test_preserves_summary_unavailable_numbers_and_zero(
    summary, decision, field, value
):
    setattr(summary, field, value)
    result = format_fixture_score_decision(summary, decision)
    assert result["score_summary"][field] == (None if value is None else "0.00")


@pytest.mark.parametrize(
    "field",
    [
        "observed_score",
        "applicable_weight",
        "not_applicable_weight",
        "missing_weight",
        "dimension_score_pct",
    ],
)
@pytest.mark.parametrize("value", [None, Decimal("0.00")])
def test_preserves_dimension_unavailable_numbers_and_zero(
    summary, decision, field, value
):
    setattr(summary.dimension_scores["market"], field, value)
    result = format_fixture_score_decision(summary, decision)
    assert result["score_summary"]["dimension_scores"]["market"][field] == (
        None if value is None else "0.00"
    )


def test_does_not_invent_absent_dimensions_or_optional_numbers(summary, decision):
    payload = summary.model_dump()
    for field in (
        "observed_score",
        "applicable_weight",
        "not_applicable_weight",
        "missing_weight",
        "normalized_score",
        "weighted_missing_pct",
        "coverage_pct",
    ):
        del payload[field]
    payload["dimension_scores"] = {"market": {"schema_version": "v3-fixture"}}
    sparse = ScoreSummary.model_validate(payload)
    result = format_fixture_score_decision(sparse, decision)["score_summary"]
    assert result["normalized_score"] is None
    assert result["observed_score"] is None
    assert result["weighted_missing_pct"] is None
    assert result["dimension_scores"] == {
        "market": {
            "schema_version": "v3-fixture",
            "observed_score": None,
            "applicable_weight": None,
            "not_applicable_weight": None,
            "missing_weight": None,
            "dimension_score_pct": None,
        },
    }
    assert ScoreSummary.model_validate(result) == sparse


def test_preserves_all_six_dimensions(summary, decision):
    dimensions = ("founder", "market", "technology", "moat", "traction", "deal_terms")
    summary.dimension_scores = {
        dim: summary.dimension_scores["market"].model_copy(deep=True)
        for dim in dimensions
    }
    result = format_fixture_score_decision(summary, decision)
    assert set(result["score_summary"]["dimension_scores"]) == set(dimensions)
    assert ScoreSummary.model_validate(result["score_summary"]) == summary


def test_decimal_representation_survives_low_precision_context(summary, decision):
    summary.criterion_points["exponent-fixture"] = Decimal("1.2300E-20")
    with localcontext() as context:
        context.prec = 2
        result = format_fixture_score_decision(summary, decision)
    restored = ScoreSummary.model_validate(result["score_summary"])
    assert restored.normalized_score.as_tuple() == summary.normalized_score.as_tuple()
    assert restored.criterion_points["exponent-fixture"].as_tuple() == (
        summary.criterion_points["exponent-fixture"].as_tuple()
    )
    assert restored.applicable_weight.as_tuple() == summary.applicable_weight.as_tuple()


def test_result_does_not_alias_or_mutate_inputs(summary, decision):
    before_score = summary.model_dump()
    before_decision = decision.model_dump()
    result = format_fixture_score_decision(summary, decision)
    result["score_summary"]["dimension_scores"]["market"]["observed_score"] = "1"
    result["score_summary"]["hold_reasons"].append("changed")
    result["decision"]["risks"].append("changed")
    assert summary.model_dump() == before_score
    assert decision.model_dump() == before_decision
