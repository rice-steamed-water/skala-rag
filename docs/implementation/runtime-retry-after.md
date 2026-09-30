# Shared runtime Retry-After — #122

## API와 소비 경계

- `RetryAfter(seconds: float, captured_at: datetime)`는 frozen metadata다.
  seconds는 strict int/float, finite/nonnegative만 허용한다. bool·문자열·음수·
  NaN/Infinity·float 표현 범위 초과는 거절한다. captured_at은 aware 시각이다.
- `parse_retry_after(value: str | None, *, clock: Clock) -> RetryAfter | None`:
  헤더 부재는 None, delta-seconds/HTTP-date는 주입 clock에서 한 번 포착한 최소
  대기로 정규화한다. 원문 헤더·URL·응답 body는 metadata에 저장하지 않는다.
  잘못된 헤더는 고정 메시지의 `InvalidRetryAfter(ValueError)`를 발생시킨다.
- 기존 `TransportFailure(code, *, usage=None)` 및 `http_failure(status_code,
  *, usage=None)`에 optional `retry_after: RetryAfter | None = None`을 추가했다.
  `AdapterRuntime.execute`와 provider-neutral 결과 계약은 그대로다.
- 직접 `httpx.HTTPStatusError`는 Retry-After 한 필드만 읽는다. retryable HTTP
  오류에서 파싱 실패하면 TOOL_RESPONSE_INVALID로 재시도하지 않는다.
  wrapper에서 parser가 발생시킨 InvalidRetryAfter도 같은 결과로 정규화한다.
  401/403은 헤더와 무관하게 TOOL_AUTH_FAILED, 추가 요청 0이다.

## 표준과 clock 원점

1차 출처: [RFC 9110 §10.2.3](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.3),
[§5.6.7](https://www.rfc-editor.org/rfc/rfc9110.html#section-5.6.7).
공식 rfc-editor.org 텍스트를 GET으로 확인했다. delay-seconds는 ASCII 1*DIGIT;
음수·소수·지수 표기는 허용하지 않는다. 필드 바깥 OWS(SP/HTAB)는 제거한다.
HTTP-date는 case-sensitive IMF-fixdate/RFC850/asctime 세 형식을 받는다.
GMT 및 asctime은 UTC이며 임의 timezone/추가 token은 거절한다.
RFC850 두 자리 연도는 injected UTC clock의 50년 미래 경계로 해석한다.
과거 date는 최소 대기 0이다. 23:59:60은 datetime에 표현 불가하므로 59초로
파싱한 뒤 대기 계산에 1초를 더하여 최소 대기를 줄이지 않는다.
표현 불가능한 delta는 fail closed; 큰 정수의 float 변환은 아래로 반올림하지 않는다.

adapter는 **HTTP 응답 수신 직후**, body 해석/후처리 전에 parser를 호출해야 한다.
그 metadata를 TransportFailure로 전달하면 후처리 동안 흐른 시간이 차감된다.
직접 HTTPStatusError 경로는 runtime의 catch 시각에 포착한다. response handling이
늦었다면 delta 대기는 그만큼 보수적으로 늦어지며 조기 재시도하지 않는다.
HTTP-date는 동일 aware clock의 절대시각과 비교한다. provider Date 헤더나
시도 시작 시각으로 원점을 바꾸지 않는다. caller는 동일 clock/time basis를 공유한다.

## 실행·예산

대기는 `max(explicit policy backoff, provider remaining minimum)`이다.
기본 정책/예산/timeout을 만들거나 기존 policy를 변경하지 않는다. 승인된 M2의
추가 최대 2회, 1/2초 backoff는 caller의 ToolBudget/RuntimePolicy에 명시해야 한다.
수면 직전 현재 시각 기준 잔여 deadline이 delay 이하이면 BUDGET_EXHAUSTED로
종료한다. 수면 뒤에도 다음 시도 시작에서 deadline과 timeout을 다시 검사한다.
주입 sleep이 provider 최소 대기를 건너뛰면 TOOL_FAILED로 추가 요청 없이 종료한다.

각 실제 요청은 기존 ledger.reserve/settle을 정확히 한 번씩 사용한다.
call/전체·tool 요청 한도 snapshot 및 ToolBudget.max_calls는 수면 전 비예약 검사로
불필요한 대기를 피한다. 이 snapshot은 예약이 아니며 경쟁 중 보장이 아니다.
다음 실제 요청 직전 atomic reserve가 요청·token·비용 예산을 모두 재검사한다.
추가 요청을 미리 예약하지 않아 이중 회계하지 않는다. token/비용 부족은 기존
reserve에서 차단되며 별도 non-reserving admission API는 추가하지 않았다.
모든 실패는 기존 typed ToolResult/WorkflowError의 status·retryable 규칙을 따른다.

## 검증과 남은 연결

`tests/unit/test_runtime_retry_after.py`는 fake clock/scripted transport와
httpx의 메모리 Response만 사용한다. 기존 offline network-blocking fixture도 공유한다.
숫자/date/legacy date/leap second/UTC offset/50년 경계/잘못된 헤더/overflow,
최소 대기와 context 시각, deadline·oversleep·undersleep·요청예산,
401/403 무재시도 및 각 physical attempt 회계, 비밀 미노출·terminal 왕복을 검증한다.
기존 `tests/unit/test_adapter_runtime.py`는 수정하지 않고 함께 회귀 검증한다.

#48/PR121의 미병합 custom Tavily bridge는 읽거나 복사하지 않았다.
#122 병합 뒤 #48에서 parser/metadata를 소비하도록 연결하는 후속 작업이 남는다.
실 API/credential/model download/smoke는 수행하지 않았다. 전체 repository gate는
상위 작업 담당자가 수행하며 이 scoped 회귀가 전체 gate 완료를 의미하지 않는다.
