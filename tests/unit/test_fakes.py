"""Fake Tool/LLM/clock satisfy the injection Protocols without network access."""

import socket
from datetime import UTC, datetime, timedelta

import pytest

import skala_rag.contracts as contracts
from skala_rag.contracts import interfaces
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import LLMError
from skala_rag.fakes import Call, FakeClock, FakeExhausted, FakeLLM, FakeTool

EMPTY = contracts.ToolResult[contracts.RetrievalBundle](
    schema_version="synthetic-1",
    status="empty",
    data=contracts.RetrievalBundle(schema_version="synthetic-1", sources={}, chunks=[]),
    retrieval_records=[],
    errors=[],
)
DECISION = {
    "schema_version": "synthetic-1",
    "label": "WATCHLIST",
    "report_grade": "synthetic grade",
    "reason_codes": ["SYNTHETIC_REASON"],
}
START = datetime(2026, 9, 1, 12, tzinfo=UTC)
BOUNDARIES = [
    name
    for name in dir(interfaces)
    if isinstance(getattr(interfaces, name), type)
    and getattr(getattr(interfaces, name), "_is_protocol", False)
    and name not in ("Protocol", "Clock", "StructuredLLM")
]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_all_contract_boundaries_are_protocols():
    assert len(BOUNDARIES) == 15


@pytest.mark.parametrize("name", BOUNDARIES)
def test_fake_tool_satisfies_every_boundary(name):
    assert isinstance(FakeTool([EMPTY]), getattr(interfaces, name))


def test_plain_function_satisfies_boundary():
    def retrieve(request: contracts.RetrievalRequest):
        return EMPTY

    assert isinstance(retrieve, interfaces.Retrieve)


def test_fake_llm_and_clock_satisfy_protocols():
    assert isinstance(FakeLLM([]), interfaces.StructuredLLM)
    assert isinstance(FakeClock(START), interfaces.Clock)


def test_fake_tool_returns_in_order_and_records_calls():
    boom = TimeoutError("synthetic")
    tool = FakeTool([EMPTY, boom])

    assert tool("q", top_k=3) is EMPTY
    with pytest.raises(TimeoutError):
        tool("q2")
    with pytest.raises(FakeExhausted):
        tool("q3")
    assert tool.calls == [
        Call(("q",), {"top_k": 3}),
        Call(("q2",), {}),
        Call(("q3",), {}),
    ]


def test_fake_llm_validates_schema_and_raises_errors():
    llm = FakeLLM(
        [
            DECISION,
            LLMError(ErrorCode.LLM_OUTPUT_INVALID, "synthetic"),
        ]
    )
    out = llm.generate(
        system="s", user="u", output_schema=contracts.DecisionPolicyResult
    )
    assert out.label == "WATCHLIST"
    with pytest.raises(LLMError) as raised:
        llm.generate(
            system="s", user="u2", output_schema=contracts.DecisionPolicyResult
        )
    assert raised.value.retryable is True
    assert [c.user for c in llm.calls] == ["u", "u2"]
    with pytest.raises(FakeExhausted):
        llm.generate(
            system="s", user="u3", output_schema=contracts.DecisionPolicyResult
        )


def test_fake_llm_rejects_wrong_schema():
    llm = FakeLLM([DECISION])
    with pytest.raises(ValueError):
        llm.generate(system="s", user="u", output_schema=contracts.RenderResult)


def test_fake_clock_is_deterministic():
    clock = FakeClock(START, step=timedelta(seconds=1))
    assert [clock.now(), clock.now()] == [START, START + timedelta(seconds=1)]
    clock.advance(timedelta(minutes=1))
    assert clock.now() == START + timedelta(minutes=1, seconds=2)
    assert clock.calls == 3
    with pytest.raises(ValueError):
        FakeClock(datetime(2026, 9, 1))
