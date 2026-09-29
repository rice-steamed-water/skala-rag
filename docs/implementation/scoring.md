# 평가 항목, 점수, 결측과 투자 판단

[문서 홈](../README.md) · [공통 계약](contracts.md) · [결정 목록](decisions.md)

근거: 원문 §3, §7, §11. **비중과 보고서 구간은 원문 팀안**, rating 척도·결측 계산·분기 매핑은 D01–D06·D14의 **승인 전 제안**이다. 이 문서는 금융 지표의 보편적 투자 기준을 제시하지 않고 수업 프로젝트의 구현 계약을 정의한다.

## 1. 먼저 적격성부터 판단한다

점수가 높아도 부적격 기업을 추천하지 않는다.

| 조건 | 통과 조건 | 정보 부족 처리 |
| --- | --- | --- |
| 도메인 | Physical AI / Robotics 대상임을 확인 | unknown |
| 상장 | 비상장임을 근거로 확인 | 검색 0건만으로 비상장 처리하지 않음 |
| 투자 단계 | Seed, Series A, B, C 중 하나를 근거로 확인 | 추정 또는 unknown은 추가 조사 후에도 미확정이면 eligibility unknown |
| Exit | 완료된 Exit가 없음을 검토 가능한 자료 범위에서 확인 | 자료 범위/확인일 명시; 모름을 false로 바꾸지 않음 |
| 최소 평가 가능성 | 기업 식별 근거와 기술/제품 또는 사업 관련 평가 근거 확보 | 추가 조사 후에도 없으면 unknown |

상장·Exit 완료·명시적 Series D 이상 등 확실한 부적격 조건이 하나라도 있으면 ineligible. 그 외 필수 조건이 하나라도 미확정이면 unknown으로 기록하고 다음 후보를 처리한다. 이 둘을 `PASS`라는 투자 판정과 혼동하지 않는다.

원문 정규화 표의 프리B·브릿지 등은 후보 검색용 힌트로 보존하되, 직전 완료 라운드를 근거로 확인한다. TIPS 선정만으로 seed를 확정하지 않는다. 프리시드/엔젤을 Seed~C 범위에 포함하는지는 D06에서 결정한다. 이번 baseline에서는 직접 확인된 Seed~C만 자동 적격 처리한다.

## 2. 평가 catalog — 원문 비중, ID는 제안

괄호 숫자는 **최대 기여 점수/비중**이다. 평가자가 입력하는 rating과 다르다.

| Dimension / 합계 | Criterion ID | 평가 포인트 | 비중 | 관측 가능하다고 볼 최소 근거의 예 |
| --- | --- | --- | --- | --- |
| founder / 5 | founder.expertise | 도메인 전문성 | 2 | 인물 일치가 확인된 전공·연구/개발 경력 |
| founder / 5 | founder.industry | 관련 산업 경험 | 2 | 회사·직무·프로젝트 맥락 |
| founder / 5 | founder.execution | 창업/사업화 경험 | 1 | 제품 출시·사업화·창업 이력 |
| market / 30 | market.size | 목표 시장 규모 | 10 | 세부 시장 정의, 수치, 단위, 지역, 기준연도 |
| market / 30 | market.growth | 시장 성장성 | 10 | 성장 지표의 기간·산정 범위 |
| market / 30 | market.demand | 수요/확장성 | 10 | 실제 문제·적용 산업·구체적 도입 사례 |
| technology / 25 | technology.maturity | 핵심 기술 완성도 | 10 | 연구/시제품/상용 제품 단계의 직접 자료 |
| technology / 25 | technology.reliability | 실제 환경 성능/안정성 | 5 | 테스트 조건과 성능·운용 결과 |
| technology / 25 | technology.integration | AI/HW/SW 통합 | 5 | 구성요소와 연결 구조를 설명하는 문서 |
| technology / 25 | technology.commercialization | 확장/상용화 가능성 | 5 | 생산·공급·PoC 전환 계획 또는 관측 결과 |
| moat / 20 | moat.differentiation | 경쟁사 대비 차별성 | 5 | 비교 대상과 동일 비교 조건 |
| moat / 20 | moat.ip | 기술/특허 진입장벽 | 5 | 권리자·관련 기술·출원/등록 상태 |
| moat / 20 | moat.data | 데이터/학습 경쟁력 | 5 | 데이터 확보·활용·학습 구조의 구체적 설명 |
| moat / 20 | moat.lock_in | 고객 락인/생태계 | 5 | 고객 연동·전환 비용·생태계 근거 |
| traction / 10 | traction.revenue_growth | 매출 성장률 | 3 | 비교 가능한 두 기간 매출 또는 직접 보고된 지표 |
| traction / 10 | traction.gross_margin | 매출총이익률 | 2 | 기간·정의가 있는 직접 값 또는 계산 입력 |
| traction / 10 | traction.burn | 번레이트 | 1 | 현금 소모의 기간·정의·값 |
| traction / 10 | traction.runway | 런웨이 | 2 | 기준일 현금과 월 소모액 또는 직접 보고된 개월 수 |
| traction / 10 | traction.concentration | 고객 집중도 | 1 | 고객별 매출 비중; 기사 등장 횟수로 수치를 대체하지 않음 |
| traction / 10 | traction.rule_of_40 | Rule of 40 | 1 | 적용 이유, 동일 기간 성장률·이익률 정의 |
| deal_terms / 10 | deal_terms.stage | 투자 시리즈 | 2 | 라운드 명칭·발표/사건일·실제 조달 상태 |
| deal_terms / 10 | deal_terms.valuation | Valuation | 5 | 금액·통화·날짜·pre/post 구분·해당 라운드 |
| deal_terms / 10 | deal_terms.ownership | 지분율 | 3 | 동일 거래의 지분율 직접 공개 또는 정확한 산정 조건 |

