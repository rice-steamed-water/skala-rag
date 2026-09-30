# 보고서 목차·인용·구조 검증 계약 — D09 검토안

[문서 홈](../README.md) · [공통 계약](contracts.md#5-보고서와-오류) · [검증 계획](delivery.md#5-보고서-계약과-pdf-검증) · [결정 목록](decisions.md)

상태: **OPEN / 승인 전 제안**, 이슈 #14, 담당 xxhigh, 작성일 2026-09-30.
근거: [통합 원문 §9.1–9.3](../raws/robotics_startup_agentic_rag_notion_integrated.md).
이 문서는 M1 Generator·Structural Validator의 인터페이스를 정한다. 렌더러 구현이나 실제 보고서 생성 결과가 아니다.
현재 main의 D03 승인 baseline을 따른다. 진행 중인 #35의 v3 설계는 별도 정합화 대상이며, 전체 후보 최우수 선택이나 새 판단 label을 여기서 도입하지 않는다.

## 1. 공통 형식

- Markdown의 보고서 최상위 섹션은 `##` heading으로 표시한다. 아래 mode별 7개 heading을 정확히 한 번씩, 지정 순서대로 둔다. 하위 heading은 `###` 이하를 사용한다.
- 첫 섹션은 `SUMMARY`, 마지막은 `REFERENCE`다. 각 섹션은 공백이 아닌 내용을 가진다. 별도 제목·표지를 추가하면 PDF 전체 분량에 포함한다.
- SUMMARY는 판단/실행 결과, 핵심 이유, 주요 위험, 근거 한계의 요약이다. 목차나 기업 소개만으로 채우지 않는다.
- PDF 전체 5페이지 이하, SUMMARY 반 페이지 이하는 과제 필수다. Markdown 구조 검증으로 페이지 준수를 선언하지 않는다.
- `execution_mode=fixture`인 출력에는 SUMMARY에 가상 데이터임을 표시한다. run_id·as_of·policy/corpus version·context_id는 산출물 manifest에 보존한다.

## 2. single_candidate 목차

```text
SUMMARY
1. 기업과 제품 / 핵심 컨셉
2. 시장성과 성장 가능성
3. 창업자·팀, 기술력과 경쟁 우위
4. 실적·투자조건과 평가 결과
5. 주요 리스크, 한계와 추가 확인 사항
REFERENCE
```

| 섹션 | 필수 내용 |
| --- | --- |
| SUMMARY | 선택 기업, 원래 label·report_grade, 핵심 이유·위험·결측 한계. D03의 첫 추천이며 전체 후보 최우수라는 표현 금지 |
| 1 | 기업 식별, 제품·사업 아이디어, 도메인·적격성 근거 |
| 2 | 시장 정의·지역·연도와 규모·성장·수요; 산업 전망과 기업 실적 구분 |
| 3 | 창업자·팀, 기술 완성도·실제 환경 성능·통합·상용화, 경쟁 우위 |
| 4 | 관측 근거 기반 점수 X/100, missing_weight·coverage_pct, 여섯 영역 rating(전부 결측이면 미상), label·grade·hold_reasons. 단위·기간·라운드와 공개되지 않은 투자조건 명시 |
| 5 | 시장·기술·규제·경쟁 위험, 상충·추정·미지원 범위, 추가 확인 사항 |
| REFERENCE | 실제 본문 인용으로 사용한 Source만 기재 |

선택 후보의 최종 세대 snapshot·ScoreSummary·Decision을 그대로 사용한다. 실패한 평가를 missing이나 0점으로 대체하지 않는다. 평가하지 않은 후보의 점수를 만들지 않는다.

## 3. no_recommendation 목차

```text
SUMMARY
1. 조사 주제와 후보 탐색 범위
2. 후보별 결과와 제외·보류 사유
3. 시장·기술 관찰과 근거 범위
4. 평가 결과와 정보 공백
5. 공통 리스크, 한계와 추가 확인 사항
REFERENCE
```

| 섹션 | 필수 내용 |
| --- | --- |
| SUMMARY | 추천 없음/후보 없음/정보 부족과 조사 실패 구분, 조사 범위·주요 이유·한계 |
| 1 | 주제·국가·as_of, 탐색 및 실제 처리 후보 수, 자료 범위 |
| 2 | CandidateOutcome별 상태·summary_reason; ineligible·eligibility_unknown·WATCHLIST·PASS·failed 구별. 0건이면 평가 후보 없음 명시 |
| 3 | 인용 가능한 시장·기술 관찰만 서술. 근거가 없으면 관찰 불가 사유를 명시 |
| 4 | 실제 성공 평가 후보의 점수·결측·보류 사유. 평가하지 않은/실패 후보는 평가 없음으로 표시하며 점수·판정 생성 금지 |
| 5 | 공통 리스크·정보 공백·도구 실패와 후속 조사 제안 |
| REFERENCE | 본문 인용 Source 집합. 빈 집합이면 아래 빈 참고문헌 규칙 적용 |

회사 한 곳을 선택한 것처럼 single_candidate 섹션을 채우지 않는다. 모든 후보 기술 실패는 workflow failed이며 최종 투자 보고서 대신 진단 draft·오류를 보존한다. 보고서 텍스트로 workflow_status를 바꾸지 않는다.

## 4. Evidence → Source → REFERENCE

### 본문 인용 문법

인용 token은 `[@evidence:<evidence_id>]`다. 예: `[@evidence:ev-fixture-001]`.
한 주장에 근거가 여럿이면 token을 연속해서 붙인다. ID는 controller가 만든 실제 값을 그대로 사용하고, LLM이 새 ID나 URL을 만들지 않는다.
token은 주장 문장 끝 또는 표의 근거 열에 둔다. code block 안의 예시 token은 실제 인용으로 세지 않는다. live 보고서에는 fixture ID/locator를 사용하지 않는다.

- 수치·기업 사실·시장 사실은 허용된 Evidence token을 가진다. 평가/추론은 평가임을 표시하고 그 해석의 입력 근거를 인용한다.
- 보고서의 점수·label은 ScoreSummary/Decision과 대조한다. 점수 자체를 증명하기 위해 가짜 Evidence를 생성하지 않는다.
- 미상·도구 실패는 context의 missing_reason/WorkflowError에 근거해 설명한다. 외부 사실처럼 인용하지 않는다.
- 추정·파생값은 evidence_kind, supporting_evidence_ids와 derivation의 입력·방법·가정을 보존한다. 근거가 기업 자기 주장인 경우 독립 검증으로 표현하지 않는다.

### 정확한 집합 관계

REFERENCE 바깥 본문에서 추출한 인용 ID 집합을 C라 한다. ReportDraft.cited_evidence_ids의 집합은 C와 같고, 중복 ID는 metadata 배열에 한 번만 기록한다.
모든 C는 ReportContext.input.permitted_evidence_ids와 context.evidence 안에 존재해야 한다.
`S = {context.evidence[e].source_id | e in C}`로 계산하며, ReportDraft.reference_source_ids와 REFERENCE 항목 ID 집합은 모두 S와 정확히 같아야 한다.
다운로드·검색만 한 Source, context에 포함됐지만 인용하지 않은 Source는 REFERENCE에 넣지 않는다.

파생 입력을 본문에서 직접 설명하거나 인용하면 해당 입력 Evidence도 C에 포함한다. supporting_evidence_ids가 참조된다는 이유만으로 보고서에서 쓰지 않은 Source를 자동 추가하지 않는다. 전체 입력 근거의 참조 폐쇄성은 ReportContext가 보장한다.

### REFERENCE 항목과 원문 양식

각 항목은 `- [@source:<source_id>] <서지 문자열>` 형식이며 source_id 오름차순으로 한 번씩 기록한다. 같은 Source의 여러 Evidence는 한 항목으로 모은다. 제목만 같다는 이유로 다른 snapshot ID를 병합하지 않는다.

```text
기관 보고서: 발행기관(YYYY). 보고서명. URL
학술 논문: 저자(YYYY). 논문제목. 학술지명, 권(호), 페이지.
웹페이지: 기관명 또는 작성자(YYYY-MM-DD). 제목. 사이트명, URL
```

서지정보는 context.sources의 실제 metadata에서만 가져온다. URL은 검색 결과 페이지 대신 해당 Source 원문 URL을 사용한다. PDF renderer가 추가된 뒤에도 이 ID 대응은 유지하며, 화면용 번호 변환은 별도 렌더링 계약에서 정한다.

| 미상 필드 | 표기 제안 |
| --- | --- |
| 기관/저자 | `발행기관 미상` 또는 `저자 미상` |
| 발행일/연도 | `발행일 미상` 또는 `발행연도 미상`; retrieved_at을 발행일로 대체 금지 |
| 제목/사이트명 | `제목 미상` / `사이트명 미상` |
| 학술지·권호·페이지 | 해당 위치에 `학술지 미상` / `권호 미상` / `페이지 미상` |
| URL 없음 | `온라인 주소 없음`; 재배포 가능한 local locator가 있으면 표시하고 개인 절대 경로는 출력하지 않음 |

미상 표기는 출처 존재·주장 검증의 대체물이 아니다. context에서 Source와 원문 locator를 해소할 수 없으면 context 오류다. 원문에 없는 서지값을 추측하지 않는다.
인용 Source가 0개이면 REFERENCE 본문을 `인용 자료 없음: <자료가 없는 구체적 사유>`로 작성하고 reference_source_ids=[]를 유지한다. 이를 위해 가짜 Source를 만들지 않는다. 0개 허용은 후보 없음/미평가 요약에 적용하며, single_candidate는 실제 평가 근거 인용이 필요하다.

## 5. Structural Validator 체크리스트 — T14·T23

아래는 #27에서 구현할 검사 명세이며 실행한 테스트 결과가 아니다.

| ID | 검사 | 실패 처리 |
| --- | --- | --- |
| SV01 | draft.context_id와 고정 context 일치, payload/ID 참조 폐쇄성·후보·세대·policy 일치 | CONTEXT_INVALID/UPSTREAM_INVALID, 재작성 대신 fail |
| SV02 | mode·selected_candidate_id·CandidateOutcome과 단일/요약 형식 일치 | context 모순은 fail; draft 모순은 revise |
| SV03 | mode별 최상위 heading 7개가 정확한 순서로 한 번씩 존재, 비어 있지 않음 | draft revise |
| SV04 | 기록된 점수·영역 rating·결측·coverage·label·grade·reason이 context와 같음; 숫자 표시는 scoring의 반올림 규칙 사용 | draft revise; 원래 점수 오류는 upstream fail |
| SV05 | 본문 Evidence token 문법·실존·허용 범위, metadata cited ID 집합 C 일치 | draft revise |
| SV06 | Evidence.source_id 해소, REFERENCE ID 유일성·집합 S 정확히 일치, reference_source_ids와 일치 | draft 오류 revise; Source 누락은 context fail |
| SV07 | Source metadata로 생성한 서지 문자열·종류별 형식·미상 표기 일치; 빈 Reference 예외 준수 | draft revise |
| SV08 | fixture 표시, 미평가·failed 후보에 점수/투자 판정 없음, 정책 용어 혼용 없음 | draft revise |
| SV09 | 검증 결과 context_id·artifact_hash가 검증한 draft와 일치 | 새 draft 재검증; 이전 통과 결과 재사용 금지 |

점수·영역 결과는 §2의 평가 결과 표, 후보 outcome은 §3의 후보 결과 표를 검증 대상으로 삼는다. 자유 문장에 숨은 다른 숫자·인용이 주장을 실제로 지지하는지·추정의 사실화·SUMMARY 품질은 Semantic Judge의 T15 검증 대상이다. token이 존재한다는 이유로 사실성 통과를 선언하지 않는다.

### 구현용 검증 시나리오

- 정상 단일 보고서: 같은 Source를 인용하는 Evidence 둘 → C는 둘, S는 하나.
- 정상 요약: 부적격·unknown·WATCHLIST·PASS를 구별; 0후보면 점수 없이 빈 REFERENCE 사유.
- draft 수정: heading 누락/중복/순서 오류, 허용 밖 ID, 여분 Reference, metadata ID 불일치, 원래 label·점수 변경, 만들어낸 발행일.
- context 실패: Evidence Source 없음, 다른 후보·세대 점수, 없는 decision ID.
- 재검증: Markdown 변경 후 이전 artifact_hash의 통과 결과 거절.

## 6. PDF와 승인 경계

구조 검증 → 같은 context의 Semantic Judge → PDF 렌더링 → 페이지/레이아웃 측정 → 사람 시각 검토 순서를 따른다. 수정하면 해당 draft의 구조·의미·PDF 검증을 다시 수행한다(D08의 공유 수정 예산).
PDF 전체 5페이지와 SUMMARY 반 페이지 제한은 유지한다. A4·폰트·여백·렌더러·SUMMARY 측정 기준·인용 token 화면 변환은 M3에서 별도 승인한다. 이번 문서로 렌더러를 선택하거나 페이지 준수를 인증하지 않는다.
D09는 목차/인용/구조 검증과 PDF 구현 선택을 나눠 기록한다. 이번 안이 승인되어도 PDF 선택의 OPEN 상태는 남는다.
