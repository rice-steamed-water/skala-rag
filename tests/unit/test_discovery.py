"""fixture Discovery adapter, 결과 구별, Candidate Normalize (T04 동명 기업 일부)."""

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.fixtures.loader import load_common_fixtures

from skala_rag.agents.discovery import (
    CandidateLimitPolicy,
    CandidateLimitUnresolved,
    DiscoveryInvalid,
    accept_discovery,
    normalize_candidates,
)
from skala_rag.contracts import Candidate, DiscoveryBundle, RunInput, ToolResult
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import SearchCandidates
from skala_rag.contracts.tools import ToolBudget
from skala_rag.fakes import FakeClock
from skala_rag.scoring.catalog import load_policy
from skala_rag.tools.fixture_discovery import FixtureSearchCandidates

ROOT = Path(__file__).resolve().parents[2]
SV = "synthetic-common-1"
BUDGET = ToolBudget(schema_version=SV, max_calls=8, max_retries=2, timeout_seconds=30)


@pytest.fixture
def common():
    policy = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
    return load_common_fixtures(policy)


@pytest.fixture
def bundle(common):
    return fixture_bundle(common)


def fixture_bundle(common):
    return DiscoveryBundle.model_validate(
        {
            "schema_version": SV,
            "candidates": list(common.candidates.values()),
            "sources": dict(common.sources),
        },
        context={"execution_mode": "fixture"},
    )


def run_input(mode="fixture"):
    return RunInput(
        schema_version=SV,
        investment_theme="가상 물류 로봇",
        countries=["KR", "US"],
        languages=["ko"],
        as_of="2026-09-30",
        policy_version="main-draft-0.1.0",
        corpus_version="corpus-fixture-v1",
        execution_mode=mode,
    )


def search(bundle, **kwargs):
    clock = FakeClock(datetime(2026, 9, 30, tzinfo=UTC))
    return FixtureSearchCandidates(bundle, run_id="run-t", clock=clock, **kwargs)


def candidate(candidate_id, name="가상 로봇", **overrides):
    payload = {
        "schema_version": SV,
        "candidate_id": candidate_id,
        "canonical_name": name,
        "aliases": [],
        "country": "KR",
        "homepage_url": None,
        "legal_identifiers": {},
        "discovery_source_ids": [f"src-{candidate_id}"],
    } | overrides
    return Candidate.model_validate(payload, context={"execution_mode": "fixture"})


# --- adapter / 0건과 실패 구별 ---


def test_adapter_satisfies_boundary(bundle):
    assert isinstance(search(bundle), SearchCandidates)


def test_found_resolves_every_discovery_source(bundle):
    result = search(bundle)(run_input(), BUDGET)
    outcome = accept_discovery(result)
    assert result.status == "ok" and outcome.status == "found"
    for item in outcome.bundle.candidates:
        assert item.discovery_source_ids
        assert set(item.discovery_source_ids) <= set(outcome.bundle.sources)
    (record,) = outcome.retrieval_records
    assert record.status == "ok" and record.error_id is None
    assert set(record.source_ids) == set(bundle.sources)


def test_adapter_returns_independent_copies(bundle):
    tool = search(bundle)
    first = tool(run_input(), BUDGET)
    first.data.candidates.clear()
    assert len(tool(run_input(), BUDGET).data.candidates) == 4


def test_zero_candidates_is_empty_not_failure():
    empty = DiscoveryBundle(schema_version=SV, candidates=[], sources={})
    result = search(empty)(run_input(), BUDGET)
    outcome = accept_discovery(result)
    assert result.status == "empty" and result.errors == []
    assert outcome.status == "no_candidates"
    assert outcome.retrieval_records[0].status == "empty"


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (ErrorCode.TOOL_UNAVAILABLE, "unavailable"),
        (ErrorCode.TOOL_AUTH_FAILED, "unavailable"),
        (ErrorCode.TOOL_TIMEOUT, "failed"),
    ],
)
def test_tool_failure_is_distinct_from_zero(bundle, code, status):
    result = search(bundle, error_code=code)(run_input(), BUDGET)
    outcome = accept_discovery(result)
    assert result.status == status and result.data is None
    assert outcome.status == "failed" and outcome.bundle is None
    (error,) = outcome.errors
    assert error.error_code == code
    assert outcome.retrieval_records[0].error_id == error.error_id