원문 `Ruld of 40`은 raw에 보존되어 있다. 구현 ID와 표시명에서는 `Rule of 40`으로 통일한다. 위 “최소 근거”는 공개 자료의 보유를 보장하지 않으며, 점수별 rubric을 대신하지 않는다.

## 3. rating과 가중점수 — D02 제안

각 criterion의 rating은 정수 `1..5` 또는 `null`이다. `0`을 missing 표현으로 사용하지 않는다.

| Rating | 공통 의미 | 요구 |
| --- | --- | --- |
| 1 | 기준을 크게 충족하지 못함 | 낮다는 직접 근거 필요 |
| 2 | 약하거나 중요한 위험이 확인됨 | 부족한 지점과 근거 명시 |
| 3 | 기준을 충족하되 뚜렷한 우위는 제한적 | 보통으로 보는 이유 |
| 4 | 강점이 구체적인 근거로 확인됨 | 항목별 높은 점수 기준 충족 |
| 5 | 팀이 정한 최상위 기준을 충족 | 재현·교차검증 가능한 강한 근거 |
| null | 필요한 근거 부족 / 적용조건 미확정 | missing_reason 필수; 부정 평가와 구별 |

M0에서 23개 criterion별로 이 척도를 구체화한다. 정책 담당자는 “왜 3이 아니라 4인가”를 검토할 수 있는 rubric과 예시를 제공해야 한다. 숫자 임계값을 새로 정할 경우 승인 근거가 필요하다. **Series C라는 이유만으로 Seed보다 높은 투자조건 점수를 자동 부여하지 않는다.**

```text
w_i = criterion의 비중 (전체 합 100)
r_i = 관측된 criterion의 rating (1..5)
p_i = w_i × r_i / 5          if observed
p_i = null                  if missing

observed_score = Σ observed p_i
missing_weight = Σ missing w_i
coverage_pct = 100 - missing_weight

dimension_rating(d) = Σ observed(w_i × r_i) / Σ observed(w_i)
                     단, 해당 영역의 관측 비중이 0이면 null
```

- missing 항목을 제외한 비중으로 총점을 100점에 재정규화하지 않는다.
- observed_score 합산에는 missing의 기여가 없지만, 이는 실제 평가점수 0이라는 뜻이 아니다. `관측 근거 기반 점수 X/100, 결측 Y%`로 함께 표시한다.
- 저점수 보류는 `dimension_rating <= 2`인 **상위 영역**이 있을 때 적용하는 제안이다. 일부 관측된 영역은 그 관측 부분만으로 계산하고 coverage를 함께 표시한다.
- 한 영역 전체가 missing이면 rating=null이고 저점수 조건은 적용하지 않는다. 결측 비중 규칙은 그대로 적용한다.
- 임계값 비교는 반올림 전 값으로 수행한다. 표시만 소수 둘째 자리로 반올림한다. Decimal 또는 동등한 정확도 정책을 사용한다.

### 계산 fixture — 실제 기업 아님

| 입력 | 기대 점수 | 결측 비중 | 기대 결과 |
| --- | --- | --- | --- |
| 전 항목 rating=5 | 100 | 0 | 투자 우선 검토 / RECOMMEND |
| 전 항목 rating=4 | 80 | 0 | 투자 우선 검토 / RECOMMEND |
| 전 항목 rating=3 | 60 | 0 | 보류 / WATCHLIST |
| 전 항목 rating=2 | 40 | 0 | 저점수 강제 보류 / WATCHLIST |
| market 전체 missing, 나머지 rating=5 | 70 | 30 | 정보 부족 강제 보류 / WATCHLIST |
| founder 전체 rating=1, 나머지 rating=5 | 96 | 0 | 저점수 강제 보류 / WATCHLIST |
| 전 항목 missing | 0 | 100 | 정보 부족 보류; 0점 기업으로 표현 금지 |

