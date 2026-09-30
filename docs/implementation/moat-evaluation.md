# Moat snapshot-only evaluator (#60)

`agents.moat.evaluate_moat`는 명시적으로 주입한 rubric/version, draft policy,
StructuredLLM, Clock, observation verifier를 사용하는 offline fixture bridge다.
`approved` + `core-0.1.0`를 허용한다. Core 14개 criterion 및 rubric-core.md
Q1–Q5 승인은 #59 comment 5904859865(heojiwon2)에 근거하며, finance rubric이나
scoring policy 승인으로 전이하지 않는다. 공유 config/결정 문서 변경은 #134 소유다.
기존 `proposed` fixture도 유지하며 알 수 없는 approved version은 거절한다.

기준은 실제 catalog의 `moat.differentiation`, `moat.ip`, `moat.data`,
`moat.lock_in`이다. 숫자 anchor·최소 근거 기본값을 만들지 않는다.
Approved Core에서는 관측 전체에 active 특허/독립 비교를 요구하지 않는다.
특허 부재·출원만 존재하는 낮은 anchor도 가능하며, 차별성의 독립 출처 요구는
rating 5에 적용한다. 회사 자기주장만이면 최대 4, 1–2는 확인된 약점 근거가
필요하다. 모든 관측에 injected verifier를 호출하고 정확히 True인 결과만 받는다.

**의미 검증 경계:** 현재 Evidence/Source DTO에는 독립성, 출원인명 변형 확인,
특허 목록·건수·관련성, 비교 조건/항목, 직접 부정 사실의 검증된 필드가 없다.
따라서 이를 provider rationale/claim 문자열, publisher 이름이나 source_kind로
추론하지 않는다. Verifier가 주입된 rubric anchors·최소 근거·자기주장 상한·
독립 교차확인·약점 근거·미해결 상충·기준일을 확인해야 한다. 테스트의 True
verifier는 가상 receipt이며 의미 검증 구현/실측이 아니다. 구조 검사만으로
Q2 또는 낮은 rating의 정당성이 자동 증명되었다고 표시하지 않는다.

기존 proposed fixture에서는 `VerifiedPatent`의 후보 권리자·active 상태·청구
범위·실제 snapshot Evidence ID와 `IndependentComparison`의 다른 competitor
candidate·Evidence ID 검사를 유지한다. 이 보조 DTO는 approved Core의 모든
anchor를 표현하지 못하므로 해당 경로의 필수 gate로 쓰지 않는다.

#22 `evaluate_dimension`은 baseline observed/missing 출력만 지원한다. 공통 schema를
복제하지 않고 좁은 bridge로 v3 `EvaluationBranchResult(branch_id="moat")`를 반환한다.
세대 identity는 원 snapshot에서 보존한다. Core의 N/A는 승인된 금지 규칙에 따라
거절하며 Missing으로 변환하지 않는다. 후보·criterion·source/provenance·retrieval·
chunk attribution 검사는 유지한다. 외부/LLM 실패는 failure이며 missing/0점이 아니다.
자체 검색·retry·여섯 번째 node 없음.

versioned `moat-evaluation-v2` prompt는 원문을 untrusted JSON 필드에 격리한다.
#47 StructuredLLM adapter를 주입할 수 있으나 현재 함수는 offline fixture 전용이다.
model/usage는 adapter call metadata, prompt version은 prompt payload,
rubric/policy version과 Evidence ID는 결과에서 추적한다. live 실측 기록은 없다.

남은 차단: 실제 semantic verifier 통합, #55 EvidenceResearch 병합·연결,
runtime readiness 및 opt-in 실제 평가. Core 승인만으로 live gate를 해제하지 않는다.
fixture 통과는 live 완료가 아니다.


## #168 approved scoring consumer

`evaluate_moat_approved_fixture` reads the explicit `policy_path` with the merged
`load_approved_policy` and consumes its catalog directly (no legacy thresholds
or policy relabelling). It requires explicit approvals and a trusted controller
approval verifier. That verifier must bind the supplied Core rubric contents to
an authoritative artifact; the proposed main Core file is not promoted by this
entry point. Synthetic tests are fixture compatibility, not artifact approval.
The entry accepts only FakeLLM and defaults `actual_runtime=False`.

Direct ApprovedScoringPolicy construction is not approval proof: the legacy
entry rejects that object. #168 currently returns no trusted-loading receipt,
so separately loaded objects cannot safely be admitted by their fields alone.
`actual_runtime=True` fails before any model call. Actual mode remains absent,
not implemented opt-in execution: authoritative Core verification, a trusted
receipt and RealRuntimeStructuredLLM with exact candidate/run/policy/runtime,
readiness and call/cost admission matching are prerequisites. No plain/fake LLM
is an actual-mode substitute. Snapshot provenance checks remain fixture checks;
no live snapshot is labelled fixture to enable actual execution.

Negative-fact rating 1–2 and independent cross-check rating 5 semantics still
belong to the injected observation verifier; no semantic implementation is
claimed. KIPRIS/KRX/중기부/Tavily exclusions remain unchanged.
