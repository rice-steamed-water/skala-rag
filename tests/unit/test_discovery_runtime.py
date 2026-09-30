"""Shared runtime receipts from synthetic HTTP only."""

from datetime import datetime, timezone

import httpx
import pytest

from skala_rag.contracts.tools import ToolBudget
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    BudgetLedger,
    CallContext,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
)


class FakeClock:
    def now(self):
        return datetime(2026, 9, 30, tzinfo=timezone.utc)


def build(handler, *, retries=0, cap=6, mode="fixture", price=None):
    from skala_rag.tools.discovery_runtime import TavilyRuntimeBridge

    clock = FakeClock()
    ledger = BudgetLedger(
        RuntimeLimits(
            schema_version="1",
            max_calls=6,
            tool_max_calls={"tavily": cap},
            max_input_tokens=None,
            max_output_tokens=None,
            max_cost_usd=None,
        )
    )
    runtime = AdapterRuntime(
        policy=RuntimePolicy(
            schema_version="1",
            execution_mode=mode,
            retry_delays_seconds=(1, 2)[:retries],
            live_approval_reference=None,
            timing_approval_reference=None,
        ),
        ledger=ledger,
        clock=clock,
        sleep=lambda delay: None,
    )
    bridge = TavilyRuntimeBridge(
        runtime=runtime,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        api_key="synthetic-not-a-real-key",
        context=CallContext(
            schema_version="1",
            call_id="call",
            run_id="run",
            candidate_id=None,
            tool_name="tavily",
            node="discovery",
        ),
        readiness=Readiness(
            schema_version="1",
            required=True,
            configured=True,
            credential_required=True,
            credential_present=True,
            model_required=False,
            model_available=False,
            index_required=False,
            index_available=False,
        ),
        allowance=Allowance(
            schema_version="1", input_tokens=0, output_tokens=0, max_cost_usd=price
        ),
    )
    budget = ToolBudget(
        schema_version="1", max_calls=3, max_retries=retries, timeout_seconds=7
    )
    return bridge, budget, ledger


def test_bridge_returns_actual_runtime_record_and_injected_timeout():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"results": []})

    bridge, budget, ledger = build(handler)
    result = bridge({"query": "public synthetic"}, budget)
    assert result.status == "ok"
    assert result.data.body == {"results": []}
    assert result.retrieval_records[0].retrieval_id.startswith("runtime-retrieval-")
    assert requests[0].extensions["timeout"]["read"] == 7
    assert ledger.snapshot()["calls"] == 1
    assert ledger.snapshot()["tool_calls"] == {"tavily": 1}


@pytest.mark.parametrize(
    "status,code",
    [(401, "TOOL_AUTH_FAILED"), (403, "TOOL_AUTH_FAILED"), (432, "BUDGET_EXHAUSTED")],
)
def test_failure_spends_once_without_auth_retry(status, code):
    bridge, budget, ledger = build(lambda r: httpx.Response(status), retries=2)
    result = bridge({}, budget)
    assert result.errors[0].error_code == code
    assert len(result.retrieval_records) == 1
    assert ledger.snapshot()["calls"] == 1


def test_retry_after_fails_closed_without_retry():
    bridge, budget, ledger = build(
        lambda r: httpx.Response(429, headers={"Retry-After": "20"}), retries=2
    )
    result = bridge({}, budget)
    assert result.errors[0].error_code == "TOOL_NOT_CONFIGURED"
    assert ledger.snapshot()["calls"] == 1


@pytest.mark.parametrize("kind", ["invalid", "timeout", "exception"])
def test_redacted_physical_failures(kind):
    def handler(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("SECRET")
        if kind == "exception":
            raise RuntimeError("SECRET")
        return httpx.Response(200, text="SECRET-not-json")

    bridge, budget, ledger = build(handler)
    result = bridge({}, budget)
    assert result.status == "failed"
    assert "SECRET" not in result.model_dump_json()
    assert ledger.snapshot()["calls"] == 1


def test_exhausted_ledger_never_sends():
    bridge, budget, ledger = build(lambda r: pytest.fail("HTTP forbidden"), cap=0)
    assert bridge({}, budget).errors[0].error_code == "BUDGET_EXHAUSTED"
    assert ledger.snapshot()["calls"] == 0


def test_runtime_owns_transient_retries_and_shared_six_request_cap():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(500 if len(seen) < 3 else 200, json={"results": []})

    bridge, budget, ledger = build(handler, retries=2)
    delays = []
    bridge.runtime.sleep = delays.append
    result = bridge({}, budget)
    assert result.status == "ok"
    assert len(result.retrieval_records) == 3
    assert delays == [1, 2]
    for _ in range(3):
        assert bridge({}, budget).status == "ok"
    assert ledger.snapshot()["calls"] == 6
    assert ledger.snapshot()["tool_calls"] == {"tavily": 6}
    assert bridge({}, budget).errors[0].error_code == "BUDGET_EXHAUSTED"
    assert len(seen) == 6


def test_live_without_approvals_deadline_price_or_limits_never_sends():
    bridge, budget, ledger = build(lambda r: pytest.fail("live forbidden"), mode="live")
    assert bridge({}, budget).errors[0].error_code == "TOOL_NOT_CONFIGURED"
    assert ledger.snapshot()["calls"] == 0