이 수치는 문서 작성 시 산술 검증 대상이며 실제 평가 결과가 아니다.

## 4. Coverage와 재조사

**관측 인정 제안:** 해당 criterion을 직접 지원하는 근거가 있고, 필요한 단위·기간·주체가 확인되며, 중요한 상충이 해소되어야 한다. 근거 하나가 여러 criterion을 지원할 수 있지만 각 criterion의 충족 여부를 따로 판단한다. 동일 기사 재배포는 독립 근거가 아니다.

- 사전 Coverage: known criteria로 missing_weight를 계산한다. `missing_weight < 30`이면 연구 진행 준비 상태로 본다.
- `missing_weight >= 30`이고 재조사 예산이 남으면 비중이 큰 gap부터 조사한다.
- 평가 노드가 맥락 부족·상충을 발견하면 observed를 missing으로 바꾸고 gap을 반환한다.
- 사전 Coverage가 충분해도 평가 후 재검사를 수행한다. 최종 판정은 **최종 평가에서 다시 계산한 결측 비중**을 따른다.
- 예산이 끝나면 missing 그대로 집계한다. 조사량을 늘렸다는 이유로 점수를 보정하지 않는다.
- `not_applicable`로 비중을 제거하는 정책은 채택하지 않았다. 적용 자체가 불명확한 지표는 missing+applicability_note로 남긴다. 재배분이 필요하면 D14와 정책 버전을 먼저 변경한다.

재무 지표가 공개되지 않았다는 이유로 도구 실패를 숨기거나 추정 재무제표를 생성하지 않는다. 원문의 SaaS 경험칙·국민연금 인원 기반 추정·기사 반복 노출은 참고 신호이지 직접 재무 관측의 대체물이 아니다. 확인되지 않은 투자액/기업가치/지분율을 서로 다른 라운드에서 섞지 않는다.

## 5. 판단 우선순위 — D03 제안

다음 표는 적격 후보이며 필수 평가 결과가 정상 생성된 경우에만 적용한다. 모든 해당 reason은 남기되, 화면의 대표 grade는 위쪽 행을 우선한다.

| 우선순위 | 조건 | report_grade | label | 다음 경로 |
| --- | --- | --- | --- | --- |
| 1 | missing_weight ≥30 | 보류 (정보 부족) | WATCHLIST | 후보 결과 저장 → 다음 후보 |
| 2 | 어느 상위 영역이든 관측 rating ≤2 | 보류 (저점수) | WATCHLIST | 후보 결과 저장 → 다음 후보 |
| 3 | observed_score ≥80 | 투자 우선 검토 | RECOMMEND | 단일 기업 보고서 |
| 4 | 70 ≤ observed_score <80 | 투자 검토 | RECOMMEND | 단일 기업 보고서 |
| 5 | 60 ≤ observed_score <70 | 보류 | WATCHLIST | 후보 결과 저장 → 다음 후보 |
| 6 | observed_score <60 | 투자비추천 | PASS | 후보 결과 저장 → 다음 후보 |

`PASS`는 “이 기업에 투자하지 않고 넘어간다”는 의미다. 적격성 통과는 `eligible`, 문서 검증 통과는 `valid`/`pass`로 별도 필드에 둔다.

LLM Investment Decision 노드는 계산된 label·grade를 수정할 수 없다. 설명이 정책과 모순되면 schema/semantic 검증에서 거절한다. 모든 후보 처리 후에는 WATCHLIST/PASS/부적격/정보부족 사유를 구분한 비교 요약을 만든다.

## 6. 최소 정책 테스트

- 비중 합 100, dimension별 합 일치, criterion ID 중복 없음.
- rating null/1/5 허용; 0/6/소수/NaN 거절.
- 점수 경계 `59.99/60/69.99/70/79.99/80` 비교; 라벨 함수 테스트에서는 이 값을 직접 입력한다.
- 결측 `29/30/31`과 상위 rating `2/2.01`에서 우선순위 확인.
- 비중 1인 criterion이 만점일 때 이를 “2점 이하”라고 오판하지 않음.
- 미상 데이터와 부정적 근거, 부적격과 PASS, branch 실패와 missing 구별.
- 여섯 번째 투자조건 결과가 없으면 총점 계산 거절.
- 서로 다른 후보·세대의 평가 결과를 섞으면 집계 거절.
