"""Versioned Tavily single-attempt bridge; runtime alone owns retries/ledger.

Caller supplies a synchronous HTTP client with redirects and transport retries
 disabled. This module never loads credentials or creates a network client.
"""

from dataclasses import dataclass, field
from typing import Literal

import httpx

from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import Clock
from skala_rag.contracts.tools import ToolBudget, ToolResult
from skala_rag.tools.discovery_live import ProviderResponse
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    AttemptResponse,
    CallContext,
    Readiness,
    TransportFailure,
    Usage,
    http_failure,
    parse_retry_after,
)


@dataclass
class TavilySingleAttempt:
    client: httpx.Client = field(repr=False)
    api_key: str = field(repr=False)
    payload: dict = field(repr=False)
    schema_version: str
    clock: Clock = field(repr=False)
    retry_owner: Literal["runtime"] = field(default="runtime", init=False)

    def __call__(self, *, timeout_seconds: float) -> AttemptResponse[ProviderResponse]:
        response = self.client.post(
            "https://api.tavily.com/search",
            json=self.payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=timeout_seconds,
            follow_redirects=False,
        )
        if response.status_code >= 400:
            # Authentication and provider budget errors never consume retry metadata.
            if response.status_code in (401, 403):
                raise http_failure(response.status_code)
            if response.status_code in (432, 433):
                raise TransportFailure(ErrorCode.BUDGET_EXHAUSTED)
            failure = http_failure(response.status_code)
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                # Capture at response arrival, before body parsing or postprocessing.
                failure = http_failure(
                    response.status_code,
                    retry_after=parse_retry_after(
                        response.headers.get("Retry-After"), clock=self.clock
                    ),
                )
            raise failure
        if response.status_code != 200:
            raise TransportFailure(ErrorCode.TOOL_RESPONSE_INVALID)
        try:
            body = response.json()
        except ValueError:
            raise TransportFailure(ErrorCode.TOOL_RESPONSE_INVALID) from None
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise TransportFailure(ErrorCode.TOOL_RESPONSE_INVALID)
        return AttemptResponse(
            schema_version=self.schema_version,
            status="ok",
            data=ProviderResponse(200, body),
            source_ids=[],
            chunk_ids=[],
            evidence_ids=[],
            usage=Usage(
                schema_version=self.schema_version,
                input_tokens=None,
                output_tokens=None,
                cost_usd=None,
            ),
        )


@dataclass
class TavilyRuntimeBridge:
    runtime: AdapterRuntime
    client: httpx.Client = field(repr=False)
    api_key: str = field(repr=False)
    context: CallContext
    readiness: Readiness
    allowance: Allowance
    bridge_version: Literal["tavily-runtime-v1"] = field(
        default="tavily-runtime-v1", init=False
    )

    def __call__(
        self, payload: dict, budget: ToolBudget
    ) -> ToolResult[ProviderResponse]:
        return self.runtime.execute(
            self.context,
            budget=budget,
            readiness=self.readiness,
            allowance=self.allowance,
            transport=TavilySingleAttempt(
                self.client,
                self.api_key,
                payload,
                self.context.schema_version,
                clock=self.runtime.clock,
            ),
        )
