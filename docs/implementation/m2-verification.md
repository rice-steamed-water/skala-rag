# M2 검증 현황 — #62

## 구현과 검증 범위

`agents.m2_research.run_research_to_trace`는 Company Research 도구를 실행하고
반환된 profile/Source/Evidence/요청 이력을 `InvestmentState`로 조립한다.
실제 관측의 provenance를 성공 요청에 연결한 뒤 기존 `check_eligibility`를 실행한다.
사용자가 적격 State JSON을 따로 만들 필요는 없다.

- 새 `outputs/` 하위 디렉터리에 `research-state.json`과 `research-receipt.json`을
  저장한다. 디렉터리는 0700, 파일은 0600이며 원문 발췌를 포함하는 State는 커밋하지 않는다.
- 다른 후보/미래 자료/잘못된 요청 이력, 경로 이탈·덮어쓰기·설정된 비밀 값 포함을 거절한다.
- `eligible`일 때만 주입한 Technology 단계로 진입한다. `unknown`/`ineligible` 및
  required provider 실패도 저장하고 평가를 호출하지 않는다.
- 평가가 실패해도 저장한 조사 State는 유지한다. 연구 receipt의 `ready`는 평가 진입
  조건을 뜻하며 평가 성공을 뜻하지 않는다. Technology 결과는 기존 trace 함수가 검증한다.

새 `tests/integration/test_m2_research_state.py`의 13개 synthetic 통합 테스트는
적격성 세 상태의 분기, 저장 후 freeze, 실제 요청 이력 연결, 비밀 값·덮어쓰기·
미래/타 기업 자료 거절, 429 provider 실패와 평가 실패 시 조사 State 보존을 검증한다.
해당 테스트는 socket 연결을 차단한다.

`agents.m2_trace.run_technology_trace`는 호출자가 제공한 적격성 조사 State와
검색 결과를 사용해 RAG segment → LLM Evidence 추출 → 이력 연결 → snapshot 동결 →
Technology 평가를 연결한다. 적격성을 만들어 넣거나 전체 M2 완료를 선언하지 않는다.

- 유료 요청 전에 기존 admission/provenance, 검색 run/candidate, 중복 retrieval ID,
  반환 Chunk 선택 및 기존 Source/Chunk 대체 여부를 검사한다.
- live 모드는 준비된 RuntimeStructuredLLM/OpenAIResponsesAttempt와 공유 요청 ledger를
  요구한다. fixture LLM을 live로 재표기할 수 없다. 모델·요금·timeout 기본값은 없다.
- 추출 Evidence가 실제 Technology 인용에 사용됐는지 확인하고 criterion/evidence/
  retrieval/chunk/source/page/snapshot 참조 폐쇄성과 snapshot hash를 검증한다.
- 입력 State는 복제하여 처리하며 실패 시 성공 receipt를 반환하지 않는다.
  receipt에는 원문·prompt·credential을 넣지 않는다. `whole_m2_verified=False`다.

`tests/integration/test_m2_trace_boundaries.py`는 네트워크를 차단한 synthetic fixture로
이 경로와 기존 평가 wrapper를 검증한다. 13개 테스트는 정상 연결, snapshot 불변성,
적격성 부재, 중복 이력, 원문 대체, 조작 Chunk/인용, 다른 기업·미래 자료,
추출 prompt injection 및 가상 환경변수 비전달을 포함한다.
실제 적격성 판정, provider 응답, v3 전체 평가 성공의 증거가 아니다.

```bash
uv run pytest tests/integration/test_m2_trace_boundaries.py -q
```

## 실제 증거와 남은 조건

### 키 후속 제공을 위한 component CLI

`agents.m2_component_live`는 실제 조사 도구·적격성 추출기·로컬 RAG·Evidence 추출기·
Technology evaluator를 한 실행으로 조립한다. `--live`가 없으면 네트워크/API 요청과
모델 로딩 없이 승인 corpus, 모델 파일 hash, SQLite metadata, 정책/rubric을 검사한다.
키가 환경에 있어도 기본 실행은 호출하지 않는다. 키·승인 rubric 미준비는 첫 외부 요청
전에 거절한다. 입력 예시는 [m2-component-input.json](../examples/m2-component-input.json)이다.

```bash
uv run python -m skala_rag.agents.m2_component_live \
  --root /Users/xxhigh/workspace/ai-service/skala-rag \
  --input docs/examples/m2-component-input.json \
  --output-dir /Users/xxhigh/workspace/ai-service/skala-rag/outputs/issue62-component-v1
```

