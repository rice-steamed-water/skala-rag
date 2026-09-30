"""T02: 원문 catalog와 가상 산술·경계값 계약 검증."""

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from skala_rag.scoring.catalog import ScoringPolicy, load_policy

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "configs/scoring.draft.json"
FIXTURE_PATH = ROOT / "tests/fixtures/scoring.draft.json"


@pytest.fixture
def policy():
    return load_policy(POLICY_PATH, execution_mode="fixture")


def test_catalog_matches_document(policy):
    document = (ROOT / "docs/implementation/scoring.md").read_text()
    rows = []
    for line in document.splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) == 7 and " / " in cells[1] and "." in cells[2]:
            rows.append((cells[2], cells[1].split(" / ")[0], cells[3], int(cells[4])))
    assert [
        (c.criterion_id, c.dimension, c.display_name, c.weight) for c in policy.criteria
    ] == rows
    assert len(rows) == 23
    assert sum(c.weight for c in policy.criteria) == 100
    assert policy.status == "draft"
    assert set(policy.open_decisions) == {"D01", "D02", "D03", "D05", "D08"}
    assert policy.budgets.max_candidates == 5
    assert policy.budgets.max_research_retries_per_candidate == 2
    assert policy.budgets.max_report_revisions == 2


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "weight",
        "dimension",
        "missing_criterion",
        "approved",
        "threshold_order",
        "nan",
        "negative_budget",
        "missing_threshold",
    ],
)
def test_invalid_catalog_rejected(mutation):
    payload = json.loads(POLICY_PATH.read_text())
    if mutation == "duplicate":
        payload["criteria"][1]["criterion_id"] = payload["criteria"][0]["criterion_id"]
    elif mutation == "weight":
        payload["criteria"][0]["weight"] += 1
    elif mutation == "dimension":
        payload["criteria"][0]["dimension"] = "market"
    elif mutation == "missing_criterion":
        payload["criteria"].pop()
    elif mutation == "approved":
        payload["status"] = "approved"
    elif mutation == "threshold_order":
        payload["thresholds"]["recommend_score"] = "90"
    elif mutation == "nan":
        payload["thresholds"]["missing_weight"] = "NaN"
    elif mutation == "negative_budget":
        payload["budgets"]["max_report_revisions"] = -1
    else:
        del payload["thresholds"]["missing_weight"]
    with pytest.raises(ValidationError):
        ScoringPolicy.model_validate(payload)


def test_draft_live_rejected():
    with pytest.raises(ValueError, match="fixture"):
        load_policy(POLICY_PATH, execution_mode="live")


def test_load_has_no_shared_state(policy):
    first = copy.deepcopy(policy)
    first.dimension_weights["founder"] = 99
    assert (
        load_policy(POLICY_PATH, execution_mode="fixture").dimension_weights["founder"]
        == 5
    )


def test_calculation_fixtures(policy):
    fixture = json.loads(FIXTURE_PATH.read_text())
    assert fixture["execution_mode"] == "fixture"
    assert fixture["locator"].startswith("fixture://")
    assert fixture["policy_version"] == policy.policy_version
    assert len(fixture["calculations"]) == 7
    identifiers = {c.criterion_id for c in policy.criteria}
    for case in fixture["calculations"]:
        assert set(case["ratings"]) == identifiers
        observed = Decimal(0)
        missing = 0
        for criterion in policy.criteria:
            rating = case["ratings"][criterion.criterion_id]
            if rating is None:
                missing += criterion.weight
            else:
                assert type(rating) is int and 1 <= rating <= 5
                observed += Decimal(criterion.weight) * rating / 5
        expected = case["expected"]
        assert observed == Decimal(expected["observed_score"])
        assert missing == Decimal(expected["missing_weight"])
        assert 100 - missing == Decimal(expected["coverage_pct"])
    # 총점이 높은 경우도 founder 저점수 조건이 별도로 보존된다.
    founder = next(c for c in fixture["calculations"] if c["case_id"] == "founder_one")
    assert founder["expected"]["label"] == "WATCHLIST"
    assert founder["expected"]["observed_score"] == "96"


def test_boundary_fixture_contract():
    cases = json.loads(FIXTURE_PATH.read_text())["boundaries"]
    expected = {
        "score_59.99": ("PASS", "투자비추천"),
        "score_60": ("WATCHLIST", "보류"),
        "score_69.99": ("WATCHLIST", "보류"),
        "score_70": ("RECOMMEND", "투자 검토"),
        "score_79.99": ("RECOMMEND", "투자 검토"),
        "score_80": ("RECOMMEND", "투자 우선 검토"),
        "missing_29": ("RECOMMEND", "투자 우선 검토"),
        "missing_30": ("WATCHLIST", "보류 (정보 부족)"),
        "missing_31": ("WATCHLIST", "보류 (정보 부족)"),
        "dimension_2": ("WATCHLIST", "보류 (저점수)"),
        "dimension_2.01": ("RECOMMEND", "투자 우선 검토"),
        "both_overrides": ("WATCHLIST", "보류 (정보 부족)"),
    }
    assert {c["case_id"]: (c["label"], c["report_grade"]) for c in cases} == expected
    # 경계 입력은 decide 단위 테스트용이며 실제 정수 rating의 집계값이 아니다.
    assert (
        next(c for c in cases if c["case_id"] == "score_59.99")["observed_score"]
        == "59.99"
    )
