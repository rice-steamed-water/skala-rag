"""Synthetic Retry-After regressions; never provider traffic."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
from tests.unit.test_adapter_runtime import (
    ScriptedTransport,
    no_network,  # noqa: F401
    reply,
    setup,
)

from skala_rag.fakes import FakeClock
from skala_rag.tools import runtime as module


def test_normalized_minimum_is_immutable_and_runtime_waits():
    runtime, call, kwargs, clock, delays = setup(retries=2)
    metadata = module.RetryAfter(seconds=3, captured_at=clock.now())
    transport = ScriptedTransport(
        [module.http_failure(429, retry_after=metadata), reply()]
    )
    result = runtime.execute(call, transport=transport, **kwargs)
    assert result.status == "ok"
    assert delays == [3]
    assert result.retrieval_records[1].started_at == clock.now()
    assert runtime.ledger.snapshot()["calls"] == 2
    assert clock.now() == metadata.captured_at + timedelta(seconds=3)


def test_parser_delta_and_dates():
    _, _, _, clock, _ = setup()
    for header, expected in [
        ("3", 3),
        (" 3\t", 3),
        ("Wed, 30 Sep 2026 00:00:03 GMT", 3),
        ("Wednesday, 30-Sep-26 00:00:03 GMT", 3),
        ("Wed Sep 30 00:00:03 2026", 3),
        ("Tue, 29 Sep 2026 00:00:00 GMT", 0),
        ("Wednesday, 30-Sep-77 00:00:03 GMT", 0),
    ]:
        parsed = module.parse_retry_after(header, clock=clock)
        assert parsed.seconds == expected
        assert parsed.captured_at == clock.now()
    assert module.parse_retry_after(None, clock=clock) is None


def test_httpx_minimum_and_malformed_are_enforced():

    for header, expected, error in [
        ("3", [3], None),
        ("-1", [], "TOOL_RESPONSE_INVALID"),
        ("Bearer PRIVATE", [], "TOOL_RESPONSE_INVALID"),
    ]:
        runtime, call, kwargs, _, delays = setup(retries=2)
        response = httpx.Response(
            429,
            headers={"Retry-After": header},
            request=httpx.Request("GET", "https://synthetic.invalid/?PRIVATE"),
        )
        transport = ScriptedTransport(
            [
                httpx.HTTPStatusError(
                    "PRIVATE", request=response.request, response=response
                ),
                reply(),
            ]
        )
        result = runtime.execute(call, transport=transport, **kwargs)
        assert delays == expected
        if error:
            assert result.errors[0].error_code == error
            assert len(transport.timeouts) == 1
        else:
            assert result.status == "ok"
        assert "PRIVATE" not in result.model_dump_json()


def test_exhausted_request_budget_does_not_sleep():
    runtime, call, kwargs, clock, delays = setup(retries=2, calls=1)
    result = runtime.execute(
        call,
        transport=ScriptedTransport(
            [module.http_failure(429, retry_after=module.RetryAfter(3, clock.now()))]
        ),
        **kwargs,
    )
    assert result.errors[0].error_code == "BUDGET_EXHAUSTED"
    assert delays == []


@pytest.mark.parametrize("value", [True, "3", -1, float("nan"), float("inf"), 10**400])
def test_numeric_metadata_rejects_non_strict_values(value):
    _, _, _, clock, _ = setup()
    with pytest.raises(ValueError):
        module.RetryAfter(value, clock.now())


@pytest.mark.parametrize(
    "header",
    [
        "",
        "-1",
        "+1",
        "1.5",
        "NaN",
        "Infinity",
        "1e3",
        "９",
        "9" * 400,
        "Wed, 30 Sep 2026 00:00:03 UTC",
        "Wed, 30 Sep 2026 00:00:03 +0000",
        "Wed, 31 Sep 2026 00:00:03 GMT",
        "wed, 30 Sep 2026 00:00:03 GMT",
        "3, 4",
        "Wed, 30 Sep 2026 00:00:03 GMT PRIVATE",
    ],
)
def test_parser_rejects_invalid_without_echo(header):
    _, _, _, clock, _ = setup()
    with pytest.raises(ValueError, match="^invalid retry-after metadata$"):
        module.parse_retry_after(header, clock=clock)


def test_aware_timezone_and_immutable_capture():
    clock = FakeClock(datetime(2026, 9, 30, 9, tzinfo=timezone(timedelta(hours=9))))
    metadata = module.parse_retry_after("Wed, 30 Sep 2026 00:00:03 GMT", clock=clock)
    assert metadata.seconds == 3
    clock.advance(timedelta(seconds=1))
    assert metadata.remaining(clock.now()) == 2
    with pytest.raises(FrozenInstanceError):
        metadata.seconds = 0
    with pytest.raises(ValueError):
        module.RetryAfter(1, datetime(2026, 9, 30))


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_auth_never_retries_and_transients_count_each_physical_attempt(status):
    runtime, call, kwargs, clock, delays = setup(retries=2)
    runtime.policy = runtime.policy.model_copy(update={"retry_delays_seconds": (1, 2)})
    transport = ScriptedTransport(
        [module.http_failure(status, retry_after=module.RetryAfter(0, clock.now()))] * 3
    )
    result = runtime.execute(call, transport=transport, **kwargs)
    expected = 1 if status in (401, 403) else 3
    assert len(transport.timeouts) == runtime.ledger.snapshot()["calls"] == expected
    assert len(result.retrieval_records) == expected
    assert delays == ([] if expected == 1 else [1, 2])
    assert type(result).model_validate_json(result.model_dump_json()).data is None


@pytest.mark.parametrize("seconds", [30, 31, 1e300])
def test_provider_minimum_at_or_beyond_deadline_stops(seconds):
    runtime, call, kwargs, clock, delays = setup(retries=2)
    result = runtime.execute(
        call,
        transport=ScriptedTransport(
            [
                module.http_failure(
                    503, retry_after=module.RetryAfter(seconds, clock.now())
                )
            ]
        ),
        **kwargs,
    )
    assert result.errors[0].error_code == "BUDGET_EXHAUSTED"
    assert delays == [] and runtime.ledger.snapshot()["calls"] == 1


def test_response_handling_time_reduces_captured_minimum():
    runtime, call, kwargs, clock, delays = setup(retries=2)
    metadata = module.parse_retry_after("3", clock=clock)
    clock.advance(timedelta(seconds=1))
    result = runtime.execute(
        call,
        transport=ScriptedTransport(
            [module.http_failure(429, retry_after=metadata), reply()]
        ),
        **kwargs,
    )
    assert result.status == "ok" and delays == [2]


def test_deadline_is_checked_again_after_oversleep():
    runtime, call, kwargs, clock, delays = setup(retries=2)

    def oversleep(seconds):
        delays.append(seconds)
        clock.advance(timedelta(seconds=31))

    runtime.sleep = oversleep
    transport = ScriptedTransport([module.http_failure(429), reply()])
    result = runtime.execute(call, transport=transport, **kwargs)
    assert result.errors[0].error_code == "BUDGET_EXHAUSTED"
    assert len(transport.timeouts) == 1


def test_http_date_leap_second_does_not_shorten_minimum():
    clock = FakeClock(datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC))
    metadata = module.parse_retry_after("Tue, 30 Jun 2026 23:59:60 GMT", clock=clock)
    assert metadata.seconds == 1


def test_wrapped_parser_failure_matches_direct_httpx_terminal():
    runtime, call, kwargs, clock, delays = setup(retries=2)

    class Wrapped:
        retry_owner = "runtime"

        def __call__(self, *, timeout_seconds):
            metadata = module.parse_retry_after("PRIVATE", clock=clock)
            raise module.http_failure(429, retry_after=metadata)

    result = runtime.execute(call, transport=Wrapped(), **kwargs)
    assert result.errors[0].error_code == "TOOL_RESPONSE_INVALID"
    assert delays == [] and runtime.ledger.snapshot()["calls"] == 1


def test_rfc850_uses_injected_clock_across_century():
    clock = FakeClock(datetime(2090, 1, 1, tzinfo=UTC))
    metadata = module.parse_retry_after(
        "Wednesday, 01-Jan-10 00:00:00 GMT", clock=clock
    )
    assert (
        metadata.seconds
        == (datetime(2110, 1, 1, tzinfo=UTC) - clock.now()).total_seconds()
    )


def test_injected_sleep_cannot_skip_provider_minimum():
    runtime, call, kwargs, clock, _ = setup(retries=2)
    runtime.sleep = lambda seconds: None
    transport = ScriptedTransport(
        [
            module.http_failure(429, retry_after=module.RetryAfter(3, clock.now())),
            reply(),
        ]
    )
    result = runtime.execute(call, transport=transport, **kwargs)
    assert result.errors[0].error_code == "TOOL_FAILED"
    assert len(transport.timeouts) == 1


def test_direct_integer_minimum_never_rounds_down():
    _, _, _, clock, _ = setup()
    metadata = module.RetryAfter(9007199254740993, clock.now())
    assert metadata.remaining(clock.now()) >= 9007199254740993


def test_rfc850_century_conversion_keeps_valid_leap_day():
    clock = FakeClock(datetime(2050, 1, 1, tzinfo=UTC))
    metadata = module.parse_retry_after("Tuesday, 29-Feb-00 00:00:00 GMT", clock=clock)
    assert metadata.seconds == 0


def test_large_delta_never_rounds_down_provider_minimum():
    _, _, _, clock, _ = setup()
    assert (
        module.parse_retry_after("9007199254740993", clock=clock).seconds
        >= 9007199254740993
    )
