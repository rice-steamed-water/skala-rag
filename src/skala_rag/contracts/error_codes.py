"""WorkflowError.error_code 목록과 재시도 가능 여부.

근거: architecture.md §6 실패 처리 표, delivery T10(0건/401·403/timeout 구별).
재시도 횟수·backoff는 여기서 정하지 않는다. `retryable`은 "같은 입력으로 다시
시도하면 성공할 수 있는가"만 뜻하며, 실제 재시도 여부는 wrapper가 예산으로 정한다.

검색 0건은 오류가 아니다. `ToolResult.status="empty"`로 표현하고 error_code를
쓰지 않는다.
"""

from enum import StrEnum
from typing import Literal, NamedTuple


class ErrorCode(StrEnum):
    # 외부 도구 — ToolResult.errors에 들어간다
    TOOL_AUTH_FAILED = "TOOL_AUTH_FAILED"  # 401/403: 반복 로그인·재시도 금지
    TOOL_NOT_CONFIGURED = "TOOL_NOT_CONFIGURED"  # key·endpoint 미설정
    TOOL_RATE_LIMITED = "TOOL_RATE_LIMITED"  # 429
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"  # 5xx·연결 실패 등 일시 오류
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_RESPONSE_INVALID = "TOOL_RESPONSE_INVALID"  # 응답 schema·요청 범위 위반
    TOOL_FAILED = "TOOL_FAILED"  # 그 밖의 도구 실패
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"  # 호출·시간·비용 한도 소진
    # LLM wrapper
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_OUTPUT_INVALID = "LLM_OUTPUT_INVALID"  # structured output schema 오류
    LLM_FAILED = "LLM_FAILED"
    # controller 검증
    SNAPSHOT_INVALID = "SNAPSHOT_INVALID"  # 해당 후보만 failed → archive
    CONTEXT_INVALID = "CONTEXT_INVALID"  # workflow failed
    UPSTREAM_INVALID = "UPSTREAM_INVALID"  # workflow failed
    # 보고서
    REPORT_REJECTED = "REPORT_REJECTED"  # Semantic Judge fail: 재수정 없이 failed


class ErrorSpec(NamedTuple):
    retryable: bool
    tool_status: Literal["unavailable", "failed"] | None
    """ToolResult에서 쓸 때의 status. None이면 도구 결과에 쓰지 않는 코드."""


ERROR_SPECS: dict[ErrorCode, ErrorSpec] = {
    ErrorCode.TOOL_AUTH_FAILED: ErrorSpec(False, "unavailable"),
    ErrorCode.TOOL_NOT_CONFIGURED: ErrorSpec(False, "unavailable"),
    ErrorCode.TOOL_RATE_LIMITED: ErrorSpec(True, "unavailable"),
    ErrorCode.TOOL_UNAVAILABLE: ErrorSpec(True, "unavailable"),
    ErrorCode.TOOL_TIMEOUT: ErrorSpec(True, "failed"),
    ErrorCode.TOOL_RESPONSE_INVALID: ErrorSpec(False, "failed"),
    ErrorCode.TOOL_FAILED: ErrorSpec(False, "failed"),
    ErrorCode.BUDGET_EXHAUSTED: ErrorSpec(False, "failed"),
    ErrorCode.LLM_TIMEOUT: ErrorSpec(True, None),
    ErrorCode.LLM_OUTPUT_INVALID: ErrorSpec(True, None),
    ErrorCode.LLM_FAILED: ErrorSpec(False, None),
    ErrorCode.SNAPSHOT_INVALID: ErrorSpec(False, None),
    ErrorCode.CONTEXT_INVALID: ErrorSpec(False, None),
    ErrorCode.UPSTREAM_INVALID: ErrorSpec(False, None),
    ErrorCode.REPORT_REJECTED: ErrorSpec(False, None),
}


def is_retryable(code: ErrorCode | str) -> bool:
    """목록에 없는 코드는 ``ValueError``."""
    return ERROR_SPECS[ErrorCode(code)].retryable
