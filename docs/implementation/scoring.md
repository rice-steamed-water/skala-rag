# 평가 항목, 점수, 결측과 투자 판단

[문서 홈](../README.md) · [공통 계약](contracts.md) · [결정 목록](decisions.md)

근거: [v3](../design/design-v3.html) A-2, C-1–C-4. **23개 항목·비중, 1..5 anchor, N/A 제외 분모, 네 label, 핵심차원 보류는 v3 명시 목표**다. ID·DTO·적용성 검증·소수 경계·reason 우선순위는 D01–D06·D14의 승인 전 제안이다. 이전 원문 §3·§7의 상세 자료는 rubric 참고로 보존한다. 이 문서는 보편적 금융 투자 기준이나 구현 완료 주장이 아니다.

## 1. 먼저 적격성부터 판단한다

점수가 높아도 부적격 기업을 추천하지 않는다.

| 조건 | 통과 조건 | 정보 부족 처리 |
| --- | --- | --- |
| 도메인 | Physical AI / Robotics 대상임을 확인 | unknown |
| 상장 | 비상장임을 근거로 확인 | 검색 0건만으로 비상장 처리하지 않음 |
| 투자 단계 | Seed, Series A, B, C 중 하나를 근거로 확인 | 추정 또는 unknown은 추가 조사 후에도 미확정이면 eligibility unknown |
| Exit | 완료된 Exit가 없음을 검토 가능한 자료 범위에서 확인 | 자료 범위/확인일 명시; 모름을 false로 바꾸지 않음 |
| 최소 평가 가능성 | v3 A-2의 여섯 차원에 필요한 최소 Evidence 확보 가능 | 정량 gate·확보 가능/현재 확보의 의미는 D05·D06 OPEN |

상장·Exit 완료·명시적 Series D 이상 등 확실한 부적격 조건이 하나라도 있으면 ineligible. 그 외 필수 조건이 하나라도 미확정이면 unknown으로 기록하고 다음 후보를 처리한다. 이 둘을 `PASS`라는 투자 판정과 혼동하지 않는다.

이전 원문 정규화 표의 프리B·브릿지 등은 후보 검색용 힌트로 보존하되, 직전 완료 라운드를 근거로 확인한다. TIPS 선정만으로 seed를 확정하지 않는다. 프리시드/엔젤의 범위와 추정 단계 허용은 D06 OPEN이다. 직접 확인된 Seed~C도 나머지 적격 조건을 모두 확인해야 한다. 최소 Evidence gate를 Coverage의 30% 결측 기준과 동일시하지 않는다. Company Research의 unknown 보강 경로·예산도 승인 전이며, 위 unknown→다음 후보는 보수적 fixture 제안이다.

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

## 3. rating·적용성·가중점수

각 criterion의 rating은 정수 `1..5` 또는 `null`이다. `0`을 missing 표현으로 사용하지 않는다.

| Rating | 공통 의미 | 요구 |
| --- | --- | --- |
| 1 | 매우 미흡: 근거가 약하거나 부정적 Evidence가 우세 | 평가 가능한 근거와 낮게 본 이유; 자료 부재와 구별 |
| 2 | 미흡: 일부 근거가 있으나 위험·불확실성이 큼 | 위험·불확실성과 근거 명시 |
| 3 | 보통: 기본 요건 충족, 차별성 또는 근거 제한 | 보통으로 보는 이유 |
| 4 | 우수: 신뢰 가능한 근거가 충분하고 긍정적 결과 확인 | 항목별 rubric에 따라 설명 |
| 5 | 매우 우수: 복수의 신뢰 가능한 근거 및 경쟁사 대비 우위 확인 | 근거·비교 조건 확인 |
| null | missing 또는 not_applicable | 아래 상태별 사유 필수; rating으로 부정 평가하지 않음 |

M0에서 23개 criterion별로 이 척도를 구체화한다. 정책 담당자는 “왜 3이 아니라 4인가”를 검토할 수 있는 rubric과 예시를 제공해야 한다. 숫자 임계값을 새로 정할 경우 승인 근거가 필요하다. **Series C라는 이유만으로 Seed보다 높은 투자조건 점수를 자동 부여하지 않는다.**

### 상태와 분모 — C-2/C-3 용어 충돌은 D05 OPEN

