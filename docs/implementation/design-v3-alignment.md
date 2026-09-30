# 설계 v3 정합화 — 출처·현재 기준·작업 영향

[문서 홈](../README.md) · [아키텍처](architecture.md) · [공통 계약](contracts.md) · [결정 목록](decisions.md)

**기준일: 2026-09-30 KST · 관련 이슈 #35 / Draft PR #36.** 사용자가 새 설계 입력으로 지정한 v3와 기존 구현 가이드·main·열린 작업을 대조한 문서다. 문서 정합화는 다른 이슈의 구현·정책 승인·테스트 통과·병합을 대신하지 않는다. 아래 GitHub 상태는 이 기준의 snapshot이지 이후 상태를 자동 갱신하는 목록이 아니다.

## 1. 출처와 우선순위

| 자료 | 보존·참조 방법 | 의미 |
| --- | --- | --- |
| 사용자 제공 `설계_산출물_최종본_v3.html` | [동일 바이트 보존본](../design/design-v3.html) | 새 구현 목표의 설계 입력. 원본 HTML 수정 금지 |
| SHA256 | `6596c47041cb925697ce4063270090d6673b1a23fb373ff2136ba2ee6b29983f` | 사용자 파일과 보존본을 직접 계산·비교하여 일치 확인 |
| 이전 설계·교수 과제 원문 | [통합 원문](../raws/robotics_startup_agentic_rag_notion_integrated.md) 및 `docs/raws/` | 읽기 전용. 이전 제안의 이력과 v3에서 생략한 과제 요구를 보존 |
| 파생 구현 가이드 | 이 폴더의 architecture/contracts/scoring/data-rag/delivery/decisions | v3 명시 목표와 승인 전 구현 제안을 구별. 원문을 조용히 수정하는 대체본이 아님 |

