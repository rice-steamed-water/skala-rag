# 팀 구현 가이드 — Robotics Startup Agentic RAG

> 상태: **v3 전환 방향 승인 / baseline 이력 보존 / 세부 정책 OPEN** · 기준일: 2026-09-30 KST
> 대상: 기능을 나누어 구현하고 통합할 팀원
> 새 설계 입력: [설계 v3](design/design-v3.html) · 이전 설계·과제 요구: [통합 원문](raws/robotics_startup_agentic_rag_notion_integrated.md)

## 먼저 할 일

**현재 우선 목표 (#219):** [Offline v3 데모 패키지](implementation/offline-delivery.md)의 합성 데모 산출물과 재현 안내를 완성한다. 실제 기업 평가·M3 live·최종 발행이나 과제 전체 완료를 뜻하지 않는다. #96/#168, 원래 필수 요구와 아래 historical snapshot은 유지한다.

**실행 인터페이스 후속 승인 #166:** [Python 직접 실행](implementation/python-execution.md)으로 마무리하며 신규 CLI·옵션·console-script·CLI UX 개발은 중단한다. 기존 #29 fixture callable/parser·테스트는 호환성으로 보존한다. 이 문서의 과거 pinned 구현 snapshot과 CLI2 승인 설명은 이력이며 현재 실행 완료 조건은 Python 상태/receipt·산출물 검증이다. 정책·예산/readiness·live 승인 범위는 바꾸지 않는다.

1. 이 문서에서 목표와 필수 요구사항을 확인한다.
2. [v3 정합화·영향표](implementation/design-v3-alignment.md)와 [결정 목록](implementation/decisions.md)을 읽는다. 특히 **#82 운영 승인·사전 무작위 선정 승인과 남은 OPEN**을 구별한다.
3. 모든 구현 담당자가 [공통 데이터 계약](implementation/contracts.md)을 먼저 읽는다.
4. [작업 분담과 검증](implementation/delivery.md)의 M0 → M1 순서로 시작한다. 외부 API부터 각자 연결하기보다, 같은 fixture로 전체 흐름을 먼저 맞춘다.

과거 pinned 통합 기준 `906312a`에는 baseline DTO/State/reducer/catalog/점수·재무 helper·DTO adapter뿐 아니라 #17 발견/Normalize, #18 fixture 조사·Eligibility, #19 GuardedRetriever/EvidenceCollector, #22 dimension 평가 wrapper, #23 fixture 후보 Graph, #26 ReportContext, #27 baseline Structural Validator 및 #73/PR #74의 독립 `contracts.v3` 구조 DTO가 있다. #23은 baseline 첫 추천 인계이며 v3 전 후보 selector가 아니다. v3 구조 DTO 자체는 계산·selector·0분모/Warning controller·State/Graph 연결을 실행하지 않는다. 이후 #29 fixture callable·보고서/PDF 경로의 가용 범위는 [Python 실행 안내](implementation/python-execution.md)로 보완하며 신규 CLI는 개발하지 않는다. 설치·검증 명령은 [루트 README](../README.md), pinned 통합과 historical snapshot은 [정합화 기록](implementation/design-v3-alignment.md)을 따른다.

## 문서를 읽는 순서

후속 통합 기준 `1f23e09`에는 #6/PR #37의 평가·점수·보고서·manifest DTO·결정적 ID와 #68/PR #69의 baseline DTO adapter도 포함된다. [공통 계약](implementation/contracts.md)의 #6 구현 shape와 v3 확장 제안을 구별한다.

| 필요한 내용 | 문서 | 우선 독자 |
| --- | --- | --- |
| 최종 실행 인터페이스, 기존 callable·receipt와 호환 경계 | [Python 직접 실행](implementation/python-execution.md) | 전원 |
| v3 출처·절별 추적·현재 GitHub 작업 영향 | [v3 정합화 기록](implementation/design-v3-alignment.md) | 전원 |
| 전체 흐름, 노드 책임, 반복과 종료 | [아키텍처](implementation/architecture.md) · [v3 실행 그래프 (Archify HTML)](../.archify/architecture-v3-agent-20260930-172142/v3-agent.html) · [소스·검증 안내](../.archify/architecture-v3-agent-20260930-172142/README.md) | Graph / Agent 담당 |
| State, Evidence, 평가 결과, Tool 경계 | [공통 데이터 계약](implementation/contracts.md) | 전원 |
| 평가 항목, 가중치, 결측, 판단 라벨 | [점수와 판단 정책](implementation/scoring.md) | 평가 / 지표 담당 |
| 창업자·시장·기술·경쟁 우위 rubric, missing 조건 | [핵심 영역 rubric](implementation/rubric-core.md) | 평가 / 지표 담당 |
| 실적·투자조건 rubric, 재무 단위 규칙 | [재무 rubric](implementation/rubric-finance.md) | 평가 / 지표 담당 |
| 자료 수집, 코퍼스 manifest, 검색, 임베딩 비교 | [데이터와 RAG](implementation/data-rag.md) | 데이터 / RAG 담당 |
| adapter readiness·요청 예산·transport retry·LLM 연결 API | [Adapter runtime](implementation/adapter-runtime.md) | live adapter / runner 담당 |
| mode별 보고서 목차, 인용, 구조 검증 | [보고서 계약](implementation/reporting.md) | 보고서 / 검증 담당 |
| 작업 패키지, 통합 순서, 테스트, 제출 | [작업 분담과 검증](implementation/delivery.md) | 전원 |
| 원문 충돌, 새 제안, 승인 기록 | [결정 목록](implementation/decisions.md) | 전원, 정책 결정 담당 |
| 이슈·브랜치·PR 규칙, 폴더 구조, 개발 도구 | [협업 규칙](../CONTRIBUTING.md) | 전원 |

## 우리가 만드는 것

사용자가 Physical AI / Robotics 투자 주제를 입력하면 시스템이 후보 기업을 찾고, 적격성을 확인하고, 출처가 있는 근거로 평가한 뒤 **투자 검토용 보고서**를 생성한다. 실제 투자 실행 시스템이 아니라 수업용 조사·평가 시스템이다.

```text
투자 주제 + 실행 설정 + 승인된 문서 코퍼스
→ 후보 발견 / 정규화·dedup / 조사·평가 대상 집합 무작위 선정 / 적격성 확인
→ Web·API·RAG 근거 수집 / 부족자료 보강
→ 후보별 5개 branch / 6개 점수 차원 평가·판단
→ 모든 후보 처리 완료 / deterministic Best Candidate Selector
→ 보고서 생성 / 구조·의미·PDF 검증
→ 검증된 보고서 또는 Warning 포함 현재 결과 + 근거·검증·실행 기록
```

**성공의 기준:** 보고서가 나온 것만으로 충분하지 않다. 실제 RAG 검색 결과가 평가에 사용되고, 점수와 인용의 근거를 추적할 수 있으며, 모든 반복이 종료되고, 같은 설정으로 다시 실행할 수 있어야 한다.

## 요구사항의 출처와 강도

| 표기 | 의미 | 변경 방법 |
| --- | --- | --- |
| **과제 필수** | 원문에 실린 교수님 요구사항 | 팀 임의 완화 없이 담당 교수님 확인 |
| **v3 명시 목표** | 사용자가 전환 방향을 승인한 v3의 명시 내용 | 새 구현의 우선 방향; 세부 정책 승인·구현 완료와 구분 |
| **이전 원문 팀안** | 통합 원문에 보존된 이전 설계 | v3와 충돌하면 과거 제안으로 추적; 과제 요구 누락은 폐기로 해석하지 않음 |
| **구현 제안** | 원문을 실행 가능한 계약으로 보완한 초안 | 팀 승인 후 채택; 승인 전 확정안으로 표현하지 않음 |
| **미결정** | 원문만으로 결론을 낼 수 없는 사항 | 담당자·결정·근거·승인일 기록 |

**현재 구현 방향 — v3 전환 승인:** [사용자 전환 승인 #35 comment 5902877317](https://github.com/rice-steamed-water/skala-rag/issues/35#issuecomment-5902877317)(luk0715, 2026-09-30T02:29:07Z)에 따라 새 작업은 기존 baseline의 계속 구현이 아니라 v3에 정합화한다. baseline 코드·승인 기록은 호환성과 이력으로 보존하며 새 구현의 우선 방향이 아니다. 방향 승인에 이어 #82 및 #35 comment 5903505208에서 N/A·0분모·최종 selector·재조사 회계·Warning 종료의 운영 규칙을 별도 승인했다. #35 comment 5903574761의 무작위 선정은 평가 전 조사·평가 대상 집합에만 적용하며 최종 selector는 무작위가 아니다. 승인과 구현 완료는 별개이며 rubric 상세·provider·corpus·시간/비용 예산 등 남은 세부 선택만 [결정 목록](implementation/decisions.md)의 OPEN gate를 따른다. 각 상세 문서의 새 필드·예외 처리·함수명은 별도 승인 기록이 없는 한 구현 제안이다. OPEN에 의존하는 선택은 주입된 가상 정책·인터페이스까지만 진행하고 해당 live 실행을 차단한다.

D09의 baseline 목차(single_candidate/no_recommendation 각각 7개 섹션)·인용·서지 미상 표기·SV01–SV09 구조 검증의 2026-09-30 xxhigh 부분 승인은 이력으로 보존한다([보고서 계약](implementation/reporting.md)). 이후 사용자 승인으로 새 구현은 v3 E-1 다섯 목차·전 후보 selector·Warning 방향을 따른다. Warning completed·CLI2·final 금지는 #82 승인이다. mode별 섹션 예외·구조 검증 상세와 PDF 구현 선택은 OPEN이며 과거 승인이 그 세부를 자동 승인하지 않는다.

## 필수 요구사항과 검증 위치

아래 `§`는 통합 원문 절, `v3`는 새 설계의 절이다. 과제 필수 요구는 v3에서 생략되어도 보존한다. 원문 충돌은 결정 목록에서 추적한다.

| ID | 요구사항 | 출처 | 구현 / 검증 |
| --- | --- | --- | --- |
| R01 | LangGraph 기반 Multi-Agent + Agentic RAG | §1.1; v3 A-1 | 실제 Graph 실행 및 역할별 노드 trace |
| R02 | 도메인 Physical AI / Robotics | §1.1, §2 | 입력 범위와 후보 도메인 검증 |
| R03 | 비상장, Seed~Series C, Exit 미완료·최소 평가 가능성 | §2.2; v3 A-2 | 적격 / 부적격 / 정보부족 fixture; 최소 Evidence gate는 D05·D06 |
| R04 | 지정된 RAG 적용 대상 중 최소 1개 Agent에 실제 RAG 적용 | §1.3, §12 교수님 노션 B; v3 B-2 | Primary RAG인 Evidence Research의 검색→근거→Technology 기술 요약/평가→인용 trace |
| R05 | RAG 문서 총 200페이지 한정 | §1.3; v3 B-2 | **적용 제외** — [D13](implementation/decisions.md#d13--200페이지-산정-규칙-적용-제외-91) |
| R06 | 오픈소스 임베딩 적용, 후보·선택 근거 문서화 | §1.3; v3 B-3 | BGE-M3 1차 선택, e5/KURE와 동일 데이터 비교·라이선스 확인 후 최종 선택 |
| R07 | 가중치 평가, 결측 및 핵심차원 저점수 보류 | v3 C-1–C-4 | Missing/N/A, 정규화, 네 label, 경계값 검증 |
| R08 | Graph의 Loop / Branch 및 State 구현 | §4, §8; v3 D-1–D-3 | Coverage 재조사, 전 후보 처리·selector, 5 branch 합류, Warning 유한 종료 |
| R09 | 보고서 5장 이내; 첫 SUMMARY는 1/2페이지 이내 | §9.2; v3 E-1 | 최종 PDF 페이지 수와 렌더링된 SUMMARY 높이 검증 |
| R10 | 마지막 REFERENCE, 실제 사용 자료만 기재 | §9.2–9.3; v3 E-2 | 인용 ID와 참조 목록의 양방향 일치 |
| R11 | README 필수 항목과 개인별 실제 수행 역할 | §10.2–10.3 | 제출 체크리스트; PM/PL 역할 표기 제외 |
| R12 | 설계 PDF, GitHub 코드·README, 재현 가능한 투자 보고서 PDF | §10 | 제출 파일·재현 로그 확인 |

## 첫 구현의 범위

**v3 목표 및 이를 위한 구현 제안 — 포함**

- 정규화·dedup 후 무작위 선정된 조사 대상 모든 후보를 순차 검증하고 적격 후보를 평가한 뒤 결정적 selector가 최종 보고서 대상을 정한다. 첫 추천에서 종료하지 않는다.
- 한 후보 내부의 Founder / Market / Technology / Moat / Business & Deal 5개 branch는 병렬이며, 마지막 branch가 traction·deal_terms 두 차원을 함께 반환한다.
- RAG 문서 수집·검색과 출처 추적, 외부 검색 adapter, 공통 Evidence 저장소를 만든다.
- 여섯 점수 차원·23개 criterion을 보존한다. branch envelope는 D04 제안이며 부분 성공 집계는 금지한다.
- 우선추천·추천·보류·비추천, 적격성 정보부족, 후보 없음, 기술 실패를 구별한다. Warning 반환과 검증된 final 발행도 구별한다.
- Python 코드에서 기존 runner/Graph를 직접 호출한다(#166). 보고서 Markdown과 PDF, 실행 manifest를 남기고 상태·Warning·acceptance·publication을 따로 검증한다. CLI 추가 개발은 완료 조건이 아니다.

**이번 범위 밖 — 별도 합의 없이는 추가하지 않음**

- 실제 투자·주문·자금 집행, 비공개 데이터 무단 수집.
- 웹 서비스·사용자 계정·실시간 대시보드·운영용 배포.
- Notion API 연동: 파일명의 `notion_integrated`는 원문 통합 형식이며, Notion 제품 연동 요구사항은 아니다.
- 모든 API 동시 연결, 단계별 가변 가중치, 멀티모달·특허 hybrid의 완성형 운영. 마지막 항목들은 단계별 확장 대상으로 분리한다.

## 설계 원칙

- **근거와 판단을 분리한다.** 외부 자료는 Evidence, 해석은 Evaluation, 합산은 deterministic 함수가 담당한다.
- **없음과 모름을 구별한다.** 검색 실패는 부정 사실도, 0점의 근거도 아니다.
- **정책은 한 곳에 둔다.** 가중치와 분기 임계값은 승인된 정책 파일을 통해서만 바꾼다.
- **상태와 이력을 남긴다.** 후보·근거·평가 세대·실행별 식별자로 과거 결과가 섞이지 않게 한다.
- **수치는 측정 후 쓴다.** 모델 품질, 처리시간, 비용, 테스트 통과율은 구현 담당자가 실제 실행해 기록한다.

다음 문서: [아키텍처](implementation/architecture.md).
