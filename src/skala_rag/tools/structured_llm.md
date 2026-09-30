# OpenAI structured-output adapter — #47

`OpenAIStructuredLLM`은 StructuredLLM Protocol을 만족한다. 승인 snapshot
`gpt-4.1-mini-2025-04-14`만 허용하며 model·prompt_version·schema_version·
max_output_tokens(1..2000)·clock·transport를 명시적으로 주입한다.
credential/.env를 읽지 않으며 자체 HTTP client·timeout·retry loop를 만들지 않는다.

transport는 Responses 요청 payload를 받아 httpx.Response를 반환하는 호출 경계다.
실제 조립에서는 반드시 #45의 readiness·인증·token/cost/time 예산·transport retry를
통과하는 함수를 전달해야 한다. 단순 HTTP client.post를 live 경로에 연결하지 않는다.
현재 #45는 미병합이며 해당 연결과 real structured-output smoke는 미완료다.
이 adapter의 MockTransport 테스트를 실제 provider 접근 성공으로 표시하지 않는다.

[OpenAI 공식 Structured Outputs 문서](https://developers.openai.com/api/docs/guides/structured-outputs)
와 Context7 조회를 기준으로 Responses text.format의 json_schema/strict를 사용한다.
모든 object property를 required로 만들고 additionalProperties=false를 명시한다.
nullable 필드는 nullable로 유지하며 default는 제거한다. root object만 지원하고
동적 key map은 거절한다. 모든 JSON Schema 기능 지원을 주장하지 않으며 실제 provider
schema admission은 opt-in smoke에서 확인해야 한다.

LLM은 wrapper가 지정한 content schema만 생성한다. controller ID·기업·세대·snapshot·
policy envelope를 adapter가 만들지 않는다. rating/criterion/Evidence 품질·항목 완전성은
#22/v3 wrapper의 책임이다. 로컬 strict Pydantic 검증과 필수/추가 object key 검사도
수행하므로 optional default를 가진 필드를 생략한 가상 응답도 거절한다.

- 정상 missing content와 실패를 구분한다. 오류를 0점·missing·빈 성공으로 바꾸지 않는다.
- timeout → LLM_TIMEOUT; 인증 → TOOL_AUTH_FAILED; 429 → TOOL_RATE_LIMITED;
  5xx → TOOL_UNAVAILABLE; refusal/incomplete/연결 오류 → LLM_FAILED;
  malformed JSON/schema/type/model mismatch → LLM_OUTPUT_INVALID.
- adapter는 재시도하지 않는다. schema 보정은 #22 wrapper에서만 요청하며 실제 추가
  호출도 동일 runtime 예산을 소비해야 한다.
- store=false 요청과 exact model을 사용한다. provider가 기록하는 내부 보관 정책의
  전체 준수를 이 옵션만으로 보장한다고 주장하지 않는다.

calls에는 model·prompt/schema version·schema hash·시작/종료 시각·status/error code와
provider usage의 input/output token을 남긴다. usage가 없으면 None이며 0으로 채우지
않는다. 실제 비용 계산·token usage 계상·RunManifest 반영은 runtime/runner 책임이다.
prompt/response 원문·인증값·HTTP headers·provider 예외 원문은 기록하지 않는다.

자동 테스트는 httpx.MockTransport와 FakeClock만 사용한다. 실제 API 호출은 하지
않으며 API key/계정/요금·다음 요청 최대비용·명시적 timeout·공통 runtime 준비 및
승인 smoke 범위 확인 후 후속 opt-in 검증을 연결해야 한다. provider/rubric 선택과
새로운 timeout/backoff 기본값을 대신 확정하지 않는다.