def test_live_mode_is_refused(bundle):
    result = search(bundle)(run_input("live"), BUDGET)
    assert result.status == "unavailable"
    assert result.errors[0].error_code == ErrorCode.TOOL_NOT_CONFIGURED


def test_no_call_budget_fails_before_search(bundle):
    budget = BUDGET.model_copy(update={"max_calls": 0})
    result = search(bundle)(run_input(), budget)
    assert result.errors[0].error_code == ErrorCode.BUDGET_EXHAUSTED


def test_non_tool_error_code_rejected(bundle):
    with pytest.raises(ValueError):
        search(bundle, error_code=ErrorCode.LLM_FAILED)


def test_accept_rejects_inconsistent_results(bundle):
    ok_without = ToolResult[DiscoveryBundle](
        schema_version=SV,
        status="ok",
        data=DiscoveryBundle(schema_version=SV, candidates=[], sources={}),
        retrieval_records=[],
        errors=[],
    )
    with pytest.raises(DiscoveryInvalid):
        accept_discovery(ok_without)
    empty_with = ok_without.model_copy(update={"status": "empty", "data": bundle})
    with pytest.raises(DiscoveryInvalid):
        accept_discovery(empty_with)
    sourceless = candidate("co-x", discovery_source_ids=[])
    no_source = ok_without.model_copy(
        update={"data": bundle.model_copy(update={"candidates": [sourceless]})}
    )
    with pytest.raises(DiscoveryInvalid):
        accept_discovery(no_source)


def test_accept_rejects_unresolved_source_even_if_unvalidated(bundle):
    broken = bundle.model_copy(update={"sources": {}})
    result = ToolResult[DiscoveryBundle].model_construct(
        status="ok", data=broken, retrieval_records=[], errors=[]
    )
    with pytest.raises(DiscoveryInvalid, match="unresolved"):
        accept_discovery(result)


# --- Normalize ---


def test_same_name_fixture_is_not_merged(common):
    eligible = common.candidates[common.cases["eligible"]]
    same_name = common.candidates[common.cases["same_name"]]
    assert eligible.canonical_name == same_name.canonical_name
    result = normalize_candidates([eligible, same_name], max_candidates=5)
    assert [c.candidate_id for c in result.candidates] == [
        eligible.candidate_id,
        same_name.candidate_id,
    ]
    assert result.merges == []


def test_same_name_same_country_without_identity_is_not_merged():
    result = normalize_candidates(
        [candidate("co-a"), candidate("co-b")], max_candidates=5
    )
    assert len(result.candidates) == 2 and result.merges == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"country": "US"},
        {"legal_identifiers": {"brn": "222"}},
    ],
)
def test_conflicting_country_or_identifier_blocks_merge(overrides):
    first = candidate(
        "co-a",
        homepage_url="https://robot.example",
        legal_identifiers={"brn": "111"},
    )
    second = candidate("co-b", homepage_url="https://robot.example", **overrides)
    result = normalize_candidates([first, second], max_candidates=5)
    assert len(result.candidates) == 2 and result.merges == []


def test_same_legal_identifier_merges_with_reason():
    first = candidate(
        "co-a", name="알파로보틱스", legal_identifiers={"BRN": "111"}, aliases=["알파"]
    )
    second = candidate(
        "co-b",
        name="Alpha Robotics",
        legal_identifiers={"brn": " 111 ", "dart": "D1"},
        homepage_url="https://alpha.example",
    )
    result = normalize_candidates([first, second], max_candidates=5)
    (merged,) = result.candidates
    assert merged.candidate_id == "co-a"
    assert merged.canonical_name == "알파로보틱스"
    assert merged.aliases == ["알파", "Alpha Robotics"]
    assert merged.homepage_url == "https://alpha.example"
    assert merged.legal_identifiers == {"BRN": "111", "dart": "D1"}
    assert merged.discovery_source_ids == ["src-co-a", "src-co-b"]
    (merge,) = result.merges
    assert asdict(merge) == {
        "kept_candidate_id": "co-a",
        "merged_candidate_id": "co-b",
        "matched_on": ["legal_identifier:brn"],
    }


