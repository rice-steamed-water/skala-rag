# 결정 목록 — 승인 전에는 제안이다

[문서 홈](../README.md) · [v3 원문](../design/design-v3.html) · [이전 통합 원문](../raws/robotics_startup_agentic_rag_notion_integrated.md) · [정합화·영향표](design-v3-alignment.md)

2026-09-30 기준 사용자가 v3를 새 설계 입력으로 지정했다. **v3 명시 목표 ≠ baseline 정책 승인 ≠ main 구현/검증**이다. issue #3 comment `5902473875` (2026-09-30 01:49:36Z, xxhigh)는 PR #41 head `7b9f6cee8f36a016d5f47191b41999430390e6ee`의 D01–D06·D08 **기존 baseline을 변경 없이 승인**한 기록이다. 그러나 그 baseline의 first-RECOMMEND 종료·serial Deal·fixed100·all-domain-low 같은 전제는 v3 목표와 다르다. 따라서 아래 표의 D01–D06·D08은 v3 대체 세부에 관한 `v3-OPEN`이며, 승인 이력을 지우거나 `SUPERSEDED`로 바꾸지 않는다. v3 replacement를 승인할 때만 근거·정확한 supersession 범위를 기록한다. D07·D09–D14와 live 총시간·LLM 호출·비용 상한은 이번 승인 범위 밖이다.

## 구현 전에 합의할 항목

| ID / 상태 | 이전 제안의 이력 | v3 목표 / 근거 | 남은 결정·제안 / 차단 대상 |
| --- | --- | --- | --- |
| D01 v3-OPEN | 통합 원문 §3·§7의 팀안/교수 예시/단계별 비중이 공존 | C-1의 23개 criterion, `5/30/25/20/10/10`, 원 catalog 합 100 | 지표 담당: catalog ID·버전 승인. 단계별 비중 자동 변경 금지; N/A 분모는 D05와 함께 검증 / 점수 정책 |
| D02 v3-OPEN | 과거 모든 상위 영역의 관측 평균 rating≤2 보류안은 v3 대상 아님 | C-2의 1..5 anchor; C-4의 market/technology만 적용가능 배점 대비 획득≤40%, 결측≥30%, 네 label | 지표 담당: 소수 연속 구간·반올림 전 비교·다중 reason 대표표시 우선순위. 부분/완전 핵심 missing의 비율과 관측 저점수를 설명에서 구별 / 평가·판정 |
| D03 v3-OPEN | 과거 첫 RECOMMEND 종료·나머지 not_evaluated는 v3 대상 아님 | B-1/D-3: 전 후보 처리 후 deterministic selector; 무적격은 selected=None+사유 보고서 | Graph+전원: label/score 우선, tie-break, 전부 WATCHLIST/PASS, 성공 평가 없음, 비교 대상 제외 기준·report mode. 임의 candidate_id/입력순 정렬 금지 / selector·종료 |
| D04 v3-OPEN | 과거 5개 평가 뒤 직렬 Deal Terms안은 v3 대상 아님 | B-1/D-2: 5번째 Business & Deal가 실적·투자조건 함께 담당; 5 branch/6 dimension | 평가+Graph: `business_deal` branch의 `{traction, deal_terms}` atomic envelope, key·wrapper·schema version. 일부 실패 성공 승격 금지 / DTO·Join |
| D05 v3-OPEN | 과거 고정 분모 100·not_applicable 미채택안은 v3 대상 아님 | C-3: Missing 포함, 해당 없음만 분모 제외; C-2의 근거부족 N/A 표기는 내부 충돌 | 데이터+지표: missing/not_applicable 구분 해석 승인, 적용성 근거·검증 주체, 전체/차원 0분모 결과. 최소 Evidence gate와 Coverage 충분성·gap 우선순위 / Coverage·집계 |
| D06 v3-OPEN | 통합 원문 §7의 TIPS·프리/브릿지·추정 정규화는 확인 필요 | A-2/B-1: 비상장·Seed~C·Exit 미완료·최소 Evidence 확보 가능 | Discovery+전원: 최소 Evidence가 확보 가능성/현재 확보 중 무엇인지, Company Research 경계·unknown 보강/다음 후보, 프리시드 허용. TIPS/검색0건/unknown 자동 적격 금지 / Eligibility |
| D07 OPEN | 과거 BGE/Jina/OpenAI 비교 참고는 v3 baseline이 아님 | B-3: BGE-M3 1차 선택, e5-large·KURE-v1과 동일 Chunk/Query의 Hit Rate@1/3/5·MRR·교차언어 비교 | RAG: 최종 선택, 모델 revision·접근/라이선스 실확인, MRR depth/cutoff·성능 기준·vector store·장비. 실험은 M2 / live index |
| D08 v3-OPEN | baseline의 후보5·추가조사2·보고서수정2, batch8calls·retry2·timeout30초와 공유 회계는 승인됨. 이를 v3 회계·Warning 경로가 자동 대체하지 않음 | D-2/D-3: Coverage에서 Evidence 재조사 최대2회 후 평가; 구조·의미 수정 공유2회 후 Warning 현재 결과 반환 | Graph+전원: 초기 제외/포함·빈/오류 batch·네트워크 retry·Company Research 별도 회계, 실행 예산, Warning 결과/acceptance/publication·workflow_status/CLI/manifest 매핑 / live·종료 |
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

