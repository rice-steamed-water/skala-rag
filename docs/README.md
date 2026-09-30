# 팀 구현 가이드 — Robotics Startup Agentic RAG

> 상태: **v3 목표 정합화 / baseline 승인 기록과 v3 대체안 분리 필요** · 기준일: 2026-09-30 KST
> 대상: 기능을 나누어 구현하고 통합할 팀원
> 새 설계 입력: [설계 v3](design/design-v3.html) · 이전 설계·과제 요구: [통합 원문](raws/robotics_startup_agentic_rag_notion_integrated.md)

## 먼저 할 일

1. 이 문서에서 목표와 필수 요구사항을 확인한다.
2. [v3 정합화·영향표](implementation/design-v3-alignment.md)와 [결정 목록](implementation/decisions.md)을 읽는다. 특히 **N/A 용어 충돌·0분모, selector 정책, Warning 결과 계약**을 합의한다.
3. 모든 구현 담당자가 [공통 데이터 계약](implementation/contracts.md)을 먼저 읽는다.
4. [작업 분담과 검증](implementation/delivery.md)의 M0 → M1 순서로 시작한다. 외부 API부터 각자 연결하기보다, 같은 fixture로 전체 흐름을 먼저 맞춘다.

현재 main에는 `pyproject.toml`·`uv.lock`, 설치 가능한 Python 패키지, #5의 구조 DTO와 #7의 `InvestmentState`/`create_initial_state`, 관련 contract fixture·tests가 있다. 이는 정책 계산이 없는 DTO/state 범위이며 Graph/reducer wiring·CLI·실제 RAG·평가·점수·보고서는 구현되지 않았다. 설치·검증 명령은 [루트 README](../README.md), 조사한 main SHA와 미병합 PR snapshot은 [정합화 기록](implementation/design-v3-alignment.md)에 있다. 아래 함수·DTO·정책은 **구현 목표/제안**이지 제공되는 기능이 아니다. v3 HTML과 `raws/`는 수정하지 않는다.

## 문서를 읽는 순서

| 필요한 내용 | 문서 | 우선 독자 |
| --- | --- | --- |
| v3 출처·절별 추적·현재 GitHub 작업 영향 | [v3 정합화 기록](implementation/design-v3-alignment.md) | 전원 |
| 전체 흐름, 노드 책임, 반복과 종료 | [아키텍처](implementation/architecture.md) | Graph / Agent 담당 |
| State, Evidence, 평가 결과, Tool 경계 | [공통 데이터 계약](implementation/contracts.md) | 전원 |
| 평가 항목, 가중치, 결측, 판단 라벨 | [점수와 판단 정책](implementation/scoring.md) | 평가 / 지표 담당 |
| 자료 수집, 페이지 예산, 검색, 임베딩 비교 | [데이터와 RAG](implementation/data-rag.md) | 데이터 / RAG 담당 |
| 작업 패키지, 통합 순서, 테스트, 제출 | [작업 분담과 검증](implementation/delivery.md) | 전원 |
| 원문 충돌, 새 제안, 승인 기록 | [결정 목록](implementation/decisions.md) | 전원, 정책 결정 담당 |
| 이슈·브랜치·PR 규칙, 폴더 구조, 개발 도구 | [협업 규칙](../CONTRIBUTING.md) | 전원 |

## 우리가 만드는 것

사용자가 Physical AI / Robotics 투자 주제를 입력하면 시스템이 후보 기업을 찾고, 적격성을 확인하고, 출처가 있는 근거로 평가한 뒤 **투자 검토용 보고서**를 생성한다. 실제 투자 실행 시스템이 아니라 수업용 조사·평가 시스템이다.

