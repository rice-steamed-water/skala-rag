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

`tests/integration/test_m2_research_state.py`의 synthetic 통합 테스트는
적격성 세 상태의 분기, 저장 후 freeze, 실제 요청 이력 연결, 비밀 값·덮어쓰기·
미래/타 기업 자료 거절, 429 provider 실패와 평가 실패 시 조사 State 보존을 검증한다.
해당 테스트는 socket 연결을 차단한다.

### #203 required Eligibility 추출 실패

구성된 `OfficialHomepage` 추출기의 `LLMError`는 정상 `unknown`이 아니다.
`OfficialHomepage → LiveResearchCompany → assemble_research_state /
run_research_to_trace`에서 후보·workflow를 `failed`, `run_outcome`을
`technical_failure`로 저장한다. Eligibility 결과, CompanyProfile, 점수나
criterion Missing을 생성하지 않고 Technology callback도 호출하지 않는다.
반면 `extractor=None`, 성공 빈 facts, homepage 미지정은 기존 자료 부재/unknown이다.
선택 provider 오류와 HTTP fetch 오류의 기존 required/optional 구분은 그대로다.

기존 `ToolResult` 검증은 `ERROR_SPECS.tool_status=None`인 LLM 코드를 오류로
직접 넣을 수 없다. 계약을 완화하지 않고 이 도구 경계에서만
`LLM_TIMEOUT → TOOL_TIMEOUT`, `LLM_OUTPUT_INVALID → TOOL_RESPONSE_INVALID`,
`LLM_FAILED → TOOL_FAILED`로 표현한다. 인증 등 도구 코드의 상태는 그대로다.
원래 추출 오류의 `WorkflowError` JSON을 기존 company-research 요약
RetrievalRecord의 `arguments_without_secrets.extractor_error`에 보관한다.
State 소비자는 요약·원래 오류·도구 오류의 run/candidate/error ID, 시각,
attempt, provider required 상태와 코드 대응을 다시 검증한 뒤 원래 코드와
`ERROR_SPECS`의 retryability를 State/연구 receipt에 복원한다.
예를 들어 OUTPUT_INVALID의 State retryable은 true이며 경계의
TOOL_RESPONSE_INVALID는 false다. 이는 실제 재시도 허용/실행이 아니다.
임의 예외 원문은 보관하지 않고 고정 오류 설명만 저장한다.

실패 `ToolResult.data`는 항상 None이다. 성공 HTTP Source snapshot은 기존 요약
metadata의 `retained_sources`에 Source ID → JSON payload로 보존하며 성공 fetch
RetrievalRecord를 실패 요청으로 바꾸거나 복제하지 않는다. State 소비자는 엄격한
Source DTO, map key/내용 hash/snapshot ID, 기준일과 동일 run/candidate의 성공
fetch 및 수집 시각 범위를 검증한다. 후행 required 실패 전 성공 provider의
Source도 보존한다. 이 archive는 facts/Evidence admission이나 semantic 승인,
원문 bytes 보관을 의미하지 않는다. malformed/mismatched payload는 저장 전에 거절한다.

component wrapper는 note만 보고 terminal을 덮어쓰지 않는다. 저장한 ResearchState의
`run_outcome=technical_failure`를 terminal status에도 반영하며 stage receipt와
State/연구 receipt/terminal artifact의 read-back을 테스트한다.
실제 401을 받는 기존 runtime의 `LLM_FAILED` 변환은 이 작업에서 바꾸지 않는다.

검증은 MockTransport 및 mocked LLM 오류를 사용한 offline synthetic 검증이다.
LLM timeout/output-invalid/auth/failure, Source hash/성공 요청·요청 예산 보존,
설정된 가상 비밀 값 비노출, malformed retention 거절, 정상 unknown 회귀와
cancellation 전파를 검사한다. 실제 기업 적격성 positive, 유료·provider·model·
index 다운로드 호출, full-live 성공은 확인하지 않았다.

