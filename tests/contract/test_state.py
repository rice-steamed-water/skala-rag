import json
from typing import get_type_hints

import pytest
from pydantic import SecretStr

from skala_rag.contracts.state import (
    CandidateStatus,
    InvestmentState,
    RunOutcome,
    WorkflowStatus,
    create_initial_state,
)

# 가상 테스트 입력, 실제 실행 설정 아님
RUN_INPUT = {
    "investment_theme": "Physical AI robotics",
    "countries": ["KR"],
    "languages": ["ko", "en"],
    "as_of": "2026-09-30",
    "policy_version": "policy-fixture",
    "corpus_version": "corpus-fixture",
    "execution_mode": "fixture",
}

NULL_FIELDS = {
    "current_candidate_id",
    "selected_candidate_id",
    "run_manifest",
    "report_input",
    "report_context",
    "report_draft",
    "report",
    "report_validation",
    "report_judgement",
    "pdf_validation",
    "run_outcome",
}


def test_initial_state_sets_every_field() -> None:
    state = create_initial_state(RUN_INPUT)

    assert set(state) == set(get_type_hints(InvestmentState))
    assert state["investment_theme"] == "Physical AI robotics"
    assert state["run_input"] == RUN_INPUT
    assert state["candidate_index"] == 0
    assert state["report_revision_count"] == 0
    assert state["workflow_status"] is WorkflowStatus.RUNNING
    for name in NULL_FIELDS:
        assert state[name] is None, name


def test_initial_state_empties_maps_and_lists() -> None:
    state = create_initial_state(RUN_INPUT)

    for name, value in state.items():
        if isinstance(value, (dict, list)) and name != "run_input":
            assert value == type(value)(), name
    for name in (
        "research_retry_count",
        "evaluation_rounds",
        "evidence_revisions",
        "snapshots",
    ):
        assert state[name] == {}


def test_initial_state_does_not_share_containers() -> None:
    first = create_initial_state(RUN_INPUT)
    second = create_initial_state(RUN_INPUT)

    first["evidence"]["ev-fixture-001"] = {}
    first["run_input"]["countries"].append("US")

    assert second["evidence"] == {}
    assert RUN_INPUT["countries"] == ["KR"]


def test_state_json_round_trip() -> None:
    state = create_initial_state(RUN_INPUT)
    state["candidate_status"]["co-fixture-001"] = CandidateStatus.EVALUATING
    state["run_outcome"] = RunOutcome.NO_RECOMMENDATION

    restored = json.loads(json.dumps(state, allow_nan=False))

    assert restored == state


@pytest.mark.parametrize(
    "secret_value",
    [object(), SecretStr("sk-fixture"), float("nan")],
    ids=["client", "secret", "nan"],
)
def test_initial_state_rejects_non_json_input(secret_value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        create_initial_state({**RUN_INPUT, "extra": secret_value})


def test_state_has_no_client_or_key_fields() -> None:
    for name in get_type_hints(InvestmentState):
        assert not any(word in name for word in ("client", "api_key", "secret")), name


def test_enum_values_match_contract() -> None:
    assert [s.value for s in CandidateStatus] == [
        "discovered",
        "researching",
        "ineligible",
        "eligibility_unknown",
        "evaluating",
        "recommend",
        "watchlist",
        "pass",
        "failed",
        "not_evaluated",
    ]
    assert [s.value for s in WorkflowStatus] == ["running", "completed", "failed"]
    assert [s.value for s in RunOutcome] == [
        "recommended",
        "no_recommendation",
        "no_candidates",
        "insufficient_evidence",
        "technical_failure",
    ]
