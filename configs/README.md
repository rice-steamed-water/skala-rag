# 설정 소유권

모델·실행 구성의 원본은 `runtime.json`이며 로더는
`skala_rag.settings`다. 점수 정책·rubric·PDF 설정은 아래의 기존 파일과
각 전용 로더가 계속 소유한다. 실행 설정으로 정책 승인을 대신하지 않는다.

## 실행 설정 (#231)

`runtime.json`은 필수 `schema_version="runtime-1"`, `llm`, `profiles`를 갖는
완전한 JSON 문서다. `llm`은 요청 모델·Responses endpoint·Decimal 단가와
요금 출처/확인일을 담는다. credentials, run/company ID, query, 모델 revision,
corpus/index hash, artifact 경로와 승인 receipt는 넣지 않는다.

| profile | 구성 대상 / shipped 값 |
| --- | --- |
| `m2_shared` | Eligibility/Evidence/Technology의 공유 8회, 입력 64,000·출력 16,000 token, USD 1, 시도 30초, 재시도 0; 요청당 입력 8,000·출력 2,000 |
| `m2_evidence_validation` | 선택적 OpenAI 검증 smoke의 별도 8회 LLM 한도·600초 deadline, retrieval 4회·시도 1회; 질의·top_k·로컬 timeout은 명시 입력 |
| `m2_source` | source-only 3회·30초·600초, HTTPS fetch 5,000,000 bytes·redirect 3, OpenDART 이름 1개·index 100,000,000 bytes |
| `m2_research` | component 600초, eligibility 1,200 chars, retrieval 3회·30초; fetch/OpenDART 범위는 source와 같지만 redirect는 반드시 0 |
| `m2_local_rag` | 로컬 retrieval 1회·30초·top_k 5, provider token/cost 0 |
| `actual_v3` | **공유** 40회, 입력 2,000,000·출력 120,000 token, USD 1, 시도 60초·deadline 3,600초·재시도 0, retrieval top_k 3 |
| `local_demo` | persisted campaign 30회·USD 3·1,200초, 요청 출력 2,000 token·timeout 최대 120초·최소 0.1초 |
| `recommended_run` | 기존 후보 5개·seed 42·최초 조사 1회·unknown 재조사/평가/refill 없음·paid allowance 0의 controller handoff |

### 파일 선택과 우선순위

```python
from pathlib import Path
from skala_rag.settings import load_runtime_document, load_runtime_settings

settings = load_runtime_settings("m2_shared")
document = load_runtime_document(path=Path("/absolute/path/runtime.json"))
```

`path`는 **같은 schema의 전체 파일**을 선택한다. 일부 profile만 넣는 patch나
recursive merge가 아니다. 명시 상대 경로는 호출자의 CWD 기준이며, 기본 파일
탐색은 CWD와 무관하다. profile 이름은 명시적으로 선택하며 환경변수로 추론하지 않는다.

- source/editable의 정확한 `<root>/src/skala_rag/settings.py` 배치에서는
  `<root>/configs/runtime.json`을 읽는다.
- installed package에서는 `skala_rag/_config/runtime.json` resource를 읽는다.
  Hatch가 canonical 원본을 wheel에 복사하므로 두 번째 유지보수 원본은 없다.
- 선택된 파일이 없거나 malformed이면 실패한다. 다른 checkout/package로 fallback하거나
  상위 디렉터리를 탐색하지 않는다. extra key·duplicate key·비유한 수·secret 필드는 거절한다.

기존에 주입한 LLM/transport/runtime/ledger/clock/verifier/allowance의 identity를
보존하고, 기존 명시 인자가 선택 파일의 값보다 우선한다. 파일 값은 생략한 구성값만
공급하며 shipped 값은 이전 동작을 재현한다. 모든 callable에 일괄 override 인자를
추가한 것은 아니다. 지원하는 composition에는 `runtime_document=`로 위 document를
전달한다. 전부 주입된 경로는 쓰지 않을 operator 설정을 만들지 않는다.