### #207 상장·Exit 제안값과 원문 대조

`LLMEligibilityExtractor._check`는 `is_listed`와 `exit_completed`를 원문의
지원 문장과 대조한다. 대상 기업명·별칭 또는 1인칭 주어에 상태·완료·부정
표현이 직접 연결되어야 한다. 모델의 `claim`이나 주제어 존재만으로 값을
받지 않는다. 반대로 제안한 값은 고치지 않고 거절한다.

발췌가 들어 있는 모든 원문 문장과 같은 출처의 다른 대상 문장도 검사한다.
반복된 발췌를 유리한 위치에만 연결하거나, 앞의 부정·뒤의 조건을 빼고
사실로 받지 않는다. 서로 반대인 상태, 질문·가정·계획·미완료·미확인 표현,
고객·파트너 사건과 대상 기업의 매수자 역할은 근거로 받지 않는다.
상장 진술과 Exit 이력 없음이 함께 있으면 Exit 제안을 거절한다. 기존
추출 prompt가 상장·IPO를 Exit에 포함하기 때문이다. 별도 문장의
`listed on NASDAQ`, `listed on NYSE`, `publicly listed company`도 Exit 맥락
검사에 포함한다. 주제어만 보고 충돌로 정하지 않고 지원하는 대상 기업의
상장 진술이 실제 `True`일 때 거절한다. 미지원 상장 문장은 미확인으로
거절한다. 상장 진술로 Exit 관측을 만들지는 않으며, 비상장만으로 Exit
없음도 만들지 않는다.

| 원문과 제안 | 추출·판정 결과 |
| --- | --- |
| 지원하는 비상장·Exit 이력 없음 진술 + `false` | 해당 관측을 만들고 기존 composer와 Eligibility에 전달 |
| 지원하는 상장·Exit 완료 진술 + `true` | 해당 관측을 만들고 기존 Eligibility의 부적격 조건으로 전달 |
| 지원 문장과 반대인 boolean | `SOURCE_POLARITY_MISMATCH`, 해당 제안의 관측·Evidence 없음 |
| 불명확하거나 지원하지 않는 문장·한정 표현 | `SOURCE_POLARITY_UNVERIFIED` |
| 원문 내 상태 충돌·상장과 Exit 없음의 충돌 | `SOURCE_POLARITY_CONFLICT` |
| 입력 상한 절단·생략 표시(`…`, `...`)·디코딩 대체 문자 | `SOURCE_CONTEXT_UNAVAILABLE` |

거절은 정상 자료 부족이다. 성공 fetch의 Source와 요청 이력을 유지하고,
다른 유효 관측이 없으면 해당 profile 필드를 `None`으로 남겨 판정을
`unknown`으로 만든다. 다른 provider가 제공한 유효 관측은 기존 composer
규칙대로 남는다. 예를 들어 원래 재현 사례의 OpenDART `E`는 독립적인
`is_listed=False` 근거로 남지만, 상장 원문의 반대 제안은 Evidence에 들어가지
않는다. 그 사례의 Exit 제안도 충돌로 거절되어 `eligible`이 되지 않는다.
거절 note에는 필드와 사유 코드만 넣는다. #203의 `LLMError`, 원래 오류 코드,
실패 `data=None` 및 Source 보존 처리는 바꾸지 않는다.

지원 범위는 코드의 닫힌 KR/EN 문장 형태다. 현재 비상장·상장 기업 진술,
명시적 상장 완료, Exit 이력 없음·완료, 피인수 수동태 등을 검사하며 문장이
`.` 또는 `。`로 끝나야 한다. 기존 GOOD의 같은 주어를 잇는 `이며` 문장도
지원한다. 특정 거래소에 상장되지 않았다는 표현만으로 전체 비상장을
확정하지 않는다. 지원하지 않는 표·인용·축약·복잡한 절이나 매수자 이름은
정확한 사실이어도 거절될 수 있다. 별도 문장의 한정 표현을 놓치지 않도록
일부 가정·미확인 표시가 원문 어디에 있어도 두 필드의 제안을 거절하므로,
관련 없는 설명 때문에 자료 부족이 늘어날 수도 있다. 같은 보수적 거절에
마침표로 끝나는 네 문장 `This is not true.`, `That is false.`,
`This statement is withdrawn.`, `We withdraw that claim.`도 포함한다.
이 문장이 무엇을 가리키는지 해석하지 않으며 다른 표현으로 확대하지 않는다.
`가정` veto는 `가정용 로봇` 같은 자기 설명에도 걸려 정상 상장·Exit 제안을 거절할 수 있다.

