# Business & Deal 평가 경계 (#61)

`agents.business_deal.evaluate_business_deal`은 frozen snapshot만 받아 단일
`StructuredLLM.generate` 호출로 traction 6개와 deal_terms 3개 criterion을
평가한다. 두 영역 모두 검증되어야 v3 `EvaluationBranchResult` success다.
부분·중복·foreign Evidence·schema 실패는 전체 failure이며 부분 결과를 버린다.
세대 ID는 모델에서 받지 않고 snapshot에서 조립한다. #22의 catalog/Evidence
검증과 gap 조립을 재사용한다. 별도 직렬 Deal node, 검색, retry loop는 없다.

`ApprovedVerifiers`의 rubric/finance/applicability 버전과 callable은 필수다.
이름이 승인 자체를 증명하지 않는다. 호출자는 승인 기록에 묶인 검증기를
주입해야 한다. finance 검증기는 실제 snapshot Evidence의 기간, 비금액 단위,
통화, 출처, 파생값 지원·수식 및 기존 `scoring.finance` helper 결과를 확인한다.
rubric 검증기는 rating과 anchor의 대응을 확인한다. applicability 검증기는
승인된 rule ID·reason·실제 applicability Evidence를 확인한다. 내부 기본값이나
금융 rating mapping은 없다. 미공개 valuation/ownership은 Missing이다.

기본 구조 gate는 관측 근거 귀속·출처·지원 Evidence 폐쇄성, 추정/상충 입력,
미지 금액 단위, 혼합 금액 단위/통화를 거절한다. finance callback 실패를
missing이나 0점으로 바꾸지 않는다. 별도 FX나 재무 파생값을 계산하지 않는다.
원문 claim/excerpt/limitations/derivation은 JSON `untrusted_source_text`에만
넣고 system prompt와 오류 메시지에 raw text를 복사하지 않는다.

## 검증과 차단

테스트의 verifier/버전은 synthetic fixture다. 정책 승인·실측이 아니다.
#55 실제 조사 통합은 병합되었지만 평가 실행 준비 완료를 의미하지 않는다.
`execution_mode="real"`은 아래 Finance semantic prerequisite 때문에 계속 차단한다.
공식 adapter/runtime의 actual mode는 `live`이며 `real`을 새 runtime mode로
해석하거나 fixture evidence를 재라벨링하지 않는다. #47 adapter/#45 runtime은
StructuredLLM protocol로 주입 가능하지만 이번 작업은 외부 요청·credential·유료 호출을 실행하지 않는다.
실제 structured-output smoke는 완료하지 않았으며 offline test와 구별한다.

## Finance 승인 후 semantic boundary

#61 comment5906253348의 finance-0.1.0 정책 승인만 반영했다. approved rubric은
두 versioned N/A rule ID만 허용하며 proposed synthetic fixture 동작은 보존한다.
기존 Evidence는 metric 역할·pre/post·pre-revenue·회계주체의 typed fact 필드가
없다. value/claim/provider rationale만 보고 그 사실을 추론하지 않는다.
주입 verifier는 정확히 True를 반환해야 한다. applicability는 실제 확인된
pre-revenue 또는 동일 기간/주체 OCF≥0와 rule/reason/evidence를 검증해야 한다.
finance는 기존 Decimal helper 수식·입력 의미·same round·pre/post·CAPEX 제외를,
rubric은 반올림 전 구간·3배/2배 비교·작은 기저 limitations 및 burn5 실제
재무 근거를 확인해야 한다. N/A 이유는 burn5 근거를 대체하지 않는다.
승인된 숫자만으로 generic Evidence.value를 특정 metric으로 취급하지 않는다.
live gate, draft scoring isolation, 두 차원 atomic 실패 의미는 유지한다.

## Approved contract consumer integration

The consumer also accepts `ApprovedScoringPolicy` in fixture mode, with matching
snapshot policy and finance rubric/verifier versions. Legacy fixture behavior is
unchanged. `financial_facts=tuple[ReviewedFinancialFact, ...]` supplies explicit
caller-reviewed metric roles, accounting entity, typed period, funding round,
pre/post basis, reviewer audit reference and exact frozen Evidence payload.
Validation rejects stale/foreign/altered evidence, absent sources, nonreported
facts, conflicts, unknown monetary units/currency and invalid accounting context.
Roles are never inferred from prose. Review references are audit pointers, not
authenticated approval. Decimal monetary checks reuse `scoring.finance`.

Approved missing results need no receipt. The two approved N/A rules additionally
require reviewed confirmed zero pre-revenue or typed-period/entity nonnegative
OCF. Approved observed results now check deterministic correspondence from the
criterion's own cited reviewed facts (or exactly validated derived citations):

- `traction.revenue_growth`: two consecutive annual revenue periods, Decimal YoY.
- `traction.gross_margin`: same-period revenue and cost of revenue.
- `traction.runway`: dated cash, same-statement OCF period and optionally reviewed,
  dated post-cash funding; existing helper subtracts elapsed months.
- `traction.concentration`: explicitly reviewed `top1_customer_revenue_share`
  percent only; generic customer revenue does not establish top-1 status.
- `traction.rule_of_40`: annual YoY plus same-current-period operating margin percent.
- `deal_terms.ownership`: same-round/date/currency investment and pre/post valuation,
  or reviewed dated post-money transaction ownership percent.
- `traction.burn`: nonnegative single-period OCF supports anchor 5; two exact annual
  OCF/revenue periods support anchors 1–5. Flat burn and nonpositive growth bases
  remain unavailable rather than receiving an invented anchor.

Numeric bands come from the approved rubric and are compared before rounding.
Inputs are limited to cited support; unrelated receipts cannot complete a formula.
No reported Evidence, source ID, or derived Evidence is synthesized. Existing
helper-derived growth/margin/runway citations require exact value, unit, currency,
period, date, supporting IDs and derivation correspondence. Composite derived
Rule-of-40/ownership/burn citations and reported facts carrying supporting formulas
remain rejected until their structured formula contract is implemented.
Stage and valuation reasonableness remain fail-closed: numeric amounts or stage
names cannot establish qualitative anchors. Direct growth/margin/runway metrics
lack reviewed typed roles and are not guessed from source prose.
All actual modes remain blocked before any call; a live policy cannot enter this
fixture consumer. Injected verifiers remain additional mandatory gates, not
substitutes for deterministic checks. Tests are offline synthetic inputs, not
provider or authenticated review evidence.
