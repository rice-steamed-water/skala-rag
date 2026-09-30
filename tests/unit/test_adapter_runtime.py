"""Synthetic transports/clocks only; no provider, model, credential or network."""

import json
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path

import httpx
import pytest
import yaml
from pydantic import BaseModel, ValidationError
from tests.fixtures.loader import load_common_fixtures

from skala_rag.agents.evaluation import make_evaluate_dimension, output_from_evaluation
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import LLMError, StructuredLLM
from skala_rag.contracts.tools import ToolBudget
from skala_rag.fakes import FakeClock
from skala_rag.scoring.catalog import load_policy
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    AttemptResponse,
    BudgetLedger,
    CallContext,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
    Usage,
    http_failure,
)
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM

SCHEMA = "synthetic-runtime-1"
START = datetime(2026, 9, 30, tzinfo=UTC)
UNKNOWN = Usage(
    schema_version=SCHEMA, input_tokens=None, output_tokens=None, cost_usd=None
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("unexpected network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


class ScriptedTransport:
    retry_owner = "runtime"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.timeouts = []

    def __call__(self, *, timeout_seconds):
        self.timeouts.append(timeout_seconds)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


def reply(*, status="ok", data=None, usage=UNKNOWN):
    return AttemptResponse(
        schema_version=SCHEMA,
        status=status,
        data={"synthetic": True} if data is None else data,
        source_ids=["synthetic-source"] if status == "ok" else [],
        chunk_ids=[],
        evidence_ids=[],
        usage=usage,
    )


def setup(*, retries=0, calls=10, limits=None, mode="fixture", live=None, timing=None):
    clock = FakeClock(START)
    delays = []

    def sleep(seconds):
        delays.append(seconds)
        clock.advance(timedelta(seconds=seconds))

    if limits is None:
        limits = RuntimeLimits(
            schema_version=SCHEMA,
            max_calls=calls,
            tool_max_calls={"synthetic-web": calls, "synthetic-llm": calls},
            max_input_tokens=100,
            max_output_tokens=100,
            max_cost_usd="1",
        )
    ledger = BudgetLedger(limits)
    runtime = AdapterRuntime(
        policy=RuntimePolicy(
            schema_version=SCHEMA,
            execution_mode=mode,
            retry_delays_seconds=tuple(0.5 for _ in range(retries)),
            live_approval_reference=live,
            timing_approval_reference=timing,
        ),
        ledger=ledger,
        clock=clock,
        sleep=sleep,
    )
    kwargs = {
        "budget": ToolBudget(
            schema_version=SCHEMA,
            max_calls=calls,
            max_retries=retries,
            timeout_seconds=2,
            deadline=START + timedelta(seconds=30),
        ),
        "readiness": Readiness(
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
        "allowance": Allowance(
            schema_version=SCHEMA,
            input_tokens=5,
            output_tokens=5,
            max_cost_usd="0.1",
        ),
    }
    call = CallContext(
        schema_version=SCHEMA,
        call_id="synthetic-call",
        run_id="synthetic-run",
        candidate_id="synthetic-candidate",
        tool_name="synthetic-web",
        node="synthetic-research",
    )
    return runtime, call, kwargs, clock, delays


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"configured": False}, "configuration"),
        ({"credential_present": False}, "credential"),
        ({"model_required": True}, "model"),
        ({"index_required": True}, "index"),
    ],
)
def test_readiness_blocks_without_request_and_retains_required(
    changes, reason, required
):
    runtime, call, kwargs, _, _ = setup()
    kwargs["readiness"] = kwargs["readiness"].model_copy(
        update={**changes, "required": required}
    )
    transport = ScriptedTransport([])
    result = runtime.execute(call, transport=transport, **kwargs)
    assert result.status == "unavailable"
    assert result.errors[0].error_code == "TOOL_NOT_CONFIGURED"
    assert reason in kwargs["readiness"].missing
    assert transport.timeouts == [] and runtime.ledger.snapshot()["calls"] == 0
    assert result.retrieval_records == [] and result.errors[0].attempt == 0
    observed = runtime.readiness_history[call.tool_name]
    assert observed["required"] is required and observed["available"] is False
    assert reason in observed["missing"] and observed["checked_at"] == START.isoformat()