def test_same_homepage_host_merges_with_reason():
    first = candidate("co-a", homepage_url="https://www.Robot.example/ko")
    second = candidate("co-b", name="다른 표기", homepage_url="http://robot.example/")
    result = normalize_candidates([first, second], max_candidates=5)
    assert len(result.candidates) == 1
    assert result.merges[0].matched_on == ["homepage_host"]


def test_identity_accumulates_across_merges():
    """B가 A에 합쳐지면 B의 식별자로 C도 A에 합친다. 충돌은 누적 식별자 기준."""
    a = candidate("co-a", legal_identifiers={"brn": "111"})
    b = candidate("co-b", legal_identifiers={"brn": "111", "dart": "D1"})
    c = candidate("co-c", legal_identifiers={"dart": "D1"})
    d = candidate(
        "co-d", homepage_url="https://x.example", legal_identifiers={"dart": "D2"}
    )
    result = normalize_candidates([a, b, c, d], max_candidates=5)
    assert [x.candidate_id for x in result.candidates] == ["co-a", "co-d"]
    assert [m.merged_candidate_id for m in result.merges] == ["co-b", "co-c"]


def test_duplicate_candidate_id_merges_but_conflict_raises():
    same = normalize_candidates(
        [candidate("co-a"), candidate("co-a")], max_candidates=5
    )
    assert same.merges[0].matched_on == ["candidate_id"]
    with pytest.raises(ValueError, match="reused"):
        normalize_candidates(
            [candidate("co-a"), candidate("co-a", country="US")], max_candidates=5
        )


OVER_LIMIT = [
    ("co-a", {"brn": "1"}),
    ("co-a2", {"brn": "1"}),
    ("co-b", {}),
    ("co-c", {}),
]


def over_limit():
    return [candidate(i, legal_identifiers=ids) for i, ids in OVER_LIMIT]


def test_limit_counts_after_dedup_without_policy():
    """중복 제거 후 3개 ≤ 상한 3이면 정책 없이 모두 남는다."""
    result = normalize_candidates(over_limit(), max_candidates=3)
    assert [c.candidate_id for c in result.candidates] == ["co-a", "co-b", "co-c"]
    assert result.dropped_candidate_ids == []


def test_over_limit_without_policy_is_not_truncated():
    """D08 OPEN: 남길 후보 기준이 없으면 발견 순서로 자르지 않고 멈춘다."""
    with pytest.raises(CandidateLimitUnresolved, match="D08"):
        normalize_candidates(over_limit(), max_candidates=2)


def test_over_limit_follows_injected_policy_only():
    calls = []

    def pick_last(candidates, max_candidates):
        calls.append(([c.candidate_id for c in candidates], max_candidates))
        return ["co-c", "co-a"]

    assert isinstance(pick_last, CandidateLimitPolicy)
    result = normalize_candidates(
        over_limit(), max_candidates=2, limit_policy=pick_last
    )
    assert calls == [(["co-a", "co-b", "co-c"], 2)]
    assert [c.candidate_id for c in result.candidates] == ["co-c", "co-a"]
    assert result.candidates[1].discovery_source_ids == ["src-co-a", "src-co-a2"]
    assert result.dropped_candidate_ids == ["co-b"]


def test_policy_not_called_within_limit():
    def refuse(candidates, max_candidates):
        raise AssertionError("policy called within limit")

    normalize_candidates(over_limit(), max_candidates=5, limit_policy=refuse)


@pytest.mark.parametrize(
    "selection",
    [["co-a", "co-b", "co-c"], [], ["co-a", "co-a"], ["co-a2"], ["co-x"], [1]],
)
def test_invalid_policy_selection_rejected(selection):
    with pytest.raises(ValueError):
        normalize_candidates(
            over_limit(), max_candidates=2, limit_policy=lambda c, m: selection
        )


@pytest.mark.parametrize("bad", [0, -1, True, 2.0])
def test_max_candidates_must_be_positive_int(bad):
    with pytest.raises(ValueError):
        normalize_candidates([], max_candidates=bad)


def test_normalize_does_not_mutate_input():
    a = candidate("co-a", legal_identifiers={"brn": "1"})
    b = candidate("co-b", legal_identifiers={"brn": "1"})
    before = a.model_dump()
    normalize_candidates([a, b], max_candidates=5)
    assert a.model_dump() == before
