"""승인 OpenAI snapshot의 Responses payload/검증 adapter. 재시도는 하지 않는다.

transport는 #45의 readiness·인증·timeout·예산 경계를 통과하는 호출자로 주입한다.
이 모듈은 credential을 읽거나 자체 HTTP client·재시도 정책을 만들지 않는다.
"""

import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import Clock, LLMError

ModelT = TypeVar("ModelT", bound=BaseModel)
APPROVED_MODEL = "gpt-4.1-mini-2025-04-14"


@dataclass(frozen=True)
class LLMCallRecord:
    model: str
    prompt_version: str
    schema_version: str
    schema_hash: str
    started_at: datetime
    finished_at: datetime
    input_tokens: int | None
    output_tokens: int | None
    status: str
    error_code: str | None


def strict_schema(output_schema: type[BaseModel]) -> dict:
    """Pydantic schema를 strict object schema로 변환하고 지원 밖 shape를 거절한다."""
    schema = copy.deepcopy(output_schema.model_json_schema())
    if schema.get("type") != "object":
        raise ValueError("출력 schema의 root는 object여야 합니다")

    def visit(value):
        if isinstance(value, dict):
            value.pop("default", None)
            if value.get("type") == "object":
                if "properties" not in value or isinstance(
                    value.get("additionalProperties"), dict
                ):
                    raise ValueError(
                        "동적 key map은 strict 출력 schema에서 지원하지 않습니다"
                    )
                value["additionalProperties"] = False
                value["required"] = list(value["properties"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    return schema


def _check_object_shape(value, node, root):
    if "$ref" in node:
        name = node["$ref"].removeprefix("#/$defs/")
        return _check_object_shape(value, root["$defs"][name], root)
    if "anyOf" in node:
        for variant in node["anyOf"]:
            if variant.get("type") == "null" and value is None:
                return
            if variant.get("type") != "null" and value is not None:
                _check_object_shape(value, variant, root)
        return
    if node.get("type") == "object" and isinstance(value, dict):
        properties = node["properties"]
        if set(value) != set(properties):
            raise ValueError("strict object의 누락/추가 key")
        for name, child in value.items():
            _check_object_shape(child, properties[name], root)
    if node.get("type") == "array" and isinstance(value, list):
        for child in value:
            _check_object_shape(child, node["items"], root)


def _reject_nonfinite(value):
    raise ValueError("비표준 JSON 숫자는 허용하지 않습니다")


class OpenAIStructuredLLM:
    """StructuredLLM Protocol. 평가 content만 파싱하며 envelope를 생성하지 않는다."""

    def __init__(
        self,
        *,
        transport: Callable[[dict[str, Any]], httpx.Response],
        model: str,
        prompt_version: str,
        schema_version: str,
        max_output_tokens: int,
        clock: Clock,
    ):
        if model != APPROVED_MODEL:
            raise ValueError("승인된 OpenAI snapshot만 사용할 수 있습니다")
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 2000:
            raise ValueError("출력 token 한도는 승인 범위 1..2000이어야 합니다")
        if not prompt_version.strip() or not schema_version.strip():
            raise ValueError("prompt/schema 버전이 필요합니다")
        self.transport = transport
        self.model = model
        self.prompt_version = prompt_version
        self.schema_version = schema_version
        self.max_output_tokens = max_output_tokens
        self.clock = clock
        self.calls: list[LLMCallRecord] = []

    def generate(
        self, *, system: str, user: str, output_schema: type[ModelT]
    ) -> ModelT:
        try:
            schema = strict_schema(output_schema)
        except ValueError:
            raise LLMError(
                ErrorCode.LLM_OUTPUT_INVALID, "지원하지 않는 출력 schema입니다"
            ) from None
        digest = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
        payload = dict(
            model=self.model,
            store=False,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_output_tokens=self.max_output_tokens,
            text={
                "format": {
                    "type": "json_schema",
                    "name": output_schema.__name__,
                    "strict": True,
                    "schema": schema,
                }
            },
        )
        started = self.clock.now()
        usage = {}
        error = None
        completed = False
        try:
            response = self.transport(payload)
            if response.status_code in (401, 403):
                raise LLMError(ErrorCode.TOOL_AUTH_FAILED, "LLM 인증이 실패했습니다")
            if response.status_code == 429:
                raise LLMError(ErrorCode.TOOL_RATE_LIMITED, "LLM 호출이 제한되었습니다")
            if response.status_code >= 500:
                raise LLMError(
                    ErrorCode.TOOL_UNAVAILABLE, "LLM 서비스를 사용할 수 없습니다"
                )
            if response.status_code != 200:
                raise LLMError(ErrorCode.LLM_FAILED, "LLM 요청이 실패했습니다")
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("응답 object 오류")
            raw_usage = body.get("usage")
            if isinstance(raw_usage, dict):
                usage = {
                    field: value
                    for field, value in raw_usage.items()
                    if type(value) is int and value >= 0
                }
            if body.get("model") != self.model:
                raise ValueError("응답 model 불일치")
            if body.get("status") != "completed":
                raise LLMError(ErrorCode.LLM_FAILED, "LLM 응답이 완료되지 않았습니다")
            texts = []
            for item in body.get("output", []):
                if item.get("type") != "message":
                    continue
                for part in item.get("content", []):
                    if part.get("type") == "refusal":
                        raise LLMError(
                            ErrorCode.LLM_FAILED, "LLM이 요청을 거절했습니다"
                        )
                    if part.get("type") == "output_text":
                        texts.append(part["text"])
            if len(texts) != 1:
                raise ValueError("완전한 JSON content 한 건이 필요합니다")
            parsed = json.loads(texts[0], parse_constant=_reject_nonfinite)
            _check_object_shape(parsed, schema, schema)
            output = output_schema.model_validate_json(texts[0], strict=True)
            completed = True
            return output
        except LLMError as exc:
            error = exc.error_code
            raise
        except httpx.TimeoutException:
            error = ErrorCode.LLM_TIMEOUT
            raise LLMError(error, "LLM 호출 시간이 초과되었습니다") from None
        except httpx.TransportError:
            error = ErrorCode.LLM_FAILED
            raise LLMError(error, "LLM 연결이 실패했습니다") from None
        except (ValueError, TypeError, KeyError, AttributeError, ValidationError):
            error = ErrorCode.LLM_OUTPUT_INVALID
            raise LLMError(error, "LLM 출력이 schema 계약을 위반했습니다") from None
        except Exception:
            error = ErrorCode.LLM_FAILED
            raise LLMError(error, "LLM 호출 경계가 실패했습니다") from None
        finally:
            self.calls.append(
                LLMCallRecord(
                    self.model,
                    self.prompt_version,
                    self.schema_version,
                    digest,
                    started,
                    self.clock.now(),
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                    "success" if completed else "failure",
                    error.value if error is not None else None,
                )
            )
