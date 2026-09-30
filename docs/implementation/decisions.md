# 결정 목록 — 승인 전에는 제안이다

[문서 홈](../README.md) · 근거: [통합 원문](../raws/robotics_startup_agentic_rag_notion_integrated.md)

이 문서는 원문에서 서로 다른 상태로 남아 있는 내용과 새 구현 제안을 분리한다. **D01–D06·D08은 APPROVED**, D09의 목차·인용·구조 검증은 부분 APPROVED이며 PDF 선택은 OPEN이다. D07·D10–D14는 OPEN이다. 표의 승인 역할은 검토 대상이며, D01–D06·D08의 실제 문서 담당자는 아래 이슈 #3 기록에 명시한다.

## 구현 전에 합의할 항목

| ID  | 충돌 / 미정 사항                                                              | 원문 근거                                     | 결정안 (상태는 승인 기록 참조)                                                                                                                                 | 승인 역할 / 차단 대상                   |
| --- | ----------------------------------------------------------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------- |
| D01 | 현재 팀 가중치, 교수님 예시 가중치, 단계별 가중치가 공존                      | §3.1, §3 단계별 제안, §7.1, §11               | 고정 `5/30/25/20/10/10`만 baseline으로 사용. unknown stage도 가중치를 자동 변경하지 않음                                                         | 지표 담당 / 점수 정책                   |
| D02 | 점수 척도와 “특정 항목 2점 이하”의 항목 단위가 없음                           | §3.1–3.2                                      | 세부항목 rating은 1~5, 가중점수는 별도 계산. 저점수 보류는 여섯 **상위 영역의 관측 가중평균 rating ≤2**에 적용                                   | 지표 담당 / 평가 prompt와 임계값 테스트 |
| D03 | 팀 Graph는 WATCHLIST 즉시 보고서, 교수님은 보류 시 다음 후보                  | §4.3; §12 교수님 노션 D, 원문 L2741–2746      | `WATCHLIST`와 `PASS` 모두 다음 후보. 첫 `RECOMMEND`에서 보고서, 모두 미추천이면 비교 요약. 첫 추천이 전체 최우수라는 표현 금지                   | Graph + 전원 / 라우팅                   |
| D04 | 가중치 영역은 6개지만 병렬 평가 노드는 5개                                    | §3.1, §3.3, §4.2                              | 5개 노드 유지 + 합류 뒤 별도 `deal_terms_evaluation` structured-output 노드. Aggregator는 숫자만 합산                                            | 평가 + Graph / State·집계               |
| D05 | 최소 데이터 / coverage 충분성 / 결측 분모가 미정                              | §2.2, §3.2, §11                               | 전체 세부항목 비중 100을 분모로 고정. 직접 근거·필수 맥락이 없는 항목은 결측. 조사 전후 기준을 분리                                              | 데이터 + 지표 / Coverage                |
| D06 | Seed~C 조건에 비해 TIPS·프리시드 정규화가 과도함; 추정 기준 미정              | §2.2, §7                                      | TIPS만으로 라운드 확정 금지. `unknown`/추정만으로 적격 처리하지 않음. 프리/브릿지는 근거로 이전 라운드를 확인                                    | Discovery + 전원 / Eligibility          |
| D07 | embedding 최종 모델과 vector store 미선정                                     | §6.3, §11                                     | `BAAI/bge-m3` 우선 실험. 모델 비교·실행환경·라이선스 확인 후 확정. 저장소 제품은 아직 선택하지 않음                                              | RAG / 실데이터 인덱스                   |
| D08 | 후보 수·반복 상한 미정; 후보 소진 시 §2.3은 Summary, §4 그림은 부적격이면 END | §2.3, §4 loop, §8, §11                        | 후보 5, 후보별 추가조사 총 2회, 보고서 수정 총 2회. 정상 조사 후 후보 고갈은 사유 있는 Summary로 통일. 비용·총 실행시간 상한은 live 실행 전 명시 | Graph + 전원 / 실 API 실행              |
| D09 | 보고서 상세 목차·렌더러·페이지 측정 미정                                      | §9, §11                                       | [reporting](reporting.md)의 목차·인용·구조 검증은 부분 승인. 렌더러·A4·폰트·여백·SUMMARY 측정은 OPEN | 보고서 / 최종 제출                      |
| D10 | 팀원 명단과 원본 역할 표기가 불일치                                           | §11, §12 마지막 원본 L2904, L2924–2926, L2988 | 실제 수행 역할은 이슈 assignee와 PR 작성자 기록으로 확인. 원문의 별명/이름을 자동 매칭하지 않음                                                  | 전원 / Contributors                     |
| D11 | DAY 3 마감의 실제 날짜·캠퍼스·반 미상                                         | §10 제출 원문                                 | DAY 3 10:00 / 15:00를 상대 일정으로 보존. 실제 날짜·시간대·제출 채널 확인                                                                        | 전원 / 제출 일정                        |
| D12 | 국내 중심 API와 국내외 탐색 목표의 범위 차이                                  | §1.1, §5                                      | 계약에는 국가를 포함. 한국 fixture부터 연결하되 해외 미지원은 명시적으로 표시. 국내만으로 최종 범위를 줄이려면 승인                              | Discovery / 후보 범위                   |
| D13 | HTML·PPT·추출 PDF의 200페이지 산정 규칙 미정                                  | §1.3, §6.2                                    | manifest에 원본·허용 페이지 구간 기록. HTML은 고정 PDF snapshot, PPT는 슬라이드 수. 승인 전 페이지 미상 자료는 인덱싱 보류                       | RAG + 과제 확인 담당 / 코퍼스           |
| D14 | 세부항목별 1~5점 rubric과 재무 지표 적용 조건 미정                            | §3 상세 기준                                  | [scoring](scoring.md)의 공통 척도를 바탕으로 각 항목의 근거·점수 예시 작성. SaaS 경험칙과 투자 단계 순서를 자동 점수 규칙으로 쓰지 않음          | 지표 / 실제 평가·추천                   |

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

