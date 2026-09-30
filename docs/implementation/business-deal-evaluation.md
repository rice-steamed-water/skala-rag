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
`execution_mode="real"`은 D14 rubric/applicability 승인 및 #55 실제 조사
통합 미완료 때문에 차단한다. #47 adapter/#45 runtime은 StructuredLLM protocol로
주입 가능하지만 이번 작업은 외부 요청·credential·유료 호출을 실행하지 않는다.
실제 structured-output smoke는 완료하지 않았으며 offline test와 구별한다.
