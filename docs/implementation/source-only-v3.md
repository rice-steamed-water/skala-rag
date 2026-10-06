# v3 source-only 직접 실행 (#209, #215)

## 범위와 승인 경계

`skala_rag.source_only_v3`는 기존 v3 outer의 `discover → normalize → research → eligibility → archive → advance → selector`를 소비한다. 별도 graph, 평가 stub, CLI는 만들지 않는다. 기본 fixture controller와 일반 live selector의 거절 조건은 그대로다.

이 경로의 `RunInput.execution_mode`와 terminal `execution_mode`는 `live`다. 이는 Source/Candidate를 live context로 재검증한다는 뜻이며 실제 HTTP 호출, 기업 사실 확인, 유료 실행 승인을 증명하지 않는다. 기존 `prepare_source_only_v3`의 `replay_scope="discovery_bundle_replay"`로 받은 Discovery는 캡처 재생이다. 새 주제 발견 실측으로 기록하지 않는다. 신규 Discovery producer는 연결하지 않았으며 기존 API의 다른 replay scope는 provider 구성 전에 거절한다.

신규 CompanyResearch 호출에는 기존 `LiveResearchCompany`, `OfficialHomepage(extractor=None)`, `SafeFetcher`만 쓴다. 사실 추출 모델을 구성하지 않으므로 홈페이지 조회가 성공해도 기업 사실이나 평가 Evidence를 새로 만들지 않는다. `research_replays`를 전달하면 해당 후보의 기존 `ToolResult[CompanyResearchBundle]`을 재생한다. 신규 provider 호출과 캡처 재생은 산출물에서 구분한다.

운영 수치는 기존 code-owned approval registry의 pinned content로 확인한다. `V3Policy`의 fixture-only 구조, 일반 `select_best_v3`/approved selector의 live 경계는 바꾸지 않는다. 정책 pin, DTO 검증, 입력 hash, Source metadata는 의미 검토나 실행 승인 근거가 아니다. `eligible` 후보도 evaluator를 만들지 않고 `SOURCE_ONLY_EVALUATION_NOT_READY`로 실패 처리한 뒤 archive/advance한다. collect/freeze, 평가 다섯 branch, scoring, Decision, 보고서 생성은 시작하지 않는다.

## Python 호출 계약

다음 두 callable을 직접 호출한다. 기존 fixture `skala_rag.cli.run()`과는 반환 계약이 다르다.

```python
from skala_rag.source_only_v3 import prepare_source_only_v3, run_source_only_v3

boundary = prepare_source_only_v3(**source_only_inputs)
result = run_source_only_v3(boundary, output_dir=private_new_directory)
```

`source_only_inputs`와 새 output directory는 호출자가 준비한다. 이 예시는 실제 입력, HTTP 권한 또는 factory를 생성하는 실행 명령이 아니다.

| 인자 | 호출자가 제공할 값 |
| --- | --- |
| `run_id` | RunProfile 및 원본 retrieval/error의 run ID와 같은 값 |
| `run_input` | `execution_mode="live"`인 `RunInput`; theme, countries/languages, as_of, corpus/policy/schema를 명시 |
| `discovery_result` | 원본 Source closure, Candidate, RetrievalRecord, WorkflowError를 포함한 캡처 `ToolResult[DiscoveryBundle]` |
| `run_profile` | 기존 운영 프로필: 최대 5개, seed 42, 초기 research 1회, unknown 재시도 0, refill 없음, 유료 allowance/cost 0 |
| `budget` | 후보별 `LiveResearchCompany` 호출에 적용하는 `ToolBudget`; 같은 schema, `max_calls ≥ 1`, `max_retries=0`, 양수 timeout 및 선택적 deadline |
| `provider` | `official-homepage`만 허용 |
| `replay_scope` | `discovery_bundle_replay`만 허용 |
| `fetcher_factory` | 호출 시 기존 `SafeFetcher`를 반환하는 factory; HTTP/transport 구성과 권한은 호출자 책임 |
| `clock` | 기존 Clock 계약 |
| `research_replays` | 선택적 candidate-ID별 원본 `ToolResult[CompanyResearchBundle]`; 새 provider 호출 없이 재생 |

provider 제외, paid0, budget, profile, run/schema/policy/corpus/as_of, Source closure, fixture URI 혼입은 fetcher factory 호출 전에 검사한다. 입력은 JSON/DTO로 분리해 pin하며 hash는 변경 감지에만 쓴다. 재생 결과가 세대 정보를 선언했다면 현재 입력과 일치해야 한다. 명시한 `canonical_name`/`homepage_url`, 기존 CompanyResearch 요약의 이름 query, OfficialHomepage의 URL 선언은 원본 후보와 대조하고 소비 시 정규화된 후보와도 다시 대조한다. 이름은 canonical name/alias, URL은 기존 host/site 비교만 사용한다. 불투명한 query나 알 수 없는 추가 metadata는 identity 증명으로 승격하지 않는다. 선언하지 않은 필드를 새 승인이나 원본 실행 세대의 증명으로 채우지 않는다.