@pytest.mark.parametrize("status", ["ok", "empty"])
def test_success_and_zero_results_are_distinct_non_errors(status):
    runtime, call, kwargs, _, _ = setup()
    result = runtime.execute(
        call, transport=ScriptedTransport([reply(status=status, data=[])]), **kwargs
    )
    assert result.status == status and result.errors == [] and result.data == []
    assert result.retrieval_records[0].status == status
    assert result.retrieval_records[0].cost is None
    assert result.retrieval_records[0].arguments_without_secrets["attempt"] == 1


@pytest.mark.parametrize(
    "http,code,status,retryable",
    [
        (401, "TOOL_AUTH_FAILED", "unavailable", False),
        (403, "TOOL_AUTH_FAILED", "unavailable", False),
        (429, "TOOL_RATE_LIMITED", "unavailable", True),
        (500, "TOOL_UNAVAILABLE", "unavailable", True),
        (503, "TOOL_UNAVAILABLE", "unavailable", True),
        (400, "TOOL_FAILED", "failed", False),
    ],
)
def test_http_failure_classification_and_bounded_retry(http, code, status, retryable):
    runtime, call, kwargs, _, delays = setup(retries=2)
    transport = ScriptedTransport([http_failure(http)] * 3)
    result = runtime.execute(call, transport=transport, **kwargs)
    assert result.status == status and result.data is None
    assert result.errors[-1].error_code == code
    assert result.errors[-1].retryable is retryable
    expected = 3 if retryable else 1
    assert len(transport.timeouts) == expected
    assert len(result.retrieval_records) == expected
    assert len(delays) == expected - 1


def test_transient_failure_then_success_preserves_history_and_ids():
    runtime, call, kwargs, _, delays = setup(retries=2)
    transport = ScriptedTransport([http_failure(503), TimeoutError("secret"), reply()])
    result = runtime.execute(call, transport=transport, **kwargs)
    assert result.status == "ok" and result.errors == []
    assert [r.status for r in result.retrieval_records] == [
        "unavailable",
        "failed",
        "ok",
    ]
    assert delays == [0.5, 0.5]
    assert runtime.ledger.snapshot()["calls"] == 3
    for record in result.retrieval_records[:-1]:
        assert record.error_id in runtime.error_history
    again = runtime.execute(call, transport=ScriptedTransport([reply()]), **kwargs)
    assert {r.retrieval_id for r in result.retrieval_records}.isdisjoint(
        r.retrieval_id for r in again.retrieval_records
    )


@pytest.mark.parametrize("http", [429, 503])
def test_retries_cannot_bypass_shared_request_budget(http):
    runtime, call, kwargs, _, _ = setup(retries=2, calls=1)
    transport = ScriptedTransport([http_failure(http), reply()])
    result = runtime.execute(call, transport=transport, **kwargs)
    assert result.errors[-1].error_code == "BUDGET_EXHAUSTED"
    assert len(transport.timeouts) == 1
    assert len(result.retrieval_records) == 1
    assert runtime.ledger.snapshot()["calls"] == 1


def test_shared_ledger_across_tools_blocks_second_request():
    runtime, call, kwargs, _, _ = setup(calls=1)
    runtime.execute(call, transport=ScriptedTransport([reply()]), **kwargs)
    other = call.model_copy(update={"tool_name": "synthetic-llm"})
    transport = ScriptedTransport([])
    result = runtime.execute(other, transport=transport, **kwargs)
    assert result.errors[0].error_code == "BUDGET_EXHAUSTED"
    assert transport.timeouts == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_calls", 0),
        ("max_input_tokens", 4),
        ("max_output_tokens", 4),
        ("max_cost_usd", "0.09"),
    ],
)
def test_insufficient_limits_reject_before_request(field, value):
    runtime, call, kwargs, _, _ = setup()
    limits = RuntimeLimits.model_validate(
        {**runtime.ledger.limits.model_dump(), field: value}
    )
    runtime.ledger = BudgetLedger(limits)
    transport = ScriptedTransport([])
    result = runtime.execute(call, transport=transport, **kwargs)
    assert result.errors[0].error_code == "BUDGET_EXHAUSTED"
    assert transport.timeouts == []


