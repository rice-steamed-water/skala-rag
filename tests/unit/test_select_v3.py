"""Synthetic selector tests, preserving original candidate ID bytes."""

from itertools import permutations

import pytest

from skala_rag.scoring.selector_v3 import select_best_v3
from skala_rag.scoring.v3_policy import load_v3_policy


@pytest.fixture
def policy():
    return load_v3_policy("configs/scoring.v3.json", execution_mode="fixture")


def entry(cid, label, score, missing="0", applicable="100"):
    return dict(
        candidate_id=cid,
        eligibility_status="eligible",
        status="evaluated",
        label=label,
        normalized_score=score,
        weighted_missing_pct=missing,
        applicable_weight=applicable,
        score_summary_id=f"score-{cid}",
    )


def test_priority_beats_recommend_and_permutation_ties(policy):
    candidates = [
        entry("z", "RECOMMEND", "100"),
        entry("a", "RECOMMEND_PRIORITY", "80"),
        entry(" b", "RECOMMEND_PRIORITY", "80", "2"),
        entry("A", "RECOMMEND_PRIORITY", "80"),
    ]
    for order in permutations(candidates):
        result = select_best_v3(order, policy, run_id="run", schema_version="synthetic")
        assert result.selected_candidate_id == "A"
        assert result.considered_candidate_ids == ("A", "a", " b", "z")


def test_missing_tiebreak_and_noneligible_cannot_win(policy):
    result = select_best_v3(
        [
            entry("z", "RECOMMEND", "80", "1"),
            entry("a", "RECOMMEND", "80", "2"),
            {
                **entry("x", "RECOMMEND_PRIORITY", "100"),
                "eligibility_status": "unknown",
            },
        ],
        policy,
        run_id="run",
        schema_version="synthetic",
    )
    assert result.selected_candidate_id == "z"


def test_no_selection_watch_pass_or_no_eligible(policy):
    assert (
        select_best_v3(
            [entry("a", "WATCHLIST", "90"), entry("b", "PASS", "50")],
            policy,
            run_id="run",
            schema_version="synthetic",
        ).reason
        == "NO_RECOMMENDATION"
    )
    assert (
        select_best_v3([], policy, run_id="run", schema_version="synthetic").reason
        == "NO_ELIGIBLE_RESULTS"
    )


def test_selector_requires_explicit_schema_version(policy):
    with pytest.raises(TypeError, match="schema_version"):
        select_best_v3([], policy, **{"run_id": "run"})


def test_duplicate_and_invalid_normal_result_rejected(policy):
    with pytest.raises(ValueError):
        select_best_v3(
            [entry("a", "RECOMMEND", "90")] * 2,
            policy,
            run_id="run",
            schema_version="synthetic",
        )
    with pytest.raises(ValueError):
        select_best_v3(
            [{**entry("a", "RECOMMEND", "90"), "normalized_score": "NaN"}],
            policy,
            run_id="run",
            schema_version="synthetic",
        )
