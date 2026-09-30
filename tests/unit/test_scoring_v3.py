"""Synthetic v3 scoring boundaries; no provider or approved rubric rules."""

from copy import deepcopy
from decimal import Decimal

import pytest

from skala_rag.contracts.v3 import Evaluation
from skala_rag.scoring.aggregate_v3 import ZeroDenominatorV3, aggregate_scores_v3
from skala_rag.scoring.decision_v3 import decide_v3
from skala_rag.scoring.v3_policy import load_v3_policy


@pytest.fixture
def policy():
    return load_v3_policy("configs/scoring.v3.json", execution_mode="fixture")


def evaluations(policy, rating=5, *, missing=(), na=()):
    result = []
    for dimension in (
        "founder",
        "market",
        "technology",
        "moat",
        "traction",
        "deal_terms",
    ):
        criteria = []
        for c in policy.criteria:
            if c.dimension != dimension:
                continue
            status = (
                "not_applicable"
                if c.criterion_id in na
                else "missing"
                if c.criterion_id in missing
                else "observed"
            )
            criteria.append(
                dict(
                    schema_version="v3-test",
                    criterion_id=c.criterion_id,
                    status=status,
                    rating=rating if status == "observed" else None,
                    evidence_ids=["ev-1"] if status == "observed" else [],
                    rationale="synthetic",
                    missing_reason="not disclosed" if status == "missing" else None,
                    applicability_reason="synthetic approved reason"
                    if status == "not_applicable"
                    else None,
                    applicability_rule_id="external-rule"
                    if status == "not_applicable"
                    else None,
                    applicability_evidence_ids=["ev-1"]
                    if status == "not_applicable"
                    else None,
                )
            )
        result.append(
            Evaluation.model_validate(
                dict(
                    schema_version="v3-test",
                    run_id="run",
                    candidate_id="co",
                    evaluation_round=1,
                    snapshot_id="snap",
                    evidence_revision=1,
                    policy_version=policy.policy_version,
                    dimension=dimension,
                    rubric_version="synthetic",
                    criteria=criteria,
                    research_gaps=[],
                    caveats=[],
                )
            )
        )
    return result


def approve(assessment, snapshot):
    return assessment.applicability_rule_id == "external-rule"


def score(policy, items, validator=approve):
    return aggregate_scores_v3(items, policy, applicability_verifier=validator)


def test_missing_included_na_excluded_and_exact_guard(policy):
    missing = {
        "technology.maturity",
        "technology.reliability",
        "technology.integration",
        "technology.commercialization",
        "founder.expertise",
        "founder.industry",
    }
    na = {"traction.revenue_growth", "traction.gross_margin", "traction.rule_of_40"}
    summary = score(policy, evaluations(policy, missing=missing, na=na))
    assert (
        summary.observed_score,
        summary.applicable_weight,
        summary.missing_weight,
        summary.not_applicable_weight,
    ) == (65, 94, 29, 6)
    assert summary.criterion_points["traction.revenue_growth"] is None
    assert summary.weighted_missing_pct > 30
    decision = decide_v3(summary, policy)
    assert decision.label == "WATCHLIST"
    assert "WEIGHTED_MISSING" in decision.reason_codes
    assert "LOW_TECHNOLOGY" in decision.reason_codes


def test_all_five_is_priority_and_noncore_low_not_guard(policy):
    items = evaluations(policy)
    for item in items:
        if item.dimension == "founder":
            for assessment in item.criteria:
                assessment.rating = 1
    summary = score(policy, items)
    assert summary.normalized_score == 96
    assert decide_v3(summary, policy).label == "RECOMMEND_PRIORITY"


def test_zero_dimension_denominator_is_candidate_error(policy):
    with pytest.raises(ZeroDenominatorV3) as exc:
        score(
            policy,
            evaluations(
                policy,
                na={c.criterion_id for c in policy.criteria if c.dimension == "market"},
            ),
        )
    assert exc.value.candidate_id == "co"
    assert exc.value.dimension == "market"


def test_na_requires_supplied_external_approval(policy):
    items = evaluations(policy, na={"traction.revenue_growth"})
    with pytest.raises(ValueError, match="applicability"):
        score(policy, items, validator=None)
    with pytest.raises(ValueError, match="applicability"):
        score(policy, items, validator=lambda a, s: False)


def test_mixed_generation_and_incomplete_rejected(policy):
    items = evaluations(policy)
    with pytest.raises(ValueError, match="six|dimension"):
        score(policy, items[:-1])
    items = deepcopy(items)
    items[0].evaluation_round = 2
    with pytest.raises(ValueError, match="generation"):
        score(policy, items)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("59.99", "PASS"),
        ("60", "WATCHLIST"),
        ("69.99", "WATCHLIST"),
        ("70", "RECOMMEND"),
        ("79.996", "RECOMMEND"),
        ("80", "RECOMMEND_PRIORITY"),
    ],
)
def test_unrounded_score_comparison(policy, value, expected):
    summary = score(policy, evaluations(policy))
    summary.normalized_score = Decimal(value)
    assert decide_v3(summary, policy).label == expected


def test_market_missing_partial_low_ratio(policy):
    summary = score(
        policy, evaluations(policy, missing={"market.size", "market.growth"})
    )
    assert summary.dimension_scores["market"].dimension_score_pct < 40
    assert decide_v3(summary, policy).label == "WATCHLIST"


def test_missing_thirty_exact_guard_and_all_missing_not_negative_evidence(policy):
    market = {c.criterion_id for c in policy.criteria if c.dimension == "market"}
    summary = score(policy, evaluations(policy, missing=market))
    assert summary.weighted_missing_pct == 30
    assert decide_v3(summary, policy).reason_codes == ["WEIGHTED_MISSING", "LOW_MARKET"]
    all_missing = {c.criterion_id for c in policy.criteria}
    summary = score(policy, evaluations(policy, missing=all_missing))
    assert summary.observed_score == 0
    assert summary.applicable_weight == 100
    assert decide_v3(summary, policy).label == "WATCHLIST"


def test_market_exact_forty_guard(policy):
    items = evaluations(policy, rating=5)
    for item in items:
        if item.dimension == "market":
            for assessment in item.criteria:
                assessment.rating = 2
    summary = score(policy, items)
    assert summary.dimension_scores["market"].dimension_score_pct == 40
    assert "LOW_MARKET" in decide_v3(summary, policy).reason_codes


def test_all_na_zero_total_has_no_score(policy):
    all_ids = {c.criterion_id for c in policy.criteria}
    with pytest.raises(ZeroDenominatorV3):
        score(policy, evaluations(policy, na=all_ids))