선정 뒤 후보를 다시 채우거나 reselection하지 않는다. dedup merged ID와 제외 후보는 연구하지 않는다. 선정 후보는 순서대로 한 번씩 처리하고 마지막에 selector를 한 번 호출한다. 전용 selector guard는 점수/결정 없는 noneligible/failed terminal row만 받고 기존 deterministic no-selection 함수를 사용한다. 원본 Eligibility를 row에 유지하며 `eligible`은 `status="failed"`일 때만 통과한다. evaluated row와 점수/결정은 계속 거절한다.

신규 홈페이지 Source에는 발행일이 없다. 선정 뒤 신규 호출이 필요한 후보가 있으면 `clock.now().date() ≤ as_of`와 미만료 deadline을 factory 전에 검사한다. factory 구성 직전과 각 신규 CompanyResearch 호출 직전에도 다시 검사한다. 최초 거절은 `ValueError`로 전파하며 완성 산출물을 만들지 않는다. 실행 중 만료하면 해당 후보를 기술 실패로 archive/advance하고 새 요청 수를 올리지 않는다. 이는 실행 시각의 admission 오류이며 비상장, Exit 없음 등 기업 사실을 뜻하지 않는다.

캡처 재생은 현재 clock이나 신규 fetch deadline을 소비하지 않는다. 원본 Source의 발행일/확보일 cutoff 검사는 그대로다. 늦은 시각에 실행해도 기준일 전에 확보한 캡처는 재생할 수 있지만, 과거 발행일만 있는 기준일 이후 편집본을 허용하지는 않는다. factory 구성이 실패하면 실패 여부만 메모리에 남기고 후속 후보에서 재구성하지 않는다. 재생 후보는 계속 소비하고 모든 처리된 후보를 한 번씩 archive/advance한다. raw exception은 저장하지 않는다.

## 고정 후보와 원본 캡처만 재생 (#215)

새 수집 없이 실행하려면 `prepare_offline_source_only_v3`를 호출한다. `candidate_bundle`은 기존 `DiscoveryBundle` DTO에 담은 고정 Candidate/Source 입력이다. Discovery 성공 ToolResult, 검색 기록이나 과거 HTTP 성공을 만들지 않는다. `research_captures`는 호출자가 확보한 원본 `ToolResult[CompanyResearchBundle]`의 candidate-ID map이다.

```python
from skala_rag.source_only_v3 import prepare_offline_source_only_v3, run_source_only_v3

boundary = prepare_offline_source_only_v3(
    run_id=run_id,
    run_input=run_input,
    candidate_bundle=candidate_bundle,
    research_captures=research_captures,
    run_profile=run_profile,
    budget=budget,
    clock=clock,
)
result = run_source_only_v3(boundary, output_dir=private_new_directory)
```

위 변수는 호출자가 준비한다. factory/provider/model 인자나 기본 수집 fallback은 없다. 기존 profile, budget, policy pin과 Source cutoff를 검사하고 기존 outer가 한 번 정규화/선정한다. 선정 후보 캡처가 하나라도 없으면 첫 research/assembler 호출 전에 `ValueError`로 거절하며 산출물을 만들지 않는다. 제외/dedup 후보의 캡처는 없어도 되고, 제공했다면 unused 입력으로 보존한다. 관계없는 후보 ID는 거절한다. 오래된 캡처는 현재 clock이나 fresh-fetch deadline으로 막지 않는다.

저장 `replay_scope`와 후보 입력 `origin`은 `fixed_candidate_input`이고 manifest의 신규 provider는 `null`이다. 원본 캡처와 진단은 바꾸지 않는다. required 오류는 기술 실패, optional 오류는 원래 하위 기록, empty는 unknown으로 남는다. 실제 assembler/Eligibility를 쓰되 eligible도 기존 `SOURCE_ONLY_EVALUATION_NOT_READY`로 종료한다. 저장 상태는 투자 결과가 아니다.

회귀 제어군은 합성 캡처와 MockTransport만 쓴다. 실제 PI 기업의 CompanyResearch 적격성 캡처는 확보하지 않았다. 승인 PI 논문 Source/Chunk나 raw PDF를 기업 비상장/완료 Seed~C/Exit 미완료 근거로 바꾸지 않는다. actual Coverage/freeze/평가/scoring/report와 유료 호출은 열지 않는다.

## 결과와 산출물

반환값은 기존 `CandidateRunV3`다. `source_only_detail`에 Discovery 원본 결과 또는 origin을 명시한 고정 후보 bundle과 재생 범위, 연구별 ToolResult, 조립 State, Source, RetrievalRecord, 원본 WorkflowError, Eligibility, receipt를 보존한다. 원본 연구 기술 오류는 code/ID/run/candidate를 유지한다. #203의 required-extractor 실패 Source retention도 기존 consumer로 복원한다. retention이 malformed이면 거절된 payload를 사실로 보존하지 않고 `UPSTREAM_INVALID`로 실패 archive/advance한다. 전체 원본 캡처는 `capture_replay_inputs`에 입력으로 따로 보존한다. 이 입력 보존은 State에 사실로 채택했다는 뜻이 아니다. `SOURCE_ONLY_EVALUATION_NOT_READY`는 controller-local WorkflowError 진단 문자열이다. ToolResult ErrorCode enum이나 Enum 재수화 계약을 추가하지 않았으며 #203 원본 typed 오류는 바꾸지 않는다.

