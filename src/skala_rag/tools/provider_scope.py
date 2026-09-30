"""Explicit caller-injected current-run boundary; not automatic Graph wiring.

Wrap the entire adapter invocation, not only its transport. No fallback, config
loading, credentials, network client, budget mutation or evidence creation.
"""

from collections.abc import Callable
from typing import TypeVar

from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.interfaces import Clock
from skala_rag.contracts.retrieval import RetrievalRecord
from skala_rag.contracts.tools import ToolResult
from skala_rag.tools.runtime import CallContext

CURRENT_SCOPE_VERSION = "issue-158-current-run-v1"
EXCLUDED_PROVIDERS = frozenset({"kipris", "krx", "중기부", "tavily"})
DataT = TypeVar("DataT")


def invoke_current_scope(
    *,
    provider: str,
    approved_providers: frozenset[str],
    context: CallContext,
    retrieval_id: str,
    clock: Clock,
    invoke: Callable[[], ToolResult[DataT]],
) -> ToolResult[DataT]:
    """Canonical provider IDs required; unknown/non-approved IDs fail closed.

    Caller approval is necessary but cannot override this run's exclusions.
    Pass a lazy callback: constructing/sending requests before this guard defeats
    its boundary. Approved callback results and exceptions propagate unchanged.
    """
    if provider in approved_providers and provider not in EXCLUDED_PROVIDERS:
        return invoke()
    now = clock.now()
    reason = (
        "excluded_current_run" if provider in EXCLUDED_PROVIDERS else "not_approved"
    )
    error_id = f"scope-error-{retrieval_id}"
    return ToolResult(
        schema_version=context.schema_version,
        status="unavailable",
        data=None,
        retrieval_records=[
            RetrievalRecord(
                schema_version=context.schema_version,
                retrieval_id=retrieval_id,
                run_id=context.run_id,
                candidate_id=context.candidate_id,
                tool_name=context.tool_name,
                query=None,
                arguments_without_secrets={
                    "scope_version": CURRENT_SCOPE_VERSION,
                    "scope_reason": reason,
                    "physical_attempts": 0,
                },
                started_at=now,
                finished_at=now,
                status="unavailable",
                source_ids=[],
                chunk_ids=[],
                evidence_ids=[],
                error_id=error_id,
                cost=None,
                cache_hit=False,
            )
        ],
        errors=[
            WorkflowError(
                schema_version=context.schema_version,
                error_id=error_id,
                run_id=context.run_id,
                candidate_id=context.candidate_id,
                node=context.node,
                error_code="TOOL_NOT_CONFIGURED",
                message_redacted=f"Current-run provider scope: {reason}",
                retryable=False,
                attempt=0,
                timestamp=now,
            )
        ],
    )