7개 항목의 승인자·승인일·승인 근거를 기록하고 scoring.md·architecture.md·contracts.md와 문서 홈에 승인 상태를 반영했다. 이슈 #3 승인에서는 다른 결정을 변경하지 않았다. D09의 후속 부분 승인은 아래 이슈 #14 기록을 따른다. 코드·정책 파일은 이 이슈에서 작성하지 않는다. PR 병합은 별도 요청 후 수행한다.

## D09 보고서 형식 승인 기록 — 이슈 #14

- Decision ID: D09 (목차·인용·구조 검증 부분)
- Status: APPROVED (목차·인용·구조 검증 부분)
- Decision: [reporting.md](reporting.md)의 single_candidate/no_recommendation 각각 7개 섹션, Evidence 인용 token과 Source ID 기반 REFERENCE, 서지 미상 표기, SV01–SV09 체크리스트를 채택한다. PDF 전체 5페이지·SUMMARY 반 페이지 과제 조건은 유지한다.
- Rationale and source: 원문 §9.1–9.3 및 contracts.md §5. 보고서 근거 추적·참고문헌 양방향 일치와 미평가/실패 후보 구분을 구현 가능하게 명시한다.
- Rejected alternatives: 미채택 — 두 mode에 동일 기업 목차 강제; URL만으로 인용 대응; 검색한 모든 자료를 REFERENCE에 포함; 미상 날짜를 수집일로 대체; Markdown 길이로 PDF 분량 판정.
- Affected documents / policy version / tests: reporting.md, delivery.md §5, docs/README.md. #26 ReportContext, #27 T14·T23, #28 fixture 보고서·수정 loop, #30 E2E. 보고서 형식 버전 식별자는 구현 이슈에서 부여.
- Owner and reviewers: xxhigh / 보고서·Graph·검증 담당 검토 대상(별도 승인 받은 것으로 간주하지 않음).
- Approval date: 2026-09-30 (Asia/Seoul), 승인자 xxhigh. [승인 근거](https://github.com/rice-steamed-water/skala-rag/issues/14#issuecomment-5902607679).
- Supersedes: 없음.

D09 중 렌더러·A4·폰트·여백·SUMMARY 측정 기준·인용 token 화면 변환은 M3까지 OPEN이다. 이 기록만 부분 APPROVED이며 PDF 선택과 D09 전체 완료 여부를 구별한다.

## D14 제안 기록 — founder·market·technology·moat (#10, OPEN)

- Decision ID: D14 (founder·market·technology·moat 부분)
- Status: OPEN — 제안 작성 완료, 팀 승인 대기
- Decision: [rubric-core.md](rubric-core.md)와 `configs/rubrics/core.yaml`(rubric_version core-0.1.0)의 14개 criterion rating 1–5 기준·최소 근거·missing 코드를 baseline으로 채택. 승인된 D05에 따라 적용조건 미확정은 `missing + applicability_note`이며 이 14개에는 not_applicable을 쓰지 않는다. 근거 없는 rating·다른 기업 근거·미해결 상충은 missing, 회사 자기주장만이면 최대 4.
- Rationale and source: 원문 §3 상세 기준, scoring §2–§4, D02·D05(APPROVED). 설계 산출물 v3 C-2·C-3(#35 / PR #36, 미병합)은 병합 시 재검토.
- Rejected alternatives: 미채택 — 근거 부족을 rating 1–2로 처리; 시장성·기술력 N/A 허용(저점수 조건 우회); 전체 로봇 시장 수치로 세부 시장 평가; 특허 검색 0건을 곧바로 "특허 없음"으로 처리.
- Affected documents / policy version / tests: scoring.md §2, rubric-core.md, configs/rubrics/core.yaml, tests/unit/test_core_rubric.py; #22 평가 wrapper, #16 집계.
- Open sub-questions: rubric-core.md §6 Q1–Q5
- Owner and reviewers: XXXXXim / 지표·평가 담당 검토 요청 대상.
- Approval date: -
- Supersedes: 없음.

## D14 제안 기록 — traction·deal_terms (#11, OPEN)

- Decision ID: D14 (traction·deal_terms 부분)
- Status: OPEN — 제안 작성 완료, 팀 승인 대기
- Decision: [rubric-finance.md](rubric-finance.md)와 `configs/rubrics/finance.yaml`(rubric_version finance-0.1.0)의 9개 criterion rating 1–5 기준·최소 근거·missing 코드, 재무 단위 규칙(런웨이 개월 통일, 연간→월 환산은 derived, 분모 0 이하·기간 불일치는 missing)을 baseline으로 채택. 승인된 D05에 따라 pre-revenue·영업현금흐름 흑자 등 적용조건 문제는 `missing + applicability_note`로 두고 비중을 제거하지 않는다.
- Rationale and source: 원문 §3 실적/투자조건 상세 기준, scoring §2–§4, D02·D05(APPROVED), T21. SaaS 경험칙·투자 단계 순서는 자동 점수 규칙에서 제외.
- Rejected alternatives: 미채택 — SaaS 기준 매출총이익률 구간(70–80%); 단계별 자동 가점; pre-revenue를 rating 1로 처리; 분모 0 이하일 때 런웨이 무한대/최대점 처리.
- Affected documents / policy version / tests: scoring.md §3·§4, rubric-finance.md, configs/rubrics/finance.yaml, tests/unit/test_finance_rubric.py; #16 집계, #22 평가 wrapper.
- Open sub-questions: rubric-finance.md §5 Q1–Q6 (작성자 의견 포함). Q4·Q6의 not_applicable 안은 v3(#35 / PR #36)가 D05를 대체하도록 승인될 때만 적용.
- Owner and reviewers: XXXXXim / 지표 담당(heojiwon2) 검토 요청 대상.
- Approval date: -
- Supersedes: 없음.

## 조용히 바꾸면 안 되는 원문

- 현재 팀 가중치를 교수님 예시 `30/25/15/10/10/10`으로 되돌리지 않는다.
- `PASS`를 적격성 통과나 보고서 검증 통과라는 의미로 재사용하지 않는다.
- 5개 병렬 노드를 구현했다고 투자조건 10%를 누락하거나 나머지를 100으로 재정규화하지 않는다.
- “특정 항목 2점”을 비중이 1점인 세부항목의 획득점수와 직접 비교하지 않는다.
- 최종 embedding 후보가 공개되어 있다는 사실만으로 과제의 오픈소스 요구 충족을 선언하지 않는다.
- 원문의 도구 비용·접근성 표는 당시 메모다. 키 발급, 이용조건, 접근 성공은 구현 시 다시 확인해야 한다.
