# 결정 목록 — 승인 전에는 제안이다

[문서 홈](../README.md) · 근거: [통합 원문](../raws/robotics_startup_agentic_rag_notion_integrated.md)

이 문서는 원문에서 서로 다른 상태로 남아 있는 내용과 새 구현 제안을 분리한다. 아래 **모든 항목은 OPEN**이다. 담당은 역할 제안이며 실명 배정이 아니다.

## 구현 전에 합의할 항목

| ID | 충돌 / 미정 사항 | 원문 근거 | 이 가이드의 제안 | 승인 역할 / 차단 대상 |
| --- | --- | --- | --- | --- |
| D01 | 현재 팀 가중치, 교수님 예시 가중치, 단계별 가중치가 공존 | §3.1, §3 단계별 제안, §7.1, §11 | 고정 `5/30/25/20/10/10`만 baseline으로 사용. unknown stage도 가중치를 자동 변경하지 않음 | 지표 담당 / 점수 정책 |
| D02 | 점수 척도와 “특정 항목 2점 이하”의 항목 단위가 없음 | §3.1–3.2 | 세부항목 rating은 1~5, 가중점수는 별도 계산. 저점수 보류는 여섯 **상위 영역의 관측 가중평균 rating ≤2**에 적용 | 지표 담당 / 평가 prompt와 임계값 테스트 |
| D03 | 팀 Graph는 WATCHLIST 즉시 보고서, 교수님은 보류 시 다음 후보 | §4.3; §12 교수님 노션 D, 원문 L2741–2746 | `WATCHLIST`와 `PASS` 모두 다음 후보. 첫 `RECOMMEND`에서 보고서, 모두 미추천이면 비교 요약. 첫 추천이 전체 최우수라는 표현 금지 | Graph + 전원 / 라우팅 |
| D04 | 가중치 영역은 6개지만 병렬 평가 노드는 5개 | §3.1, §3.3, §4.2 | 5개 노드 유지 + 합류 뒤 별도 `deal_terms_evaluation` structured-output 노드. Aggregator는 숫자만 합산 | 평가 + Graph / State·집계 |
| D05 | 최소 데이터 / coverage 충분성 / 결측 분모가 미정 | §2.2, §3.2, §11 | 전체 세부항목 비중 100을 분모로 고정. 직접 근거·필수 맥락이 없는 항목은 결측. 조사 전후 기준을 분리 | 데이터 + 지표 / Coverage |
| D06 | Seed~C 조건에 비해 TIPS·프리시드 정규화가 과도함; 추정 기준 미정 | §2.2, §7 | TIPS만으로 라운드 확정 금지. `unknown`/추정만으로 적격 처리하지 않음. 프리/브릿지는 근거로 이전 라운드를 확인 | Discovery + 전원 / Eligibility |
| D07 | embedding 최종 모델과 vector store 미선정 | §6.3, §11 | `BAAI/bge-m3` 우선 실험. 모델 비교·실행환경·라이선스 확인 후 확정. 저장소 제품은 아직 선택하지 않음 | RAG / 실데이터 인덱스 |
| D08 | 후보 수·반복 상한 미정; 후보 소진 시 §2.3은 Summary, §4 그림은 부적격이면 END | §2.3, §4 loop, §8, §11 | 후보 5, 후보별 추가조사 총 2회, 보고서 수정 총 2회. 정상 조사 후 후보 고갈은 사유 있는 Summary로 통일. 비용·총 실행시간 상한은 live 실행 전 명시 | Graph + 전원 / 실 API 실행 |
| D09 | 보고서 상세 목차·렌더러·페이지 측정 미정 | §9, §11 | [delivery](delivery.md)의 목차 사용. 렌더러와 A4·폰트·여백을 고정한 뒤 PDF로 검증 | 보고서 / 최종 제출 |
| D10 | 팀원 명단과 원본 역할 표기가 불일치 | §11, §12 마지막 원본 L2904, L2924–2926, L2988 | 실제 수행 역할은 이슈 assignee와 PR 작성자 기록으로 확인. 원문의 별명/이름을 자동 매칭하지 않음 | 전원 / Contributors |
| D11 | DAY 3 마감의 실제 날짜·캠퍼스·반 미상 | §10 제출 원문 | DAY 3 10:00 / 15:00를 상대 일정으로 보존. 실제 날짜·시간대·제출 채널 확인 | 전원 / 제출 일정 |
| D12 | 국내 중심 API와 국내외 탐색 목표의 범위 차이 | §1.1, §5 | 계약에는 국가를 포함. 한국 fixture부터 연결하되 해외 미지원은 명시적으로 표시. 국내만으로 최종 범위를 줄이려면 승인 | Discovery / 후보 범위 |
| D13 | HTML·PPT·추출 PDF의 200페이지 산정 규칙 미정 | §1.3, §6.2 | manifest에 원본·허용 페이지 구간 기록. HTML은 고정 PDF snapshot, PPT는 슬라이드 수. 승인 전 페이지 미상 자료는 인덱싱 보류 | RAG + 과제 확인 담당 / 코퍼스 |
| D14 | 세부항목별 1~5점 rubric과 재무 지표 적용 조건 미정 | §3 상세 기준 | [scoring](scoring.md)의 공통 척도를 바탕으로 각 항목의 근거·점수 예시 작성. SaaS 경험칙과 투자 단계 순서를 자동 점수 규칙으로 쓰지 않음 | 지표 / 실제 평가·추천 |

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