def test_per_tool_limit_does_not_consume_other_tools_quota():
    runtime, call, kwargs, _, _ = setup()
    runtime.ledger = BudgetLedger(
        runtime.ledger.limits.model_copy(
            update={"tool_max_calls": {"synthetic-web": 0, "synthetic-llm": 1}}
        )
    )
    assert (
        runtime.execute(call, transport=ScriptedTransport([]), **kwargs).status
        == "failed"
    )
    other = call.model_copy(update={"tool_name": "synthetic-llm"})
    assert (
        runtime.execute(other, transport=ScriptedTransport([reply()]), **kwargs).status
        == "ok"
    )


def test_unknown_cost_keeps_reservation_and_never_claims_measured_zero():
    runtime, call, kwargs, _, _ = setup()
    result = runtime.execute(call, transport=ScriptedTransport([reply()]), **kwargs)
    assert result.retrieval_records[0].cost is None
    assert runtime.ledger.snapshot()["cost_usd_accounted"] == "0.1"
    assert runtime.ledger.snapshot()["unknown_cost_requests"] == 1
    kwargs["allowance"] = kwargs["allowance"].model_copy(update={"max_cost_usd": None})
    transport = ScriptedTransport([])
    assert runtime.execute(call, transport=transport, **kwargs).status == "failed"
    assert transport.timeouts == []


def test_observed_usage_reconciles_exact_cost_but_does_not_refund_calls():
    runtime, call, kwargs, _, _ = setup()
    usage = Usage(
        schema_version=SCHEMA,
        input_tokens=2,
        output_tokens=3,
        cost_usd="0.01234567890123456789",
    )
    result = runtime.execute(
        call, transport=ScriptedTransport([reply(usage=usage)]), **kwargs
    )
    snap = runtime.ledger.snapshot()
    assert snap["calls"] == 1 and snap["input_tokens_accounted"] == 2
    assert snap["output_tokens_accounted"] == 3
    assert snap["cost_usd_accounted"] == "0.01234567890123456789"
    args = result.retrieval_records[0].arguments_without_secrets
    assert args["cost_usd_exact"] == "0.01234567890123456789"
    assert result.retrieval_records[0].cost.currency == "USD"


@pytest.mark.parametrize(
    "field,value", [("input_tokens", 6), ("output_tokens", 6), ("cost_usd", "0.11")]
)
def test_overreported_usage_poison_ledger_and_never_retry(field, value):
    runtime, call, kwargs, _, _ = setup(retries=2)
    usage = Usage.model_validate({**UNKNOWN.model_dump(), field: value})
    transport = ScriptedTransport([reply(usage=usage)])
    result = runtime.execute(call, transport=transport, **kwargs)
    assert result.errors[0].error_code == "TOOL_RESPONSE_INVALID"
    assert runtime.ledger.snapshot()["usage_invalid"] is True
    assert len(transport.timeouts) == 1
    assert (
        runtime.execute(call, transport=ScriptedTransport([]), **kwargs).status
        == "failed"
    )


def test_atomic_admission_cannot_overdraw_with_parallel_callers():
    runtime, _, kwargs, _, _ = setup(calls=1)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: runtime.ledger.reserve(
                    "synthetic-web", kwargs["allowance"], live=False
                ),
                range(20),
            )
        )
    assert sum(results) == 1


