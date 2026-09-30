"""#16: aggregate_scores·decide — T02·T03 및 #9 기대값 fixture."""

import json
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

import pytest

from skala_rag.scoring import (
    ScoringError,
    aggregate_scores,
    compute_scores,
    decide,
    decide_breakdown,
)
from skala_rag.scoring.catalog import load_policy

ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
FIXTURE = json.loads((ROOT / "tests/fixtures/scoring.draft.json").read_text())
T = POLICY.thresholds


def _dec(value):
    return None if value is None else Decimal(value)


# --- #9 계산 fixture 7종 -------------------------------------------------


@pytest.mark.parametrize("case", FIXTURE["calculations"], ids=lambda c: c["case_id"])
def test_calculation_fixture(case):
    breakdown = compute_scores(case["ratings"], POLICY)
    expected = case["expected"]
    assert breakdown.observed_score == Decimal(expected["observed_score"])
    assert breakdown.missing_weight == Decimal(expected["missing_weight"])
    assert breakdown.coverage_pct == Decimal(expected["coverage_pct"])
    decision = decide_breakdown(breakdown, T)
    assert decision.label == expected["label"]
    assert decision.report_grade == expected["report_grade"]


# --- #9 직접 입력 경계값 12종 (T03) ---------------------------------------


@pytest.mark.parametrize("case", FIXTURE["boundaries"], ids=lambda c: c["case_id"])
def test_boundary_fixture(case):
    decision = decide(
        Decimal(case["observed_score"]),
        Decimal(case["missing_weight"]),
        {k: _dec(v) for k, v in case["dimension_ratings"].items()},
        T,
    )
    assert decision.label == case["label"]
    assert decision.report_grade == case["report_grade"]


def test_all_reasons_recorded_with_top_row_as_grade():
    case = next(c for c in FIXTURE["boundaries"] if c["case_id"] == "both_overrides")
    decision = decide(
        case["observed_score"],
        case["missing_weight"],
        {k: _dec(v) for k, v in case["dimension_ratings"].items()},
        T,
    )
    assert decision.reason_codes == (
        "INSUFFICIENT_EVIDENCE",
        "LOW_DIMENSION:founder",
        "SCORE_PRIORITY",
    )
    assert decision.low_score_dimensions == ("founder",)


# --- §3 산술 규칙 (T02) ----------------------------------------------------


def _ratings(value=5, **overrides):
    ratings = {c.criterion_id: value for c in POLICY.criteria}
    ratings.update({k.replace("__", "."): v for k, v in overrides.items()})
    return ratings


def test_no_renormalization_when_missing():
    ratings = _ratings(deal_terms__valuation=None)
    b = compute_scores(ratings, POLICY)
    assert b.observed_score == Decimal(95)
    assert b.missing_weight == Decimal(5)
    assert b.criterion_points["deal_terms.valuation"] is None
    assert b.dimension_ratings["deal_terms"] == Decimal(5)


def test_whole_dimension_missing_is_null_not_low():
    b = compute_scores(
        _ratings(
            founder__expertise=None, founder__industry=None, founder__execution=None
        ),
        POLICY,
    )
    assert b.dimension_ratings["founder"] is None
    assert decide_breakdown(b, T).low_score_dimensions == ()


def test_weight_one_full_marks_is_not_low_score():
    """비중 1 criterion의 기여 점수 1을 '2점 이하'로 오판하지 않는다."""
    b = compute_scores(_ratings(), POLICY)
    assert b.criterion_points["traction.burn"] == Decimal(1)
    assert decide_breakdown(b, T).low_score_dimensions == ()


def test_dimension_rating_is_weighted_average():
    # traction: 3×1 + 2×5 + 1×5 + 2×5 + 1×5 + 1×5 = 38 / 10
    b = compute_scores(_ratings(traction__revenue_growth=1), POLICY)
    assert b.dimension_ratings["traction"] == Decimal("3.8")


@pytest.mark.parametrize("bad", [0, 6, 2.5, True, "3", float("nan")])
def test_invalid_rating_rejected(bad):
    with pytest.raises(ScoringError) as err:
        compute_scores(_ratings(market__size=bad), POLICY)
    assert err.value.code == "INVALID_RATING"


