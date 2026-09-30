# 결정 목록 — v3 방향·운영 승인과 남은 세부 정책

<a id="execution-python-direct"></a>
## EXECUTION-PYTHON-DIRECT — Python 직접 실행 승인 (#166)

- Decision ID: EXECUTION-PYTHON-DIRECT
- Status: APPROVED (실행 인터페이스 방향만; 문서 정합화)
- Decision: Python 코드에서 기존 runner/Graph를 직접 호출하는 방식으로 마무리한다. 신규 CLI·flag·subcommand·console-script 패키징·CLI UX/배포 개선은 중단한다. 기존 병합 CLI/parser와 테스트는 호환성으로 보존한다.
- Rationale and source: 사용자 작업 대화 “cli 관련 부분은 더이상 진행하지 말자. python을 직접 실행하는 것으로 끝내자.” 및 “issue로 만들어서 진행해. 설계 문서 업데이트부터”; [#166](https://github.com/rice-steamed-water/skala-rag/issues/166)에 기록.
- Rejected alternatives: CLI-first 추가 개발; `python -m skala_rag.cli`를 직접 callable 실행으로 재명명; 존재하지 않는 실행 스크립트/API를 구현된 것으로 안내; 기존 코드·테스트 삭제.
- Affected documents / policy version / tests: [Python 실행 안내](python-execution.md), README·문서 홈·architecture·contracts·delivery·alignment·fixture 안내. 정책/schema 버전 변경 없음. Python 호출 receipt·artifact 검증과 기존 fixture 회귀를 구별한다. #96 live runner 및 #111 도식은 담당자 정합화 대상이며 이 결정으로 완료되지 않는다.
- Owner and reviewers: 사용자 승인, 기록자 luk0715 (#166); 다른 담당자 승인을 추정하지 않는다.
- Source date: 2026-09-30 (작업 대화 및 이슈 근거 기록일). 이슈 생성 `2026-09-30T09:16:42Z`는 사용자 승인 시각이 아니며 별도 승인 시각은 미기록이다.
- Supersedes: 신규 실행 인터페이스의 CLI-first 방향과 CLI 기능/프로세스 종료 코드를 새 개발 완료 조건으로 요구하는 부분만 대체한다. 아래 D01–D14·V3-OPERATIONS 원문 기록은 수정하지 않는다. completed+Warning·현재 draft/findings·final 금지·fatal 구분은 유지하며 CLI exit 0/2/1은 기존 호환 매핑이다.
- Still gated: 정책·rubric·selector·provider·corpus·예산/readiness·유료 호출 승인은 이 결정의 범위가 아니다. 기존 `skala_rag.cli.run(...) -> Path` 재사용만 안내하며 runner 이동·새 RunResult API·live 구현 완료를 주장하지 않는다.

## #158 현재 실행 provider 제외 승인 범위

사용자의 이번 실행 지시로 KIPRIS·KRX·중기부·Tavily를 제외하고 각각
#154/#155/#156/#157 후속 작업으로 보류한다. Tavily 실패는 사용자 보고이며
이 구현에서 재현한 관측이 아니다. 기존 승인·adapter·fixture는 이력/호환성으로
보존한다. 기존 승인 RAG/공식 출처는 원래 승인·readiness·예산 gate 아래 계속한다.
새 Naver 도입·유료 fallback·예산 확대·M3 실행은 승인하지 않았다.
Evidence 부족은 missing이며 rubric/evaluator semantic gate를 완화하지 않는다.
범위 축소를 M2 전체 성공이나 #48/#55 완료로 표시하지 않는다.
[caller 주입 경계와 미연결 owner 파일](provider-scope.md)을 따른다.

[문서 홈](../README.md) · [v3 원문](../design/design-v3.html) · [이전 통합 원문](../raws/robotics_startup_agentic_rag_notion_integrated.md) · [정합화·영향표](design-v3-alignment.md)

새 구현은 v3를 따른다. **방향 승인, 운영 규칙 승인, 구조 DTO 가용성, 실제 runtime 구현은 별개**다. #3의 D01–D06·D08, #14의 D09 부분 승인과 D14 OPEN 원본 10개 레코드는 아래에 byte-identical 이력으로 보존한다. 과거 기록의 상태·날짜·근거를 소급 변경하지 않는다. 아래 후속 승인이 현재 v3 범위를 정하며, 남은 `v3-OPEN`만 기본값 설치/live 실행의 gate다.

## #168 승인 값의 live-compatible scoring 계약 — 2026-09-30

- Status: APPROVED (기존 승인 값의 계약 코드 호환 경계만).
- Source: #168 본문의 사용자 확인 UI 기록과 이번 구현 지시. #82 운영 값,
  Core `core-0.1.0`, Finance `finance-0.1.0`의 기존 값만 보존한다.
- Decision: legacy draft 계약을 live로 확장하지 않고 별도 승인 계약을 둔다.
  외부 승인 근거/버전을 신뢰된 caller verifier가 확인하며, runtime readiness와
  call/token/cost 예산 gate는 별도로 확인한다. fixture가 기본이며 OPEN 의존성은 차단한다.
- Not approved: 새 점수/rubric/ranking 값, 최소 Evidence/Coverage gate, 새 RNG,
  시간·호출·비용 상한, 네 제외 provider 복구, paid fallback, 실제 유료 호출 또는 M3.
- Implementation: [호출 계약 및 남은 gate](live-scoring-policy.md). 기존 fixture
  config의 `not_approved` budget/readiness를 승인으로 덮어쓰지 않는다. rubric 파일의
  승인 상태 반영과 실제 semantic verifier는 각 owner 작업이며 여기서 완료하지 않는다.
- Historical records: 아래 기존 D14 OPEN 기록은 소급 변경하지 않는다. #168이
  참조하는 정확한 Core/Finance 버전 승인과 미결정 controller 세부를 구분한다.

## v3 전환 방향 승인 — 별도 기록

- Decision ID: V3-TRANSITION
- Status: APPROVED (전환 방향)
- Decision: baseline 계속 구현 대신 v3로 전환한다. 전 후보 처리 후 deterministic selector, 5 branch/6 dimension과 atomic Business & Deal, Missing 포함/N/A 제외 분모, 네 label, market/technology 40% 보류, 추가 조사·보고서 수정 최대2회와 Warning, E-1 다섯 섹션을 새 방향으로 한다.
- Rationale and source: [#35 comment 5902877317](https://github.com/rice-steamed-water/skala-rag/issues/35#issuecomment-5902877317), luk0715가 사용자 확인 응답을 기록.
- Rejected alternatives: 새 구현에서 baseline 방향을 계속 적용하지 않는다.
- Affected documents / policy version / tests: #35 가이드 및 후속 구현. 방향 승인 자체는 schema/policy 버전 확정이나 runtime 검증이 아니다.
- Owner and reviewers: 사용자 승인, 기록자 luk0715. 다른 담당자 승인을 추정하지 않는다.
- Approval date: 2026-09-30T02:29:07Z (근거 기록 시각).
- Supersedes: 새 작업의 baseline 계속 구현 방향. 역사적 레코드의 상태는 유지한다. 아래 V3-OPERATIONS와 V3-PRE-EVALUATION-RANDOM이 후속 상세 범위를 정한다.

## v3 운영 규칙 승인 — 별도 기록

- Decision ID: V3-OPERATIONS
- Status: APPROVED (아래 운영 규칙 한정; 구현 완료 아님)
- Decision:
  - 자료 부재는 `missing`. `not_applicable`은 적용 사유·승인된 applicability rule·적용성 Evidence가 있을 때만 허용한다. Missing은 분모에 남기며 정당한 N/A만 제외한다.
  - 전체 또는 차원의 분모가 0이면 점수를 생성하지 않고 명시적 후보 오류를 남겨 archive → advance한다. 임의 0/100점·추천 label을 만들지 않는다.
  - 최종 selector는 적격·정상 평가 후보 중 `RECOMMEND_PRIORITY` 우선, 다음 `RECOMMEND`; 같은 label이면 `normalized_score DESC`, `weighted_missing_pct ASC`, **원본 `candidate_id ASC`** 순이다. 입력 순서는 기준이 아니다. 전부 WATCHLIST/PASS면 선택 없이 비교 보고서를 만든다. 부적격/unknown/failed를 추천하지 않는다.
  - Evidence 추가 재조사는 최초 수집 제외 후보별 2회, 요청 전에 예산 차감(count 증가), empty/failure도 소비하며 평가 후 재조사는 없다.
  - 보고서 구조·의미 수정은 최초 생성 제외 공유 2회. 소진 시 `workflow_status=completed` + Warning, 현재 draft/findings 반환, validated final 발행 금지, **CLI exit=2**. context/upstream 파손은 `failed`다.
  - 기존 catalog 23 ID·6차원·비중 `5/30/25/20/10/10`, 결측률 `>=30%`, Market/Technology 적용가능 배점 비율 `<=40%`, normalized_score의 `80/70/60` 경계·네 label을 보존한다. 비교에는 반올림 전 exact 수치를 쓰고 표시 반올림은 계산에 넣지 않는다.
- Rationale and source: [#35 comment 5903505208](https://github.com/rice-steamed-water/skala-rag/issues/35#issuecomment-5903505208), [#82 명시 승인 본문](https://github.com/rice-steamed-water/skala-rag/issues/82). 사용자 확인 UI에서 운영 세부안을 전체 승인했다는 기록.
- Rejected alternatives: 미상 자료를 N/A로 제외, 0분모 임의 점수, 입력순/무작위 최종 selector, 오류 batch 환불, 평가 후 재조사, 미검증 draft의 validated final 승격.
- Affected documents / policy version / tests: D01·D02·D03·D05·D08·D09 중 위 범위; scoring/contracts/architecture/delivery의 T01–T03/T07–T09/T16/T20. #82/PR #85는 별도 versioned config/loader/fixture 작업이며 pinned snapshot에서 OPEN Draft, head `05bc4318abee114b4adc85de73a61f04b7e9a778`. 이 문서의 통합 기준에는 없고 runtime 연결 완료도 아니다.
- Owner and reviewers: 사용자 승인; #82·#35 근거 기록. 다른 담당자 승인을 추정하지 않는다.
- Approval date: 2026-09-30 (근거 기록일; 확인 UI의 별도 시각은 추정하지 않음).
- Supersedes: 새 v3 실행 정책에서 baseline 고정100·모든 영역 관측 rating 보류·첫 추천 종료·평가 후 조사·보고서 소진 일괄 failed 대신 위 운영 규칙을 적용한다. baseline 원본 기록은 그대로 보존한다.
- Still OPEN: 실제 rubric/applicability rule 목록·품질 검증, 최소 Evidence/사전 Coverage gate, Company Research 보강·transport retry 회계 변경, 대표 reason 표시·표시 반올림 형식, runtime schema/manifest 연결, PDF renderer/layout 회계, provider·corpus·모델 실험·live 시간/호출/비용 예산.

## 평가 전 후보 무작위 선정 승인 — 최종 selector와 분리

- Decision ID: V3-PRE-EVALUATION-RANDOM
- Status: APPROVED (평가 전 조사·평가 대상 기업 선정 방식만)
- Decision: 사용자의 “후보 선택은 무작위로 진행하면 될 것 같아.”와 단계 확인 응답 “평가 시작 전, 조사·평가할 후보 기업 선택”에 따라, 발견·정규화·중복 제거된 후보에서 **조사·평가할 집합을 무작위 선정**한다. 상한 초과 시 남길 집합 선정도 이 범위다. 단순 Iterator 순서 shuffle로 축소하지 않는다. 선정된 모든 후보는 Company Research·Eligibility를 거치며 적격 후보만 평가한다.
- Rationale and source: [#35 comment 5903574761](https://github.com/rice-steamed-water/skala-rag/issues/35#issuecomment-5903574761), 사용자 응답과 범위 확인 기록.
- Rejected alternatives: 최종 Best Candidate Selector를 무작위로 변경; dedup/Eligibility 생략; 조사 대상 집합은 그대로 둔 채 처리 순서만 섞어 요구 충족으로 표시.
- Affected documents / policy version / tests: #17/#48의 사전 선정 경계, architecture 흐름·contracts·delivery T24/T19. PR #77은 `CandidateLimitPolicy` 주입 경계만 제공하며 기본 random 정책은 없다. RNG 주입, 모집단/선정·제외 ID·정책 버전·replay metadata 기록은 구현 제안이다.
- Owner and reviewers: 사용자 승인; 근거 기록자 luk0715.
- Approval date: 2026-09-30T03:39:05Z (근거 코멘트 기록 시각).
- Supersedes: 평가 전 상한 초과 후보를 어떤 방식으로 남길지에 대한 미결정 중 무작위 방식만 승인. V3-OPERATIONS의 최종 순위·동점 규칙은 변경하지 않는다.
- Still OPEN: 새 `max_candidates` 값, 구체 난수 알고리즘·seed/재현 기록 방식, 추가 표본/보충 선정, 실행 예산. baseline 후보5 승인 이력은 보존하지만 이 응답으로 새로운 5 또는 다른 수치 기본값을 승인하지 않는다.

## 구현 전에 합의할 남은 세부 항목

표의 `v3-OPEN`은 **위 승인 규칙을 다시 OPEN으로 돌리는 뜻이 아니다**. 승인된 정책도 후속 코드 연결·검증은 별도이며, #74의 독립 v3 DTO 병합이 이를 실행하지 않는다.

| ID / 상태 | 승인된 v3 범위 | 남은 결정·제안 / 차단 대상 |
| --- | --- | --- |
| D01 운영 APPROVED / 연결 OPEN | 기존 23 ID·6차원·비중 보존 | 정책 버전 연결·catalog 완전성 검증 |
| D02 운영 APPROVED / 표현 OPEN | 네 label·80/70/60·결측30%·핵심40%·exact 비교 | 표시 반올림 형식·다중 reason 대표 표시; missing 유래 비율 저하와 부정 관측 구별 |
| D03 selector APPROVED / 예외 연결 OPEN | label → score DESC → missing ASC → 원본 candidate_id ASC, 전부 WATCHLIST/PASS 무선택 비교 보고서 | 성공 평가 없음/전부 기술실패의 결과 payload와 보고서 mode 매핑; 기술실패를 비추천으로 바꾸지 않음 |
| D04 방향 APPROVED / runtime 연결 OPEN | atomic Business & Deal, 5 branch/6 dimension; #74 EvaluationBranchResult 구조 제공 | State/reducer·wrapper·Join 원자적 저장·schema version 연결 |
| D05 운영 APPROVED / rule·gate OPEN | missing와 정당한 N/A 분리, 0분모 후보 오류/archive/advance | applicability rule 목록·실제 근거 검증, Coverage 충분성·gap 우선순위 |
| D06 v3-OPEN | 비상장·Seed~C·Exit 미완료, baseline 정규화 이력 보존 | 최소 Evidence 확보 가능/현재 확보 gate·unknown 보강 경로·Company Research 경계. TIPS/검색0건 자동 적격 금지 |
| D07 OPEN | BGE-M3 1차 선택, e5/KURE 비교 방향 | 최종 모델·revision·라이선스·MRR cutoff·장비/vector store·실험 승인 |
| D08 운영/무작위 방식 APPROVED / 예산 OPEN | 추가 조사2회·요청 전 소비·empty/failure 소비·평가 후 조사 없음; 구조/의미 공유 수정2회·completed Warning·CLI2; 사전 후보 집합 무작위 선정 | 새로운 후보 수·난수/seed/replay·보충 선정, Company Research/도구 retry 회계 변경, live 총시간·LLM 호출·비용. baseline 5/2/2·8calls/retry2/30초 이력 보존 |
| D09 방향/Warning APPROVED / 형식 OPEN | E-1 다섯 목차, Warning draft/findings·final 금지; 과제 PDF≤5·SUMMARY≤0.5 | mode별 섹션 예외·구조 검증 상세, renderer·A4·폰트·여백·SUMMARY 측정·layout 회계 |
| D10 OPEN | v3 표지 명단 보존 | 실제 역할·계정 매핑은 수행 증거와 본인 확인 필요 |
| D11 OPEN | 원문 DAY 3 일정 보존 | 실제 제출 날짜·시간대·Slack thread |
| D12 OPEN | Physical AI / Robotics 도메인 | 국가 범위·지원 provider·미지원 표시·live 탐색 |
| D13 REJECTED | 전체 RAG 문서≤200페이지 한도 적용 제외 ([기록](#d13--200페이지-산정-규칙-적용-제외-91)) | 없음. 승인 corpus manifest gate(#44)는 페이지 산정 없이 유지 |
| D14 OPEN | N/A에 승인 rule·사유·근거 필요 | criterion별 rating·rule·품질 기준, 재무 단위/기간/동일 라운드·burn 적용성 |

승인되지 않은 세부는 가상 정책 주입/인터페이스로 검증하며 live 기본값으로 숨기지 않는다. 승인된 운영 규칙의 기대값 테스트와 남은 OPEN gate 테스트를 분리한다. 현재 구현 가용성은 [공통 계약](contracts.md)과 pinned [정합화 기록](design-v3-alignment.md)을 따른다.

## 승인 방법

승인된 v3 방향만으로 결정할 수 없는 세부 항목은 `v3-OPEN`으로 남기고, 해당 기능은 fixture/인터페이스까지만 구현한다. baseline 승인 기록을 OPEN으로 되돌리거나, 반대로 이를 v3 replacement의 승인으로 읽지 않는다. `OPEN`을 기본값으로 감추거나 LLM이 실행 때 임의 결정하게 하지 않는다.

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

## D13 — 200페이지 산정 규칙 적용 제외 (#91)

- Decision ID: D13
- Status: REJECTED
- Decision: 이 프로젝트는 원문 §1.3·v3 B-2의 RAG 코퍼스 200페이지 한도(R05)를 적용하지 않는다. 따라서 페이지 산정 규칙(HTML·PPT·부분 PDF)을 정하지 않으며, corpus manifest gate는 문서 승인·추출 상태·버전 고정·인덱스 입력 대조만 검사한다.
- Rationale and source: 팀 결정 — 이 프로젝트에서 200페이지 제한 규칙이 필요 없다고 판단했다(heojiwon2, 2026-09-30 작업 대화).
- Rejected alternatives: 미채택 — 전체 코퍼스 200페이지 합계 gate; 형식별 페이지 산정 규칙 승인 후 적용.
- Affected documents / policy version / tests: data-rag.md §3, delivery.md §3·T11, docs/README.md R05, design-v3-alignment.md(후속 변경 메모); #44 / PR #88 `rag/corpus.py`·`tests/unit/test_corpus_gate.py`; #13·#62의 200페이지 관련 할 일.
- Owner and reviewers: heojiwon2 / RAG 담당·팀 전원 검토 요청 대상.
- Approval date: 2026-09-30 (Asia/Seoul), 결정자 heojiwon2.
- 과제 담당자 확인 근거: **미기록.** 과제 필수 조건 완화에 해당하므로 아래 승인 방법에 따라 확인 근거 링크를 추가해야 한다.
- Supersedes: v3 남은 세부 항목 표의 `D13 OPEN`.

## 조용히 바꾸면 안 되는 원문

- 현재 팀 가중치를 교수님 예시 `30/25/15/10/10/10`으로 되돌리지 않는다.
- `PASS`를 적격성 통과나 보고서 검증 통과라는 의미로 재사용하지 않는다.
- 5개 branch라는 이유로 투자조건 차원을 누락하지 않는다. 승인된 v3 방향은 해당 없음 N/A만 분모에서 제외하고 Missing은 남기는 것이다. 적용 사유·승인 rule·Evidence 요건과 0분모 후보 오류는 V3-OPERATIONS에서 승인되었다. 실제 rule 목록·품질 검증만 `v3-OPEN`이다. 기존 고정100 + `missing + applicability_note`는 baseline 호환성·역사이며 새 구현의 우선 방향이 아니다.
- “특정 항목 2점”을 비중이 1점인 세부항목의 획득점수와 직접 비교하지 않는다.
- 최종 embedding 후보가 공개되어 있다는 사실만으로 과제의 오픈소스 요구 충족을 선언하지 않는다.
- 원문의 도구 비용·접근성 표는 당시 메모다. 키 발급, 이용조건, 접근 성공은 구현 시 다시 확인해야 한다.

## #52 텍스트 범위 corpus 사용 승인 — 2026-09-30

xxhigh가 [사용자 확인 UI에서 텍스트 전용 범위를 승인](https://github.com/rice-steamed-water/skala-rag/issues/52#issuecomment-5905563007)했다.
π0 v4/π0.5 v1의 본문·caption·텍스트 표만 인덱싱하며 이미지·그래프 미추출 정보는
한계로 보존한다. 기존 전체 문서 `partial`을 유지하고, 원문/페이지 텍스트 hash·추출 설정·
검토된 누락 항목에 묶인 별도 text_index_review로 승인 범위를 고정한다.
다른 partial 문서·텍스트 누락/추출 실패를 이 승인으로 허용하지 않는다.
실제 두 PDF/36페이지 텍스트 검증과 범위 gate 결과는
[검증 기록](issue52-text-scope.md)에 있다. BGE-M3 다운로드·embedding 실행·store 선택·
#52 전체 완료는 승인/검증되지 않았다.

### #52 후속 범위 승인 — 2026-09-30

이후 xxhigh가 실제 embedding/index 성공 조건을 후속 작업으로 옮기고 현재 #52의
blocked 제거를 요청했다. 로컬 다운로드 대신 Hugging Face API를 사용하며,
endpoint 주소는 이후 설정하고 지금은 API 인터페이스·테스트를 진행하도록 확인했다.
위 이전 승인 기록의 전체 완료 보류는 이 구현 범위 변경으로 대체한다.
모델/tokenizer 다운로드 보류와 실제 실행 미검증은 유지한다. endpoint/provider 지원,
배포 revision 확인·실제 저장소 선택·실측 검증은 후속 live 작업에서 해결한다.
SQLite는 선택 가능한 offline 구현이며 운영 기본값을 정하지 않는다.

## D09 PDF renderer·분량 측정·layout 회계 승인 — #95

- Decision ID: D09 PDF
- Status: APPROVED
- Decision: ReportLab4.4.9, NanumGothic Regular/Bold(OFL1.1 포함), A4·18mm 여백,
  본문10.5pt/leading15pt·heading14pt. SUMMARY heading 포함 실제 draw bbox 높이를
  전체 A4 높이로 나누며 단일 page·fraction<=0.5. 표지/REFERENCE 포함 PDF<=5page.
  renderer는 사실/점수/label을 바꾸지 않으며 저장 파일의 페이지/인용/hash를 대조한다.
- Error accounting: layout 위반은 기존 공유 report 수정 예산(최대2회)을 소비한다.
  renderer 오류와 변경/stale artifact는 fatal. 새 render retry loop는 없고
  소진/미검증 PDF는 final 승격 금지. 수정된 draft는 구조·의미 검증부터 다시 실행한다.
- Rationale: 실제 PDF fixture 검토와 사용자 확인 UI의 명시적 승인.
- Rejected alternatives: 이번 범위에 여러 renderer 비교·새 품질 benchmark·추가 재시도 없음.
- Affected: configs/pdf.layout.v1.json, pyproject/uv.lock, reporting.pdf, #94/#96 runner 인계.
- Owner/reviewer: wjd990819-ops / 사용자
- Approval date: 2026-09-30
- Evidence: 사용자 질문 답변 「제안한 설정·의존성·오류 처리 승인」.

앞선 D09 PDF OPEN 표현은 당시 기록이다. 위 PDF 선택/측정/회계만 승인되었으며
mode별 예외·실제 Generator/Judge·M3 live 완료를 함께 승인하거나 완료로 표시하지 않는다.

## #94 D09 mode 형식·Generator/Judge 사용 범위 승인 — 2026-09-30

xxhigh가 [제안한 형식·모델 범위를 승인](https://github.com/rice-steamed-water/skala-rag/issues/94#issuecomment-5906219758)했다.
기존 OpenAI gpt-4.1-mini-2025-04-14 adapter를 Generator/Judge에 주입하고
single_candidate/no_recommendation 모두 E-1 다섯 섹션을 유지한다.
무선택 SUMMARY에는 selector의 이유와 후보 비교를 보존하며 upstream 점수·판정·N/A·
인용을 바꾸지 않는다. 실제 API 성공 검증은 #96 최종 live 실행에서 확인한다.
이 승인은 미확인 credential/요금/예산의 호출, 새 모델 비교·다운로드 승인이나
M3 품질 완료가 아니다. [구현·검증 경계](reporting-v3-pipeline.md)를 따른다.

## D14 Finance 승인 — #61 (2026-09-30)

Status: APPROVED (Finance 부분만). 사용자 승인 기록: #61 comment5906253348.
finance-0.1.0 §1–§3의 9개 criterion 구간·최소근거 및 Q1–Q6는
[rubric-finance 현재 승인](rubric-finance.md)의 범위로 승인되었다. 이전 Finance
OPEN·개정 제안은 이력이다. Core rubric·다른 정책을 승인하지 않는다.
N/A는 확인된 pre-revenue Rule40·동일 기간/주체 OCF≥0 runway만 허용;
rule ID/reason/snapshot evidence 필수, 미확인은 missing, burn5는 실제 재무근거 필요.
작은 기저 limitations 필수(새 cap/threshold 없음), pre/post 미상은 missing,
valuation 직전3배/동종중앙값2배, CAPEX 제외. 정책 승인과 #55 live 완료는 별개.