이 검사는 일반적인 의미 증명이나 독립 사실 검증이 아니다. 외부에서 이미
잘린 자료에 생략 표시가 없으면 원래 맥락을 복원할 수 없다. 임의의 표현과
대명사·부정 관계를 모두 해석하지 않으며, 공식 홈페이지 자기 진술이라는
기존 한계를 유지한다. `domain_match`·business·stage의 일반 의미 검증,
`claim` 문장 전체의 정당화, 법인 동일성·최소 Evidence 정책은 범위 밖이다.
Source/DTO·공통 계약·정책·prompt version은 변경하지 않았다.

검증은 socket을 차단한 synthetic fixture로 실제
`OfficialHomepage → LLMEligibilityExtractor → LiveResearchCompany → check_eligibility`
경로를 실행했다. KR/EN의 올바른 양방향 관측과 반대값 거절, 발췌의 맥락 누락,
반복 위치·상충·미래·매수자 역할·입력 절단을 검사한다. 첫 구현 검증에서는
추출기 테스트 157개, 기존 Eligibility·composer 및 #203 필수 추출 실패 회귀까지
포함한 아래 범위 239개가 통과했다. 실제 기업·provider·모델 응답이나 전체 M2
live 성공을 확인한 결과는 아니다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. UV_OFFLINE=1 uv run --no-sync pytest \
  -p no:cacheprovider \
  --basetemp=/Users/luk/.hermes/cache/scratch/skala-207-worker-pytest \
  tests/unit/test_eligibility_extraction.py \
  tests/unit/test_eligibility.py tests/unit/test_company_research.py \
  tests/integration/test_m2_research_state.py::test_required_extractor_failure_persists_technical_failure -q
```

### #207 부모 검증 후 보완

2026-10-06 부모 검증에서 영어 상장 문장이 별도로 있으면 Exit 맥락 검사에서
빠지는 경로를 확인했다. 보완 전 실제 consumer 회귀는 `8 failed, 9 passed`였다.
KR/EN 별도 문장과 역순, NASDAQ/NYSE/publicly listed 충돌, 미지원 상장 문장,
비상장과 명시적 Exit 없음의 정상 조합을 검사했다. truth-denial/withdrawal
회귀는 보완 전 `16 failed, 9 passed`였고 네 문장에만 보수적 거절을 추가했다.

기존 component 전체 파일에서는 `2 failed, 8 passed`를 재현했다. 긍정 fixture의
`Synthetic Robot has not completed an acquisition or IPO exit.`는 지원 형태가
아니므로 `unknown`이었다. 이를 받도록 추출 규칙을 완화하지 않았다.
`tests/integration/test_m2_component_runner.py`의 HOME 본문과 모델 발췌 두 문자열만
`Synthetic Robot has no history of an exit.`로 맞췄다. 기존 assertion, 호출 횟수,
공유 사용량, 실패 시 admission/terminal 보존 검사는 그대로다. consumer 단위
회귀에서도 새 문장은 `eligible`, 옛 문장은 계속 `unknown`임을 확인했다.

보완 후 추출기와 component 전체 파일은 `209 passed`였다. 아래 scoped 검증은
`363 passed`, 실패·오류·skip 0건이었다. #203의 기술 실패, `data=None`, Source 보존,
정상 unknown과 State 회귀도 포함한다. 수정하지 않은 부모의 4-case polarity 및
영어 충돌 probe는 모두 exit 0이었다. 영어 probe는 OpenDART `E`의 독립 근거인
`is_listed=False`를 유지하고 `exit_completed=None`, Eligibility `unknown`이었다.
Ruff check, Python 세 파일의 format check와 `git diff --check`도 통과했다.
전체 suite/build/독립 review/graphify/CI/merge는 이 보완에서 재실행하지 않았다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. UV_OFFLINE=1 uv run --no-sync pytest \
  -p no:cacheprovider \
  --basetemp=/Users/luk/.hermes/cache/scratch/skala-207-postreview-scoped-tmp \
  --junitxml=/Users/luk/.hermes/cache/scratch/skala-207-postreview-scoped.xml \
  tests/unit/test_eligibility_extraction.py tests/unit/test_eligibility.py \
  tests/unit/test_company_research.py tests/integration/test_m2_component_runner.py \
  tests/integration/test_m2_research_state.py tests/integration/test_reducer_state.py \
  tests/contract/test_state.py -q --tb=short
```