표의 `v3-OPEN`은 별도 v3 대체안의 `Status: OPEN`을 뜻하는 문맥 표기다. 아래 원본 승인 레코드의 상태를 바꾸지 않는다. 대체 승인이 기록되기 전까지 baseline의 승인 권한은 유지되며, 실행은 사용한 정책 버전과 승인 범위를 명시해야 한다. v3 가상 fixture의 성공은 baseline 변경 승인이 아니다.

## 승인 방법

v3 replacement에서 결정할 수 없는 항목은 `v3-OPEN`으로 남기고, 해당 기능은 fixture/인터페이스까지만 구현한다. baseline 승인 기록을 OPEN으로 되돌리거나, 반대로 이를 v3 replacement의 승인으로 읽지 않는다. `OPEN`을 기본값으로 감추거나 LLM이 실행 때 임의 결정하게 하지 않는다.

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

## M0 승인 검토 기록 — 이슈 #3

작성일·승인일: 2026-09-30 (Asia/Seoul). 아래 7개 결정은 이슈 담당자 `xxhigh`가 작업 대화에서 명시적으로 승인했다. [승인 근거 기록](https://github.com/rice-steamed-water/skala-rag/issues/3#issuecomment-5902473875). Owner와 승인자는 xxhigh이며, 각 reviewers 역할은 검토 대상이지 별도 승인을 받았다는 뜻은 아니다. 정책 파일의 버전 식별자는 #9에서 부여한다.
### D01 — 고정 baseline 가중치

- Decision ID: D01
- Status: APPROVED
- Decision: founder/market/technology/moat/traction/deal_terms에 `5/30/25/20/10/10`을 적용한다. 단계가 unknown이어도 가중치를 자동 변경하지 않는다.
- Rationale and source: 원문 §3.1·§7.1·§11의 현재 팀 비중을 보존하고, 단계별 비교에서 계산 정책을 재현할 수 있게 한다.
- Rejected alternatives: 미채택 — 교수님 예시 `30/25/15/10/10/10`으로 교체; 단계 추정에 따른 자동 비중 변경.
- Affected documents / policy version / tests: scoring.md §2–3, contracts.md §4; 승인된 baseline; 정책 파일 버전 식별자는 #9에서 부여. #9 비중 합·catalog fixture, #16 집계 검증.
- Owner and reviewers: xxhigh / 지표 담당·팀 전원 검토 요청 대상.
- Approval date: 2026-09-30 (Asia/Seoul), 승인자 xxhigh; 위 승인 근거 기록 참조.
- Supersedes: 없음.

### D02 — rating과 저점수 보류 단위

- Decision ID: D02
- Status: APPROVED
- Decision: criterion rating은 정수 1..5 또는 null. 기여 점수는 `비중 × rating / 5`. 여섯 상위 영역 중 관측 비중으로 계산한 가중평균 rating이 2 이하이면 WATCHLIST. 영역 전체 결측이면 rating=null이며 D05를 적용한다. 임계값은 반올림 전에 비교한다.
- Rationale and source: 원문 §3.1–3.2의 비중과 rating을 구분하여 비중 1인 항목의 만점을 저점수로 오판하지 않는다. 항목별 rubric은 D14에서 별도 승인한다.
- Rejected alternatives: 미채택 — 기여 점수를 2와 비교; 개별 criterion 하나의 rating만으로 전체 강제 보류; null을 0점으로 대체.
- Affected documents / policy version / tests: scoring.md §3·5–6, contracts.md §4; 승인된 baseline; 정책 파일 버전 식별자는 #9에서 부여. #9·#16 상위 rating 2/2.01과 작은 비중 경계 fixture, #10·#11 rubric, #22 평가 검증.
- Owner and reviewers: xxhigh / 지표·평가 담당 검토 요청 대상.
- Approval date: 2026-09-30 (Asia/Seoul), 승인자 xxhigh; 위 승인 근거 기록 참조.
- Supersedes: 없음.

### D03 — 보류·비추천 후보 이동

- Decision ID: D03
- Status: APPROVED
- Decision: WATCHLIST와 PASS는 결과 저장 후 다음 후보로 이동한다. 첫 RECOMMEND에서 단일 기업 보고서를 만들며 나머지는 not_evaluated로 기록한다. 후보 소진 시 결과와 제외 사유를 구별한 요약을 만든다. 첫 추천을 전체 최우수로 표현하지 않는다. scoring.md §5의 판정 순서와 점수 경계(80/70/60)를 함께 검토한다.
- Rationale and source: 원문 §4.3과 §12 교수님 노션 D의 보류 후보 이동 요구를 일관된 유한 흐름으로 연결한다. 기술 실패를 투자 비추천으로 표현하지 않는다.
- Rejected alternatives: 미채택 — 첫 WATCHLIST에서 즉시 단일 기업 보고서 종료; 전체 평가 없이 최우수 후보 선언; 적격성 통과 의미로 PASS 재사용.
- Affected documents / policy version / tests: scoring.md §5, architecture.md §2–3·5–6, contracts.md §5–6; 승인된 baseline; 정책 파일 버전 식별자는 #9에서 부여. #16 판정 우선순위, #23 후보 이동·요약, #26 보고서 context, #30 종료 시나리오.
- Owner and reviewers: xxhigh / Graph 담당·팀 전원 검토 요청 대상.
- Approval date: 2026-09-30 (Asia/Seoul), 승인자 xxhigh; 위 승인 근거 기록 참조.
- Supersedes: 없음.

### D04 — 투자조건 평가의 책임

- Decision ID: D04
- Status: APPROVED
- Decision: founder/market/technology/moat/traction의 다섯 병렬 노드를 유지하고, 다섯 성공 결과의 합류 뒤 같은 snapshot으로 deal_terms_evaluation을 직렬 실행한다. 여섯 성공 결과가 있어야 집계한다. Aggregator는 rating과 승인 catalog로 산술만 수행한다. 평가 실패는 후보 failed로 보존한다.
- Rationale and source: 원문 §3.1·§3.3·§4.2의 다섯 병렬 노드와 여섯 평가 영역을 모두 보존하며 투자조건 10% 누락을 방지한다.
- Rejected alternatives: 미채택 — 투자조건 누락 후 나머지 비중 재정규화; Aggregator 안에서 LLM 투자조건 평가; 병렬 노드를 여섯 개로 변경.
- Affected documents / policy version / tests: architecture.md §2–4, contracts.md §4·6, scoring.md §6; 승인된 baseline; 정책 파일 버전 식별자는 #9에서 부여. #6 결과 계약, #22 wrapper, #24 합류·직렬 실패, #16 여섯 영역 완전성.
- Owner and reviewers: xxhigh / 평가·Graph 담당 검토 요청 대상.
- Approval date: 2026-09-30 (Asia/Seoul), 승인자 xxhigh; 위 승인 근거 기록 참조.
- Supersedes: 없음.

### D05 — 결측 분모와 coverage

- Decision ID: D05
- Status: APPROVED
- Decision: 전체 비중 100을 고정 분모로 사용한다. 직접 근거와 필요한 단위·기간·주체가 없거나 중요한 상충이 미해결이면 missing. observed_score는 관측 기여만 합산하며 coverage와 함께 표시한다. missing_weight가 30 미만이면 사전 research_ready, 30 이상이면 예산 내 보강한다. 최종 평가 후 다시 계산해 30 이상이면 정보부족 WATCHLIST. 적용조건 미확정은 missing+applicability_note로 남기고 비중을 제거하지 않는다.
- Rationale and source: 원문 §2.2·§3.2·§11과 scoring.md §3–4. 정보 부족 때문에 점수가 부풀거나 조사량 기준이 평가 근거의 질을 대신하지 않게 한다.
- Rejected alternatives: 미채택 — 관측 비중만으로 100점 재정규화; 결측에 rating=0 입력; not_applicable 자동 비중 재배분; 사전 coverage만으로 최종 판정.
- Affected documents / policy version / tests: scoring.md §3–5, contracts.md §4, architecture.md §3·5; 승인된 baseline; 정책 파일 버전 식별자는 #9에서 부여. #9·#16 결측 29/30/31, #20 coverage, #25 보강 예산. 지표 적용조건은 #10·#11/D14에서 추가 승인.
- Owner and reviewers: xxhigh / 데이터·지표 담당 검토 요청 대상.
- Approval date: 2026-09-30 (Asia/Seoul), 승인자 xxhigh; 위 승인 근거 기록 참조.
- Supersedes: 없음.

### D06 — 라운드 정규화와 적격성

- Decision ID: D06
- Status: APPROVED
- Decision: 직접 확인된 Seed~Series C만 투자 단계 조건을 통과한다. TIPS 선정만으로 Seed를 확정하지 않는다. 프리시드·엔젤은 명시적으로 확인되면 out_of_scope; 프리A/B/C·브릿지는 명칭만으로 다음 라운드로 올리지 않고 직전 완료 라운드를 근거로 확인한다. 근거가 없거나 추정만 있으면 unknown으로 남기고, 공통 보강 예산 소진 후 eligibility_unknown으로 다음 후보로 이동한다. 다른 필수 적격성 조건도 모두 확인되어야 eligible이다.
- Rationale and source: 원문 §2.2의 Seed~C 범위를 완화하지 않고 §7의 정규화 힌트를 검색과 확정 판정으로 구분한다.
- Rejected alternatives: 미채택 — TIPS를 Seed로 자동 매핑; 프리시드·엔젤을 Seed 범위에 포함; 누적 투자액 추정만으로 eligible; 미상 값을 false로 대체.
- Affected documents / policy version / tests: scoring.md §1, contracts.md §2, architecture.md §2–3·5; 승인된 baseline; 정책 파일 버전 식별자는 #9에서 부여. #5 StageInfo, #18 explicit/estimated/unknown·TIPS·프리시드·브릿지 fixture, #23 적격성 이동.
- Owner and reviewers: xxhigh / Discovery 담당·팀 전원 검토 요청 대상.
- Approval date: 2026-09-30 (Asia/Seoul), 승인자 xxhigh; 위 승인 근거 기록 참조.
- Supersedes: 없음.

### D08 — 반복 상한과 후보 소진

- Decision ID: D08
- Status: APPROVED
- Decision: normalize 후 후보 최대 5개, 후보별 추가 조사 batch 총 2회(적격성·coverage·평가 후 보강 공유, 최초 수집 제외), 보고서 최초 생성 후 수정 총 2회(구조·의미·layout 공유). 조사 batch는 요청 전에 차감하며 오류로 되돌리지 않는다. 후보 정상 소진은 사유 있는 Summary, 모든 후보 기술 실패와 총예산 소진은 workflow failed. 보조 도구 제한은 batch당 호출 8회(네트워크 재시도 포함), 추가 도구 재시도 2회, 시도별 timeout 30초다. live 총시간·LLM 호출·비용 상한은 환경별 별도 승인·명시 전까지 live 시작을 거절한다.
- Rationale and source: 원문 §2.3·§4·§8·§11과 architecture.md §5–6. 모든 반복에 공유 상한을 두고 정상 후보 소진과 기술 실패를 구별한다. 5/2/2 및 보조 제한은 측정 결과가 아닌 승인된 초기 제한이다.
- Rejected alternatives: 미채택 — 분기마다 별도 2회 예산 부여; 오류 batch 환불; 후보 재발견 무한 반복; Graph step 제한만 사용; live 예산 무제한 기본값.
- Affected documents / policy version / tests: architecture.md §5–6, contracts.md §6, scoring.md §4; 승인된 baseline; 정책 파일 버전 식별자는 #9에서 부여. #7 count 초기화, #23 후보 소진, #25 공유 예산, #28 보고서 수정, #29 manifest, #30 유한 종료.
- Owner and reviewers: xxhigh / Graph 담당·팀 전원 검토 요청 대상.
- Approval date: 2026-09-30 (Asia/Seoul), 승인자 xxhigh; 위 승인 근거 기록 참조.
- Supersedes: 없음.

### 승인 반영 범위

7개 항목의 승인자·승인일·승인 근거를 기록하고 scoring.md·architecture.md·contracts.md와 문서 홈에 승인 상태를 반영했다. 다른 결정(D07·D09–D14)의 OPEN 상태는 유지한다. 코드·정책 파일은 이 이슈에서 작성하지 않는다. PR 병합은 별도 요청 후 수행한다.

## 조용히 바꾸면 안 되는 원문

- 현재 팀 가중치를 교수님 예시 `30/25/15/10/10/10`으로 되돌리지 않는다.
- `PASS`를 적격성 통과나 보고서 검증 통과라는 의미로 재사용하지 않는다.
- 5개 branch라는 이유로 투자조건 차원을 누락하지 않는다. 해당 없음만 승인된 적용성으로 분모에서 제외하며 Missing/실패를 삭제해 100으로 재정규화하지 않는다.
- “특정 항목 2점”을 비중이 1점인 세부항목의 획득점수와 직접 비교하지 않는다.
- 최종 embedding 후보가 공개되어 있다는 사실만으로 과제의 오픈소스 요구 충족을 선언하지 않는다.
- 원문의 도구 비용·접근성 표는 당시 메모다. 키 발급, 이용조건, 접근 성공은 구현 시 다시 확인해야 한다.