C-2는 근거 부족을 `N/A`라 부르지만 C-3는 `N/A`를 지표 자체의 해당 없음으로 정의한다. 두 의미를 같은 machine status로 인코딩하지 않는다. 아래 세 상태로 분리하는 안을 제안하며 C-2 문구 해석·정정, 적용성 근거와 검증 주체는 승인받아야 한다.

| status 제안 | rating | 사유/근거 | 분모 처리 |
| --- | --- | --- | --- |
| `observed` | 정수 1..5 | 평가 가능한 Evidence·rationale | 포함 |
| `missing` | null | missing_reason; 공개값 부재·맥락 부족·미해결 상충 | 포함, 결측 비중에도 포함 |
| `not_applicable` | null | applicability_reason·근거·승인 rubric rule | 제외; 원 catalog 비중은 삭제하지 않음 |

공개되지 않은 valuation·지분율은 Missing이다. C-3는 매출 전 Seed 기업의 매출성장률·매출총이익률·Rule of 40을 해당 없음의 예로 든다. 그러나 단순 `Seed` 문자열이나 검색 실패만으로 N/A를 부여하지 않는다. 매출 전 상태와 지표 적용조건을 확인하는 D14 rubric이 필요하다. 적용 여부 자체가 미확정이면 missing+applicability_note로 남기는 보수적 제안이다. 기술적 평가 실패는 이 세 상태가 아닌 failure envelope다.

```text
w_i = 원 catalog 비중 (23개 합 100)
r_i = observed rating (1..5)
p_i = w_i × r_i / 5   if observed; 그 외 null
observed_score = Σ observed p_i                       # 원배점 획득점수
not_applicable_weight = Σ not_applicable w_i
applicable_weight = Σ observed/missing w_i            # 100 - N/A 비중
missing_weight = Σ missing w_i                       # 원배점, % 아님
normalized_score = observed_score / applicable_weight × 100
weighted_missing_pct = missing_weight / applicable_weight × 100
coverage_pct = 100 - weighted_missing_pct

dimension_score_pct(d) = Σ d의 observed p_i / Σ d의 observed/missing w_i × 100
```

위 비율식은 분모가 양수일 때만 정의한다. **Missing을 제외한 관측 항목만의 분모로 정규화하는 것은 금지**다. Missing의 p_i=null은 합산에 기여하지 않지만 실제 0점 관측이 아니다. `획득 X / 적용가능 A, 정규화 S/100, 결측 M/A=Y%, N/A N`을 구분해 표시한다.

- 핵심 보류는 **market 또는 technology의 dimension_score_pct ≤40%**에만 적용한다. founder/moat/traction/deal_terms가 낮다는 이유만으로 강제 보류하지 않는다.
- 관측 rating 가중평균은 이 비율을 대신할 수 없다. Missing도 핵심차원 분모에 남는다. 해당 차원 전체가 missing이고 분모가 양수면 문언식 결과는 0%이나 이를 “관측된 기술력이 0점”이라 서술하지 않는다.
- 전체 또는 어느 차원의 applicable_weight=0은 0/0이다. 임의 0·100점이나 추천을 만들지 않는다. null 표현·오류/보류·집계 계속 여부는 D05 OPEN이며 해당 정책 없는 live는 차단한다. 특히 핵심차원 전부 N/A의 보류 여부는 미정이다.
- 반올림 전 정확한 수로 비교하고 표시만 소수 둘째 자리로 반올림하는 안이다(D02). Decimal 정밀도/rounding 또는 분수·교차곱 비교를 정책에 고정한다.

### 설명용 산술 예시 — 구현된 테스트·실제 기업 결과 아님

나머지=rating 5, 별도 N/A 표기가 없으면 N/A=0이다. Python 분수 연산으로 수치만 확인했다. 라벨은 양수 분모의 v3 규칙과 아래 소수 구간 제안에 따른 **조건부 기대값**이며 팀 승인이나 런타임 검증이 아니다.

