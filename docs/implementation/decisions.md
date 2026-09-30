# 결정 목록 — 승인 전에는 제안이다

[문서 홈](../README.md) · [v3 원문](../design/design-v3.html) · [이전 통합 원문](../raws/robotics_startup_agentic_rag_notion_integrated.md) · [정합화·영향표](design-v3-alignment.md)

2026-09-30 기준 사용자가 v3를 새 설계 입력으로 지정했다. **v3 명시 목표 ≠ 팀의 구현 정책 APPROVED ≠ main 구현/검증**이다. 아래 **D01–D14는 모두 OPEN**이며 승인자·승인일은 기록되지 않았다. 표의 과거 제안은 역사이지 활성 기본값이 아니다. 담당은 검토 역할 제안으로 실명/GitHub 계정 배정이 아니다. PR #33의 OPEN 승인기록 초안과 PR #34의 proposed rubric도 팀 승인을 대신하지 않는다.

## 구현 전에 합의할 항목

| ID / 상태 | 이전 제안의 이력 | v3 목표 / 근거 | 남은 결정·제안 / 차단 대상 |
| --- | --- | --- | --- |
| D01 OPEN | 통합 원문 §3·§7의 팀안/교수 예시/단계별 비중이 공존 | C-1의 23개 criterion, `5/30/25/20/10/10`, 원 catalog 합 100 | 지표 담당: catalog ID·버전 승인. 단계별 비중 자동 변경 금지; N/A 분모는 D05와 함께 검증 / 점수 정책 |
| D02 OPEN | 과거 모든 상위 영역의 관측 평균 rating≤2 보류안은 v3 대상 아님 | C-2의 1..5 anchor; C-4의 market/technology만 적용가능 배점 대비 획득≤40%, 결측≥30%, 네 label | 지표 담당: 소수 연속 구간·반올림 전 비교·다중 reason 대표표시 우선순위. 부분/완전 핵심 missing의 비율과 관측 저점수를 설명에서 구별 / 평가·판정 |
| D03 OPEN | 과거 첫 RECOMMEND 종료·나머지 not_evaluated는 v3 대상 아님 | B-1/D-3: 전 후보 처리 후 deterministic selector; 무적격은 selected=None+사유 보고서 | Graph+전원: label/score 우선, tie-break, 전부 WATCHLIST/PASS, 성공 평가 없음, 비교 대상 제외 기준·report mode. 임의 candidate_id/입력순 정렬 금지 / selector·종료 |
| D04 OPEN | 과거 5개 평가 뒤 직렬 Deal Terms안은 v3 대상 아님 | B-1/D-2: 5번째 Business & Deal가 실적·투자조건 함께 담당; 5 branch/6 dimension | 평가+Graph: `business_deal` branch의 `{traction, deal_terms}` atomic envelope, key·wrapper·schema version. 일부 실패 성공 승격 금지 / DTO·Join |
| D05 OPEN | 과거 고정 분모 100·not_applicable 미채택안은 v3 대상 아님 | C-3: Missing 포함, 해당 없음만 분모 제외; C-2의 근거부족 N/A 표기는 내부 충돌 | 데이터+지표: missing/not_applicable 구분 해석 승인, 적용성 근거·검증 주체, 전체/차원 0분모 결과. 최소 Evidence gate와 Coverage 충분성·gap 우선순위 / Coverage·집계 |
| D06 OPEN | 통합 원문 §7의 TIPS·프리/브릿지·추정 정규화는 확인 필요 | A-2/B-1: 비상장·Seed~C·Exit 미완료·최소 Evidence 확보 가능 | Discovery+전원: 최소 Evidence가 확보 가능성/현재 확보 중 무엇인지, Company Research 경계·unknown 보강/다음 후보, 프리시드 허용. TIPS/검색0건/unknown 자동 적격 금지 / Eligibility |
| D07 OPEN | 과거 BGE/Jina/OpenAI 비교 참고는 v3 baseline이 아님 | B-3: BGE-M3 1차 선택, e5-large·KURE-v1과 동일 Chunk/Query의 Hit Rate@1/3/5·MRR·교차언어 비교 | RAG: 최종 선택, 모델 revision·접근/라이선스 실확인, MRR depth/cutoff·성능 기준·vector store·장비. 실험은 M2 / live index |
| D08 OPEN | 과거 후보5·batch8calls·30초, 적격성/사후 보강 합산, 수정 소진 failed 일괄안은 승인되지 않음 | D-2/D-3: Coverage에서 Evidence 재조사 최대2회 후 평가; 구조·의미 수정 공유2회 후 Warning 현재 결과 반환 | Graph+전원: 초기 제외/포함·빈/오류 batch·네트워크 retry·Company Research 별도 회계, 실행 예산, Warning 결과/acceptance/publication·workflow_status/CLI/manifest 매핑 / live·종료 |
| D09 OPEN | 과거 SUMMARY+본문5개+REFERENCE안은 v3 대상 아님 | E-1/E-2의 다섯 목차·인용, 실제 PDF≤5·SUMMARY≤0.5 | 보고서: 렌더러·A4·폰트·여백·SUMMARY 측정, 무적격/Warning 목차 예외, 서지 누락 표기, layout 수정예산 / 보고서·제출 |
| D10 OPEN | 통합 원문 §11/§12의 별명·역할 매핑 미확정 | v3 표지는 울산 4반 2조, 김근홍·정순욱·허지원·심혁·박태준·한유진 명시 | 전원: 표지 명단≠실제 역할/계정. 이슈·PR 수행 증거와 본인 확인으로 Contributors 기록, 자동 매칭 금지 / 기여 역할 |
| D11 OPEN | 통합 원문 §10의 DAY 3 일정만 있음 | v3가 캠퍼스·반·조는 제공하지만 실제 제출일은 제공하지 않음 | 전원: DAY 3 10:00 설계/15:00 개발의 실제 날짜·시간대·Slack thread 확인 / 제출 |
| D12 OPEN | 통합 원문 §1·§5 국내 중심 API와 국내외 탐색 목표 차이 | v3 A의 Physical AI / Robotics 도메인 | Discovery: 국가 범위·지원 provider·해외 미지원 표시. 한국 fixture를 최종 지역 제한 승인으로 보지 않음 / live 탐색 |
| D13 OPEN | HTML·PPT·부분 PDF의 페이지 산정 미정 | B-2: 전체 RAG 문서≤200페이지 | RAG+과제 확인 담당: 원본/사용 구간 manifest, HTML 고정 PDF·PPT 슬라이드 산정 제안 승인. 미상/초과/미승인 인덱싱 보류 / corpus |
| D14 OPEN | 이전 상세표·PR #34의 rubric은 제안이며 자동 투자 기준 아님 | C-2 anchor와 C-3의 pre-revenue 해당 없음 예시 | 지표: criterion별 rating·적용성 근거·rule, 재무 단위/기간/동일 라운드·0 이하 burn 처리. Seed 자동 N/A나 Series C 가점 금지 / 실제 평가 |