def test_deadline_clips_transport_timeout_and_rejects_late_success():
    runtime, call, kwargs, clock, _ = setup()
    kwargs["budget"].deadline = START + timedelta(seconds=1)

    class Slow(ScriptedTransport):
        def __call__(self, *, timeout_seconds):
            clock.advance(timedelta(seconds=1))
            return super().__call__(timeout_seconds=timeout_seconds)

    transport = Slow([reply()])
    result = runtime.execute(call, transport=transport, **kwargs)
    assert transport.timeouts == [1.0]
    assert result.status == "failed" and result.errors[0].error_code == "TOOL_TIMEOUT"


def test_expired_deadline_prevents_any_transport():
    runtime, call, kwargs, _, _ = setup()
    kwargs["budget"].deadline = START
    result = runtime.execute(call, transport=ScriptedTransport([]), **kwargs)
    assert result.errors[0].error_code == "BUDGET_EXHAUSTED"
    assert runtime.ledger.snapshot()["calls"] == 0


def test_backoff_beyond_deadline_no_sleep_or_retry():
    runtime, call, kwargs, _, delays = setup(retries=2)
    kwargs["budget"].deadline = START + timedelta(seconds=0.5)
    transport = ScriptedTransport([http_failure(429)])
    result = runtime.execute(call, transport=transport, **kwargs)
    assert result.errors[-1].error_code == "BUDGET_EXHAUSTED"
    assert len(transport.timeouts) == 1 and delays == []


def test_unapproved_live_timing_refuses_transport():
    runtime, call, kwargs, _, _ = setup(mode="live", live="synthetic-approved")
    result = runtime.execute(call, transport=ScriptedTransport([]), **kwargs)
    assert result.errors[0].error_code == "TOOL_NOT_CONFIGURED"
    assert runtime.ledger.snapshot()["calls"] == 0


def test_explicit_live_settings_do_not_authorize_or_invoke_network_in_test():
    runtime, call, kwargs, _, _ = setup(
        mode="live", live="synthetic-scope", timing="synthetic-timing"
    )
    result = runtime.execute(call, transport=ScriptedTransport([reply()]), **kwargs)
    assert result.status == "ok"  # synthetic transport, never live evidence


def test_nested_retry_owner_refused_before_request():
    runtime, call, kwargs, _, _ = setup()
    transport = ScriptedTransport([])
    transport.retry_owner = "provider"
    result = runtime.execute(call, transport=transport, **kwargs)
    assert result.errors[0].error_code == "TOOL_RESPONSE_INVALID"
    assert transport.timeouts == []


@pytest.mark.parametrize("exception", [RuntimeError, TimeoutError, ConnectionError])
def test_exception_secrets_and_raw_body_are_not_logged(exception):
    runtime, call, kwargs, _, _ = setup()
    secret = "Bearer synthetic-secret-key https://example.org/?token=secret RAW_BODY"
    result = runtime.execute(
        call, transport=ScriptedTransport([exception(secret)]), **kwargs
    )
    serialized = result.model_dump_json() + json.dumps(
        {k: v.model_dump(mode="json") for k, v in runtime.error_history.items()}
    )
    assert "synthetic-secret-key" not in serialized and "RAW_BODY" not in serialized
    assert "https://example.org" not in serialized


@pytest.mark.parametrize("value", [True, -1, "NaN", "Infinity"])
def test_invalid_costs_are_rejected(value):
    with pytest.raises(ValidationError):
        Allowance(
            schema_version=SCHEMA, input_tokens=0, output_tokens=0, max_cost_usd=value
        )


def test_unknown_and_unconfigured_tool_has_no_implicit_quota():
    runtime, call, kwargs, _, _ = setup()
    call = call.model_copy(update={"tool_name": "not-in-limits"})
    result = runtime.execute(call, transport=ScriptedTransport([]), **kwargs)
    assert result.status == "failed" and runtime.ledger.snapshot()["calls"] == 0


class Output(BaseModel):
    answer: int


class ScriptedLLM:
    retry_owner = "runtime"

    def __init__(self, outputs):
        self.script = ScriptedTransport(outputs)
        self.calls = []

    def generate_once(self, **kwargs):
        self.calls.append(kwargs)
        return self.script(timeout_seconds=kwargs["timeout_seconds"])