| 입력 | observed_score / applicable_weight | normalized_score | missing_weight / weighted_missing_pct | 조건부 판단 |
| --- | --- | --- | --- | --- |
| 전 항목 5 | 100/100 | 100 | 0 / 0% | RECOMMEND_PRIORITY |
| 전 항목 4 | 80/100 | 80 | 0 / 0% | RECOMMEND_PRIORITY |
| 전 항목 3 | 60/100 | 60 | 0 / 0% | WATCHLIST |
| 전 항목 2 | 40/100 | 40 | 0 / 0% | 핵심차원 각 40% → WATCHLIST |
| market 전체 missing | 70/100 | 70 | 30 / 30% | 결측+market 0% 사유 모두 보존, WATCHLIST |
| founder 전체 1 | 96/100 | 96 | 0 / 0% | 비핵심 저점수만으로 강제보류하지 않음, RECOMMEND_PRIORITY |
| 전 항목 missing | 0/100 | 0 | 100 / 100% | WATCHLIST, 정보 부족이며 0점 기업이 아님 |
| traction 성장률·마진·Rule of 40 N/A(비중 6) | 94/94 | 100 | 0 / 0% | 적용성 승인 전제, RECOMMEND_PRIORITY |
| 위 N/A 6 + missing 29 | 65/94 | ≈69.148936 | 29 / ≈30.851064% | 원배점 29라도 결측률 ≥30%, WATCHLIST |
| market.size·growth missing(20), demand=5 | 80/100 | 80 | 20 / 20% | market=10/30=33.333333…%, WATCHLIST |

마지막 행의 market 관측 rating 평균은 5지만 핵심비율은 40% 이하다. 자료 부족에서 생긴 핵심비율 저하와 실제 낮은 rating을 reason/설명에서 구별한다(D02). N/A 6+missing 29 예시는 `technology` 전체 25와 founder.expertise·industry 4를 missing으로 둔 경우다.

핵심차원 N/A 예시(정책상 적용성이 인정됐다고 가정): market.size N/A(10), growth·demand rating=2이면 획득 8 / 적용가능 20 = 40%로 보류다. 모든 항목 N/A 또는 market 전부 N/A 예시는 기대 숫자/label을 만들어 넣지 않고 D05 정책 부재를 검출해야 한다.

## 4. Coverage와 재조사

**관측 인정 제안:** 해당 criterion을 직접 지원하는 근거가 있고, 필요한 단위·기간·주체가 확인되며, 중요한 상충이 해소되어야 한다. 근거 하나가 여러 criterion을 지원할 수 있지만 각 criterion의 충족 여부를 따로 판단한다. 동일 기사 재배포는 독립 근거가 아니다.

- 사전 Coverage도 세 상태와 적용가능 분모를 기록한다. `weighted_missing_pct <30`을 research_ready로 삼는 안과 gap 우선순위는 D05 OPEN이다. 원배점 missing_weight를 %와 비교하지 않는다.
- Coverage 부족이면 같은 Evidence Research가 부족 근거만 최대 2회 재조사한다(v3 D-2/D-3). 초기 조사 포함 여부와 오류 batch 회차 산정은 D08에서 명시한다.
- 그 후에는 부족해도 불변 snapshot으로 평가한다. 평가자가 맥락 부족·상충을 발견하면 missing과 gap을 반환할 수 있지만 **평가 후 Research로 돌아가는 loop는 v3 기본 흐름에 없다.**
- 최종 집계는 여섯 Evaluation에서 적용성·결측률을 다시 계산한다. 사전 Coverage를 덮어써 사전 조사 충족 기록을 조작하지 않으며 최종 판정은 최종 값으로 한다.
- 조사량을 늘렸다는 이유로 점수를 보정하지 않는다. 분모 0·미정 적용성은 승인 정책의 guard가 필요하다.

재무 지표가 공개되지 않았다는 이유로 도구 실패를 숨기거나 추정 재무제표를 생성하지 않는다. 원문의 SaaS 경험칙·국민연금 인원 기반 추정·기사 반복 노출은 참고 신호이지 직접 재무 관측의 대체물이 아니다. 확인되지 않은 투자액/기업가치/지분율을 서로 다른 라운드에서 섞지 않는다.

**이전 원문 단위 충돌 — 런웨이:** §3 상세 표 L229는 정의에 `보유 현금 ÷ 월 번레이트`, 데이터 칸에는 `현금 ÷ 연간 영업현금유출`을 적고 있다. 월·연 결과를 같은 값으로 취급하지 않는다. D14 rubric에서 현금소모 지표·기간·부호·단위 변환을 명시한다. 연간→월평균 환산은 원자료·식·평균화 가정을 `derivation`에 남기고 현재 월 번레이트의 직접 관측으로 표시하지 않는다. 분모 0 이하·기간 불일치에서는 유한 개월 수를 만들지 않는다. 지표 적용성 자체가 맞지 않는지(not_applicable), 적용되나 입력이 부족한지(missing)는 D14에서 정하며 non_positive_burn을 자동 N/A로 바꾸지 않는다.