composition은 기존 입력/mode guard 뒤에서 한 번 읽은 strict/frozen document의
profile을 공유한다. 구성 시 import에서 operator 파일·환경을 읽거나 provider를
만들지 않는다. 이후 파일 변경은 기존 snapshot/ledger를 바꾸지 않으며 다음 구성에서만
반영된다. demo 재시작·재승인도 기존 spend와 persisted limits/deadline을 초기화하지 않는다.

설정은 **요청값이지 authority가 아니다**. 코드의 모델 allowlist, 정확한 endpoint와
승인 단가, token/비용 ceiling, component redirect=0, 실제 승인·artifact hash·campaign
검증을 독립적으로 유지한다. unsupported/raised 요청은 거절하고 clamp하거나 새 승인을
만들지 않는다. per-tool 한도는 공유 총한도를 늘리지 않는다.

### Credentials와 prompt

키는 settings JSON/snapshot/repr·State·보고서에 직렬화하지 않는다.
`OPENAI_API_KEY`/`OPENDART_API_KEY` 소비와 경로별 resolver 우선순위는
[adapter runtime](../docs/implementation/adapter-runtime.md#중앙-설정과-credentials-231)과
[.env.example](../.env.example)을 따른다. 전역 환경 override나 dotenv 탐색은 없다.

authored prompt와 builder·버전의 소유자는 `src/skala_rag/prompt/`다.
`prompt/text/*.json`의 16개 resource는 **nonempty JSON literal string fragment array**다.
UTF-8 decode·JSON parse 후 `""`로 연결하며 trim/dedent/newline normalization은 하지 않는다.
파일 formatting의 공백·마지막 LF는 문자열 밖에 있고, 효과가 필요한 공백·개행은
fragment 안에 명시한다. 모듈 상수는 process import 때 한 번 로드하므로 편집 후
새 프로세스로 실행한다. hot reload·template engine·alternate resource fallback은 없다.

기존 `skala_rag.prompts.*` 여섯 모듈은 동일 object의 import/`__all__` 호환 export만
제공한다. 수정은 singular owner에서 한다. 기존 논리 버전과 `actual-v3-1`·`local-demo-1`
composition tag는 `prompt/versions.py`에 그대로 보존된다. generic evaluator와 demo의
개별 layer에 과거 버전을 새로 부여하지 않는다. actual replay는 sorted relative path의
Python/config/font/lock과 prompt JSON byte hash를 비교하며 text 변경/삭제를 authority
callback 전에 거절한다. historical capture는 해당 historical code를 요구하며 bypass는 없다.

## Draft 점수 정책

`scoring.draft.json`은 현재 main의 `docs/implementation/scoring.md` §2–6과
D08 제안을 그대로 옮긴 **미승인 fixture 정책**이다. D01·D02·D03·D05·D08은
OPEN이며, v3 정합화 작업 #35 / PR #36의 변경은 반영하지 않았다.
이 파일은 팀 승인이나 실제 투자 평가 결과가 아니다.

`skala_rag.scoring.catalog.load_policy(path, execution_mode="fixture")`로
경로와 모드를 명시해 읽는다. 암묵적 경로나 정책 기본값은 없다.
`execution_mode="live"`는 거절하며 status를 approved로 바꿔도 로더가 거절한다.
후속 설계가 병합되면 별도 버전과 해당 fixture를 함께 변경해야 한다.

`tests/fixtures/scoring.draft.json`에는 가상 계산 7종과 직접 입력 경계값의
기대 점수·label·grade가 있다. 경계값은 라벨 함수 테스트용 입력이므로
23개 정수 rating에서 생성 가능한 평가라고 주장하지 않는다.
실제 aggregate_scores·decide 구현과 세대 검증은 #16 범위다.

검증: `uv run pytest tests/unit/test_scoring_catalog.py`

## rubric (D14 제안)

`rubrics/core.yaml`(founder·market·technology·moat)과 `rubrics/finance.yaml`(traction·deal_terms)은
criterion별 rating 1–5 기준·최소 근거·missing 조건을 담은 **미승인 제안**이다. criterion ID·비중·표시명은
`scoring.draft.json`과 같아야 하며 `tests/unit/test_core_rubric.py`·`test_finance_rubric.py`가 확인한다.
설명: `docs/implementation/rubric-core.md`, `docs/implementation/rubric-finance.md`.