키를 제공한 후 명시적으로 `--live`를 추가하면 실행한다. 날짜·run_id·새 출력 디렉터리와
이전 campaign 비용 상한 회계를 실행에 맞게 설정한다. 예시의 `0.00`은 #62의 현재
LLM 요청 0회를 전제로 한 입력 예시이며 계정 잔액·실제 청구 비용·다른 작업 비용을
관측했다는 뜻이 아니다. 반복 실행 전에 이전 요청의 보수적 비용 회계를 반영해야 한다.
이미 있는 출력 디렉터리는 덮어쓰지 않는다. 추천 정책은 여전히 draft이며 component
smoke 결과를 투자 추천·v3 전체 M2 성공으로 표시하지 않는다.

- 적격성·Evidence·Technology LLM은 `M2LLMs`의 공유 ledger를 사용한다. 최대 8회,
  요청당 입력 8,000/출력 2,000 token, 전체 64,000/16,000 token, USD 1.00이다.
  timeout 30초·transport 재시도 0회, 구조 보정 1회도 같은 한도를 소비한다.
- 공개 provider 요청은 최대 3회이고 redirect를 끈다. LLM과 합쳐 최대 11 외부 요청으로
  승인된 총 20회/단일 동시성 안에 있으며, 모두 같은 10분 deadline을 쓴다.
- BGE-M3는 기존 파일만 로딩하며 다운로드·새 index 쓰기는 하지 않는다. 후보가
  eligible로 판정되기 전에는 모델을 로딩하거나 RAG/Evidence/Technology를 호출하지 않는다.
- 검색 Top-5의 반환 순서대로 요청당 입력 상한을 만족하는 첫 Chunk 1개를 선택한다.
  원문을 자르거나 바꾸지 않는다. 반환 Chunk가 전부 한도를 넘으면 호출 전에 실패한다.
  이는 component trace용 선택이며 검색 품질 benchmark가 아니다.
- 실제 요청/사용량·모델/prompt/schema version과 에러 코드는 `component-receipt.json`에
  저장한다. 실패 시에도 조사 State와 사용량을 보존한다. 필수 LLM 인증 실패는
  `required_llm_failed`이며 정상 missing·성공으로 바꾸지 않는다.
- 검증된 Technology State/trace는 `technology/` 아래 별도로 저장한다. live CLI에서
  성공 trace가 없으면 종료 코드 2다. receipt의 `whole_m2_verified`는 계속 false다.
