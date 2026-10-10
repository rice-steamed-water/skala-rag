# Adapter 공통 runtime — #45

`skala_rag.tools.runtime`은 provider 중립의 동기 단일 요청 runtime이다.
`ToolResult`, `ToolBudget`, `Clock`, `RetrievalRecord`, `WorkflowError`는 기존 계약을
그대로 사용한다. 공통 DTO/State/config/의존성 변경 없이 별도 모듈로 제공한다.
실제 Web/LLM 응답 파싱·키 조회·모델 다운로드·Graph/live runner 연결은 포함하지 않는다.

## 중앙 설정과 credentials (#231)

위 #45 공통 runtime 자체는 주입된 객체만 소비한다. 후속 M2/actual/demo composition의
요청 기본값은 [configs/runtime.json](../../configs/runtime.json)과
[`skala_rag.settings`](../../src/skala_rag/settings.py)가 소유한다.
[여덟 profile과 선택 계약](../../configs/README.md#실행-설정-231)을 따른다.
`load_runtime_document(path=...)`는 부분 patch가 아닌 전체 strict/frozen 문서를 읽는다.
기본 source/editable은 정확한 module-relative `configs/runtime.json`, installed는
`skala_rag/_config/runtime.json`을 선택한다. 선택 실패에 fallback은 없다.

기존 주입 identity와 명시 인자가 파일보다 우선한다. multi-profile composition은
기존 guard 뒤에서 한 document를 읽어 dependent constructor에 전달한다.
기존 runtime/ledger/clock/verifier/allowance를 설정으로 교체하지 않으며 import에서
operator config/environment나 provider를 만들지 않는다. 다음 composition의 reload는
가능하지만 기존 불변 snapshot·공유 예산·persisted campaign을 reset하지 않는다.

설정은 requested settings이지 실행 authority가 아니다. 모델 allowlist·정확한 Responses
endpoint·승인된 Decimal rates·상한·실제 승인·receipt·artifact 검증은 독립이다.
M2의 세 stage는 8회 ledger 하나, actual OpenAI/retrieve는 40회 ledger 하나를 공유한다.
component `m2_research.fetch.max_redirects != 0`은 source/LLM 요청 전에 거절하고,
별도 source-only `m2_source`의 redirect 3은 유지한다. usage 미상은 실측 0이 아니다.

| 경로 | key 선택 / 거절 |
| --- | --- |
| `OpenAIResponsesAttempt`, actual runner | 명시 `api_key=`만 소비; 환경·dotenv 조회 없음. attempt는 키가 `None`일 때 정확한 `type(http_transport) is httpx.MockTransport`만 허용; blank 명시 키는 거절 |
| M2 component·evidence validation | `resolve_environment_credential("OPENAI_API_KEY")`: 환경만 읽고 strip; openai 실행에 missing/blank 키 거절 |
| M2 source/component OpenDART | 같은 resolver로 `OPENDART_API_KEY`를 strip; optional 미설정 상태는 승인과 별개 |
| local demo | `resolve_demo_credential(root)`: truthy env 값 우선, missing/empty env는 정확한 `root/.env` fallback, whitespace env는 우선 후 blank 검사에서 거절 |

resolver는 환경을 수정하거나 dotenv를 다른 디렉터리에서 찾지 않는다.
credentials는 serializable settings/repr·State·report 밖에 두며 readiness에는
presence boolean만 전달한다. 실제 `.env`는 커밋하지 않는다.
prompt는 singular `prompt/`의 process-loaded 상수·명시 builder에서 소비하고 plural
`prompts/`는 호환 export만 제공한다. 버전/tag·strict schema·수정 순서·반환 shape를
바꾸지 않으며 JSON text의 sorted byte commitments도 strict actual replay에 포함한다.

## 연결 API

| 이름 | 공급하거나 소비할 값 |
| --- | --- |
| `Readiness` | required/optional, configuration·credential·model·index 필요/가용 boolean. 비밀 문자열을 받지 않음 |
| `RuntimePolicy` | 명시 execution_mode, retry_delays_seconds, live/timing 승인 근거. 숫자 기본값 없음 |
| `RuntimeLimits` / `BudgetLedger` | 전체/도구별 요청·토큰·비용 한도. 모든 관련 adapter가 같은 ledger 사용 |
| `Allowance` | 단일 실제 요청의 입력/출력 token 상한·검증된 최대 USD 비용. 오류 응답도 포함 |
| `Usage` | 실제 관측 token/cost 또는 None. 비용은 기존 exact Decimal 문자열 타입 재사용 |
| `CallContext` | 비밀·질의·원문이 없는 call/run/candidate/tool/node 식별자 |
| `SingleAttempt` / `AttemptResponse` | 한 번의 요청·ok/empty payload·근거 ID·실제 사용량 |
| `TransportFailure` / `http_failure` | raw message 없이 기존 도구 오류 코드로 실패 전달 |
| `AdapterRuntime.execute` | 위 입력과 기존 ToolBudget으로 terminal ToolResult 반환 |
| `RuntimeStructuredLLM` | 기존 StructuredLLM.generate 경계를 단일 요청 LLM transport와 연결 |

읽을 수 있는 관측은 `ledger.snapshot()`, `runtime.readiness_history`,
`runtime.error_history`, terminal 결과의 `retrieval_records`다. readiness는 **환경 가용성**
기록이며 live 실행 승인과 다르다. 키/모델/index를 실제로 확인하는 adapter가 boolean을
공급해야 한다. 이 runtime은 dotenv·secret store를 읽거나 모델을 로드하지 않는다.
실제 사용 여부와 요청 결과는 기록을 확인한다. `required=false` 도구도 미준비면
unavailable로 반환하되 전체 workflow를 차단할지는 runner가 정한다.

## 요청과 예산 계약

`execute(call, budget=..., readiness=..., allowance=..., transport=...)`의
transport는 `retry_owner="runtime"`을 선언하고 `__call__(timeout_seconds=...)`에서
**한 번만** 요청해야 한다. provider SDK/HTTP transport의 자체 retry를 꺼야 하며
redirect·schema correction·재호출을 숨겨 수행하지 않는다. runtime 밖에서 직접
provider를 호출하면 이 예산 보장을 우회하므로 후속 adapter는 모든 요청을 이 경계에 넣는다.

실제 요청 직전에 공유 ledger와 이번 논리 호출의 `ToolBudget.max_calls`를 확인한다.
최초 요청을 포함해 각 시도를 차감하고 실패/empty도 요청 횟수를 돌려주지 않는다.
retry delay 개수는 `ToolBudget.max_retries`와 정확히 같아야 한다. 각 delay는 호출자가
명시하며 delay 뒤에도 예산/deadline을 재검사한다. 도구별 한도 누락은 무제한으로
해석하지 않는다. ledger admission/settlement는 lock으로 보호하나 병렬 live 요청을
승인하는 것은 아니다. #43 pilot의 동시성 1은 runner가 지킨다.

토큰·최대 비용을 먼저 예약하고 실제 usage가 알려졌을 때만 미사용 예약분을 정산한다.
usage 미상은 예약을 유지하며 `RetrievalRecord.cost=None`과 unknown_cost_requests를 남긴다.
미확인 요금을 0으로 계산하지 않는다. 비용 gate가 있을 때 최대 비용이 미상이면
호출을 거절한다. usage가 예약 상한을 넘으면 응답을 거절하고 ledger를 invalid로
표시해 후속 호출을 막는다. 이미 소비한 초과량은 record에 관측으로 남으며 상한 준수로
표시하지 않는다. provider의 정확한 token 계수·최대 비용 산정은 #47/#48의 책임이다.
`BudgetLedger.reserve/settle`은 runtime 내부용이며 직접 이중 정산하지 않는다.

비용 비교·누적은 Decimal의 호출자 precision에 의해 반올림되지 않게 처리한다.
`cost_usd_accounted`는 실측 비용 합계가 아니라 **관측+미상 예약을 포함한 예산 회계**다.
최대 비용조차 없는 fixture 관측의 비용 미상은 이 값도 None이다.
기존 MonetaryObservation의 Number 필드는 표현용 숫자로 유지하고 원래 Decimal은
record.arguments_without_secrets.cost_usd_exact 문자열에 보존한다. 예산 계산은
표현용 float를 소비하지 않는다. 기존 Number로 유한/비영 값을 표현할 수 없으면
cost는 None으로 남기고 exact 문자열을 보존한다. 아주 작은 비용을 0으로 표시하지 않는다.

overall campaign 누적·run 종료/Warning·M3 max_cost controller는 runner/#96 범위다.
각 run에서 ledger를 새로 만들고 같은 run의 도구/LLM 구조 수정에 공유한다.
allowance/limits를 #43의 승인값으로 구성하는 loader나 기본 정책은 이번 PR에 없다.

## Timeout·오류·로그

시도 timeout은 `ToolBudget.timeout_seconds`와 남은 deadline 중 작은 값이다.
transport는 이 값을 실제 I/O에 적용하고 **전체 시도**가 끝날 때까지의 시간도
제한해야 한다. runtime은 반환된 늦은 응답을 TOOL_TIMEOUT으로 거절하지만 임의의
동기 callback을 강제 중단하지는 않는다. 무시된 timeout에서 작업 thread를 남긴 채
중복 재시도하지 않는다. 후속 adapter는 단일 시도 종료/취소를 보장해야 한다.

[HTTPX timeout](https://www.python-httpx.org/advanced/timeouts/)은 connect/read/write/pool
각 구간 제한이다. 요청 전체 시간 제한과 같다고 주장하지 않으며, streaming/redirect가
필요한 #46 adapter는 전체 시도 deadline도 확인한다.
[HTTPX 예외](https://www.python-httpx.org/exceptions/)는 raw URL/message를 복사하지 않고
정규화한다. [SDK/transport retry](https://www.python-httpx.org/advanced/transports/) 설정도
runtime과 중첩되지 않게 한다.

| 상황 | terminal 구분 / 재시도 |
| --- | --- |
| 결과 0건 | empty + 명시 빈 data, 오류 없음 |
| 키/config/model/index 미준비 | unavailable / TOOL_NOT_CONFIGURED, 실제 요청 0회 |
| HTTP 401/403 | unavailable / TOOL_AUTH_FAILED, 재시도 없음 |
| HTTP 429 | unavailable / TOOL_RATE_LIMITED, 승인된 retry schedule/예산 내 |
| HTTP 5xx·일시적 네트워크 오류 | unavailable / TOOL_UNAVAILABLE, bounded retry |
| timeout / 늦은 응답 | failed / TOOL_TIMEOUT, bounded retry |
| 영구 요청 오류·잘못된 응답 | failed / TOOL_FAILED 또는 TOOL_RESPONSE_INVALID, 재시도 없음 |
| 예산/deadline 부족·미확인 최대 비용 | failed / BUDGET_EXHAUSTED, 새 요청 없음 |

실제 request마다 RetrievalRecord를 남긴다. preflight 거절은 요청 기록을 꾸미지 않고
attempt=0 오류/준비 상태를 남긴다. retry 중 실패→성공이어도 과거 record/error ID를
보존한다. ToolResult의 성공은 errors=[]를 요구하므로 과거 오류 payload는
`runtime.error_history`에서 소비자가 State.errors로 함께 인계해야 한다.
terminal 실패의 errors는 해당 status와 맞는 최종 오류만 포함한다.
임의 tool/run/candidate ID는 신뢰된 controller가 공급한다. 질의/URL/headers/body,
prompt/system/user·provider 예외 메시지는 기록하지 않는다.

## LLM wrapper 인계

`RuntimeStructuredLLM`은 `StructuredAttempt.generate_once`에 system/user/schema 및
timeout/input_token_limit/output_token_limit를 넘긴다. `allowance_for(system,user,schema)`는
각 구조 수정 prompt에도 다시 실행된다. LLM adapter는 token 한도와 단일 요청 규칙을
실제로 적용해야 한다. schema 검증 실패는 TOOL_RESPONSE_INVALID로 종료하고
LLM_OUTPUT_INVALID를 기존 #22 wrapper에 전달한다. runtime 자체는 prompt 수정이나
schema correction을 재시도하지 않는다. #22의 수정 generate도 같은 ledger를 사용한다.
소진된 timeout은 LLM_TIMEOUT, 기타 기술 실패는 LLM_FAILED로 인계해 #22의 구조
수정 loop에 들어가지 않는다. bridge의 `retrieval_records`와 runtime.error_history를
runner가 인계한다. 실제 provider output 파싱과 요금/tokenizer 선택은 #47 범위다.

## 승인과 검증 범위

[#43 A/B 승인·C 보류](https://github.com/rice-steamed-water/skala-rag/issues/43#issuecomment-5903724740)는
pilot 경로와 예산을 허용했지만 추가 backoff/LLM timeout을 확정하지 않았다.
`execution_mode=live`에는 live_approval_reference와 timing_approval_reference,
deadline 및 token/cost bound를 명시해야 한다. 승인 근거 문자열은 caller가 승인된
설정을 인계하는 인터페이스이며 이 코드가 실제 승인 여부를 GitHub에서 확인하지 않는다.
허위 문자열을 넣어 live 승인을 대체하지 않는다. 현재 timing 승인이 미확정이면
해당 live 호출은 거절하고 fixture 설정만 사용한다.

tests/unit/test_adapter_runtime.py는 socket 연결을 차단하고 FakeClock·scripted transport·
HTTPX MockTransport 및 기존 평가 wrapper fixture로 검증한다. live라는 설정 문자열을
쓰는 부정/가상 gate 테스트도 실제 live 사용 증거가 아니다. 외부 API·유료 LLM·실제
모델/tokenizer 다운로드·index·benchmark는 실행하지 않았다. #43 C 보류도 유지한다.

이번 브랜치 실제 검증: `uv run ruff check .` 통과,
`uv run ruff format --check .` 140 files already formatted,
`uv run pytest` 1577 passed in 1.65s (신규 runtime 테스트 69개 포함).
