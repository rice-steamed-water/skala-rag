# Moat snapshot-only evaluator (#60)

`agents.moat.evaluate_moat`는 명시적으로 주입한 proposed rubric/version,
draft policy, StructuredLLM, Clock, observation verifier와 검증된 특허/독립 비교
사실을 사용한다. 기준은 실제 catalog의 `moat.differentiation`, `moat.ip`,
`moat.data`, `moat.lock_in`이다. 숫자 anchor·최소 근거 기본값을 만들지 않는다.

`VerifiedPatent`는 권리자 candidate, active 상태, 청구 범위와 실제 snapshot
Evidence ID를 요구한다. `IndependentComparison`는 다른 competitor candidate와
실제 비교 Evidence ID를 요구한다. upstream 검증자 및 observation verifier는
내용·독립성·rubric 충족을 확인해야 한다. 단순 회사 자기 주장이나 ID 존재는
독립 증명이 아니다. 모든 관측 criterion에 verifier를 호출한다.

#22 `evaluate_dimension`은 baseline observed/missing 출력만 지원한다. 공통 schema를
복제하지 않고 좁은 bridge로 v3 `EvaluationBranchResult(branch_id="moat")`를 반환한다.
세대 identity는 원 snapshot에서 보존한다. N/A는 승인 rule/reason/applicability
Evidence 검증을 공통 wrapper가 지원할 때까지 거절한다. Missing으로 변환하지 않는다.
외부/LLM 실패는 failure이며 missing/0점이 아니다. 자체 검색·retry·여섯 번째 node 없음.

versioned `moat-evaluation-v1` prompt는 원문을 untrusted JSON 필드에 격리한다.
#47 StructuredLLM adapter를 주입할 수 있으나 현재 함수는 offline fixture 전용이다.
model/usage는 adapter call metadata, prompt version은 prompt payload,
rubric/policy version과 Evidence ID는 결과에서 추적한다. live 실측 기록은 없다.

남은 차단: D14 rubric 승인, 실제 applicability rule/verifier 통합, #55 EvidenceResearch
병합·연결, runtime readiness 및 opt-in 실제 평가. fixture 통과는 live 완료가 아니다.