### #207 Exit 사건 어휘 누락 보완

부모의 수정하지 않은 consumer probe에서 `We merged with Beta in 2022.`,
`We went public in 2022.`, `We were sold to Beta in 2022.`와 별도 Exit 이력
없음 진술이 함께 있으면 `exit_completed=False`, `eligible`로 잘못 허용됐다.
세 문장의 주제어가 기존 Exit 검사에서 빠졌기 때문이다.

Exit 주제 검사에 `merge/merged/merging/merger`, `go/went public`,
`public offering`, sale/sell/sold, buy/bought, purchase/purchased, takeover와
관련 활용형, 한국어 `매수`, `매입`, `공모`, `기업공개`를 포함했다.
대상 기업명·별칭·1인칭이 있는 문장을 기존 거절 검사로 보내기 위한 어휘다.
지원하지 않는 완료·계획·불명확 사건 문장은 `SOURCE_POLARITY_UNVERIFIED`로
거절한다. `_source_assertions`와 기존 지원 술어는 바꾸지 않았으며 새 어휘로
`True`나 `False` 관측을 만들지 않는다. OpenDART `E`의 독립 비상장 근거와
business/domain/stage 관측은 유지한다.

어휘 사전 밖 표현이나 다른 문장 대명사의 의미를 모두 해석하는 검사는 아니다.
대상 기업을 언급한 제품 판매·구매나 파트너 설명도 보수적으로 거절될 수 있다.
이런 false-negative를 줄이려고 기존 veto나 지원 문장 규칙을 완화하지 않았다.
기존 component HOME/발췌의 두 문자열과 검증된 테스트 본문은 그대로 유지했다.

같은 31개 사건 문장의 순서 양방향 consumer 회귀와 boolean 제안 양방향 거절,
기존 지원 진술 및 다른 기업 사건 controls를 먼저 추가했다. 생산 코드 수정 전
`112 failed, 23 passed`를 확인했고 보완 후 새 범위는 `135 passed`였다.
component와 #203을 포함한 기존 7개 파일 scoped 검증은 `498 passed`,
실패·오류·skip 0건이었다. 수정하지 않은 부모 probe 세 파일, Ruff/format,
`git diff --check`도 통과했다. 증거는 scratch의 `skala-207-vocabulary-*`에
저장했다. 전체 suite/build/graphify/독립 review와 live 호출은 재실행하지 않았다.

후속 보완은 `acquisition(s)`, `buyout(s)/buy-out(s)/buy out(s)`, `takeover(s)/take-over(s)/take over(s)` 명사형도 거절 검사에 연결한다.
부분 어휘 검사이며 일반 NLP 의미 증명이나 사실 판단 권한은 아니다. 기존 지원 assertion과 routing은 유지한다.

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
  저장한 State와 같은 `technical_failure`이며 정상 missing·성공으로 바꾸지 않는다.
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
