"""#47 adapter → #45 runtime 브리지 (#51). MockTransport만 쓴다(실제 API 없음)."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import LLMError
from skala_rag.contracts.tools import ToolBudget
from skala_rag.fakes import FakeClock
from skala_rag.tools.openai_attempt import (
    FRAMING_TOKENS,
    RESPONSES_URL,
    OpenAIResponsesAttempt,
    byte_bound_allowance,
)
from skala_rag.tools.runtime import (
    AdapterRuntime,
    BudgetLedger,
    CallContext,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
)
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM
from skala_rag.tools.structured_llm import APPROVED_MODEL

SCHEMA = "synthetic-bridge-1"
KEY = "sk-FAKE-KEY-DO-NOT-LOG"
START = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
IN_PRICE = Decimal("0.4") / 1_000_000
OUT_PRICE = Decimal("1.6") / 1_000_000
TOOL = "openai-eligibility-facts"


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: int


def body(text='{"answer": 4}', **changes):
    data = {
        "model": APPROVED_MODEL,
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": text}]}
        ],
        "usage": {"input_tokens": 120, "output_tokens": 8},
    }
    data.update(changes)
    return data


def build(handler, *, max_input_tokens=8000, max_output_tokens=50):
    clock = FakeClock(START)
    seen = []

    def record(request):
        seen.append(request)
        return handler(request)

    runtime = AdapterRuntime(
        policy=RuntimePolicy(
            schema_version=SCHEMA,
            execution_mode="live",
            retry_delays_seconds=(),
            live_approval_reference="synthetic-live-approval",
            timing_approval_reference="synthetic-timing-approval",
        ),
        ledger=BudgetLedger(
            RuntimeLimits(
                schema_version=SCHEMA,
                max_calls=1,
                tool_max_calls={TOOL: 1},
                max_input_tokens=max_input_tokens,
                max_output_tokens=2000,
                max_cost_usd="1.00",
            )
        ),
        clock=clock,
        sleep=lambda seconds: None,
    )
    attempt = OpenAIResponsesAttempt(
        api_key=KEY,
        prompt_version="synthetic-prompt",
        schema_version=SCHEMA,
        clock=clock,
        http_transport=httpx.MockTransport(record),
    )
    llm = RuntimeStructuredLLM(
        runtime=runtime,
        call=CallContext(
            schema_version=SCHEMA,
            call_id="synthetic-call",
            run_id="synthetic-run",
            candidate_id="co-alpha",
            tool_name=TOOL,
            node="company_research",
        ),
        budget=ToolBudget(
            schema_version=SCHEMA,
            max_calls=1,
            max_retries=0,
            timeout_seconds=30,
            deadline=START + timedelta(minutes=10),
        ),
        readiness=Readiness(
            schema_version=SCHEMA,
            required=True,
            configured=True,
            credential_required=True,
            credential_present=True,
            model_required=False,
            model_available=False,
            index_required=False,
            index_available=False,
        ),
        transport=attempt,
        allowance_for=lambda system, user, schema: byte_bound_allowance(
            system,
            user,
            schema,
            schema_version=SCHEMA,
            max_output_tokens=max_output_tokens,
            usd_per_input_token=IN_PRICE,
            usd_per_output_token=OUT_PRICE,
        ),
    )
    return llm, attempt, seen


def test_allowance_bounds_input_by_utf8_bytes_and_prices_it():
    allowance = byte_bound_allowance(
        "시스템",
        "사용자",
        Output,
        schema_version=SCHEMA,
        max_output_tokens=100,
        usd_per_input_token=IN_PRICE,
        usd_per_output_token=OUT_PRICE,
    )
    assert allowance.input_tokens > len("시스템사용자".encode()) + FRAMING_TOKENS
    expected = allowance.input_tokens * IN_PRICE + 100 * OUT_PRICE
    assert allowance.max_cost_usd == expected


def test_one_request_through_runtime_returns_parsed_output():
    llm, attempt, seen = build(lambda request: httpx.Response(200, json=body()))
    assert llm.generate(system="s", user="u", output_schema=Output) == Output(answer=4)
    (request,) = seen
    assert str(request.url) == RESPONSES_URL
    assert request.headers["authorization"] == f"Bearer {KEY}"
    payload = json.loads(request.content)
    assert payload["model"] == APPROVED_MODEL and payload["store"] is False
    (record,) = llm.retrieval_records
    assert record.status == "ok"
    args = record.arguments_without_secrets
    assert (args["input_tokens"], args["output_tokens"]) == (120, 8)
    assert args["cost_usd_exact"] is None
    assert attempt.llm_calls[0].status == "success"


@pytest.mark.parametrize(
    ("response", "tool_code", "llm_code"),
    [
        (httpx.Response(401), ErrorCode.TOOL_AUTH_FAILED, ErrorCode.LLM_FAILED),
        (httpx.Response(429), ErrorCode.TOOL_RATE_LIMITED, ErrorCode.LLM_FAILED),
        (
            httpx.Response(200, json=body(text="not json")),
            ErrorCode.TOOL_RESPONSE_INVALID,
            ErrorCode.LLM_OUTPUT_INVALID,
        ),
        (
            httpx.Response(200, json=body(status="incomplete")),
            ErrorCode.TOOL_FAILED,
            ErrorCode.LLM_FAILED,
        ),
    ],
)
def test_provider_failures_are_mapped_without_retry(response, tool_code, llm_code):
    llm, _, seen = build(lambda request: response)
    with pytest.raises(LLMError) as error:
        llm.generate(system="s", user="u", output_schema=Output)
    assert error.value.error_code == llm_code
    assert len(seen) == 1
    (record,) = llm.retrieval_records
    assert record.status in ("unavailable", "failed")
    assert llm.runtime.error_history[record.error_id].error_code == tool_code.value


def test_timeout_is_llm_timeout():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    llm, _, _ = build(slow)
    with pytest.raises(LLMError) as error:
        llm.generate(system="s", user="u", output_schema=Output)
    assert error.value.error_code == ErrorCode.LLM_TIMEOUT


def test_request_over_token_limit_is_refused_before_sending():
    llm, _, seen = build(
        lambda request: httpx.Response(200, json=body()), max_input_tokens=10
    )
    with pytest.raises(LLMError):
        llm.generate(system="s", user="u" * 100, output_schema=Output)
    assert seen == []
    assert llm.retrieval_records == []


def test_api_key_is_not_recorded():
    llm, attempt, _ = build(lambda request: httpx.Response(401))
    with pytest.raises(LLMError):
        llm.generate(system="s", user="u", output_schema=Output)
    dumped = json.dumps(
        [r.model_dump(mode="json") for r in llm.retrieval_records]
        + [e.model_dump(mode="json") for e in llm.runtime.error_history.values()]
    )
    assert KEY not in dumped
    assert KEY not in repr(attempt.llm_calls)