## M0 baseline 승인 기록 — D01–D06·D08 (#3)

팀 논의 전 상태다. `Decision`은 위 표의 제안이며 승인된 내용이 아니다. 승인·거절되면 이 기록과 영향 문서의 '제안' 표기를 같은 PR에서 바꾼다. `Blocks`는 이 항목이 `OPEN`인 동안 fixture·인터페이스 단계를 넘을 수 없는 이슈다.

```text
Decision ID: D01
Status: OPEN
Decision: (제안) 고정 가중치 5/30/25/20/10/10만 baseline으로 사용한다. unknown stage에서도 가중치를 자동 변경하지 않는다.
Rationale and source: §3.1 현재 팀 가중치. 교수님 예시(§12)와 단계별 가중치(§3 단계별 제안, §7.1)는 비교용으로만 남긴다.
Rejected alternatives: (논의 후 기록) 후보 — 교수님 예시 30/25/15/10/10/10, 투자 단계별 가변 가중치
Affected documents / policy version / tests: scoring.md §2, delivery.md §3 M0 / 정책 파일 가중치 / 점수 집계 기대값 fixture
Blocks: #9, #16
Owner and reviewers: 지표 담당 / 전원
Approval date: —
Supersedes: —
```

```text
Decision ID: D02
Status: OPEN
Decision: (제안) 세부항목 rating은 1~5, 가중점수는 별도로 계산한다. 저점수 보류는 여섯 상위 영역의 관측 가중평균 rating ≤ 2에 적용한다.
Rationale and source: §3.1–3.2. "특정 항목 2점 이하"의 단위가 없고, 비중 1점짜리 세부항목의 획득점수와 직접 비교하면 안 된다.
Rejected alternatives: (논의 후 기록) 후보 — 세부항목 단위 ≤ 2 적용, 획득 가중점수 기준 적용
Affected documents / policy version / tests: scoring.md §3 / 저점수 임계값 / 평가 prompt와 임계값 경계 테스트
Blocks: #9, #16, #22
Owner and reviewers: 지표 담당 / 전원
Approval date: —
Supersedes: —
```

```text
Decision ID: D03
Status: OPEN
Decision: (제안) WATCHLIST와 PASS 모두 다음 후보로 간다. 첫 RECOMMEND에서 보고서를 만들고, 모두 미추천이면 비교 요약을 만든다. 첫 추천을 전체 최우수라고 표현하지 않는다.
Rationale and source: §4.3 팀 Graph(WATCHLIST 즉시 보고서)와 §12 교수님 노션 D(보류 시 다음 후보, 원문 L2741–2746)가 충돌한다.
Rejected alternatives: (논의 후 기록) 후보 — WATCHLIST 즉시 보고서
Affected documents / policy version / tests: scoring.md §5, architecture.md §2 / 판단 정책 / 라우팅 분기 테스트, E2E 시나리오
Blocks: #16, #23, #30
Owner and reviewers: Graph 담당 / 전원
Approval date: —
Supersedes: —
```

