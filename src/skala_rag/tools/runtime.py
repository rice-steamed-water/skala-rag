"""Provider-neutral, synchronous single-attempt runtime; no network/config defaults.

The injected transport must honor its timeout and perform exactly one request.
Provider response parsing and schema correction belong to the adapter/wrapper.
"""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, localcontext
from math import isfinite
from threading import Lock
from typing import Annotated, Generic, Literal, Protocol, Self, TypeVar
from uuid import uuid4

import httpx
from pydantic import ConfigDict, Field, StrictBool, ValidationError, model_validator

from skala_rag.contracts.common import (
    Contract,
    Count,
    MonetaryObservation,
    Number,
    Text,
)
from skala_rag.contracts.decisions import ScoreNumber
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode
from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.interfaces import Clock
from skala_rag.contracts.retrieval import RetrievalRecord
from skala_rag.contracts.tools import ToolBudget, ToolResult

DataT = TypeVar("DataT")
Money = Annotated[ScoreNumber, Field(ge=0)]
Delay = Annotated[Number, Field(ge=0)]


def _add_cost(left: Decimal, right: Decimal) -> Decimal:
    """Add finite decimal amounts without the caller's context rounding."""
    with localcontext() as context:
        context.prec = max(
            28,
            max(left.adjusted(), right.adjusted())
            - min(left.as_tuple().exponent, right.as_tuple().exponent)
            + 2,
        )
        return left + right


class _Frozen(Contract):
    model_config = ConfigDict(frozen=True)


class Readiness(_Frozen):
    """Observed flags only: no secret, endpoint, model download, or index loading."""

    required: StrictBool
    configured: StrictBool
    credential_required: StrictBool
    credential_present: StrictBool
    model_required: StrictBool
    model_available: StrictBool
    index_required: StrictBool
    index_available: StrictBool

    @property
    def missing(self) -> tuple[str, ...]:
        checks = (
            (self.configured, "configuration"),
            (not self.credential_required or self.credential_present, "credential"),
            (not self.model_required or self.model_available, "model"),
            (not self.index_required or self.index_available, "index"),
        )
        return tuple(name for present, name in checks if not present)


class RuntimePolicy(_Frozen):
    """No inferred retry delay or live timing approval; every field is explicit."""

    execution_mode: Literal["fixture", "live"]
    retry_delays_seconds: tuple[Delay, ...]
    live_approval_reference: Text | None
    timing_approval_reference: Text | None


class CallContext(_Frozen):
    """Opaque IDs, never prompts/queries/headers/URLs or exception text."""

    call_id: Text
    run_id: Text
    candidate_id: Text | None
    tool_name: Text
    node: Text


class Allowance(_Frozen):
    """Upper bound for ONE physical request, including error responses."""

    input_tokens: Count
    output_tokens: Count
    max_cost_usd: Money | None


class Usage(_Frozen):
    """Observed usage; unknown is None, never fabricated zero."""

    input_tokens: Count | None
    output_tokens: Count | None
    cost_usd: Money | None


class RuntimeLimits(_Frozen):
    """Injected shared limits. Overall run/campaign policy is owned by the runner."""

    max_calls: Count
    tool_max_calls: dict[Text, Count]
    max_input_tokens: Count | None
    max_output_tokens: Count | None
    max_cost_usd: Money | None


class AttemptResponse(_Frozen, Generic[DataT]):
    status: Literal["ok", "empty"]
    data: DataT
    source_ids: list[Text]
    chunk_ids: list[Text]
    evidence_ids: list[Text]
    usage: Usage

    @model_validator(mode="after")
    def require_data(self) -> Self:
        if self.data is None:
            raise ValueError("success/empty requires an explicit payload")
        return self


class TransportFailure(Exception):
    """Only a tool ErrorCode and optional observed usage; raw messages discarded."""

    def __init__(self, code: ErrorCode, *, usage: Usage | None = None) -> None:
        code = ErrorCode(code)
        if ERROR_SPECS[code].tool_status is None:
            raise ValueError("transport requires a tool error code")
        super().__init__(code.value)
        self.code = code
        self.usage = usage