## 5. 판단 규칙과 최종 후보 선택

적격이며 5개 branch의 여섯 차원 평가가 정상 생성되고 분모 guard가 해소된 후보에만 적용한다. 보류 예외는 총점보다 우선한다(v3 C-3/C-4). 모든 해당 reason을 보존한다. 여러 보류 사유 중 대표 grade를 정보 부족 우선으로 표시하는 안은 D02 OPEN이며 아래 행 순서 자체가 승인 우선순위는 아니다.

| 조건 | report_grade | label |
| --- | --- | --- |
| weighted_missing_pct ≥30% | 보류 (정보 부족) | WATCHLIST |
| market 또는 technology의 dimension_score_pct ≤40% | 보류 (핵심차원 비율) | WATCHLIST |
| 예외 없음, normalized_score ≥80 | 투자 우선 검토 | RECOMMEND_PRIORITY |
| 예외 없음, 70 ≤ normalized_score <80 | 투자 검토 | RECOMMEND |
| 예외 없음, 60 ≤ normalized_score <70 | 보류 | WATCHLIST |
| 예외 없음, normalized_score <60 | 투자비추천 | PASS |

C-4의 `70~79`, `60~69` 정수 표기를 위 연속 구간으로 읽는 것은 D02의 **소수 구간 제안**이다. 반올림 전 비교를 함께 승인한다. 예외 없는 label 함수 직접입력 예: `59.99→PASS`, `60/69.99→WATCHLIST`, `70/79.99/79.996→RECOMMEND`, `80→RECOMMEND_PRIORITY`. 79.996이 표시상 80.00이어도 승인 전 소수 정책을 숨기거나 표시값으로 재판정하지 않는다. 결측 `29.999/30/30.001%`, 핵심비율 `39.999/40/40.001%`를 각 경계 양쪽에서 검사한다. 이 직접입력 예는 정수 rating catalog에서 모두 생성 가능한 조합이라는 주장이 아니다.

`PASS`는 “이 기업에 투자하지 않고 넘어간다”는 의미다. 적격성 통과는 `eligible`, 문서 검증 통과는 `valid`/`pass`로 별도 필드에 둔다.

Investment Decision은 deterministic 노드다. 선택적으로 붙이는 LLM 설명은 label·grade를 수정할 수 없고 모순 시 거절한다. **네 label 모두 결과 저장→다음 후보**이며 첫 추천에서 보고서를 생성하지 않는다. 모든 후보 처리 후 Best Candidate Selector가 승인된 selection policy를 사용한다(D03). label/점수 우선순위·동점·전부 WATCHLIST/PASS·성공 평가 없음의 선택 규칙은 미정이다. 입력 순서나 candidate_id로 임의 tie-break하지 않는다. 부적격/unknown/failed를 추천으로 승격하지 않는다. 적격 후보가 하나도 없으면 selected=None과 사유 있는 종료 보고서는 v3 명시 목표다. 적격 후보가 있었지만 전부 기술 실패한 경우와 이를 구별한다.

## 6. 최소 정책 테스트

- 비중 합 100, dimension별 합 일치, criterion ID 중복 없음.
- observed rating 1..5만 허용; missing/not_applicable는 null과 상태별 사유. 0/6/소수/NaN 거절.
- §3 설명용 예시를 fixture로 구현하고 §5 소수 구간·반올림 전 경계는 주입 정책별 기대값을 검증한다.
- N/A만 분모 제외, Missing 포함, 원배점과 % 구별; 전체·각 차원 0분모 정책 없으면 live 차단.
- 핵심비율 40%·결측률 30%의 포함 경계, 여러 reason 보존, 부분/완전 핵심 Missing, 비핵심 저점수 사례 검증.
- 비중 1인 criterion이 만점일 때 이를 “2점 이하”라고 오판하지 않음.
- 미상 데이터와 부정적 근거, 부적격과 PASS, branch 실패와 missing 구별.
- Business & Deal의 traction/deal_terms 중 하나라도 없거나 failure면 branch 성공 및 총점 계산 거절.
- 서로 다른 후보·세대의 평가 결과를 섞으면 집계 거절.
- 승인된 selector fixture로 후보 순서 불변·동점·전부 WATCHLIST/PASS·무적격·성공 평가 없음 검증. 임의 기본 정책 없이 OPEN 차단을 테스트한다.