```text
Decision ID: D04
Status: OPEN
Decision: (제안) 병렬 평가 노드 5개를 유지하고, 합류 뒤 별도 deal_terms_evaluation structured-output 노드를 둔다. Aggregator는 숫자만 합산한다.
Rationale and source: §3.1 가중치 영역 6개와 §3.3·§4.2 병렬 노드 5개의 불일치. 투자조건 10%를 누락하거나 재정규화하지 않는다.
Rejected alternatives: (논의 후 기록) 후보 — 병렬 노드 6개, Aggregator 안에서 투자조건 평가
Affected documents / policy version / tests: architecture.md §2–3, contracts.md §4·§6, README / State·집계 / 평가 합류 테스트
Blocks: #16, #24
Owner and reviewers: 평가 담당 + Graph 담당 / 전원
Approval date: —
Supersedes: —
```

```text
Decision ID: D05
Status: OPEN
Decision: (제안) 전체 세부항목 비중 100을 분모로 고정한다. 직접 근거나 필수 맥락이 없는 항목은 결측이다. 조사 전과 조사 후의 coverage 기준을 분리한다.
Rationale and source: §2.2, §3.2, §11. 최소 데이터, coverage 충분성, 결측 분모가 정해지지 않았다.
Rejected alternatives: (논의 후 기록) 후보 — 관측 항목만으로 분모 재정규화
Affected documents / policy version / tests: scoring.md §3–4, contracts.md §4 / coverage 임계값 / coverage·결측 계산 테스트
Blocks: #16, #20
Owner and reviewers: 데이터 담당 + 지표 담당 / 전원
Approval date: —
Supersedes: —
```

```text
Decision ID: D06
Status: OPEN
Decision: (제안) TIPS만으로 라운드를 확정하지 않는다. unknown이나 추정만으로는 적격 처리하지 않는다. 프리시드·브릿지 라운드는 근거로 이전 라운드를 확인한다.
Rationale and source: §2.2, §7. Seed~C 조건에 비해 TIPS·프리시드 정규화가 과도하고, 추정 기준이 없다.
Rejected alternatives: (논의 후 기록) 후보 — TIPS 선정을 Seed로 간주
Affected documents / policy version / tests: scoring.md §1, delivery.md §3 M0 / 적격성 정책 / 적격성 판정 테스트
Blocks: #17, #18
Owner and reviewers: Discovery 담당 / 전원
Approval date: —
Supersedes: —
```

```text
Decision ID: D08
Status: OPEN
Decision: (제안) 후보 5개, 후보별 추가조사 총 2회, 보고서 수정 총 2회로 제한한다. 정상 조사 후 후보가 소진되면 사유를 담은 Summary로 끝낸다. 비용·총 실행시간 상한은 live 실행 전에 명시한다.
Rationale and source: §2.3(후보 소진 시 Summary)와 §4 그림(부적격이면 END)이 충돌하고, §8·§11에 반복 상한이 없다.
Rejected alternatives: (논의 후 기록) 후보 — 후보 소진 시 END
Affected documents / policy version / tests: architecture.md §2·§5, delivery.md §3 M0 / 실행 예산 설정 / 재조사·수정 loop 상한 테스트
Blocks: #23, #25, #28
Owner and reviewers: Graph 담당 / 전원
Approval date: —
Supersedes: —
```

## 조용히 바꾸면 안 되는 원문

- 현재 팀 가중치를 교수님 예시 `30/25/15/10/10/10`으로 되돌리지 않는다.
- `PASS`를 적격성 통과나 보고서 검증 통과라는 의미로 재사용하지 않는다.
- 5개 병렬 노드를 구현했다고 투자조건 10%를 누락하거나 나머지를 100으로 재정규화하지 않는다.
- “특정 항목 2점”을 비중이 1점인 세부항목의 획득점수와 직접 비교하지 않는다.
- 최종 embedding 후보가 공개되어 있다는 사실만으로 과제의 오픈소스 요구 충족을 선언하지 않는다.
- 원문의 도구 비용·접근성 표는 당시 메모다. 키 발급, 이용조건, 접근 성공은 구현 시 다시 확인해야 한다.
