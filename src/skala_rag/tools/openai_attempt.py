"""#47 OpenAI adapter를 #45 runtime의 ``StructuredAttempt``로 잇는 브리지 — #51.

``RuntimeStructuredLLM``(#45)이 readiness·예산·timeout·재시도를 맡고, 이 모듈은
물리 요청 한 번만 한다. payload 구성·응답 검증은 ``OpenAIStructuredLLM``(#47)을 그대로
쓴다. key는 Authorization header에만 두고 기록·예외 메시지에 넣지 않는다.

``byte_bound_allowance``는 tokenizer 없이 입력 token 상한을 UTF-8 byte 수로 잡는다
(token 하나는 1 byte 이상). 단가는 호출자가 확인한 공식 요금을 넘긴다(기본값 없음).
실제 청구 비용은 알 수 없으므로 ``Usage.cost_usd``는 None이다(0으로 채우지 않음).
"""

import json
from decimal import Decimal

import httpx
from pydantic import BaseModel

from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import Clock, LLMError
from skala_rag.tools.runtime import Allowance, AttemptResponse, TransportFailure, Usage
from skala_rag.tools.structured_llm import (
    APPROVED_MODEL,
    OpenAIStructuredLLM,
    strict_schema,
)

RESPONSES_URL = "https://api.openai.com/v1/responses"
# 메시지 틀·schema 이름 등 payload 밖에서 붙는 token 여유분.
FRAMING_TOKENS = 64

_TRANSPORT_CODE = {
    ErrorCode.LLM_TIMEOUT: ErrorCode.TOOL_TIMEOUT,
    ErrorCode.LLM_OUTPUT_INVALID: ErrorCode.TOOL_RESPONSE_INVALID,
    ErrorCode.LLM_FAILED: ErrorCode.TOOL_FAILED,
}


def byte_bound_allowance(
    system: str,
    user: str,
    output_schema: type[BaseModel],
    *,
    schema_version: str,
    max_output_tokens: int,
    usd_per_input_token: Decimal,
    usd_per_output_token: Decimal,
) -> Allowance:
    """요청 1회의 token·비용 상한. 입력은 system+user+schema의 UTF-8 byte 수."""
    schema = json.dumps(strict_schema(output_schema), ensure_ascii=False)
    input_tokens = len((system + user + schema).encode()) + FRAMING_TOKENS
    cost = (
        Decimal(input_tokens) * usd_per_input_token
        + Decimal(max_output_tokens) * usd_per_output_token
    )
    return Allowance(
        schema_version=schema_version,
        input_tokens=input_tokens,
        output_tokens=max_output_tokens,
        max_cost_usd=cost,
    )


class OpenAIResponsesAttempt:
    """``StructuredAttempt``. 호출마다 새 client로 한 번만 요청한다(재시도 없음)."""

    retry_owner = "runtime"

    def __init__(
        self,
        *,
        api_key: str,
        prompt_version: str,
        schema_version: str,
        clock: Clock,
        http_transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key is required")
        self._key = api_key.strip()
        self._prompt_version = prompt_version
        self._schema_version = schema_version
        self._clock = clock
        self._http_transport = http_transport
        self.llm_calls = []

    def generate_once(
        self,
        *,
        system: str,
        user: str,
        output_schema: type[BaseModel],
        timeout_seconds: float,
        input_token_limit: int,
        output_token_limit: int,
    ) -> AttemptResponse:
        with httpx.Client(
            transport=self._http_transport,
            timeout=timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        ) as client:

            def post(payload: dict) -> httpx.Response:
                return client.post(
                    RESPONSES_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._key}"},
                )

            llm = OpenAIStructuredLLM(
                transport=post,
                model=APPROVED_MODEL,
                prompt_version=self._prompt_version,
                schema_version=self._schema_version,
                max_output_tokens=output_token_limit,
                clock=self._clock,
            )
            try:
                data = llm.generate(
                    system=system, user=user, output_schema=output_schema
                )
            except LLMError as exc:
                code = _TRANSPORT_CODE.get(exc.error_code, exc.error_code)
                raise TransportFailure(code, usage=self._usage(llm)) from None
            finally:
                self.llm_calls.extend(llm.calls)
        return AttemptResponse(
            schema_version=self._schema_version,
            status="ok",
            data=data,
            source_ids=[],
            chunk_ids=[],
            evidence_ids=[],
            usage=self._usage(llm),
        )

    def _usage(self, llm: OpenAIStructuredLLM) -> Usage | None:
        if not llm.calls:
            return None
        call = llm.calls[-1]
        return Usage(
            schema_version=self._schema_version,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            cost_usd=None,
        )