def test_missing_key_is_not_treated_as_missing():
    ratings = _ratings()
    del ratings["deal_terms.ownership"]
    with pytest.raises(ScoringError) as err:
        compute_scores(ratings, POLICY)
    assert err.value.code == "CRITERIA_MISMATCH"


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", "100.01"])
def test_decide_rejects_invalid_numbers(bad):
    ratings = dict.fromkeys(
        ("founder", "market", "technology", "moat", "traction", "deal_terms"),
        Decimal(3),
    )
    with pytest.raises(ScoringError):
        decide(bad, "0", ratings, T)


# --- aggregate_scores: 여섯 영역·세대 검증 ----------------------------------


@dataclass(frozen=True)
class _A:
    criterion_id: str
    status: str
    rating: int | None


@dataclass(frozen=True)
class _E:
    candidate_id: str
    evaluation_round: int
    snapshot_id: str
    evidence_revision: int
    policy_version: str
    dimension: str
    criteria: tuple


def _evaluations(rating=4):
    out = []
    for dim in ("founder", "market", "technology", "moat", "traction", "deal_terms"):
        criteria = tuple(
            _A(c.criterion_id, "observed", rating)
            for c in POLICY.criteria
            if c.dimension == dim
        )
        out.append(_E("cand-1", 1, "snap-1", 3, POLICY.policy_version, dim, criteria))
    return out


def test_aggregate_six_dimensions():
    b = aggregate_scores(_evaluations(), POLICY)
    assert b.observed_score == Decimal(80)
    assert decide_breakdown(b, T).label == "RECOMMEND"


def test_aggregate_rejects_missing_deal_terms():
    evs = [e for e in _evaluations() if e.dimension != "deal_terms"]
    with pytest.raises(ScoringError) as err:
        aggregate_scores(evs, POLICY)
    assert err.value.code == "INCOMPLETE_EVALUATIONS"


@pytest.mark.parametrize(
    "field,value",
    [
        ("candidate_id", "cand-2"),
        ("evaluation_round", 2),
        ("snapshot_id", "snap-2"),
        ("evidence_revision", 4),
        ("policy_version", "other"),
    ],
)
def test_aggregate_rejects_mixed_generation(field, value):
    evs = _evaluations()
    evs[5] = replace(evs[5], **{field: value})
    with pytest.raises(ScoringError) as err:
        aggregate_scores(evs, POLICY)
    assert err.value.code == "MIXED_GENERATION"


def test_aggregate_rejects_policy_mismatch():
    evs = [replace(e, policy_version="other") for e in _evaluations()]
    with pytest.raises(ScoringError) as err:
        aggregate_scores(evs, POLICY)
    assert err.value.code == "POLICY_MISMATCH"


def test_aggregate_rejects_duplicate_dimension():
    evs = _evaluations()
    evs.append(evs[0])
    with pytest.raises(ScoringError) as err:
        aggregate_scores(evs, POLICY)
    assert err.value.code == "DUPLICATE_DIMENSION"


def test_aggregate_rejects_criterion_in_wrong_dimension():
    evs = _evaluations()
    moved = evs[0].criteria + (_A("market.size", "observed", 4),)
    evs[0] = replace(evs[0], criteria=moved)
    with pytest.raises(ScoringError) as err:
        aggregate_scores(evs, POLICY)
    assert err.value.code == "CRITERIA_MISMATCH"


@pytest.mark.parametrize(
    "status,rating", [("observed", None), ("missing", 3), ("not_applicable", None)]
)
def test_aggregate_rejects_status_rating_conflict(status, rating):
    evs = _evaluations()
    first = evs[0].criteria
    evs[0] = replace(
        evs[0], criteria=(_A(first[0].criterion_id, status, rating),) + first[1:]
    )
    with pytest.raises(ScoringError):
        aggregate_scores(evs, POLICY)


def test_missing_assessment_counts_as_missing_weight():
    evs = _evaluations(rating=5)
    first = evs[1].criteria  # market
    evs[1] = replace(
        evs[1], criteria=(_A(first[0].criterion_id, "missing", None),) + first[1:]
    )
    b = aggregate_scores(evs, POLICY)
    assert b.missing_weight == Decimal(10)
    assert b.observed_score == Decimal(90)