def make_llm(runtime, call, kwargs, transport):
    return RuntimeStructuredLLM(
        runtime=runtime,
        call=call.model_copy(update={"tool_name": "synthetic-llm"}),
        budget=kwargs["budget"],
        readiness=kwargs["readiness"],
        transport=transport,
        allowance_for=lambda *args: kwargs["allowance"],
    )


def test_llm_schema_repairs_consume_same_ledger_without_transport_retry():
    runtime, call, kwargs, _, delays = setup(retries=2, calls=2)
    provider = ScriptedLLM(
        [reply(data={"answer": "invalid"}), reply(data={"answer": 4})]
    )
    llm = make_llm(runtime, call, kwargs, provider)
    assert isinstance(llm, StructuredLLM)
    with pytest.raises(LLMError) as error:
        llm.generate(system="PRIVATE_PROMPT", user="PRIVATE_DOC", output_schema=Output)
    assert error.value.error_code == ErrorCode.LLM_OUTPUT_INVALID
    assert len(provider.calls) == 1 and delays == []
    output = llm.generate(system="PRIVATE_PROMPT", user="correct", output_schema=Output)
    assert output.answer == 4 and runtime.ledger.snapshot()["calls"] == 2
    with pytest.raises(LLMError) as denied:
        llm.generate(system="PRIVATE_PROMPT", user="third", output_schema=Output)
    assert denied.value.retryable is False and len(provider.calls) == 2
    serialized = json.dumps([r.model_dump(mode="json") for r in llm.retrieval_records])
    assert "PRIVATE_PROMPT" not in serialized and "PRIVATE_DOC" not in serialized
    assert provider.calls[0]["input_token_limit"] == 5
    assert provider.calls[0]["output_token_limit"] == 5


