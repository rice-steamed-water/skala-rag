"""Synthetic regressions for optional common-wrapper hooks."""

import json

import pytest
from tests.unit.test_market import CLOCK, POLICY, RUBRIC, Case

from skala_rag.agents.evaluation import build_user_prompt, evaluate_dimension
from skala_rag.fakes import FakeLLM


@pytest.mark.parametrize("repair", [False, True])
def test_extra_validator_uses_only_original_repair_budget(repair):
    case = Case()
    llm = FakeLLM([case.output()] * 3)
    calls = []

    def validate(output):
        calls.append(output)
        return [] if repair and len(calls) == 2 else ["SYNTHETIC_INVALID: private"]

    result = evaluate_dimension(
        "market",
        case.snapshot,
        RUBRIC,
        llm=llm,
        policy=POLICY,
        clock=CLOCK,
        schema_version=case.snapshot.schema_version,
        extra_validator=validate,
    )
    assert len(llm.calls) == len(calls) == 2
    assert result.status == ("success" if repair else "failure")
    assert "private" not in llm.calls[1].user
    if not repair:
        assert result.errors[0].attempt == 2
        assert result.errors[0].error_code == "LLM_OUTPUT_INVALID"


def test_generic_defaults_and_explicit_user_precedence():
    case = Case()
    default = build_user_prompt("market", case.snapshot, RUBRIC, POLICY)
    assert "context" not in json.loads(default)
    llm = FakeLLM([case.output()])
    result = evaluate_dimension(
        "market",
        case.snapshot,
        RUBRIC,
        llm=llm,
        policy=POLICY,
        clock=CLOCK,
        schema_version=case.snapshot.schema_version,
    )
    assert result.status == "success"
    assert llm.calls[0].user == default
    assert len(llm.calls) == 1
    explicit_llm = FakeLLM([case.output()])
    explicit_result = evaluate_dimension(
        "market",
        case.snapshot,
        RUBRIC,
        llm=explicit_llm,
        policy=POLICY,
        clock=CLOCK,
        schema_version=case.snapshot.schema_version,
        user_prompt="synthetic explicit",
    )
    assert explicit_result.status == "success"
    assert explicit_llm.calls[0].user == "synthetic explicit"
    assert len(explicit_llm.calls) == 1
    assert json.loads(
        build_user_prompt("market", case.snapshot, RUBRIC, POLICY, {"synthetic": True})
    )["context"] == {"synthetic": True}