- 공식 [GPT-4.1 mini 문서](https://developers.openai.com/api/docs/models/gpt-4.1-mini)를
  2026-09-30 열어 확인한 공개 요금(입력 USD 0.40/출력 USD 1.60 per 1M token)을
  보수적 요청 상한에 사용한다. actual_cost는 None이며 계정 credit 검증을 주장하지 않는다.
  추후 실행 시 공개 가격 변경 여부를 확인해야 한다.

신규 runtime/runner 16개 테스트는 실제 API 대신 `httpx.MockTransport`와 가상
index/provider 관측을 사용하고 socket을 차단한다. 세 LLM 공유 한도, 인증/429/5xx/
timeout 재시도 0회, 입력 한도 초과, 키/rubric 미준비, unknown admission, 평가 실패의
State·이력 보존과 전체 정상 연결을 검증한다. 실제 성공 trace 증거는 아니다.

### 실제 로컬 사전 점검

API 키를 프로세스에서 제거한 상태로 위 CLI의 `run(..., live=False)`를 실행했다.
corpus=`issue52-reviewed-text-v1`, BGE-M3 revision=`5617a9f61b028005a4858fdac845db406aefb181`,
index=`sha256:bc345db2d4ed9c6c4edb46e02ccc3021408ae8aabe1431b376af1fa579ba53c7`의
실제 파일/metadata 점검을 통과했다. 모델 로딩·원문 웹 조회·OpenAI 호출은 0회다.
`execution_started=False`, `credential_present=False`, missing readiness는 키 1개다.
이는 설정·파일 사전 점검이며 기업 적격성·LLM 추출·Technology 평가 실측이 아니다.

core 승인 반영 PR #134는 아직 미병합이므로 main의 `proposed`를 변경하지 않았다.
승인 기록 [#59 comment5904859865](https://github.com/rice-steamed-water/skala-rag/issues/59#issuecomment-5904859865)에
연결된 PR 고정 head `c22df2ccc22faf8d3e42690e628c3c1fc5633aca`의 approved rubric을
변경 없이 로컬 `data/local/issue62/core-approved-c22df2c.yaml`에 저장해 명시적으로 사용했다.
SHA-256=`7d7f64277849659b88d94088be7b94a3ee8efd017bf856719b235b92a35de176`.
파일은 Git 제외이며 사전 점검 receipt에 실제 rubric/policy hash를 기록한다.

| 경로 | 상태 및 필요한 증거 |
| --- | --- |
| corpus/index/retrieval | #52/#145/#54 완료. PR #137의 실제 로컬 BGE-M3 검색 검증은 재사용 가능하다. 이번 fixture 실행은 해당 실측의 재실행이 아니다 |
| Evidence → snapshot → Technology | 연결 함수와 offline 경계 검증 완료. 실제 적격성 Evidence가 포함된 Company Research State 및 실제 LLM 성공 trace는 아직 없다 |
| Discovery / Company Research (#48/#51) | required provider별 실사용 smoke와 기업·출처 ID가 필요하다. 일부 구현/이슈 종료만으로 전체 live 성공을 선언하지 않는다 |
| 다섯 평가 branch (#57–#61) | 실제 smoke 및 원자적 여섯 차원 결과가 필요하다. Technology component 성공만으로 대체하지 않는다 |
| runtime | required/optional readiness와 공유 예산·사용량 기록을 연결했다. 키 후속 제공 후 실제 실행 증거가 필요하며 계정 잔액·실제 비용 미검증 상태를 그대로 기록한다 |

OpenAI credential의 존재만 확인했으며 값은 기록하지 않았다. 실제 LLM 요청은 실행하지
않았다. 사용자 후속 승인으로 [승인 요청안 §3](m2-live-approval-proposal.md)의
#62 LLM timeout은 30초, 추가 transport 재시도는 0회로 확정되었다.
사용자가 OpenAI API key만 있으며 요금·잔여 credit 확인 정보는 없다고 응답했다.
앞선 실행에서는 기존 승인 조건에 따라 과금 가능한 새 호출을 하지 않았다.
이후 사용자가 키 후속 제공과 개발 지속을 지시하여 위 component 경로를 준비했다.
State 생성·저장 연결은
구현됐으므로 더 이상 사용자에게 State JSON 작성을 요청하지 않는다.

## #62 실제 공개자료 조회 기록

승인 corpus와 같은 후보 `co-physical-intelligence`를 수동 선택해 source-only opt-in
CLI를 실행했다. Discovery 성공을 주장하지 않는다.

```bash
uv run python -m skala_rag.agents.m2_research_live \
  --root /Users/xxhigh/workspace/ai-service/skala-rag \
  --input /private/tmp/issue62-research-input.json \
  --output-dir /Users/xxhigh/workspace/ai-service/skala-rag/outputs/issue62-company-research-v1 \
  --live
```

- run: `issue62-research-20260930T084103Z` (2026-09-30 08:41:03 UTC).
- `https://www.pi.website/` 공식 사이트 요청 1회: HTTP 429 / `TOOL_RATE_LIMITED`.
  추가 재시도 0회. required provider 실패로 research=`unavailable`, eligibility 미판정이다.
- Source/Evidence는 0건이고 Technology 요청은 없다. OpenAI 요청 0회, 비용 실측 없음.
  source-only CLI 자체는 LLM 사실 추출을 구성하지 않는다. optional OpenDART는 이
  required 실패 뒤 실행되지 않았으며 계정 key도 없다. US 후보의 법인 식별 근거도 필요하다.
- 전체 State와 receipt는 위 로컬 outputs에 저장했다. source-only CLI는 `--live`가
  없으면 실행을 거절한다. input에는 candidate/run_input/run_id만 넣고 credential은 넣지 않는다.

이 기록은 실제 접근 실패 증거이며 실제 적격 후보·LLM 추출·평가 성공 증거가 아니다.

실제 실행 기록에는 run/retrieval/chunk/source/page/evidence/snapshot/evaluation ID와
model/prompt/policy/corpus/index version, 공유 예산 사용량을 남긴다.
필수 live 경로의 skip/실패를 성공 또는 missing으로 바꾸지 않는다.

#62의 오래된 200페이지 요구는 #91의 적용 제외와 승인 manifest gate(#44)를 따른다.
임베딩은 BGE-M3 직접 선정 및 #56 Not planned 종료 기록을 따른다.
상충 재무 단위·private URL·v3 전체 snapshot/평가의 통합 부정 시나리오는 여전히
추가 검증이 필요하다. 기존 단위 테스트를 전체 M2 통합 성공으로 재표기하지 않는다.
최종 보고서 citation, Generator/Judge, 실제 PDF 및 전체 live runner는 #94/#95/#96의
M3 범위다. 필수 실사용 trace가 준비될 때까지 #62와 PR #123은 blocked/Draft로 유지한다.