def http_failure(status_code: int, *, usage: Usage | None = None) -> TransportFailure:
    """Map HTTP failures without retaining headers/body/URL or provider messages."""
    if type(status_code) is not int or not 400 <= status_code <= 599:
        raise ValueError("expected an HTTP error status")
    if status_code in (401, 403):
        code = ErrorCode.TOOL_AUTH_FAILED
    elif status_code == 429:
        code = ErrorCode.TOOL_RATE_LIMITED
    elif status_code >= 500:
        code = ErrorCode.TOOL_UNAVAILABLE
    else:
        code = ErrorCode.TOOL_FAILED
    return TransportFailure(code, usage=usage)


class SingleAttempt(Protocol[DataT]):
    retry_owner: Literal["runtime"]

    def __call__(self, *, timeout_seconds: float) -> AttemptResponse[DataT]: ...


class BudgetLedger:
    """One ledger shared across tools, transport retries, and schema corrections.

    Requests never refund. Unknown usage retains its reserved upper bound.
    Admission/settlement is atomic; this does not authorize parallel live requests.
    """

    def __init__(self, limits: RuntimeLimits) -> None:
        self.limits = limits.model_copy(deep=True)
        self._lock = Lock()
        self._calls = 0
        self._tool_calls: dict[str, int] = {}
        self._input = 0
        self._output = 0
        self._cost = Decimal(0)
        self._invalid_usage = False
        self._unknown_cost_requests = 0
        self._unbounded_unknown_cost = False

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "calls": self._calls,
                "tool_calls": dict(self._tool_calls),
                "input_tokens_accounted": self._input,
                "output_tokens_accounted": self._output,
                "cost_usd_accounted": (
                    None if self._unbounded_unknown_cost else str(self._cost)
                ),
                "unknown_cost_requests": self._unknown_cost_requests,
                "usage_invalid": self._invalid_usage,
            }

    def reserve(self, tool: str, allowance: Allowance, *, live: bool) -> bool:
        with self._lock:
            limits = self.limits
            cost = allowance.max_cost_usd
            if live and (
                cost is None
                or limits.max_cost_usd is None
                or limits.max_input_tokens is None
                or limits.max_output_tokens is None
            ):
                return False
            if limits.max_cost_usd is not None and cost is None:
                return False
            if (
                self._invalid_usage
                or self._calls >= limits.max_calls
                or tool not in limits.tool_max_calls
                or self._tool_calls.get(tool, 0) >= limits.tool_max_calls[tool]
            ):
                return False
            proposed = (
                (self._input + allowance.input_tokens, limits.max_input_tokens),
                (self._output + allowance.output_tokens, limits.max_output_tokens),
                (_add_cost(self._cost, cost or Decimal(0)), limits.max_cost_usd),
            )
            if any(cap is not None and value > cap for value, cap in proposed):
                return False
            self._calls += 1
            self._tool_calls[tool] = self._tool_calls.get(tool, 0) + 1
            self._input += allowance.input_tokens
            self._output += allowance.output_tokens
            self._cost = _add_cost(self._cost, cost or Decimal(0))
            return True

    def settle(self, allowance: Allowance, usage: Usage | None) -> bool:
        if usage is None:
            usage = Usage(
                schema_version=allowance.schema_version,
                input_tokens=None,
                output_tokens=None,
                cost_usd=None,
            )
        pairs = (
            (usage.input_tokens, allowance.input_tokens),
            (usage.output_tokens, allowance.output_tokens),
            (usage.cost_usd, allowance.max_cost_usd),
        )
        with self._lock:
            if usage.cost_usd is None:
                self._unknown_cost_requests += 1
                if allowance.max_cost_usd is None:
                    self._unbounded_unknown_cost = True
            if any(
                actual is not None and cap is not None and actual > cap
                for actual, cap in pairs
            ):
                self._invalid_usage = True
                return False
            if usage.input_tokens is not None:
                self._input -= allowance.input_tokens - usage.input_tokens
            if usage.output_tokens is not None:
                self._output -= allowance.output_tokens - usage.output_tokens
            if usage.cost_usd is not None:
                if allowance.max_cost_usd is not None:
                    difference = _add_cost(
                        usage.cost_usd, allowance.max_cost_usd.copy_negate()
                    )
                    self._cost = _add_cost(self._cost, difference)
                else:
                    self._cost = _add_cost(self._cost, usage.cost_usd)
            return True


