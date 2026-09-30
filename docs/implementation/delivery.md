# 작업 분담, 구현 순서, 검증과 제출

[문서 홈](../README.md) · [아키텍처](architecture.md) · [계약](contracts.md) · [결정 목록](decisions.md)

**상태: v3 목표에 맞춘 승인 전 실행 계획.** [v3](../design/design-v3.html)의 명시 목표·현재 main 구현·OPEN 정책은 [정합화 기록](design-v3-alignment.md)으로 구별한다. 아래 WP는 구현 묶음이지 사람·역할 배정이 아니다. 실제 작업은 GitHub 이슈로 나누고 assignee 규칙을 따른다([협업 규칙](../../CONTRIBUTING.md#이슈로-일하기)).

## 1. 작업 패키지

| WP | 구현 범위 | 다른 작업에 넘길 것 | 완료 조건 |
| --- | --- | --- | --- |
| WP1 Contracts / Graph | 공통 schema, reducer, graph wiring, 전 후보 loop·selector, Coverage 조사·보고서 수정, 예산 | schema와 fixture, graph trace, runner | 유한 종료·5 branch/6 dimension 합류·상태 격리·Warning/acceptance 분리 |
| WP2 Discovery / Eligibility | 후보 탐색/정규화, 기업 조사, 상장·Exit·단계 근거 | Candidate, CompanyProfile, EligibilityResult, ToolResult | 적격/부적격/unknown/동명 기업 fixture와 live adapter 확인 |
| WP3 Evidence / RAG | 코퍼스 manifest, 추출·chunk·embedding·검색, 근거 병합 | Source/Chunk/Evidence, retrieve adapter, 모델 비교 기록 | 200페이지 gate와 실제 검색→평가 연결 증거 |
| WP4 Evaluation / Rubrics | Founder/Market/Technology/Moat rubric·prompt·구조화 출력 | 영역별 Evaluation과 rubric fixtures | 근거 없는 rating 거절, missing·상충·다른 기업 오염 테스트 |
| WP5 Finance / Scoring | Business & Deal branch의 traction/deal_terms rubric·평가, deterministic score·decision | 여섯 차원 집계 계약, 정책, 숫자·라벨 테스트 | Missing/N/A·정규화·핵심차원40%·네 label·단위 검증 |
| WP6 Reports / Integration QA | ReportInput/ReportContext, 생성·구조·의미 검증, PDF, README, 제출 묶음 | Markdown/PDF renderer, validation manifest, 재현 절차 | 5페이지·SUMMARY·REFERENCE·근거 일치 및 clean run 확인 |

`contracts/`·`graph/`·`configs/`·`pyproject.toml`·`uv.lock`은 여러 이슈가 함께 쓰는 공통 파일이다. 바꾸는 PR은 변경 내용을 본문에 명시하고, 영향받는 열린 이슈에 알리고, fixture를 함께 바꾼다([공통 파일](../../CONTRIBUTING.md#공통-파일)). 재무 Evidence 수집은 WP3의 adapter 계약을 사용하고, 의미·단위 검증은 WP5 범위다.

## 2. 디렉터리 구조와 현재 구현

채택한 디렉터리 구조와 공통 파일 규칙은 [CONTRIBUTING.md](../../CONTRIBUTING.md#폴더-구조)에 있다.

main의 `pyproject.toml`은 Python `>=3.11`과 직접 의존성·uv/ruff/pytest를 명시하고 `uv.lock`이 설치 해석 결과를 고정한다. 빈 패키지와 offline import/의존성 API smoke test만 제공하며 업무 DTO·Graph·CLI·실 RAG·보고서는 없다. 설치·기존 테스트 명령은 [루트 README](../../README.md)에 있다. `CONTRIBUTING.md`의 “첫 코드 PR에서 만든다”는 시점 문구보다 현재 파일과 [기준 SHA](design-v3-alignment.md)를 본다. 그 협업문서는 #35 수정 범위 밖이다.

Vector store, LLM provider/model, 최종 embedding, PDF renderer는 미정이다. BGE-M3의 v3 1차 선택이 모델 설치·벤치마크·D07 최종 승인을 뜻하지 않는다. WP1의 fixture 실행 명령은 실제 구현·검증 후 Usage에 추가한다.

## 3. 구현 순서와 병렬화

### M0 — 공통 계약과 정책 합의

**선행:** v3·과제 원문·정합화 기록을 읽고 D01–D06·D08·D14의 구현 정책을 승인한다. D09 목차/인용/Warning 예외는 보고서 fixture 전, renderer는 M3 전 합의한다. D07·D12·D13은 해당 live 기능 전에 해소한다. 미승인 질문은 주입된 fixture 정책으로만 검증하고 live 기본값으로 설치하지 않는다.

- WP1: schema, catalog interface, mock Tool/LLM, failure 타입, 최소 실행환경 설정.
- WP4/WP5: 23개 criterion rubric, missing/not_applicable 조건, 정규화·네 label·핵심차원 fixture.
- WP3: 코퍼스 후보와 페이지 산정 표, 접근 가능한 모델/자료 점검.
- WP6: 출력 목차, 인용 표기, 검증 체크리스트 합의.

**완료:** 다른 WP가 동일 fixture를 읽고 타입 검증할 수 있다. 정책 승인 기록이 있고 M1 작업이 이슈로 나뉘어 있다. 새 문서를 작성한 것만으로 M0 완료가 아니다.

### M1 — fixture 기반 전체 세로 흐름

네트워크·유료 모델 없이 다음을 한 번 끝까지 연결한다.

```text
모든 fixture 후보 → 후보별 적격성/근거 → Coverage 재조사(최대2회)
→ 5개 branch(Business & Deal 포함)의 여섯 차원 → 집계/네 label → 다음 후보
→ 후보 소진 → 주입 selection policy → fixture 보고서
→ 구조·의미 검증 stub / 최대2회 수정 → 결과·Warning·검증 기록 구분 저장
```

WP1·WP5·WP6가 우선 통합하고 WP2–WP4는 같은 인터페이스에 맞춘다. fixture 출력에는 `execution_mode=fixture`를 표시한다.

**완료:** 정상 추천(첫 추천 이후도 처리), 보류 후 다음 후보, 모두 비추천, 재조사 소진 후 평가, 보고서 수정 소진 후 Warning의 trace가 있다. 추가로 selector 순서 불변·동점 정책과 Business & Deal 부분 실패를 검사한다. stub Judge 통과나 render stub은 실모델 품질·실제 PDF·실 RAG 증거가 아니다.

### M2 — 실제 수집과 RAG 연결

WP2/WP3가 독립적으로 adapter를 연결하되, 승인된 corpus의 실제 RAG→Evidence→Technology 경로부터 성공시킨다. WP4/WP5는 실제 근거에 대한 structured output을 연결한다.

**완료:** 도구별 readiness 기록, 실제 Chunk·Evidence ID·출처 페이지, 모델 비교 기록이 있다. key 없는 도구의 실패가 명시되며 테스트는 외부 API 없이도 실행된다.

### M3 — 전체 live 평가와 검증

WP1이 예산 제한을 적용한 runner로 통합한다. WP6는 real Report Generator/Judge, PDF renderer, 인용 검증을 연결한다.

**완료:** 허용된 실제 자료·오픈소스 embedding으로 생성된 보고서를 실제 PDF로 확인하고 manifest에 남긴다. 합리적으로 추천 후보가 없을 수도 있다. 데모를 위해 추천 라벨이나 자료를 꾸며내지 않는다.

### M4 — 재현·제출

작성자가 아닌 팀원이 깨끗한 환경에서 README만 따라 재실행한다. 코드·설계·README·보고서가 같은 policy/model/corpus version을 설명하는지 검토한다.

**완료:** 설치·실행·검증 로그, 제출 파일, 기여 역할, 미지원/실패 조건이 확인된다. 모든 숫자는 측정값이며 placeholder가 남지 않는다.

## 4. 공통 테스트 계획

아래는 **구현할 테스트 목록**이지 현재 저장소에서 실행한 결과가 아니다.

| ID | 계층 / 상황 | 통과 기준 | 관련 WP |
| --- | --- | --- | --- |
| T01 | schema / 관측·결측·해당 없음 | observed의 rating·근거 필수, missing/not_applicable rating=null과 각 사유·적용성 근거, 없는 ID 거절 | WP1/WP4 |
| T02 | policy / 가중치 | criterion ID 유일, 합 100, 영역 합 일치 | WP5 |
| T03 | policy / 경계·우선순위 | scoring 설명용 예시를 조건부 fixture로 구현; 정규화와 원배점 분리, 네 label·소수·30%·핵심40%·비핵심 저점수·부분핵심 Missing·0분모 guard | WP5 |
| T04 | identity / 회사·단계 | 동명 기업 분리, TIPS/unknown 자동 적격 금지, Exit 제외 | WP2 |
| T05 | merge / 재실행 | Web→RAG 재발견은 하나의 Evidence와 두 provenance로 병합; confidence·criterion 등 해석 차이는 병합 규칙 적용; 재삽입 멱등, 식별 core 충돌은 오류, 기존 snapshot 불변 | WP1/WP3 |
| T06 | graph / 병렬 합류 | 다섯 branch terminal result→여섯 dimension 성공 승격, business_deal은 두 payload 필수; 이전 세대·다른 후보 무효 | WP1 |
| T07 | graph / 재조사 | Coverage→동일 Evidence Research 최대2회 후 평가; 평가 후 gap으로 재진입 없음; 초기 제외/오류회차 등 주입 정책 확인; 후보별 count·이력 격리/보존 | WP1 |
| T08 | graph / 후보 이동 | 네 label 모두 archive/advance; 첫 RECOMMEND 뒤 후속 RECOMMEND_PRIORITY도 처리; index 한 번 증가, selector는 전 후보 소진 후만 실행 | WP1 |
| T09 | graph / selector·고갈 | 승인/가상 selection policy에 따른 순서 불변·동점·전부 WATCHLIST/PASS·성공 평가 없음. 무적격은 None+사유 보고서; 0건/전부 부적격/unknown/기술실패 구별 | WP1/WP6 |
| T10 | adapter / 실패 | 0건/401·403/timeout 구별, bounded retry, 인증 실패 반복 금지 | WP2/WP3 |
| T11 | corpus / 페이지 | 전체 200 허용, 201 거절, 미상·미승인·교체 문서 검색 제외 | WP3 |
| T12 | RAG / 귀속 | 같은 query·기업·corpus에 서로 다른 as_of를 전달해 cutoff와 cache 격리 확인; 다른 기업/미허용 Source/날짜 미상 미래 snapshot 제외 | WP3/WP4 |
| T13 | RAG / 실사용 | 실제 retrieval_id/chunk_id → rag provenance → 평가 snapshot의 Evidence → 기술 평가 → 보고서 citation; 사후 rag 표기만으로 통과 금지 | WP3/WP6 |
| T14 | report / 인용 | Evidence·Source 연결, 실제 인용과 REFERENCE 정확히 일치 | WP6 |
| T15 | report / 사실성 | 없는 수치·출처·단위 혼합·추정의 사실화 탐지 | WP5/WP6 |
| T16 | report / 수정 예산 | 구조+의미 공유2회 후 Warning+현재 draft/findings, 미검증 final 승격 금지; 최초 생성 제외·layout 포함은 주입 정책별 검증, fatal context 오류 별도 | WP1/WP6 |
| T17 | PDF / 형식 | 실제 PDF ≤5페이지, SUMMARY ≤반 페이지, 표·인용 잘림 없음 | WP6 |
| T18 | security / untrusted content | 문서의 prompt injection 무시, key 누출 없음, private URL fetch 차단 | WP1/WP3 |
| T19 | reproducibility / 재실행 | lock·설정·corpus·모델·prompt·policy 기록으로 clean run 가능 | 전원 |
| T20 | budget / 조기 실패 | 시간·호출·비용 제한에서 새 호출 중지, 미완성 결과를 final로 표시하지 않음 | WP1 |
| T21 | finance / 런웨이 단위 | 월·연 현금소모 구별, 환산 provenance 필수, 0 이하 분모·기간 불일치에서 임의 개월 수 생성 금지 | WP5 |
| T22 | graph / 평가 failure | 4 success+1 failure 및 Business & Deal의 traction만/투자조건만 유효한 경우 전부 집계 없이 archive → advance; missing과 구별 | WP1/WP4/WP5 |
| T23 | report / context 참조 | 누락된 Source·다른 세대 점수·없는 decision ID 거절; 실제 payload만으로 생성/검증 가능, upstream 오류는 fail | WP1/WP6 |
| T24 | discovery / 출처 전달 | DiscoveryBundle 모든 discovery_source_ids 해소; 후속 Company Research 실패 후에도 발견 Source·이력 보존 | WP1/WP2 |
| T25 | snapshot / 불변성 | 근거·provenance 추가 후 이전 snapshot 불변, 새 세대에서만 보임; superseded/파생 입력 무효화·참조 폐쇄성 확인; 누락·적격성 근거 무효화는 SNAPSHOT_INVALID로 해당 후보만 archive | WP1/WP3 |

T03의 0분모/소수, T09의 순위·동점, T16의 status/CLI 기대값은 **OPEN 정책별 조건부 테스트**다. 임의 기대값으로 승인하지 않는다. 정책 없는 live를 차단하는 테스트와, 제안 정책이 주입됐을 때의 경로 테스트를 분리한다. 기존 6개 smoke test는 이 T01–T25 구현 증거가 아니다.

unit/contract 테스트는 네트워크 없이 실행한다. live integration은 명시적 설정과 예산이 있을 때만 실행하고, 미설정 시 skipped 사유를 남긴다. 외부 LLM 출력의 완전 동일성은 보장하지 않지만, 점수 함수·분기·근거 추적 계약은 동일하게 검증한다.

## 5. 보고서 계약과 PDF 검증

### v3 E-1 다섯 목차 — 세부 검증 설정은 D09

```text
1. SUMMARY
2. COMPANY & TEAM
3. TECHNOLOGY & MARKET
4. INVESTMENT ASSESSMENT & RISKS
5. REFERENCE
```

| 섹션 | 입력·내용 | v3 목표 분량 |
| --- | --- | --- |
| SUMMARY | 최종 선택·정규화 점수/등급, 핵심 투자 포인트·위험·결론 | ≤0.5 page |
| COMPANY & TEAM | CompanyProfile·Founder, 개요·아이디어·팀/사업화 역량 | 0.5~0.75 page |
| TECHNOLOGY & MARKET | Technology·Market·Moat, 성능/통합·규모/성장/수요·차별성 | 1.25~1.5 pages |
| INVESTMENT ASSESSMENT & RISKS | Business & Deal의 traction/deal_terms, 최종 점수·결측/해당 없음·판단·리스크 | 1.5~1.75 pages |
| REFERENCE | 실제 인용 Evidence가 가리키는 Source 서지 | 0.5 page |

v3는 본문 약 4~4.5페이지를 목표로 둔다. 목표 분량 합이나 Markdown 길이가 실제 PDF 검증을 대체하지 않는다. 제목 문자열·순서는 위 E-1을 검사하고 번호/Markdown 레벨 허용 형식은 D09에서 고정한다.

**과제 필수:** 5장 이내, SUMMARY는 전체 보고서의 핵심 요약이며 1/2페이지 이내, 마지막 REFERENCE는 실제 사용 자료만. 표지·참고문헌을 분량 제한 밖으로 빼는 예외는 원문에 없으므로 전체 PDF를 세는 제안이다.

- SUMMARY: 추천 여부, 핵심 이유, 가장 큰 위험, 근거 한계. 단순 목차나 회사 소개로 대체하지 않는다.
- 점수: 원배점 observed_score·applicable_weight, normalized_score, missing_weight·weighted_missing_pct, N/A 항목·차원별 비율·모든 보류 사유를 구별한다. 네 label은 원 Decision과 일치해야 한다.
- 모든 수치·기업 사실: Evidence로 되돌아가는 인용을 둔다. LLM의 평가는 사실과 구별한다.
- 추정값: 방법·입력·가정을 밝히고 직접 관측과 구분한다.
- 무적격: selected=None과 사유 있는 종료 보고서를 만든다. 회사가 선택된 것처럼 빈 섹션을 채우지 않는다. all-WATCHLIST/PASS의 선택 및 no_recommendation mode는 D03, 무적격/Warning의 E-1 섹션 예외는 D09 OPEN이다. 정책 주입 fixture에서는 전 후보 조사 범위·제외/보류/비추천/오류를 표로 구분하는 안을 검증한다.
- Warning: 현재 draft·findings·미실행 검사를 명시하고 validated final과 다른 artifact로 반환한다. workflow_status·CLI exit·manifest 매핑을 정하기 전 완료/발행 성공으로 표시하지 않는다.
- 조사 실패: 시장에서 투자 기회가 없다는 결론으로 바꾸지 않는다.

Reference 양식은 v3 E-2와 이전 원문 §9.3을 따른다.

```text
기관 보고서: 발행기관(YYYY). 보고서명. URL
학술 논문: 저자(YYYY). 논문제목. 학술지명, 권(호), 페이지.
웹페이지: 기관명 또는 작성자(YYYY-MM-DD). 제목. 사이트명, URL
```

알 수 없는 날짜·권호·저자는 만들어 넣지 않는다. 누락 표기 정책을 승인받거나 인용 가능한 원출처를 확보한다. 사용한 Evidence의 Source 집합과 REFERENCE가 일치해야 한다.

### 검증 순서

1. 먼저 ReportContext의 참조 폐쇄성·세대·policy를 검증한다. 같은 context로 생성한 draft의 schema·필수 섹션·점수/판정·인용·Reference를 deterministic하게 대조한다.
2. Semantic Judge가 같은 context의 실제 근거·평가로 문장을 확인한다. 수정 가능한 문장 문제는 revise, context/upstream 오류는 fail이며 보고서 재작성으로 원래 평가를 바꾸지 않는다.
3. 고정 A4 템플릿·폰트·여백으로 PDF 렌더링.
4. PDF parser로 실제 페이지 수, 렌더러 좌표 또는 페이지 이미지로 SUMMARY 점유 영역 확인.
5. 사람이 표/한글 폰트/페이지 넘김/잘린 URL·인용을 눈으로 검토.
6. 수정했다면 해당 draft를 다시 검증하고 검증된 최종 hash와 PDF를 묶어 저장.

구조/의미 수정 한도 소진은 현재 결과와 Warning 반환 경로이며 실패한 검사를 pass로 바꾸지 않는다. `CONTEXT_INVALID/UPSTREAM_INVALID`는 별도 fatal이다. PDF renderer 장애·layout 위반의 retry/Warning 매핑은 D08·D09에서 합의하고, 어떤 경우에도 실제 PDF≤5·SUMMARY≤0.5 미검증 산출물을 제출용 final로 승격하지 않는다.

Markdown 줄 수나 토큰 수로 PDF 페이지 준수를 선언하지 않는다. SUMMARY 반 페이지는 고정된 인쇄 가능 본문 영역의 절반으로 측정하는 제안이며 D09에서 합의한다.

## 6. PR / 작업 완료 정의

- 맡은 모듈의 입력·출력 계약과 실행 범위가 설명되어 있다.
- 변경을 검증하는 자동 테스트와 실제 실행 로그가 있다.
- 외부 자료·fixture·실행 예시를 구분하며 가짜 fixture를 실측으로 표시하지 않는다.
- 공통 schema/정책 변경은 영향받는 소비자·문서·테스트를 함께 변경한다.
- 리뷰는 선택이다. 리뷰를 요청했다면 중요한 지적을 해결했다.
- 실행하지 못한 테스트·API·기능은 실패/미검증 사유를 명시한다.
- 비밀·재배포 불가 원문·생성 index·대용량 output을 실수로 commit하지 않는다.

공통 계약과 첫 세로 흐름이 합쳐진 뒤 WP2/WP3/WP4의 독립 모듈 작업을 병렬화한다. Graph wiring을 여러 사람이 동시에 수정하는 방식은 피한다.

## 7. 제출 체크리스트 — 원문 §10

### 설계 PDF

- [ ] 도메인과 문제 정의
- [ ] Agent별 책임, RAG 대상, Tool 경계
- [ ] 임베딩 후보·실험·선정 이유
- [ ] 승인된 팀 평가표와 예외 규칙
- [ ] State table과 실제 구현에 맞는 Mermaid
- [ ] 보고서 목차
- [ ] 파일명: `RAG-Design_{캠퍼스}-{X반}_{이름1+이름2+이름3+이름4+이름5+이름6}.pdf`

### 개발 제출

- [ ] GitHub Repository와 실제 코드
- [ ] README: Overview / Features / Tech Stack / Agents / Architecture / Directory Structure / Usage / Contributors
- [ ] Generator/Judge 모델, embedding revision, 검색 지표는 실제 사용/측정한 값
- [ ] README 실행법으로 실제 보고서 재생성 가능
- [ ] Contributors: 개인별 수행 역할, PM/PL 제외
- [ ] 보고서 PDF의 내용·분량·인용 검증
- [ ] 파일명: `RAG-Output_{캠퍼스}-{X반}_{이름1+이름2+이름3+이름4+이름5+이름6}.pdf`
- [ ] 실행 manifest와 검증 로그 보관

### 일정·발표

- 원문 마감: **DAY 3 10:00 설계**, **DAY 3 15:00 개발/보고서**. 실제 날짜·시간대는 확인되지 않았다(D11).
- 원문 제출 방식: 반별 채널의 Slack thread. 정확한 대상은 확인 후 제출한다.
- README를 기준으로 10분 발표. 차별화 설계/구현, 실제 보고서 핵심, Lessons Learned 포함.
- v3 표지의 metadata는 **울산 4반 2조**, **김근홍·정순욱·허지원·심혁·박태준·한유진**이다. 이는 설계 표지에 실린 명단이지 GitHub 계정 매핑·실제 수행 역할의 증거가 아니다(D10). 제출명과 순서는 제출 전에 확인한다.

이 문서 작성은 애플리케이션 구현, 실 API 접근, RAG 벤치마크, 보고서 생성 또는 제출 완료를 의미하지 않는다.
