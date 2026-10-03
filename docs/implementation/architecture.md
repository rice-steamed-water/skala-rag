# 아키텍처와 실행 흐름

[문서 홈](../README.md) · [공통 계약](contracts.md) · [결정 목록](decisions.md)

근거: [v3](../design/design-v3.html) B-1, D-1–D-3, E. **Evidence Research 단일 책임, 5 branch/6 dimension과 atomic Business & Deal, 전 후보 처리 후 selector, 재조사·수정 최대 2회와 Warning 반환은 승인된 v3 전환 방향**이다. N/A·0분모·최종 순위·회차·Warning 종료는 #82 운영 승인이다. #74의 독립 v3 envelope 구조와 미구현 controller/State 연결·PDF 상세는 구별한다. issue #3 baseline 승인 기록, 새 사용자 방향 승인, 현재 구현 여부는 [정합화 기록](design-v3-alignment.md)에서 구별한다.

**현재 구현 방향 — v3 전환 승인:** [사용자 전환 승인 #35 comment 5902877317](https://github.com/rice-steamed-water/skala-rag/issues/35#issuecomment-5902877317)(luk0715, 2026-09-30T02:29:07Z)에 따라 새 작업은 기존 baseline의 계속 구현이 아니라 v3에 정합화한다. baseline 코드·승인 기록은 호환성과 이력으로 보존하며 새 구현의 우선 방향이 아니다. 방향 승인에 이어 #82 및 #35 comment 5903505208에서 N/A·0분모·최종 selector·재조사 회계·Warning 종료의 운영 규칙을 별도 승인했다. #35 comment 5903574761의 무작위 선정은 평가 전 조사·평가 대상 집합에만 적용하며 최종 selector는 무작위가 아니다. 승인과 구현 완료는 별개이며 rubric 상세·provider·corpus·시간/비용 예산 등 남은 세부 선택만 [결정 목록](decisions.md)의 OPEN gate를 따른다.

## 1. 역할을 나누는 기준

**실행 경계 — #166:** 최종 인터페이스는 [Python 직접 호출](python-execution.md)이다. 기존 fixture `skala_rag.cli.run(...)`은 argparse 없이 실행하고 산출물 `Path`를 반환한다. 목표 Graph/RunResult 계약과 현재 callable은 별개이며 runner 이동·새 API를 구현하지 않는다. CLI/parser·테스트는 호환 보존하고 신규 CLI·옵션·패키징·UX는 개발하지 않는다. 과거 #82의 CLI2는 [기존 매핑](fixture-cli.md#기존-exit-code-매핑)이며 도메인 종료 조건은 아래 completed+Warning/fatal과 receipt다.

| 종류 | 책임 | 예 |
| --- | --- | --- |
| Tool-using Agent | 제한된 도구 집합에서 조사 계획·도구 선택·근거 추출 | Discovery, Company Research, Evidence Research(RAG/Web/API 초기·gap 조사) |
| Structured-output LLM Node | 제공된 근거만 해석, 직접 검색 없음 | 5개 평가 branch, 선택적 판단 설명, 보고서, Semantic Judge |
| Deterministic Node | 검증·산술·상태 전이 | Normalize, Iterator, Eligibility, Coverage, Merge, Join, Aggregate, Decision, Best Candidate Selector, Validator |

LLM이 산술을 수행하거나 정책 임계값을 변경하지 않는다. 도구 결과의 본문은 분석 대상 데이터이며 에이전트 지시문이 아니다.

## 2. 전체 Graph — 승인된 v3 방향·운영 규칙의 구현 목표

```mermaid
flowchart TD
    START([START]) --> discover[Startup Discovery]
    discover --> normalize[Candidate Normalize]
    normalize --> sample[Random pre-evaluation candidate set selection]
    sample --> left{Candidate left?}
    left -->|yes| select[Candidate Iterator: current ID]
    left -->|no| best[Best Candidate Selector: policy required]
    select --> research[Company Research]
    research --> eligible{Eligibility}
    eligible -->|eligible| collect[Evidence Research: RAG / Web / API]
    eligible -->|ineligible or unresolved| archive[Record Candidate Result]
    collect --> coverage{Coverage}
    coverage -->|enough or research exhausted| snapshot[Freeze Evidence Revision]
    coverage -->|gaps and retry below 2| collect
    snapshot --> snapok{Snapshot valid?}
    snapok -->|invalid| archive
    snapok -->|yes| founder[Founder Evaluation]
    snapok -->|yes| market[Market Evaluation]
    snapok -->|yes| technology[Technology Evaluation]
    snapok -->|yes| moat[Moat Evaluation]
    snapok -->|yes| business[Business and Deal: traction + deal_terms]
    founder --> join[Evaluation Join: all five]
    market --> join
    technology --> join
    moat --> join
    business --> join
    join --> evalok{All five successful?}
    evalok -->|yes: six dimension payloads| aggregate[Score Aggregator: validate denominators]
    evalok -->|no| archive
    aggregate --> denominator{Valid scored result?}
    denominator -->|yes| decision[Deterministic Investment Decision]
    denominator -->|no: no score, candidate error| archive
    decision -->|all four labels| archive
    best --> finalize[SelectionResult + ReportInput]
    finalize --> prepare[Build ReportContext]
    archive --> advance[Advance Candidate Index]
    advance --> left

    prepare --> contextok{Context valid?}
    contextok -->|yes| report[Report Generator]
    contextok -->|no| failed
    report --> structure{Structural Validator}
    structure -->|valid| judge{Semantic Judge}
    structure -->|draft invalid| retry{Revision budget?}
    structure -->|context invalid| failed
    judge -->|revise| retry
    judge -->|fail| failed
    retry -->|yes| report
    retry -->|no| warning[Completed + Warning: draft/findings, publication denied]
    judge -->|pass| render[Render PDF]
    render --> layout{Pages and layout valid?}
    layout -->|no| layoutpolicy[Layout policy required: D08 / D09]
    layoutpolicy -->|approved shared revision policy| retry
    layoutpolicy -->|fatal or policy absent| failed[Failed: preserve diagnostics]
    layout -->|yes| done[Completed: persist artifacts]
    done --> END([END])
    warning --> END
    failed --> END
```

오류 처리 공통 규칙은 §6이다. 그림의 v3 방향은 승인되었지만 세부 정책의 승인·주입과 Graph 구현·검증은 별개다. 없는 정책을 노드가 생성하지 않는다. Company Research unknown 보강·최소 Evidence gate는 OPEN이며, #82에서 회차가 승인된 Evidence Research Coverage 재조사와 구별한다. 정규화·동일 법인 dedup 뒤 조사·평가할 후보 집합을 무작위 선정한다(#35 comment 5903574761). 상한 초과 시 남길 집합 선정도 포함하며 단순 Iterator 순서 shuffle이 아니다. 선정된 모든 후보를 조사/Eligibility 확인하고 적격 후보만 평가한다. 비선정 모집단은 제외 기록만 남기며 평가한 것으로 표시하지 않는다. 예산/취소 등의 예외 중단을 전 후보 정상 처리로 표시하지 않는다. 별도 Targeted Research나 평가 후 재조사 화살표는 v3 기본 흐름에 없다.

## 3. 노드별 입출력과 완료 조건

| 노드 | 읽기 | 쓰기 / 완료 조건 |
| --- | --- | --- |
| Discovery | theme, 국가/언어 범위, 후보 상한 | DiscoveryBundle의 후보와 Source payload를 저장; discovery_source_ids 참조 확인, 검색 실패와 0건 구별 |
| Normalize | 원시 후보 | `Candidate[]`; 동일 법인 중복 제거, 동명이인 임의 병합 금지 |
| 사전 후보 집합 선정 | dedup 후보, 명시 상한·선정 정책 | 조사·평가 대상 집합을 무작위 선정; RNG 주입·선정/제외/replay 기록은 구현 제안. 최종 selector와 별개 |
| Candidate Iterator | candidates, candidate_index | current_candidate_id, 후보 상태; 리스트 밖 접근 방지. 최종 selected ID를 쓰지 않음 |
| Company Research | Candidate, 수집 도구 | CompanyProfile, StageInfo, Evidence, RetrievalRecord |
| Eligibility | CompanyProfile와 관련 Evidence | `eligible/ineligible/unknown`; 각 조건의 이유·근거 |
| Evidence Research | 후보, 평가 기준, manifest, Coverage gaps | 최초 수집·부족 근거 재조사 모두 수행; 실제 RAG/Web/API 이력과 Evidence |
| Coverage | criterion catalog, 후보 Evidence | CoverageResult와 ResearchGap; 판단 기준은 scoring 문서 |
| Evidence Research의 Merge controller | 신규 Evidence, Source/Chunk, provenance | 참조 검증 후 병합; snapshot에서 보이는 내용/경로가 바뀌면 evidence_revision 증가 |
| Freeze Evidence Revision | 현 후보 근거·평가 정책·Source/Chunk/수집 기록, 최종 EligibilityResult | 세대 증가 후 EvaluationSnapshot payload 복사·참조 검증·저장; 이후 불변. 참조 누락 또는 적격성 근거 무효화는 `SNAPSHOT_INVALID`로 해당 후보 failed → archive → advance |
| 5 Evaluation Branches | 같은 후보·세대·근거 snapshot | founder/market/technology/moat/business_deal 각각 terminal envelope; 마지막은 traction·deal_terms 둘 다 필수 |
| Evaluation Join | 해당 세대의 다섯 terminal result | 모두 성공일 때 여섯 dimension Evaluation을 원자적으로 저장. failure/부분 payload면 후보 failed → archive → advance; 누락은 전체 timeout/오류 처리 |
| Score Aggregator | 여섯 영역, 승인된 정책 | 양수 분모에서 ScoreSummary; 0분모면 점수 없이 명시 후보 오류/archive/advance |
| Decision Policy + 설명 | ScoreSummary, Eligibility | 정책이 label 결정, LLM은 근거·리스크·한계 서술만 추가 |
| Candidate Archive / Advance | 판정 또는 적격성·오류 사유 | 후보 결과 보존; index를 정확히 한 번 증가 |
| Best Candidate Selector | 전 후보 outcome, 적격 후보 판정·점수, 승인 selection policy | deterministic SelectionResult와 selected_candidate_id; 모든 후보 처리 전 호출 금지, 순위·동점은 #82 승인; 원본 candidate_id 최종 tie-break |
| ReportInput controller | SelectionResult, 전 후보 이력 | 적격 후보 없음은 selected=None 및 사유. 전부 WATCHLIST/PASS·평가 실패의 선택/mode는 D03 정책에 따름 |
| Build ReportContext | ReportInput, 최종 State의 평가/판정·snapshot·출처 | 모든 참조를 해소한 payload context 고정. 불완전/모순이면 CONTEXT_INVALID로 실패 |
| Report Generator | 검증된 ReportContext, 직전 feedback | ReportDraft; context의 실제 근거·서지정보만 사용 |
| Structural Validator | ReportDraft, 같은 ReportContext, 승인 policy | 섹션·schema·인용·Reference와 원래 점수/판정 일치 검증 |
| Semantic Judge | draft, 같은 ReportContext | 원래 평가·실제 근거와 대조해 pass/revise/fail. upstream 오류는 fail |
| PDF Renderer / Layout Validator | 검증된 draft, 고정 템플릿 | PDF와 페이지/요약영역 검증; 렌더링 실패는 완료가 아님 |

## 4. 병렬 실행과 데이터 소유권

원문은 공유 State의 dictionary에 `operator.or_`, 이력에 `operator.add`를 제안한다. 실제 구현에서는 **동일 키 충돌과 재실행**을 먼저 다룬다.

- 후보는 직렬, Founder / Market / Technology / Moat / Business & Deal의 다섯 branch는 병렬로 시작한다.
- 평가 wrapper는 `evaluation_results`에 자신의 key와 terminal result만 반환한다. `current_candidate_id`, index, retry count, 공통 gap 목록, 성공 `evaluations`를 동시에 수정하지 않는다. 오류 객체도 failure envelope로 보내 join controller가 State.errors에 기록한다.
- 모든 branch는 같은 `candidate_id`, `evaluation_round`, `evidence_revision`, `snapshot_id`와 그 payload 복사본을 읽는다. 현재 State.evidence를 다시 읽어 snapshot에 없는 근거를 끼워 넣지 않는다.
- 합류는 **이번 세대의 다섯 terminal result**를 기다린다. 다섯 성공일 때만 평가 결과를 승격하고, 하나라도 failure이면 후보를 실패 처리한다. 과거 세대 결과가 dictionary에 있다는 이유로 통과하지 않는다.
- branch ID는 `founder/market/technology/moat/business_deal`, dimension ID는 `founder/market/technology/moat/traction/deal_terms`다. [계약 §4](contracts.md)의 envelope/key 매핑을 따른다. Business & Deal의 둘 중 하나만 정상이어도 branch 성공으로 처리하지 않는다.
- Join만 여섯 성공 Evaluation을 저장한다. 평가 gap은 감사/최종 결측 계산용이며 사전 Coverage의 actionable gap을 덮어쓰지 않는다. 평가 뒤 재조사·재평가는 기본 경로가 아니다. 세대와 immutable snapshot은 재실행·중복 전달·향후 확장에도 혼입을 막기 위해 유지한다.

**연결 제안 [LG1, LG2]:** 고정 다섯 노드의 합류는 아래 형태로 표현하고, 실제 설치 버전에서 다섯 terminal result 대기·부분실패 처리를 통합 테스트로 검증한다. API 문서 참조나 기존 import smoke test는 이 Graph가 실행됐다는 증거가 아니다.

```python
# v3 다섯 branch 연결 형태 설명용: 현재 fixture 후보 Graph와 별도인 미구현 wiring.
builder.add_edge(
    ["founder", "market", "technology", "moat", "business_deal"],
    "evaluation_join",
)
```

일반 결과 map은 동일 ID·동일 payload 재삽입을 무시하고, 동일 ID·다른 payload는 오류로 처리한다. **Evidence는 동일 core에서 provenance 집합 병합을 허용하고, Source는 같은 core의 최초 수집 시각을 보존하는 예외**를 둔다([계약 §3](contracts.md)). 신규 RAG 경로가 추가되면 새 snapshot 세대에 반영하되 기존 snapshot은 변경하지 않는다. 새로운 평가에는 새로운 세대 key를 부여한다. 전체 State가 아닌 변경 부분만 반환한다.

## 5. 반복 예산과 종료 — baseline 승인 기록과 v3 대체안

D08의 baseline `5/2/2`, batch당 8회, 추가 retry 2회, 시도별 30초는 승인 기록으로 보존한다. 새 구현의 최대2회 loop·Warning 방향은 승인되었다. #82에서 최초 제외 추가 조사2회·요청 전 차감·empty/failure 소비 및 최초 제외 구조/의미 공유 수정2회·completed Warning·CLI2를 별도 승인했다. 도구 한도 변경·PDF layout 회계·live 예산은 OPEN이다.

| 설정 | 값의 상태 | 의미 |
| --- | --- | --- |
| `max_candidates` | baseline 승인 5; v3 대체값 미정 | 중복 제거 뒤 상한을 적용할 대상 집합은 무작위 선정 승인; 새 수치·알고리즘/seed는 미승인. 선정 집합은 모두 처리; 첫 추천 조기종료 금지, 고갈 후 무한 재발견 금지 |
| `max_research_retries_per_candidate` | baseline 승인 2; v3도 최대 2회 명시 | Coverage 부족 시 동일 Evidence Research가 재조사. 최초 제외 추가 batch 2회·요청 전 차감·empty/failure 소비 승인 |
| `max_tool_calls_per_research_batch` | baseline 승인 8; v3 대체 해석 미정 | 도구 retry를 호출 예산에 포함할지 정책으로 고정 |
| `max_report_revisions` | baseline 승인 2; v3도 최대 2회 명시 | 구조·의미가 공유. 최초 생성 제외 승인; PDF layout 포함 여부는 D08·D09 OPEN |
| `max_tool_retries` | baseline 승인 2; v3 대체 해석 미정 | 네트워크 retry는 Evidence 재조사와 별도 개념 |
| `tool_timeout_seconds` | baseline 승인 30초; v3 대체값 미정 | 단일 도구 시도 제한 |
| `run_timeout_seconds`, `max_llm_calls`, `max_cost` | 미정 | live 실행 전 환경·모델 기준으로 승인·설정. 비어 있으면 live 시작 거절 |

**#82 승인 회차 산정:** 최초 수집 제외, Coverage retry 요청 **전에** 후보별 count 증가, 빈 결과/오류 batch도 소비하며 rollback하지 않는다. Company Research 적격성 보강은 별도 한도를 두는 미승인 안이며 Coverage count와 자동 합산하지 않는다. 네트워크 retry, LLM schema 수정, Evidence 재조사, 보고서 수정은 각각 다른 카운터다. 후보 A에서 B로 이동해도 A count를 지우지 않고 B는 0에서 시작한다. 승인 정책에 횟수·호출·비용·총시간 제한과 소진 경로를 명시한다. live에서는 값/승인/readiness가 비어 있으면 시작을 거절한다.

- coverage 부족 + 조사 여유: 부족 항목만 검색.
- Coverage 부족 + 재조사 2회 소진: missing 유지 후 freeze→평가→최종 결측 재계산으로 진행. #82 승인에 따라 요청한 재조사 batch는 empty/failure여도 각각 1회 소비하며, 평가 후 research loop를 만들지 않는다.
- `RECOMMEND_PRIORITY/RECOMMEND/WATCHLIST/PASS` 모두 결과 저장 후 다음 후보. index는 한 번만 증가한다.
- 사전 선정된 모든 후보 처리 뒤 selector: 적격·정상 평가 후보 중 RECOMMEND_PRIORITY 우선, 다음 RECOMMEND; 같은 label은 normalized_score 내림차순 → weighted_missing_pct 오름차순 → 원본 candidate_id 오름차순이다. 전부 WATCHLIST/PASS면 선택 없이 비교 보고서를 만든다. 입력 순서는 사용하지 않는다. 실패/unknown 후보를 추천으로 승격하지 않는다.
- 적격 후보가 한 건도 없으면 selected=None과 “투자 평가 가능한 적격 후보 없음” 사유 보고서를 생성한다(v3 D-3). 후보 0건·전부 부적격·전부 unknown을 구별한다. 적격이었으나 평가 실패한 경우는 “적격 후보 없음”과 다르다.
- 구조/의미 수정 2회 소진: Warning과 현재 draft/findings를 반환한다. 이는 **검증된 final 아님**이다. #82의 workflow_status=completed·final 금지는 유지한다. #166 이후 Python receipt의 warnings·acceptance·publication_allowed로 확인하며 CLI exit=2는 기존 호환 정보다. 새 workflow enum은 추가하지 않는다. 현재 fixture 연결과 목표 RunResult의 차이는 [실행 안내](python-execution.md)를 따른다.

Graph 전체 step 제한은 보조 안전장치다. 이를 정상 종료 정책이나 후보별 예산 대신 사용하지 않는다.

## 6. 실패와 관측

| 상황 | 처리 |
| --- | --- |
| 검색 0건 | `empty` 수집 기록, missing gap. 실제 조회 자체는 성공 |
| API timeout / 일시 오류 | 제한된 backoff 재시도; 초과 시 `ToolFailure` |
| 인증·권한 부족 | `unavailable`; 반복 로그인·무한 재시도 금지 |
| 일부 보조 도구 실패 | failure를 기록하고 대체 근거만 사용. 신뢰도를 지어내지 않음 |
| 핵심 RAG 미구축 / 정책 미승인 / 필수 provider 전부 실패 | 정상 투자 결과 대신 workflow failed; 진단 산출물 보존 |
| LLM 출력 schema 오류 | 제한된 구조 수정 1회 제안; 계속 실패하면 실패 표시. 검증 실패를 0점으로 대체하지 않음 |
| 평가 branch 실패 | wrapper가 failure envelope 반환, join이 오류를 저장하고 후보 archive → advance; 일부 성공만으로 추천 금지 |
| Business & Deal 일부 payload 실패 | 두 차원 전체를 branch failure로 처리, 어떤 일부 점수도 집계 금지 |
| Semantic Judge `fail` | context/upstream 파손은 fatal 종료. 수정 가능한 품질 문제는 `revise`이며 소진 시 Warning; 품질 실패를 fatal로 재분류해 Warning 경로 우회 금지 |
| 평가 snapshot 참조 누락 / 적격성 근거 무효화 | `SNAPSHOT_INVALID` 오류 저장, 해당 후보만 failed → archive → advance. 불완전 snapshot으로 평가 LLM을 호출하지 않음 |
| 보고서 context 참조 불일치 또는 upstream 평가·점수 오류 | CONTEXT_INVALID/UPSTREAM_INVALID로 workflow failed. 보고서 loop가 upstream 사실·평가·점수를 수정하지 않음 |
| PDF 렌더러 실패 / layout 위반 | 렌더러 장애는 진단·bounded retry 후 fatal 제안. layout의 공유 수정/소진 처리는 D08·D09 OPEN; 실제 PDF 미통과 상태로 final 발행 금지 |
| 실행 총예산/시간 소진 | 신규 호출 중지, 현재 결과·오류 보존, workflow failed. 검증되지 않은 보고서는 draft |

노드별 `run_id`, candidate, branch/dimension, step, duration, input/output ID, 모델·prompt·policy 버전, 사용량, 오류 코드를 남긴다. key와 비공개 원문은 로그에 기록하지 않는다. 모든 후보가 도구 오류로 실패했다면 “투자비추천”이 아니라 “조사 실패”다. 성공 평가 없는 경우의 selector/result 반환 형식은 D03·D08 OPEN이지만 기술 실패를 정상 투자 판단/검증된 final로 승격할 수 없다.

## 공식 기술 참고

아래는 이전 가이드가 2026-09-29에 남긴 참고 위치다. 이번 문서 정합화에서 외부 API 동작을 재검증하지 않았다. 설치 의존성은 lock에 있지만 v3 다섯 branch 실제 합류 검증은 후속 M1 업무다.

```text
[LG1] LangChain — Graph API overview, State / Reducers
https://docs.langchain.com/oss/python/langgraph/graph-api
[LG2] LangChain Reference — StateGraph.add_edge
https://reference.langchain.com/python/langgraph/graph/state/StateGraph/add_edge
```
