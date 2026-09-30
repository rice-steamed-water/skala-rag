"""Synthetic OpenAI HTTP responses; socket access and actual API calls denied."""

import json
import socket
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import BaseModel

from skala_rag.agents.m2_llm import M2LLMs
from skala_rag.contracts.interfaces import LLMError
from skala_rag.fakes import FakeClock
from skala_rag.tools.structured_llm import APPROVED_MODEL

KEY = "sk-synthetic-m2-key-not-a-real-credential"


class Output(BaseModel):
    answer: int


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("offline M2 runtime attempted network")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


def build(handler):
    clock = FakeClock(datetime(2026, 9, 30, tzinfo=UTC))
    return M2LLMs(
        api_key=KEY,
        run_id="synthetic-run",
        candidate_id="synthetic-company",
        schema_version="test",
        execution_mode="fixture",
        clock=clock,
        deadline=clock.now() + timedelta(minutes=10),
        http_transport=httpx.MockTransport(handler),
    )


def response():
    return httpx.Response(
        200,
        json={
            "model": APPROVED_MODEL,
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"answer":1}'}],
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 10},
        },
    )


def test_three_stages_share_eight_physical_requests_and_safe_observations():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return response()

    llms = build(handler)
    assert len({id(llm.runtime.ledger) for llm in llms.stages.values()}) == 1
    for i in range(8):
        llm = list(llms.stages.values())[i % 3]
        assert (
            llm.generate(
                system="synthetic system", user="synthetic user", output_schema=Output
            ).answer
            == 1
        )
    with pytest.raises(LLMError):
        llms.stages["technology"].generate(
            system="synthetic", user="synthetic", output_schema=Output
        )
    assert len(seen) == 8
    assert all(
        p["model"] == APPROVED_MODEL and p["max_output_tokens"] == 2000 for p in seen
    )
    observation = llms.observations()
    assert observation["ledger"]["calls"] == 8
    assert observation["actual_cost_usd"] is None
    assert KEY not in json.dumps(observation)
    assert "synthetic user" not in json.dumps(observation)


@pytest.mark.parametrize("failure", [401, 429, 500, "timeout"])
def test_transport_failure_never_retries_and_consumes_shared_budget(failure):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout(KEY)
        return httpx.Response(failure, text=KEY)

    llms = build(handler)
    with pytest.raises(LLMError) as error:
        llms.stages["eligibility"].generate(
            system="synthetic", user="synthetic", output_schema=Output
        )
    assert len(calls) == 1
    assert llms.runtime.ledger.snapshot()["calls"] == 1
    assert KEY not in str(error.value)
    assert KEY not in json.dumps(llms.observations())


def test_overlarge_request_is_blocked_before_http_or_budget_spending():
    seen = []
    llms = build(lambda request: seen.append(request) or response())
    with pytest.raises(LLMError):
        llms.stages["evidence"].generate(
            system="synthetic", user="x" * 8001, output_schema=Output
        )
    assert seen == []
    assert llms.runtime.ledger.snapshot()["calls"] == 0
