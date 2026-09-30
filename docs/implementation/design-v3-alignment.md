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

우선순위는 **새 사용자 지정 설계의 명시 내용 → 모순을 기록한 구현 목표 → 승인받아야 할 세부 제안**이다. v3 내부 충돌은 임의 해소하지 않는다. v3가 과제 필수 요구를 생략했다고 폐기하지 않는다. 예컨대 README 필수 항목·개인별 수행 역할(PM/PL 제외)·제출 파일명·DAY 3 상대 일정은 통합 원문 §10에서 계속 추적한다. v3와 이전 설계가 다른 것은 팀 승인 기록의 `SUPERSEDED` 전환과 다르다. D01–D14는 여전히 OPEN이다.

표지의 **울산 4반 2조 / 김근홍·정순욱·허지원·심혁·박태준·한유진**은 v3가 제공한 metadata다. 실제 역할·GitHub 계정 매핑·기여량 또는 달력상 제출일은 제공하지 않는다.

## 2. 현재 구현과 미병합 제안 snapshot

| 구분 | 확인한 기준 | 포함 / 포함하지 않는 것 |
| --- | --- | --- |
| main 구현 | `a0b3608e3c0fc1f1a41f3dd189ae8caaf7bd513c` | `pyproject.toml`·`uv.lock`, Python `>=3.11`, 빈 패키지와 offline import/의존성 API smoke test. 업무 DTO·Graph·CLI·실 RAG·평가·보고서 없음 |
| v3 목표 | §1의 HTML hash | Evidence Research 통합, 5 branch/6 dimension, 전 후보 처리·selector, 점수/보고서 목표. 아직 구현 증거 아님 |
| 팀 승인 | [결정 목록](decisions.md) D01–D14 OPEN | 사용자 지정 목표는 반영하지만 승인자·승인일·거절된 대안을 생성하지 않음 |
| 미병합 작업 | 아래 PR head와 이슈 snapshot | main 동작이 아니며 PR 작성자의 검증 보고도 #35의 재실행 결과와 별개 |

이 작업의 상위 검증 담당자가 보고한 baseline은 Python 3.12.14에서 `uv sync --frozen`, `ruff check`, `ruff format --check`(23 files), `pytest`(6 passed), `uv lock --check` 통과다. 이는 **기존 골격의 검증**이며 v3의 DTO·점수·Graph·PDF 테스트가 아니다. #35 최종 변경 head의 gate·독립 리뷰 결과는 해당 PR 검증란에 실제 실행 후 기록한다.

### 열린 PR 기준

| PR / 관련 이슈 | 상태·base / 확인 head | 정합화 영향과 증거 경계 |
| --- | --- | --- |
| #32 / #5 | OPEN Draft, main / `b0451007d4fa6a4ad59f1228c9bce63ac8c36c4b` | DTO 진행 중. snapshot의 changed files는 비어 있고 본문은 최종 검증 전이다. Coverage의 적용성·분모/%와 소비자 계약 협의 필요. 로컬 미푸시 구현은 조사/복사하지 않음 |
| #33 / #3 | OPEN Draft, main / `ca91eda9c85a7a24cbd9f3eb8c7041d8c6fec503` | decisions.md의 D01–D06·D08 승인기록 초안은 모두 OPEN. 모든 영역 저점수·첫 추천 종료·직렬 Deal·고정100·옛 예산 제안 재유입 방지. 같은 파일 충돌은 텍스트뿐 아니라 의미를 조정해야 함 |
| #34 / #11 | OPEN Draft, main / `732fdc0baeeef3525c90afc818b3a7a7695e2eb8` | 재무 rubric·설정은 proposed. pre-revenue/Rule of 40을 missing으로 고정한 부분은 C-3 적용성과 재논의. 단위·derived·동일 라운드 보호는 유지. non_positive_burn을 자동 N/A로 바꾸지 않음. 본문 35 passed는 작성자 보고이며 #35 재검증 아님 |
| #36 / #35 | OPEN Draft, main / 초기 source 보존 head `790c8dc667d421e341f782803778f9a07dcaaae8` | 이 문서 작업의 시작 기준. v3 source 보존만 담긴 당시 head이며 최종 문서·review·CI·병합 상태 주장이 아님 |

#32–#34 snapshot의 reviews/comments는 비어 있으며 팀 승인 근거가 없다. 이슈 #5의 과거 “PR #31 미병합” 댓글보다 이후 병합 확인 댓글과 위 main을 기준으로 한다. 다른 작성자의 PR 코드를 가져오거나 그 이슈를 완료 처리하지 않는다.

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

## 4. 열린 이슈별 영향과 acceptance migration

조사한 open issue snapshot은 **28개(#3, #5–#30, #35)**이며 번호 중복이 없다. 당시 assignee는 #3=`heojiwon2`, #5/#35=`luk0715`, #11=`XXXXXim`; 나머지는 미할당이다. 이는 실제 수업 역할·실명 매핑이 아니다. 아래는 **필요한 후속 AC 변경**이지 이미 해당 이슈 본문을 수정/승인/완료했다는 기록이 아니다. 알림·PR 통합·read-back은 #35 상위 작업에서 처리한다.

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

## 5. 전환 시 검증과 남은 차단

1. 이 문서의 이전 제안은 활성 지시로 재사용하지 않는다. 특히 PR #33/#34 병합 조정 때 고정100·모든 영역 저점수·첫 추천 종료·직렬 Deal·N/A 미채택 문구가 돌아오지 않게 의미 diff를 검토한다.
2. D02–D06·D08의 남은 질문을 정책/DTO fixture로 구체화하고, 승인되지 않은 선택을 실제 기본값으로 설치하지 않는다. D07/D09/D12/D13의 live·제출 gate도 보존한다.
3. [delivery §4](delivery.md)의 T01–T25는 목표 테스트다. #35의 문서 수치 계산·링크/정합성 검사는 구현 테스트·실 API·RAG 벤치마크·PDF 측정이 아니다.
4. source hash/바이트 일치, 원 catalog 비중, 설명용 산술을 확인했다. 최종 문서 diff·링크·잔존 옛 지시 검색과 프로젝트 gate·독립 리뷰는 PR 검증란에서 해당 head의 실제 결과를 추적한다.
5. `docs/raws/`, 보존 HTML, 협업문서, 애플리케이션·테스트·설정·의존성은 이 문서 변경으로 수정하지 않는다. main의 skeleton을 업무 구현으로, Draft PR을 병합/팀 승인으로 표시하지 않는다.