### M0에서 실행 정책으로 만들기 전 필요한 답

- **D02/D05:** C-2의 근거 부족 N/A는 machine `missing`, C-3의 해당 없음은 `not_applicable`로 분리하는 안을 확인한다. 전체/비핵심/핵심차원 분모 0 각각에 대해 null·오류·보류·계속 여부를 선택한다. 공개되지 않은 값이 분모에서 사라지면 안 된다.
- **D02:** `70≤s<80`, `60≤s<70`의 연속 구간, 정확도·표시 반올림, missing≥30과 핵심≤40 동시 발생 시 대표 grade를 정한다. 모든 reason은 보존하고 부족 근거를 부정 관측으로 설명하지 않는다.
- **D03:** Selector는 모든 candidate outcome을 검증하되 적격·정상 평가 결과만 추천할 수 있다. 정렬 키/방향·동점·순서 불변성·모두 WATCHLIST/PASS·성공 평가 없음의 반환 방식을 명시한다. 무적격 None과 전부 기술 실패를 구별한다.
- **D04:** [공통 계약](contracts.md)의 5개 branch-key envelope→6개 dimension-key 성공 map을 검토한다. Business & Deal의 두 payload 중 하나만 유효할 때 전체 branch failure, Join 후에만 원자적 승격하는 안을 승인한다.
- **D05/D06:** 여섯 차원 최소 Evidence 확보 가능성의 검사 항목·근거/책임, unknown 처리, Company Research와 Evidence Research 경계를 정한다. Coverage 30%를 Eligibility gate로 복사하지 않는다.
- **D08:** 최초 수집 제외 추가2회·요청 전 count+1·empty/failure도 소비하는 안과 Company Research/도구 retry 별도 한도를 검토한다. 보고서는 최초 생성 제외 구조+의미 공유2회 수정, 그 후 현재 draft+findings+Warning 반환을 제안한다. PDF layout은 공유 여부를 따로 정한다.
- **D08/D09:** 실행 종료·현재 결과 반환·검증 수용·final 발행을 분리한다. v3 `running/completed/failed`에 Warning을 매핑하는 방법과 CLI exit·artifact 이름·manifest를 정한다. 미검증 draft의 final 승격 금지와 context/upstream fatal 경계를 유지한다. 새 workflow enum은 승인 전 추가하지 않는다.

이 질문이 미정인 채로도 schema/가상 정책 주입/조건부 fixture는 작성할 수 있다. 단, 영향을 받는 live 실행은 승인 정책·예산·readiness를 검사해 **시작 전 차단**해야 한다. 설명용 예시 숫자가 있다는 사실은 해당 정책의 승인 증거가 아니다.

## 승인 방법

M0에서 결정할 수 없는 항목은 `OPEN`으로 남기고, 해당 기능은 fixture/인터페이스까지만 구현한다. `OPEN`을 기본값으로 감추거나 LLM이 실행 때 임의 결정하게 하지 않는다.

결정마다 아래 양식을 채운다. 거절된 대안도 남겨 같은 논의를 반복하지 않게 한다.

```text
Decision ID: Dxx
Status: OPEN | APPROVED | REJECTED | SUPERSEDED
Decision:
Rationale and source:
Rejected alternatives:
Affected documents / policy version / tests:
Owner and reviewers:
Approval date:
Supersedes:
```

`APPROVED` 전환 후에는 해당 문서와 정책 fixture를 같은 변경으로 수정한다. 과제 필수 조건을 완화하는 결정에는 팀 승인뿐 아니라 과제 담당자의 확인 근거가 필요하다.

## 조용히 바꾸면 안 되는 원문

- 현재 팀 가중치를 교수님 예시 `30/25/15/10/10/10`으로 되돌리지 않는다.
- `PASS`를 적격성 통과나 보고서 검증 통과라는 의미로 재사용하지 않는다.
- 5개 branch라는 이유로 투자조건 차원을 누락하지 않는다. 해당 없음만 승인된 적용성으로 분모에서 제외하며 Missing/실패를 삭제해 100으로 재정규화하지 않는다.
- “특정 항목 2점”을 비중이 1점인 세부항목의 획득점수와 직접 비교하지 않는다.
- 최종 embedding 후보가 공개되어 있다는 사실만으로 과제의 오픈소스 요구 충족을 선언하지 않는다.
- 원문의 도구 비용·접근성 표는 당시 메모다. 키 발급, 이용조건, 접근 성공은 구현 시 다시 확인해야 한다.
