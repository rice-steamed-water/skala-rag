"""T24: Discovery 출처 전달. Company Research 실패 뒤에도 발견 Source·이력 보존."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.fixtures.loader import load_common_fixtures

from skala_rag.agents.discovery import (
    accept_discovery,
    discovery_state_update,
    normalize_candidates,
)
from skala_rag.contracts import (
    Candidate,
    CompanyResearchBundle,
    DiscoveryBundle,
    RunInput,
    ToolResult,
    WorkflowError,
)
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.state import CandidateStatus, create_initial_state
from skala_rag.contracts.tools import ToolBudget
from skala_rag.fakes import FakeClock, FakeTool
from skala_rag.graph.reducers import merge_errors, merge_sources
from skala_rag.scoring.catalog import load_policy
from skala_rag.tools.fixture_discovery import FixtureSearchCandidates

ROOT = Path(__file__).resolve().parents[2]
SV = "synthetic-common-1"
NOW = datetime(2026, 9, 30, tzinfo=UTC)
BUDGET = ToolBudget(schema_version=SV, max_calls=8, max_retries=2, timeout_seconds=30)
RUN_INPUT = RunInput(
    schema_version=SV,
    investment_theme="가상 물류 로봇",
    countries=["KR", "US"],
    languages=["ko"],
    as_of="2026-09-30",
    policy_version="main-draft-0.1.0",
    corpus_version="corpus-fixture-v1",
    execution_mode="fixture",
)


@pytest.fixture
def common():
    policy = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
    return load_common_fixtures(policy)


def apply(state, update):
    """테스트용 LangGraph 흉내: reducer 필드는 병합, 나머지는 덮어쓴다."""
    state = dict(state)
    for key, value in update.items():
        if key == "sources":
            state[key] = merge_sources(state[key], value)
        elif key == "errors":
            state[key] = merge_errors(state[key], value)
        else:
            state[key] = value
    return state


def discover(common):
    bundle = DiscoveryBundle.model_validate(
        {
            "schema_version": SV,
            "candidates": list(common.candidates.values()),
            "sources": dict(common.sources),
        },
        context={"execution_mode": "fixture"},
    )
    tool = FixtureSearchCandidates(bundle, run_id="run-t24", clock=FakeClock(NOW))
    outcome = accept_discovery(tool(RUN_INPUT, BUDGET))
    normalized = normalize_candidates(outcome.bundle.candidates, max_candidates=5)
    state = create_initial_state(RUN_INPUT.model_dump(mode="json"))
    return apply(state, discovery_state_update(state, outcome, normalized)), outcome


def research_failure(candidate_id):
    error = WorkflowError(
        schema_version=SV,
        error_id="error-research-1",
        run_id="run-t24",
        candidate_id=candidate_id,
        node="company_research",
        error_code=ErrorCode.TOOL_UNAVAILABLE.value,
        message_redacted="fixture research outage",
        retryable=True,
        attempt=1,
        timestamp=NOW,
    )
    return ToolResult[CompanyResearchBundle](
        schema_version=SV,
        status="unavailable",
        data=None,
        retrieval_records=[],
        errors=[error],
    )


def test_discovery_sources_resolve_in_state(common):
    state, outcome = discover(common)
    assert len(state["candidates"]) == 4
    for payload in state["candidates"]:
        for source_id in payload["discovery_source_ids"]:
            assert state["sources"][source_id]["source_id"] == source_id
        assert state["candidate_status"][payload["candidate_id"]] == (
            CandidateStatus.DISCOVERED
        )
    assert [r["retrieval_id"] for r in state["retrieval_history"]] == [
        outcome.retrieval_records[0].retrieval_id
    ]


def test_company_research_failure_keeps_discovery_sources(common):
    state, _ = discover(common)
    sources_before = dict(state["sources"])
    history_before = list(state["retrieval_history"])
    target = Candidate.model_validate(
        state["candidates"][0], context={"execution_mode": "fixture"}
    )

    research = FakeTool([research_failure(target.candidate_id)])
    result = research(target, BUDGET)
    assert result.status == "unavailable"
    state = apply(
        state,
        {
            "errors": [e.model_dump(mode="json") for e in result.errors],
            "candidate_status": {
                **state["candidate_status"],
                target.candidate_id: CandidateStatus.FAILED,
            },
        },
    )

    assert state["sources"] == sources_before
    assert state["retrieval_history"] == history_before
    assert set(target.discovery_source_ids) <= set(state["sources"])
    assert [e["error_id"] for e in state["errors"]] == ["error-research-1"]
    assert research.calls[0].args[0] == target


def test_failed_discovery_records_history_without_candidates(common):
    bundle = DiscoveryBundle(schema_version=SV, candidates=[], sources={})
    tool = FixtureSearchCandidates(
        bundle,
        run_id="run-t24",
        clock=FakeClock(NOW),
        error_code=ErrorCode.TOOL_UNAVAILABLE,
    )
    outcome = accept_discovery(tool(RUN_INPUT, BUDGET))
    state = create_initial_state(RUN_INPUT.model_dump(mode="json"))
    state = apply(state, discovery_state_update(state, outcome, None))
    assert state["candidates"] == [] and state["sources"] == {}
    assert state["retrieval_history"][0]["status"] == "unavailable"
    assert state["errors"][0]["error_code"] == ErrorCode.TOOL_UNAVAILABLE
    with pytest.raises(ValueError):
        discovery_state_update(
            state, outcome, normalize_candidates([], max_candidates=1)
        )


def test_dropped_candidate_sources_are_still_kept(common):
    bundle = DiscoveryBundle.model_validate(
        {
            "schema_version": SV,
            "candidates": list(common.candidates.values()),
            "sources": dict(common.sources),
        },
        context={"execution_mode": "fixture"},
    )
    tool = FixtureSearchCandidates(bundle, run_id="run-t24", clock=FakeClock(NOW))
    outcome = accept_discovery(tool(RUN_INPUT, BUDGET))
    normalized = normalize_candidates(
        outcome.bundle.candidates,
        max_candidates=2,
        limit_policy=lambda candidates, limit: [candidates[-1].candidate_id],
    )
    state = create_initial_state(RUN_INPUT.model_dump(mode="json"))
    state = apply(state, discovery_state_update(state, outcome, normalized))
    assert len(state["candidates"]) == 1
    assert set(state["sources"]) == set(common.sources)