```text
투자 주제 + 실행 설정 + 승인된 문서 코퍼스
→ 후보 발견 / 정규화 / 적격성 확인
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
| **v3 명시 목표** | 사용자가 새 설계 입력으로 지정한 v3의 명시 내용 | 구현 목표에 우선 반영; 팀 승인·구현 완료와 구분 |
| **이전 원문 팀안** | 통합 원문에 보존된 이전 설계 | v3와 충돌하면 과거 제안으로 추적; 과제 요구 누락은 폐기로 해석하지 않음 |
| **구현 제안** | 원문을 실행 가능한 계약으로 보완한 초안 | 팀 승인 후 채택; 승인 전 확정안으로 표현하지 않음 |
| **미결정** | 원문만으로 결론을 낼 수 없는 사항 | 담당자·결정·근거·승인일 기록 |

각 상세 문서의 새 필드, 예외 처리, 함수명은 별도 표시가 없어도 **구현 제안**이다. #3 issue comment의 D01–D06·D08 baseline 승인 기록은 존재하지만, v3가 바꾸는 selector·N/A·Warning 세부는 자동으로 승인되지 않아 `v3-OPEN`이다. v3의 명시 내용은 목표로 반영하되 세부 정책을 승인 없이 기본값으로 만들지 않는다. OPEN에 의존하는 기능은 주입된 가상 정책·인터페이스까지만 진행하고 live를 차단한다.

## 필수 요구사항과 검증 위치

아래 `§`는 통합 원문 절, `v3`는 새 설계의 절이다. 과제 필수 요구는 v3에서 생략되어도 보존한다. 원문 충돌은 결정 목록에서 추적한다.

| ID | 요구사항 | 출처 | 구현 / 검증 |
| --- | --- | --- | --- |
| R01 | LangGraph 기반 Multi-Agent + Agentic RAG | §1.1; v3 A-1 | 실제 Graph 실행 및 역할별 노드 trace |
| R02 | 도메인 Physical AI / Robotics | §1.1, §2 | 입력 범위와 후보 도메인 검증 |
| R03 | 비상장, Seed~Series C, Exit 미완료·최소 평가 가능성 | §2.2; v3 A-2 | 적격 / 부적격 / 정보부족 fixture; 최소 Evidence gate는 D05·D06 |
| R04 | 지정된 RAG 적용 대상 중 최소 1개 Agent에 실제 RAG 적용 | §1.3, §12 교수님 노션 B; v3 B-2 | Primary RAG인 Evidence Research의 검색→근거→Technology 기술 요약/평가→인용 trace |
| R05 | RAG 문서 총 200페이지 한정 | §1.3; v3 B-2 | 전체 코퍼스 manifest의 페이지 합 검증 |
| R06 | 오픈소스 임베딩 적용, 후보·선택 근거 문서화 | §1.3; v3 B-3 | BGE-M3 1차 선택, e5/KURE와 동일 데이터 비교·라이선스 확인 후 최종 선택 |
| R07 | 가중치 평가, 결측 및 핵심차원 저점수 보류 | v3 C-1–C-4 | Missing/N/A, 정규화, 네 label, 경계값 검증 |
| R08 | Graph의 Loop / Branch 및 State 구현 | §4, §8; v3 D-1–D-3 | Coverage 재조사, 전 후보 처리·selector, 5 branch 합류, Warning 유한 종료 |
| R09 | 보고서 5장 이내; 첫 SUMMARY는 1/2페이지 이내 | §9.2; v3 E-1 | 최종 PDF 페이지 수와 렌더링된 SUMMARY 높이 검증 |
| R10 | 마지막 REFERENCE, 실제 사용 자료만 기재 | §9.2–9.3; v3 E-2 | 인용 ID와 참조 목록의 양방향 일치 |
| R11 | README 필수 항목과 개인별 실제 수행 역할 | §10.2–10.3 | 제출 체크리스트; PM/PL 역할 표기 제외 |
| R12 | 설계 PDF, GitHub 코드·README, 재현 가능한 투자 보고서 PDF | §10 | 제출 파일·재현 로그 확인 |

## 첫 구현의 범위

**v3 목표 및 이를 위한 구현 제안 — 포함**

- 모든 후보를 순차 검증하고 적격 후보를 평가한 뒤 selector가 최종 보고서 대상을 정한다. 첫 추천에서 종료하지 않는다.
- 한 후보 내부의 Founder / Market / Technology / Moat / Business & Deal 5개 branch는 병렬이며, 마지막 branch가 traction·deal_terms 두 차원을 함께 반환한다.
- RAG 문서 수집·검색과 출처 추적, 외부 검색 adapter, 공통 Evidence 저장소를 만든다.
- 여섯 점수 차원·23개 criterion을 보존한다. branch envelope는 D04 제안이며 부분 성공 집계는 금지한다.
- 우선추천·추천·보류·비추천, 적격성 정보부족, 후보 없음, 기술 실패를 구별한다. Warning 반환과 검증된 final 발행도 구별한다.
- CLI 중심으로 시작한다. 보고서 Markdown과 PDF, 실행 manifest를 남긴다.

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