| terminal | 의미 |
| --- | --- |
| `no_candidates` | 정상 Discovery 캡처 또는 고정 후보 입력에 후보가 없음 |
| `discovery_failed` | 원본 Discovery의 기술 실패 또는 발견·정규화·선정 단계의 입력/실행 실패 |
| `no_eligible_candidates` | 연구 기술 실패 없이 전 후보가 unknown/ineligible |
| `source_only_partial_failure` | 일부 후보에 연구 오류, 잘못된 retention 또는 아직 열리지 않은 평가 경계 등 기술 실패 발생 |
| `source_only_technical_failure` | 전 후보가 기술 실패로 종료 |

위 상태에서 선정 후보, scores, decisions는 없다. 정상 empty/unknown/ineligible의 selector reason은 `NO_ELIGIBLE_RESULTS`다. Discovery 실패는 `SOURCE_ONLY_DISCOVERY_FAILED`, 전 후보 실패는 `SOURCE_ONLY_TECHNICAL_FAILURE`, 정상 후보와 실패가 섞이면 `SOURCE_ONLY_PARTIAL_FAILURE`로 구분한다. 기술 실패를 투자 비추천 진단이나 정상 결측으로 바꾸지 않는다. 취소 예외는 전파하며 terminal artifact를 만들지 않는다. 일반 입력 거절과 파일 I/O 오류도 전파될 수 있다.

새 directory를 `0700`, JSON 파일을 `0600`으로 만들고 저장 byte를 다시 읽어 확인한다. 기존 directory는 덮어쓰지 않는다.

- `candidate-run.json`: terminal state와 원본/조립 연구 상세
- `trace.json`: 기존 trace와 discover/normalize/research/eligibility 실행 시간. `status="ok"`는 callback이 반환됐다는 뜻이지 Eligibility 성공이나 적격 판정을 뜻하지 않는다.
- `graph-events.json`: 실제 LangGraph node, namespace, candidate/index/route 요약
- `manifest.json`: run input/profile/budget/provider, 입력 binding hash, 신규 CompanyResearch 호출/캡처 재생 시도 수, factory 구성 시도 수, 후보별 호출/재생 시도 수, 캡처 supplied/attempted/unused/excluded/dedup ID와 원본 입력 hash, 나머지 세 파일의 SHA-256

실패로 detail을 조립하지 못한 재생도 시도 수에 포함한다. factory 실패와 admission 거절은 provider 호출 수에 넣지 않는다. budget scope는 `per_candidate`이며 캠페인 전체 요청 한도가 아니다. replay budget scope는 `no_new_network_not_charged_to_fresh_fetch_deadline`, 물리 HTTP 요청 수는 `unmeasured`로 명시한다. 이 usage는 이번 composition 범위이며 실제 HTTP request 수, 과거 유료 호출이나 과거 ledger를 추정하지 않는다. 과거 ledger는 `not_supplied_unverified`다. manifest는 자기 hash를 포함하지 않는다. `semantic_review="unreviewed"`, `evaluation="not_started"`, `scoring="not_started"`, `publication_allowed=false`를 유지한다. private JSON은 보고서 발행 허가가 아니다.

clients/factory/환경 snapshot/raw exception은 State에 넣지 않는다. `run_source_only_v3(..., secret_values=...)`는 호출자가 지정한 secret 문자열의 저장을 거절한다. 이것은 입력 DTO나 원본 Source 전체가 비밀을 포함하지 않는다는 보증이 아니므로 호출자가 캡처를 확인해야 한다. 취소나 I/O 오류로 중단하면 완성 manifest가 없을 수 있다.

## 확인한 범위

고정 입력 경로는 선정 캡처 누락의 첫 research 전 거절, 후보와 Source의 연결·cutoff·세대 검증, 입력 분리와 변경 거절, 실제 assembler/Eligibility 소비와 private JSON hash를 검사한다. 이 경로에서는 budget을 신규 호출에 쓰지 않는다.

통합 테스트는 socket을 거절하고 `httpx.MockTransport`로 기존 source tool, 실제 CompanyResearch/Eligibility consumer, 기존 LangGraph outer와 Python composition을 실행한다. 가상 홈페이지와 기존 합성 fixture는 기업 사실 실측이 아니다. unknown, 원본 LLM_TIMEOUT와 Source retention, no homepage, 정상 empty/failed Discovery, malformed 입력/retention, 제외 provider, paid0, callback 변경, dedup/no-refill, eligible 거절, no-selection guard, 취소, JSON/trace/hash를 검사한다.

이 문서는 actual HTTP, 실제 모델, 새 corporate eligibility 사실, 정책 의미 승인, 유료 캠페인, full scoring/live evaluation을 실행하거나 승인하지 않는다. 그 경계는 [live 정책](live-scoring-policy.md), [run 설정](run-settings.md), [Python 직접 실행](python-execution.md)의 기존 승인 조건을 따른다.