def test_llm_exhausted_transport_failure_is_not_a_schema_repair():
    runtime, call, kwargs, _, _ = setup(retries=1)
    provider = ScriptedLLM([TimeoutError("PRIVATE"), TimeoutError("PRIVATE")])
    llm = make_llm(runtime, call, kwargs, provider)
    with pytest.raises(LLMError) as error:
        llm.generate(system="system", user="user", output_schema=Output)
    assert error.value.error_code == ErrorCode.LLM_TIMEOUT
    assert len(provider.calls) == 2
    assert all(e.error_code == "TOOL_TIMEOUT" for e in runtime.error_history.values())


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_httpx_mock_transport_status_errors_are_normalized_without_secrets(status):
    runtime, call, kwargs, _, _ = setup(retries=1)
    requests = []

    def handler(request):
        requests.append(request)
        assert request.extensions["timeout"]["read"] == 2
        return httpx.Response(status, text="PRIVATE_BODY")

    with httpx.Client(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:

        class HttpAttempt:
            retry_owner = "runtime"

            def __call__(self, *, timeout_seconds):
                response = client.get(
                    "https://synthetic.invalid/?token=PRIVATE_KEY",
                    timeout=timeout_seconds,
                    follow_redirects=False,
                )
                response.raise_for_status()
                return reply()

        result = runtime.execute(call, transport=HttpAttempt(), **kwargs)
    expected = 1 if status in (401, 403) else 2
    assert len(requests) == expected
    assert runtime.ledger.snapshot()["calls"] == expected
    assert "PRIVATE_KEY" not in result.model_dump_json()
    assert "PRIVATE_BODY" not in result.model_dump_json()


@pytest.mark.parametrize(
    "exception,code",
    [
        (httpx.ReadTimeout, "TOOL_TIMEOUT"),
        (httpx.ConnectTimeout, "TOOL_TIMEOUT"),
        (httpx.PoolTimeout, "TOOL_TIMEOUT"),
        (httpx.ConnectError, "TOOL_UNAVAILABLE"),
        (httpx.LocalProtocolError, "TOOL_FAILED"),
    ],
)
def test_httpx_typed_exceptions(exception, code):
    runtime, call, kwargs, _, _ = setup()
    result = runtime.execute(
        call, transport=ScriptedTransport([exception("PRIVATE_KEY")]), **kwargs
    )
    assert result.errors[0].error_code == code
    assert "PRIVATE_KEY" not in result.model_dump_json()


def test_unknown_unbounded_fixture_usage_is_not_zero():
    runtime, call, kwargs, _, _ = setup()
    runtime.ledger = BudgetLedger(
        runtime.ledger.limits.model_copy(update={"max_cost_usd": None})
    )
    kwargs["allowance"] = kwargs["allowance"].model_copy(update={"max_cost_usd": None})
    result = runtime.execute(call, transport=ScriptedTransport([reply()]), **kwargs)
    assert result.status == "ok" and result.retrieval_records[0].cost is None
    assert runtime.ledger.snapshot()["cost_usd_accounted"] is None


def test_failed_transport_with_known_usage_retains_paid_observation():
    runtime, call, kwargs, _, _ = setup()
    usage = Usage(
        schema_version=SCHEMA, input_tokens=1, output_tokens=0, cost_usd="0.04"
    )
    result = runtime.execute(
        call, transport=ScriptedTransport([http_failure(503, usage=usage)]), **kwargs
    )
    assert result.status == "unavailable"
    assert runtime.ledger.snapshot()["calls"] == 1
    assert Decimal(runtime.ledger.snapshot()["cost_usd_accounted"]) == Decimal("0.04")
    assert (
        result.retrieval_records[0].arguments_without_secrets["cost_usd_exact"]
        == "0.04"
    )


def test_schema_error_with_final_http_status_has_compatible_terminal_errors():
    runtime, call, kwargs, _, _ = setup(retries=1)
    result = runtime.execute(
        call,
        transport=ScriptedTransport([TimeoutError("synthetic"), http_failure(401)]),
        **kwargs,
    )
    assert result.status == "unavailable"
    assert [r.status for r in result.retrieval_records] == ["failed", "unavailable"]
    assert len(result.errors) == 1 and result.errors[0].error_code == "TOOL_AUTH_FAILED"
    assert len(runtime.error_history) == 2


def test_policy_requires_explicit_delay_for_every_retry():
    runtime, call, kwargs, _, _ = setup()
    kwargs["budget"].max_retries = 1
    result = runtime.execute(call, transport=ScriptedTransport([]), **kwargs)
    assert result.errors[0].error_code == "TOOL_NOT_CONFIGURED"


@pytest.mark.parametrize(
    "field", ["live_approval_reference", "timing_approval_reference"]
)
def test_live_scope_and_timing_are_separate_explicit_gates(field):
    runtime, call, kwargs, _, _ = setup(
        mode="live", live="synthetic", timing="synthetic"
    )
    runtime.policy = runtime.policy.model_copy(update={field: None})
    result = runtime.execute(call, transport=ScriptedTransport([]), **kwargs)
    assert result.status == "unavailable" and runtime.ledger.snapshot()["calls"] == 0


def test_live_without_deadline_or_verified_cost_is_refused():
    runtime, call, kwargs, _, _ = setup(
        mode="live", live="synthetic", timing="synthetic"
    )
    kwargs["budget"].deadline = None
    assert (
        runtime.execute(call, transport=ScriptedTransport([]), **kwargs).status
        == "unavailable"
    )
    kwargs["budget"].deadline = START + timedelta(seconds=10)
    kwargs["allowance"] = kwargs["allowance"].model_copy(update={"max_cost_usd": None})
    assert (
        runtime.execute(call, transport=ScriptedTransport([]), **kwargs).status
        == "failed"
    )
    assert runtime.ledger.snapshot()["calls"] == 0


@pytest.mark.parametrize("calls", [1, 2])
def test_existing_evaluation_wrapper_schema_repair_obeys_runtime_ledger(calls):
    root = Path(__file__).resolve().parents[2]
    policy = load_policy(root / "configs/scoring.draft.json", execution_mode="fixture")
    fx = load_common_fixtures(policy)
    snapshot = next(iter(fx.snapshots.values()))
    key = f"{snapshot.candidate_id}:{snapshot.evaluation_round}:founder"
    good = output_from_evaluation(fx.evaluations[key])
    bad = good.model_dump()
    bad["criteria"][0]["points"] = 999  # forbidden derived output, schema repair
    runtime, call, kwargs, clock, _ = setup(retries=1, calls=calls)
    provider = ScriptedLLM([reply(data=bad), reply(data=good)])
    llm = make_llm(runtime, call, kwargs, provider)
    evaluate = make_evaluate_dimension(
        llm=llm, policy=policy, clock=clock, schema_version=SCHEMA
    )
    rubric = yaml.safe_load((root / "configs/rubrics/core.yaml").read_text())
    result = evaluate("founder", snapshot, rubric)
    assert result.status == ("success" if calls == 2 else "failure")
    assert len(provider.calls) == runtime.ledger.snapshot()["calls"] == calls
    if calls == 1:
        assert result.errors[0].error_code == "LLM_FAILED"
    else:
        assert "계약을 어겼다" in provider.calls[1]["user"]


def test_existing_wrapper_does_not_retry_exhausted_transport_timeout():
    root = Path(__file__).resolve().parents[2]
    policy = load_policy(root / "configs/scoring.draft.json", execution_mode="fixture")
    fx = load_common_fixtures(policy)
    snapshot = next(iter(fx.snapshots.values()))
    runtime, call, kwargs, clock, _ = setup(retries=1)
    provider = ScriptedLLM([TimeoutError("synthetic"), TimeoutError("synthetic")])
    evaluate = make_evaluate_dimension(
        llm=make_llm(runtime, call, kwargs, provider),
        policy=policy,
        clock=clock,
        schema_version=SCHEMA,
    )
    rubric = yaml.safe_load((root / "configs/rubrics/core.yaml").read_text())
    result = evaluate("founder", snapshot, rubric)
    assert result.status == "failure" and result.errors[0].error_code == "LLM_TIMEOUT"
    assert len(provider.calls) == 2


def test_cost_admission_uses_exact_decimal_boundary_even_beyond_context_precision():
    runtime, call, kwargs, _, _ = setup()
    kwargs["allowance"] = kwargs["allowance"].model_copy(
        update={"max_cost_usd": Decimal(1)}
    )
    runtime.execute(call, transport=ScriptedTransport([reply()]), **kwargs)
    tiny = Decimal("1e-100")
    kwargs["allowance"] = kwargs["allowance"].model_copy(update={"max_cost_usd": tiny})
    with localcontext() as context:
        context.prec = 6
        denied = runtime.execute(call, transport=ScriptedTransport([]), **kwargs)
    assert denied.errors[0].error_code == "BUDGET_EXHAUSTED"
    assert runtime.ledger.snapshot()["calls"] == 1


def test_response_instance_cannot_bypass_nested_usage_validation():
    runtime, call, kwargs, _, _ = setup()
    invalid = UNKNOWN.model_copy(update={"cost_usd": Decimal("NaN")})
    forged = reply().model_copy(update={"usage": invalid})
    result = runtime.execute(call, transport=ScriptedTransport([forged]), **kwargs)
    assert result.errors[0].error_code == "TOOL_RESPONSE_INVALID"
    assert runtime.ledger.snapshot()["calls"] == 1
    assert result.retrieval_records[0].cost is None


def test_tiny_known_cost_is_not_falsely_displayed_as_zero():
    runtime, call, kwargs, _, _ = setup()
    usage = Usage(
        schema_version=SCHEMA, input_tokens=0, output_tokens=0, cost_usd="1e-1000"
    )
    result = runtime.execute(
        call, transport=ScriptedTransport([reply(usage=usage)]), **kwargs
    )
    record = result.retrieval_records[0]
    assert record.cost is None  # existing Number cannot represent this amount
    assert record.arguments_without_secrets["cost_usd_exact"] == "1E-1000"
    assert Decimal(runtime.ledger.snapshot()["cost_usd_accounted"]) == Decimal(
        "1e-1000"
    )