우선순위는 **사용자가 전환 승인한 v3의 명시 방향 → 남은 세부 정책의 명시적 승인 → 해당 버전의 구현·검증**이다. [승인 근거 #35 comment 5902877317](https://github.com/rice-steamed-water/skala-rag/issues/35#issuecomment-5902877317)(luk0715, 2026-09-30T02:29:07Z)이 baseline 계속 구현 대신 v3 정합화를 승인했다. 전 후보 selector, 5 branch/6 dimension·atomic Business & Deal, Missing/N/A 규칙, 네 label·market/technology 40%, 두 loop 최대2회·Warning, 다섯 보고서 섹션이 새 구현 방향이다. baseline D01–D06·D08 APPROVED·D09 부분 APPROVED·D14 OPEN 원본 기록과 기존 코드는 역사·호환성으로 보존한다. 전환 승인을 DTO 전체 필드·selector 정렬·0분모·적용성·회계·Warning 상태/PDF·rubric·provider·corpus·실행 예산의 승인으로 확대하지 않는다. 세부 OPEN과 정확한 정책별 대체 범위는 [결정 목록](decisions.md)에서 추적한다. v3의 생략은 과제 요구 폐기가 아니므로 README·실제 역할·제출 파일명·DAY 3 일정은 이전 원문 §10에서 계속 추적한다.

표지의 **울산 4반 2조 / 김근홍·정순욱·허지원·심혁·박태준·한유진**은 v3가 제공한 metadata다. 실제 역할·GitHub 계정 매핑·기여량 또는 달력상 제출일은 제공하지 않는다.

## 2. 현재 통합 cutoff와 historical GitHub snapshot

### 이번 upstream 통합 기준 — 24eaa36

충돌 해소 통합 기준은 `24eaa366f9ef0b3dce64b7d698542426878e45c7`다. 앞선 `c5a30f3`에 #6/PR #37(`b8eef06`)과 #68/PR #69(`1f23e09`), 이어 #8/PR #70·#12/PR #71·#21/PR #72가 추가된 Git 이력·코드를 확인했다. 이 cutoff 이후 main/이슈 상태를 추정하지 않는다. 아래 과거 API snapshot·44개 이슈 표는 그대로 역사 자료이며 현재 열린 작업 수를 뜻하지 않는다.

| 통합된 PR / 이슈 | 현재 코드·문서 범위 | v3 전환 경계 |
| --- | --- | --- |
| #42 / #15 | `graph/reducers.py`, State의 sources/chunks/evidence/evaluation_results/errors Annotated 연결; ID 멱등 병합·core 충돌 검증 | 업무 Graph wiring·revision controller는 별도. dimension 기반 평가 key를 v3 branch-key·atomic Business & Deal 계약으로 전환해야 함 |
| #64 / #16 | `aggregate_scores`→ScoreBreakdown, `decide`→Decision 순수 함수; 여섯 dimension/세대 검증, 고정100·관측 rating·세 label | N/A 제외·핵심차원40%·RECOMMEND_PRIORITY·selector 미구현; baseline 테스트를 v3 완료 증거로 쓰지 않음 |
| #66 / #53 | `scoring/finance.py`의 금액·기간, 월 burn·잔여 runway·성장률·매출총이익률·환율 파생 helper, Derived/Unavailable 및 provenance | rating·rubric/N/A 승인 아님. 전체 재무 정책/agent 구현과 구별 |
| #65 / #14 | reporting.md 및 D09 mode별 7개 섹션·인용·서지 미상·SV01–SV09 부분 승인 기록 | PDF 선택은 OPEN. 이후 #35 사용자 승인으로 v3 다섯 섹션·selector·Warning 방향을 채택; 세부는 OPEN |
| #40 / #10, #34 / #11 | core/finance rubric 문서·YAML·fixture 테스트 및 D14 제안 기록 | 병합 후에도 D14 OPEN. baseline 고정100/missing 규칙과 작성자 조건부 N/A 제안을 보존 |
| #37 / #6 | 평가·점수·판정·보고서·오류·manifest DTO, Decimal 왕복과 결정적 ID 인코딩 | dimension 단위·observed/missing·세 label 구조. v3 복합 branch/N/A/네 label·SelectionResult는 별도 확장 |
| #69 / #68 | `build_score_summary`·`build_investment_decision`으로 baseline 계산값을 #6 DTO에 연결 | adapter 병합은 v3 점수·label·selector 정책의 승인·구현이 아님 |
| #70 / #8, #71 / #12, #72 / #21 | ToolResult·Protocol·fake 주입, 공통 가상 fixture/loader, snapshot 복사·참조 검증 | Protocol≠live adapter. EvaluateDimension/기존 DTO는 baseline이며 v3 복합 branch·selector 계약은 별도. 가상 검증은 live 실측·v3 정책 승인이 아님 |

#5 DTO/#7 State/#9 draft catalog 위에 이 구현이 추가되었다. 업무 Graph·CLI·live RAG·평가 agent·보고서 출력이나 v3 세부 정책 승인이 완료된 것은 아니다. v3 전환 방향의 별도 사용자 승인은 §1과 구별한다. 이 문서 통합은 upstream 코드·tests·configs를 수정하지 않는다. 최종 delivery head·gate·리뷰 결과는 PR 검증 기록에서 별도로 보고한다.

### 후속 승인·작업 snapshot — PR36 Ready 준비 시점

2026-09-30의 후속 supplied snapshot은 열린 이슈 34개다. 아래 과거 44개 열린 이슈와 48개 영향표 행은 역사 자료로 그대로 보존하며 현재 수량으로 읽지 않는다. #35 comment 5902877317의 2026-09-30T02:29:07Z 사용자 전환 승인은 §1 및 별도 V3-TRANSITION 기록에 반영했다.

- #73 / PR #74: 독립 `skala_rag.contracts.v3` 구조 DTO·atomic branch·offline fixture 후속 작업. snapshot의 PR 상태는 OPEN, head `97ab903c14e1d2635735509fdbbba9e8300b3397`, mergedAt=null이다. 이는 제안/진행 중 계약이지 이 문서의 통합 cutoff에 구현된 public API가 아니다. 기존 baseline DTO·State를 바꾸거나 그 PR의 코드를 가져오지 않는다. 정책 계산·selector·Warning enum·live 예산은 그 구조 작업으로 승인되지 않는다.
- #67: #53의 재무 helper 이후 실제 IR/공시 Evidence를 T21 검증에 연결하는 후속 작업이다. 승인된 자료·#43 provider/예산 gate·#50 추출 연계가 필요하며 재무 rubric 승인을 대신하지 않는다.

### 이전 API 조회 snapshot — 85fa030 (historical)

**확인 기준: 2026-09-30 11:04 KST; 통합한 main 커밋 `85fa0304b664f07ce54c9b0a1e8d9b95b144a6c8`.** 아래 GitHub 정보는 이 시점의 API 조회 snapshot이며 이후 상태를 뜻하지 않는다. 문서 정합화는 다른 PR의 구현·검증·병합을 대신하지 않는다.

| 구분 | 현재 확인 | 경계 |
| --- | --- | --- |
| main 구현 | #5/PR #32, #7/PR #39, #9/PR #38 draft catalog, #3/PR #41 baseline 승인 기록이 pinned main `85fa030`에 병합됨 | #38은 draft catalog/config/fixture 범위일 뿐 `aggregate_scores`·`decide`·v3 replacement를 구현하지 않는다. Graph/reducer wiring, CLI, live RAG, 평가·점수·보고서는 이 사실만으로 구현됐다고 말할 수 없다 |
| #3 승인 근거 | issue #3 comment [5902473875](https://github.com/rice-steamed-water/skala-rag/issues/3#issuecomment-5902473875), 2026-09-30T01:49:36Z, xxhigh | D01–D06·D08 **baseline**만 승인 기록. v3 replacement와 D07·D09–D14, live 총시간·LLM 호출·비용 상한은 자동 승인 아님 |
| PR #41 | MERGED 2026-09-30T01:52:47Z, merged head `7b9f6cee8f36a016d5f47191b41999430390e6ee` | baseline 승인 기록만 main에 반영했다. v3 replacement 정책 승인을 대신하지 않는다 |
| #35 / PR #36 | #35 assignee `luk0715`; PR #36 OPEN Draft, 이 작업 시작 head `790c8dc667d421e341f782803778f9a07dcaaae8` | source 보존/문서 정합화 작업. 이 문서의 이후 변경, review, gate, merge를 그 head에 소급해 주장하지 않는다 |
| 열린 작업 | supplied snapshot 기준 open issues 44개 | 아래 28개 영향표는 historical snapshot이며 현재 assignment/count를 나타내지 않는다 |

### historical pinned PR 상태 요약

| PR | 상태 / pinned head | 정합화 경계 |
| --- | --- | --- |
| #32, #38, #39, #41 | MERGED (`0eb1c9039268b47720f41e0b89261f21290d7422`, `552cd5b3d5d1f8513ebccaaf8127d9c9553b387f`, `28961c229c7bba069f7d4f79141cc74ede88bcae`, `7b9f6cee8f36a016d5f47191b41999430390e6ee`) | DTO/state, draft catalog fixture, baseline approval record; v3 replacement 미구현 |
| #33, #34, #36, #37, #40, #42, #63, #64, #65 | OPEN (`ca91eda9c85a7a24cbd9f3eb8c7041d8c6fec503`, `813cf70cd3f0cc5616f5bd425dfdfb4b2130078b`, `790c8dc667d421e341f782803778f9a07dcaaae8`, `ed8f9967b51c6347f67eb819dc5a4446d3a1b50e`, `48c0aa90b8c70c690f5f5f365f23eca6867db502`, `4758c57627c36c2cee15197e00394295ec539211`, `14540847499fee7a03d541bbe3638969d46fd8ab`, `a9175103f97e416deb4440bcca75ab1deb801697`, `f5471db6f88783c5bb679f8320a7a6da457cd033`) | 각 제안/진행 작업은 v3 승인·구현 증거가 아님 |


### historical snapshot 당시 열린 PR 영향

- #37/#6, #40/#10, #34/#11, #42/#15, #63/#20, #64/#16, #65/#14는 OPEN pinned heads이며 v3 구현/승인을 뜻하지 않는다. #38/#9 draft catalog는 MERGED이나 runtime fixture-only이고 `aggregate_scores`는 #64 OPEN이다. 이 문서는 어느 PR의 코드를 복사하거나 완료 처리하지 않는다.
- PR #41의 baseline 기록과 v3의 newer user-selected design input이 충돌한다. baseline을 지우지 않고, `v3-OPEN` 질문으로 별도 승인·supersession 범위를 요구한다.

## 3. v3 절별 추적과 이전 계약의 변경

아래 `L`은 §1 SHA256으로 고정한 **HTML 원본 행**이다. 브라우저 자동 anchor를 가정하지 않고 절 제목과 행을 함께 제공한다. 문서에 없는 새 DTO/함수명까지 원문 요구로 표시하지 않는다.

| v3 절 / HTML 위치 | 원문 목표·확인 사항 | 이전 계약 → 파생 문서·검증 / OPEN |
| --- | --- | --- |
| 표지 L227–229; A-1 L240; A-2 L256 | Physical AI / Robotics, LangGraph Multi-Agent Agentic RAG; 비상장·Seed~C·Exit 미완료·최소 Evidence 확보 가능 | [문서 홈](../README.md), [scoring §1](scoring.md), delivery §7. 최소 Evidence gate는 Coverage와 별도 D05·D06; 역할·기한 D10·D11 |
| B-1 L291, 특히 L355–363·482–497 | Evidence Research가 초기/gap RAG·Web/API 수집을 모두 담당; 평가/보고서는 검색 안 함 | 별도 Targeted Research 책임 제거 → architecture §1–§3, contracts §7, data-rag §1. T07/T13 |
| B-1 L408–423; D-2 L1307–1319 | Founder/Market/Technology/Moat/Business & Deal 다섯 branch; 마지막은 실적+투자조건 | 직렬 Deal Terms 제거 → contracts §4/§6/§7의 atomic 두-dimension payload, architecture §4. D04; T01/T06/T22 |
| B-1 L442–449; D-3 L1341–1359 | 모든 후보 검증·적격 후보 평가 후 selector. 무적격은 selected=None+사유 보고서 | 첫 추천 조기종료 제거 → architecture §2/§5, contracts SelectionResult/ReportInput, scoring §5. D03 순위·동점·all-WATCHLIST/PASS·성공 평가 없음 OPEN; T08/T09 |
| B-2 L502–620 | Primary RAG, 문서 우선순위, 전체 200페이지, 문서별 chunk | data-rag §1/§3/§4. 공식 기업자료→논문/공공/시장→기사/인터뷰; 구조·슬라이드·표 맥락·청구항. D13; T11–T13 |
| B-3 L623–837 | BGE-M3 1차 선택, e5/KURE 비교; 같은 Chunk/Query; Hit Rate@1/3/5·MRR·교차언어; dense 우선/hybrid 필요 시 | data-rag §5, D07. 이전 Jina/OpenAI 비교 중심은 과거 참고로 분리; 실측·라이선스/readiness 완료 주장 없음 |
| C-1 L843–906 | 23개 criterion, 여섯 비중 5/30/25/20/10/10 | scoring §2 catalog 보존. D01·D14; T02 |
| C-2 L909–961 | 1..5 비례 환산; 근거 부족을 N/A라고 표기 | scoring §3, contracts CriterionAssessment. C-3 해당 없음과 충돌하여 세 machine status 분리 제안(D05); T01 |
| C-3 L964–1003 | Missing은 분모 포함; 해당 없음만 제외; 결측률≥30% 보류 | fixed100/N/A 미채택안 제거 → scoring §3/§4, contracts Coverage/ScoreSummary. normalized_score와 observed_score, weighted_missing_pct와 missing_weight 구별; 0분모 D05; T03 |
| C-4 L1006–1069 | 네 label; market/technology만 적용가능 배점 대비 획득≤40% 보류 | 모든 영역의 관측 rating 평균 규칙 제거 → scoring §5, contracts labels/dimension_scores, delivery T03. 소수 구간·다중 reason D02 |
| D-1 L1075–1286 | State producer/consumer/reducer, 후보별 retry, workflow_status 세 값 | contracts §6의 단독 writer·branch/dimension key·불변 snapshot·참조 폐쇄성 보완. 새 enum 승인 아님; D04·D08; T05/T25 |
| D-2 L1290–1335; D-3 L1363–1370 | Coverage 재조사 최대2회 후 부족해도 평가; 다섯 결과 fan-in | architecture §2/§5, delivery T07. 평가 후 Missing Check→research loop 제거; 회차/Company Research/네트워크 retry 계정 D08 |
| D-3 L1373–1376 | 구조·의미 수정 최대2회 후 Warning과 현재 결과 반환 | 일괄 failed 종료안 수정 → contracts §5, architecture §5/§6, delivery T16. 결과/acceptance/publication 분리, status/CLI/manifest·layout D08/D09 OPEN |
| E-1 L1391–1455; E 서문 L1382 | 정확한 다섯 목차, 전체≤5페이지·SUMMARY≤반 페이지 | delivery §5, contracts ReportContext, T14/T17. 실 PDF 측정·hash 일치 유지; 무적격/Warning 예외 D09 |
| E-2 L1458 이후 | 기관 보고서·논문·웹 Reference, 실제 사용 자료만 | delivery §5, contracts §3/§5. Evidence↔Source↔REFERENCE 양방향 일치; T14/T23 |

### 유지하는 안전 계약

- Source/Chunk/Evidence 식별과 provenance, 실제 retrieval_id/chunk_id→Evidence→평가 snapshot→인용 trace. method만 rag로 바꾸는 것은 금지한다.
- Evidence 식별 core 충돌 거절, 허용된 provenance 병합의 멱등성, 불변 snapshot payload와 참조 폐쇄성, 후보·세대·policy 격리.
- 기술 failure와 missing/not_applicable 분리. business_deal의 한 차원만 유효해도 성공 집계하지 않는다.
- Reporter/Validator/Judge는 같은 고정 context와 현재 draft hash를 사용하며 검색하거나 upstream 점수를 고치지 않는다. context 파손은 fatal이며 Warning 품질 경로로 덮지 않는다.
- 실제 PDF 분량·인용 검증, 재배포/200페이지/as_of/권한·비밀 보호, fixture와 실측 구별.

## 4. historical 열린 이슈 snapshot의 acceptance migration

아래는 refresh 이전의 **28개(#3, #5–#30, #35) historical snapshot**이며, open-issue count/assignee 역시 §2의 과거 API snapshot 범위에 한정된다. 당시 assignee는 #3=`heojiwon2`, #5/#35=`luk0715`, #11=`XXXXXim`; 나머지는 미할당이었다. 이는 실제 수업 역할·실명 매핑이 아니다. 아래는 **필요한 후속 AC 변경**이지 이미 해당 이슈 본문을 수정/승인/완료했다는 기록이 아니다. 알림·PR 통합·read-back은 #35 상위 작업에서 처리한다.

| 이슈 / snapshot 작업 | 필요한 acceptance migration | 문서·결정 / 검증 |
| --- | --- | --- |
| #3 M0 baseline 승인기록 / PR #33 | D02 핵심차원, D03 전 후보+selector, D04 복합 branch, D05 N/A 분모, D08 Warning/회차로 논의 입력 갱신; 승인 전 OPEN 유지 | decisions; 정책 주입/미승인 live 차단 |
| #5 입력·후보·근거·수집 DTO / PR #32 | CoverageResult에 적용성·applicable_weight·weighted_missing_pct 포함 협의. 기존 재무/날짜/provenance 보호 유지; 본 작업은 DTO 코드 수정 아님 | contracts §1–§4; T01 |
| #6 평가·점수·보고서 DTO | 세 criterion status, 네 label, 5 branch-key envelope/6 dimension-key map, SelectionResult·RunResult 제안 검토 | contracts §4–§7; D04/D08; T01/T22 |
| #7 InvestmentState·초기화 | Iterator current ID와 selector selected ID writer 분리; branch/result key·Warning 별도 결과. workflow enum 임의 추가 금지 | contracts §6; D03/D08 |
| #8 주입 Protocol·실패 타입 | Evidence Research 공통 경계, evaluate_branch/join/selector/finalize 인터페이스로 정합화. fake와 missing/기술오류 구별 유지 | contracts §7; T10 |
| #9 catalog·정책 fixture | 원 23개/비중 보존, N/A 분모·정규화·핵심40%·네 label·소수/0분모/30% 경계 예시로 교체 | scoring §2–§6; D01/D02/D05; T02/T03 |
| #10 Founder/Market/Technology/Moat rubric | 공통 anchor에 맞춰 관측·missing·적용성 근거 구분. 비핵심 저점수 강제보류 기대값 제거 | scoring §3; D14; T01/T03 |
| #11 재무 rubric / PR #34 | pre-revenue·Rule of 40을 missing 고정하지 않고 C-3/D14 적용성 검토. burn·런웨이 단위/동일 라운드/derived 보호 유지 | scoring §3/§4; D05/D14; T21 |
| #12 공통 가상 fixture | 여섯 Evaluation은 유지, business_deal 두 차원을 포함한 다섯 envelope·3상태·4 label fixture 추가 | contracts §4; T01/T06 |
| #13 corpus·모델 접근 점검 | Jina 중심 점검 대신 BGE-M3/e5/KURE 후보군과 B-2 자료 우선순위 반영. 페이지·권한 gate 유지; 모델 비교 실험은 M2 | data-rag §1/§3/§5; D07/D13; T11 |
| #14 보고서 목차·인용 합의 | E-1 다섯 제목으로 교체. 무적격/Warning 예외·서지 누락·검증 체크리스트 합의 | delivery §5; D09; T14/T17 |
| #15 State reducer | provenance/core 충돌·멱등 보존. branch-key 결과와 dimension-key 성공 map 타입 영향 검증 | contracts §3/§6; T05 |
| #16 aggregate_scores·decide | “재정규화 금지”를 Missing 제외 금지/N/A 제외 정규화로 변경. market/technology 적용가능 분모≤40%, 네 label | scoring §3/§5; T02/T03 |
| #17 후보 탐색·Normalize | 후보 상한은 OPEN 정책 주입. 확정 normalize 후보는 모두 처리하고 최초 추천으로 자르지 않음. 회사 식별·discovery 출처 유지 | architecture §3/§5; T24 |
| #18 Company Research·Eligibility | 최소 Evidence 확보 가능성과 Coverage 구별, unknown 경로/예산 승인. TIPS·상장 검색0건 자동 적격 금지 | scoring §1; D05/D06/D08; T04 |
| #19 fixture retrieve·Evidence Collector | 작업 책임명을 Evidence Research로 정합화하고 초기/gap 공통 collect_evidence 사용. 실제 RAG provenance·as_of 유지 | data-rag §1/§4; T12/T13 |
| #20 Coverage·ResearchGap | 원배점 missing_weight<30 대신 적용가능 분모의 weighted_missing_pct 기준을 정책으로 주입. N/A/0분모·readiness gate 추가 | scoring §4; D05; T03/T07 |
| #21 Freeze snapshot | payload 불변·참조 폐쇄성·세대식별 유지. 사후 재평가 loop를 기본 요구로 추가하지 않음 | contracts §4; T25 |
| #22 evaluate_dimension wrapper | 외부 경계를 evaluate_branch로 재검토. Business & Deal 두 payload atomic success, criterion 세 상태 검증, 기술 실패 별도 | contracts §4/§7; D04; T01/T22 |
| #23 Graph 후보 loop | 첫 RECOMMEND→single report/not_evaluated AC 제거. 네 label 모두 advance, 전 후보 후 selector, 무적격/전부 실패 구별 | architecture §2/§5; D03; T08/T09 |
| #24 병렬 평가·직렬 Deal Terms | 제목/AC의 직렬 Deal 제거, founder/market/technology/moat/business_deal 다섯 fan-out, 모두 terminal 후 여섯 payload 승격 | architecture §4; D04; T06/T22 |
| #25 사전·사후 재조사 loop | 별도 Targeted Research·평가 후 Missing Check 재진입 AC 제거. Coverage→Evidence Research 최대2회 후 평가로 한정; 회차 정책 명시 | architecture §5; D08; T07/T20 |
| #26 build_report_context | selector 결과와 전 후보 outcome·비교 score 참조 검증. 여섯 차원 payload·동일 context/참조 폐쇄성 유지 | contracts §5; T23 |
| #27 Structural Validator | E-1 다섯 섹션·네 label·정규화/N/A/% 표기 검사. hash·인용/REFERENCE 양방향 일치 유지 | delivery §5; T14 |
| #28 fixture 보고서·Judge 수정 loop | 구조/의미 공유2회 뒤 Warning+현재 결과 반환. context fatal과 revise 소진 분리, 미검증 final 승격 금지; layout 정책 조건부 | contracts §5; D08/D09; T16 |
| #29 CLI·산출물·RunManifest | draft/Warning/validated final 분리, 실제 검증·미실행 표시와 hash 보존. workflow/CLI/acceptance 매핑 승인 전 기본값 금지 | contracts §5; D08; T19/T20 |
| #30 M1 E2E trace | 첫 추천 뒤 후속 후보, 우선추천 비교·순서 불변, N/A/부분핵심결측, 복합 branch 부분실패, Warning 소진을 시나리오에 반영 | delivery §3/§4; T03/T06–T09/T16/T22 |
| #35 문서 정합화 / PR #36 | 원문 보존·정합화·영향표·OPEN 경계·검증을 완료 범위로 한정. 다른 이슈 승인·구현·병합 대행 없음 | 이 문서 및 관련 가이드; PR의 실제 gate/리뷰 기록 |

### historical M2 이슈의 수용 조건 대조

기준 snapshot의 #43–#62 본문은 이미 v3와 #35를 참조하고 미승인 정책의 live 연결을 차단한다. 따라서 전체를 구설계로 분류하지 않는다. 아래는 본문 확인 후 추가로 대조할 세부 항목이며, 다른 담당자의 이슈를 수정하거나 승인한 기록이 아니다. 역사 표에서 당시 닫힌 #3·#5·#7·#9를 제외한 24개와 아래 20개가 기준 시점의 열린 이슈 44개에 대응한다.

| 당시 이슈 | 본문에 이미 반영된 범위 / 남은 대조 | 문서·결정 / 검증 |
| --- | --- | --- |
| #43 live 사전 승인 | 실험 승인과 최종 모델 선정 분리 유지; baseline D08과 v3 회계·Warning 대체 범위를 별도 기록 | decisions D07/D08/D12; data-rag §5 |
| #44 corpus gate | 전체 200페이지·승인·불변 manifest·교체 검증 유지; 이번 정합화로 승인 상태 변경 없음 | data-rag §3; D13/T11 |
| #45 adapter runtime | readiness·실패 분리·단일 retry 소유자·공유 호출 예산 유지; v3의 조사 회차와 transport retry 구별 | architecture §5; contracts §7; T10/T20 |
| #46 safe fetch | URL/redirect/경로·크기 제한, Source snapshot·as_of 유지; 이번 정합화로 안전 경계 변경 없음 | data-rag §6; T18 |
| #47 LLM adapter | wrapper/controller의 ID·schema 소유권과 실패 분리 유지; 확정될 5 branch-key/6 dimension payload를 소비 | contracts §4/§7; D04/T01/T22 |
| #48 discovery | 승인 provider·범위·출처·Normalize 유지; 후보 상한의 baseline/v3 정책 버전을 명시 | architecture §3/§5; D08/T24 |
| #49 extraction/chunking | 페이지·구조·표 맥락 보존 반영됨; document class/year·slide/patent metadata 확장 shape를 추가 대조 | contracts §1; data-rag §4; T12 |
| #50 Evidence extraction | 실제 RAG 참조·core/provenance 병합·불신 입력 경계 유지; 출처 class를 Evidence에 임의 복제하지 않음 | contracts §3; T05/T13 |
| #51 Company Research | unknown·TIPS·동명 기업 보호 반영됨; v3 최소 Evidence gate와 Coverage의 별도 책임을 추가 대조 | scoring §1; D05/D06/T04 |
| #52 embedding/index | 승인 실험·model revision·index 격리 반영됨; 입력 길이/절단 설정도 실험 기록과 일치시킴 | data-rag §4/§5; D07 |
| #53 finance Evidence | 실제 단위·기간·파생값·분모 검증 유지; Missing/N/A 적용성 승인을 임의로 대신하지 않음 | scoring §4; D14/T21 |
| #54 retrieval | 기업·출처·as_of·cache 격리 반영됨; 승인될 doc_type/year filter와 반환 Chunk/cache 검증을 추가 대조 | contracts §1/§7; data-rag §4; T12 |
| #55 Evidence Research | 초기/gap 단일 진입점·평가 후 loop 금지 반영됨; 승인될 회차 소비/Company Research 회계와 정합화 | architecture §5; D08/T07/T25 |
| #56 embedding 비교 | 동일 corpus/query·Hit Rate@1/3/5·MRR·교차언어 실측 반영됨; 입력 길이/절단·통합/운영 복잡도도 비교표에 추가 | data-rag §5; D07 |
| #57 Technology 평가 | snapshot-only·실제 RAG trace·Missing/N/A 반영됨; 새 wrapper envelope와 승인 rubric을 사용 | contracts §4; T01/T13/T22 |
| #58 Founder 평가 | 인물 귀속·snapshot-only·실패 분리 반영됨; 비핵심 저점수를 전체 강제보류로 확대하지 않음 | scoring §3/§5; T01/T22 |
| #59 Market 평가 | 시장 맥락·snapshot-only 반영됨; 평가에서 label을 만들지 않고 집계기의 적용가능 배점 40% 규칙과 구별 | scoring §3/§5; T01/T22 |
| #60 Moat 평가 | 특허/비교 근거·snapshot-only 반영됨; 비핵심 저점수를 전체 강제보류로 확대하지 않음 | scoring §3/§5; T01/T22 |
| #61 Business & Deal | 단일 branch·두 배점·직렬 Deal 제거 반영됨; 두 payload의 atomic success/실패와 N/A 근거 검증을 추가 대조 | contracts §4; D04/D05/T22 |
| #62 M2 trace | 실제 검색→Evidence→Technology와 M3 보고서 검증 분리 반영됨; 갱신된 T12 metadata·실험 항목과 정책 버전 대조 | delivery §3/§4; T12/T13/T25 |

## 5. 전환 시 검증과 남은 차단

1. baseline 승인 기록은 보존하되 v3 목표 절에 고정100·모든 영역 저점수·첫 추천 종료·직렬 Deal·N/A 미채택을 섞지 않는다. 새 작업은 #35 사용자 승인에 따라 v3 방향으로 진행한다. 방향 승인을 미명시 세부의 일괄 승인으로 읽지 않으며 관련 PR 통합 시 적용 정책 버전과 정확한 대체 범위를 대조한다.
2. D02–D06·D08의 남은 질문을 정책/DTO fixture로 구체화하고, 승인되지 않은 선택을 실제 기본값으로 설치하지 않는다. D07/D09/D12/D13의 live·제출 gate도 보존한다.
3. [delivery §4](delivery.md)의 T01–T25는 목표 테스트다. #35의 문서 수치 계산·링크/정합성 검사는 구현 테스트·실 API·RAG 벤치마크·PDF 측정이 아니다.
4. source hash/바이트 일치, 원 catalog 비중, 설명용 산술을 확인했다. 최종 문서 diff·링크·잔존 옛 지시 검색과 프로젝트 gate·독립 리뷰는 PR 검증란에서 해당 head의 실제 결과를 추적한다.
5. `docs/raws/`, 보존 HTML, 협업문서, 애플리케이션·테스트·설정·의존성은 이 문서 변경으로 수정하지 않는다. main의 skeleton을 업무 구현으로, Draft PR을 병합/팀 승인으로 표시하지 않는다.
