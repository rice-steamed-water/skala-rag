from datetime import UTC, datetime

import pytest

from skala_rag.contracts.tools import ToolResult
from skala_rag.tools.provider_scope import EXCLUDED_PROVIDERS, invoke_current_scope
from skala_rag.tools.runtime import CallContext


class Clock:
    def now(self):
        return datetime(2026, 9, 30, tzinfo=UTC)


def call(provider, invoke, approved):
    return invoke_current_scope(
        provider=provider,
        approved_providers=frozenset(approved),
        context=CallContext(
            schema_version="1.0",
            call_id="call-1",
            run_id="run-1",
            candidate_id=None,
            tool_name=provider,
            node="discovery",
        ),
        retrieval_id="retrieval-1",
        clock=Clock(),
        invoke=invoke,
    )


@pytest.mark.parametrize("provider", sorted(EXCLUDED_PROVIDERS))
def test_exclusion_precedes_approval_and_never_invokes(provider):
    def forbidden():
        pytest.fail("Excluded adapter/transport/fallback invoked")

    result = call(provider, forbidden, {provider})
    assert result.status == "unavailable"
    assert result.data is None
    assert result.errors[0].retryable is False
    assert result.errors[0].attempt == 0
    record = result.retrieval_records[0]
    assert record.arguments_without_secrets["scope_reason"] == "excluded_current_run"
    assert record.arguments_without_secrets["physical_attempts"] == 0
    assert record.source_ids == record.chunk_ids == record.evidence_ids == []
    assert record.cost is None
    assert record.error_id == result.errors[0].error_id
    ToolResult.model_validate(result.model_dump())


@pytest.mark.parametrize("provider", ["naver", "unknown", "Tavily"])
def test_unapproved_has_no_fallback(provider):
    def forbidden():
        pytest.fail("Unapproved provider invoked")

    assert call(provider, forbidden, {"approved-rag"}).status == "unavailable"


@pytest.mark.parametrize("provider", ["approved-rag", "official-homepage"])
def test_existing_approved_result_is_returned_unchanged(provider):
    original = ToolResult(
        schema_version="1.0",
        status="empty",
        data={"fixture": True},
        retrieval_records=[],
        errors=[],
    )
    calls = []

    def approved():
        calls.append(provider)
        return original

    assert call(provider, approved, {provider}) is original
    assert calls == [provider]


def test_approved_failure_is_not_swallowed_or_replaced():
    def broken():
        raise RuntimeError("synthetic failure")

    with pytest.raises(RuntimeError, match="synthetic failure"):
        call("approved-rag", broken, {"approved-rag"})