class AdapterRuntime:
    def __init__(
        self,
        *,
        policy: RuntimePolicy,
        ledger: BudgetLedger,
        clock: Clock,
        sleep: Callable[[float], None],
    ) -> None:
        self.policy = policy.model_copy(deep=True)
        self.ledger = ledger
        self.clock = clock
        self.sleep = sleep
        self.error_history: dict[str, WorkflowError] = {}
        self.readiness_history: dict[str, dict] = {}

    def execute(
        self,
        call: CallContext,
        *,
        budget: ToolBudget,
        readiness: Readiness,
        allowance: Allowance,
        transport: SingleAttempt[DataT],
    ) -> ToolResult[DataT]:
        records: list[RetrievalRecord] = []
        execution_id = uuid4().hex
        self.readiness_history[call.tool_name] = {
            "required": readiness.required,
            "available": not readiness.missing,
            "missing": list(readiness.missing),
            "checked_at": self.clock.now().isoformat(),
        }
        # Metadata retained in records is a closed, runtime-created allowlist.
        arguments = {
            "execution_mode": self.policy.execution_mode,
            "required": readiness.required,
            "missing_readiness": list(readiness.missing),
            "call_id": call.call_id,
        }

        def failure(code: ErrorCode, attempt: int, started: datetime) -> WorkflowError:
            spec = ERROR_SPECS[code]
            error = WorkflowError(
                schema_version=call.schema_version,
                error_id=f"runtime-error-{uuid4().hex}",
                run_id=call.run_id,
                candidate_id=call.candidate_id,
                node=call.node,
                error_code=code.value,
                message_redacted=f"adapter attempt rejected: {code.value}",
                retryable=spec.retryable,
                attempt=attempt,
                timestamp=started,
            )
            self.error_history[error.error_id] = error
            return error

        def terminal(code: ErrorCode, attempt: int) -> ToolResult[DataT]:
            error = failure(code, attempt, self.clock.now())
            return ToolResult(
                schema_version=call.schema_version,
                status=ERROR_SPECS[code].tool_status,
                data=None,
                retrieval_records=records,
                errors=[error],
            )

        if readiness.missing:
            return terminal(ErrorCode.TOOL_NOT_CONFIGURED, 0)
        if getattr(transport, "retry_owner", None) != "runtime":
            return terminal(ErrorCode.TOOL_RESPONSE_INVALID, 0)
        if len(self.policy.retry_delays_seconds) != budget.max_retries:
            return terminal(ErrorCode.TOOL_NOT_CONFIGURED, 0)
        if self.policy.execution_mode == "live" and (
            self.policy.live_approval_reference is None
            or self.policy.timing_approval_reference is None
            or budget.deadline is None
        ):
            return terminal(ErrorCode.TOOL_NOT_CONFIGURED, 0)

        for attempt in range(1, budget.max_retries + 2):
            started = self.clock.now()
            timeout = float(budget.timeout_seconds)
            if budget.deadline is not None:
                timeout = min(timeout, (budget.deadline - started).total_seconds())
            if timeout <= 0 or attempt > budget.max_calls:
                return terminal(ErrorCode.BUDGET_EXHAUSTED, attempt - 1)
            if not self.ledger.reserve(
                call.tool_name, allowance, live=self.policy.execution_mode == "live"
            ):
                return terminal(ErrorCode.BUDGET_EXHAUSTED, attempt - 1)
            response = None
            usage = None
            code = None
            try:
                response = transport(timeout_seconds=timeout)
                if not isinstance(response, AttemptResponse):
                    raise TransportFailure(ErrorCode.TOOL_RESPONSE_INVALID)
                response = AttemptResponse.model_validate(
                    response, context={"execution_mode": self.policy.execution_mode}
                )
                usage = response.usage
            except TransportFailure as exc:
                code, usage = exc.code, exc.usage
                if usage is not None:
                    try:
                        usage = Usage.model_validate(usage)
                    except ValidationError:
                        code, usage = ErrorCode.TOOL_RESPONSE_INVALID, None
            except ValidationError:
                code = ErrorCode.TOOL_RESPONSE_INVALID
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                code = (
                    http_failure(status_code).code
                    if 400 <= status_code <= 599
                    else ErrorCode.TOOL_RESPONSE_INVALID
                )
            except (TimeoutError, httpx.TimeoutException):
                code = ErrorCode.TOOL_TIMEOUT
            except (httpx.LocalProtocolError, httpx.UnsupportedProtocol):
                code = ErrorCode.TOOL_FAILED
            except (ConnectionError, httpx.RequestError):
                code = ErrorCode.TOOL_UNAVAILABLE
            except Exception:
                # Never echo a provider exception (often contains URLs/keys/body).
                code = ErrorCode.TOOL_FAILED
            finished = self.clock.now()
            usage_valid = self.ledger.settle(allowance, usage)
            if finished < started:
                code = ErrorCode.TOOL_FAILED
                finished = started
            elif (finished - started).total_seconds() >= timeout:
                code = ErrorCode.TOOL_TIMEOUT
            if not usage_valid:
                code = ErrorCode.TOOL_RESPONSE_INVALID
            if code is not None:
                error = failure(code, attempt, finished)
                status = ERROR_SPECS[code].tool_status
            else:
                error = None
                status = response.status
            observed_cost = None
            if (
                usage is not None
                and usage.cost_usd is not None
                and isfinite(float(usage.cost_usd))
                and (usage.cost_usd == 0 or float(usage.cost_usd) > 0)
            ):
                observed_cost = MonetaryObservation(
                    schema_version=call.schema_version,
                    value=float(usage.cost_usd),
                    currency="USD",
                    unit="USD",
                    as_of=finished.date(),
                )
            records.append(
                RetrievalRecord(
                    schema_version=call.schema_version,
                    retrieval_id=f"runtime-retrieval-{execution_id}-{attempt}",
                    run_id=call.run_id,
                    candidate_id=call.candidate_id,
                    tool_name=call.tool_name,
                    query=None,
                    arguments_without_secrets={
                        **arguments,
                        "attempt": attempt,
                        "timeout_seconds": timeout,
                        "input_tokens": usage.input_tokens if usage else None,
                        "output_tokens": usage.output_tokens if usage else None,
                        "cost_usd_exact": (
                            str(usage.cost_usd)
                            if usage is not None and usage.cost_usd is not None
                            else None
                        ),
                    },
                    started_at=started,
                    finished_at=finished,
                    status=status,
                    source_ids=response.source_ids if response and code is None else [],
                    chunk_ids=response.chunk_ids if response and code is None else [],
                    evidence_ids=response.evidence_ids
                    if response and code is None
                    else [],
                    error_id=error.error_id if error else None,
                    cost=observed_cost,
                    cache_hit=False,
                )
            )
            if code is None:
                return ToolResult.model_validate(
                    {
                        "schema_version": call.schema_version,
                        "status": status,
                        "data": response.data,
                        "retrieval_records": records,
                        "errors": [],
                    },
                    context={"execution_mode": self.policy.execution_mode},
                )
            if not ERROR_SPECS[code].retryable or attempt > budget.max_retries:
                # Mixed-status history stays in records; terminal errors match status.
                return ToolResult(
                    schema_version=call.schema_version,
                    status=status,
                    data=None,
                    retrieval_records=records,
                    errors=[error],
                )
            delay = float(self.policy.retry_delays_seconds[attempt - 1])
            if (
                budget.deadline is not None
                and (budget.deadline - self.clock.now()).total_seconds() <= delay
            ):
                return terminal(ErrorCode.BUDGET_EXHAUSTED, attempt)
            try:
                self.sleep(delay)
            except Exception:
                return terminal(ErrorCode.TOOL_FAILED, attempt)
        raise AssertionError("bounded attempt loop must terminate")
