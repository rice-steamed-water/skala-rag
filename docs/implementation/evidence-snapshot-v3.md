# EvidenceResearch 결과에서 v3 fixture snapshot까지 (#211)

[Python 직접 실행](python-execution.md) · [공통 계약](contracts.md) · [Source-only v3](source-only-v3.md)

## 연결 범위

기존 `run_candidates_v3`와 public LangGraph `run_candidate_workflow_v3`에 선택적 artifact 입력 경로를 연결한다. `CandidateStagesV3.evidence_research=None`이면 기존 Evidence sequence 수집, 추가 조사 callback, template freeze 계약을 그대로 쓴다. 새 경로는 아래 순서로 실행한다.

```text
CompanyResearchArtifactsV3 + 원래 EligibilityResult
→ collect 노드의 EvidenceResearch.run(candidate, (), budget)
→ 전체 ResearchOutcome을 후보별 detached JSON State로 검증·병합
→ 기존 Coverage와 build_research_gaps_v3
→ 필요한 경우 동일 EvidenceResearch.run(candidate, generated_gaps, budget)
→ freeze 노드의 기존 freeze_snapshot(candidate_id, owned_state, run_input, ...)
→ 기존 Technology fixture callable / bind_baseline_evaluator_v3
→ 기존 five-way join / 여섯 dimension / aggregate / decision / archive / advance
```

`ResearchOutcome.evidence`만 꺼내거나 호출자 cache에서 Source를 다시 찾지 않는다. producer 객체는 stage binding에 남고 State에 들어가지 않는다. Graph의 중간 `data`에는 CompanyResearch seed도 JSON으로 저장하며 callback에 전달할 때만 타입을 복원한다. 새 CLI, provider, live admission token은 없다.

## 명시적 입력 계약

두 dataclass는 `skala_rag.graph.research_artifacts_v3`에 있다. 기존 `candidates_v3` 모듈에서도 stage 계약용으로 노출한다.

| 입력 | 필드와 의미 |
| --- | --- |
| `CompanyResearchArtifactsV3` | `candidate_id`, `run_id`, `schema_version`, `evidence_revision`, `sources`, `chunks`, `records`, `evidence`. CompanyResearch의 실제 원래 payload와 Eligibility 근거를 전달한다. 새 초기 조사 결과로 적격성 근거를 대체하지 않는다. |
| `EvidenceResearchBindingV3` | 기존 fixture `EvidenceResearch`, 명시 `ToolBudget`, fixture `RunInput`, `run_id`, `schema_version`, `index_version`, `allowed_source_ids`, `industry_evidence_ids`. corpus·as_of·policy는 RunInput에 고정한다. |
| `CandidateStagesV3.evidence_research` | 선택적 binding. 설정하면 collect와 추가 조사 노드가 producer의 `.run(...)`을 직접 호출하고 freeze 노드가 기존 `freeze_snapshot`을 직접 호출한다. 이 경로에서는 legacy `collect`/`freeze` callback을 호출하지 않는다. |
| `CandidateRunV3.research_artifacts` | 후보별 `state`와 `batches`의 detached JSON 사본. 기존 결과 필드는 유지한다. |

binding은 실행 시작에 producer의 fixture mode, run/schema/corpus/index/as_of/허용 Source와 호출 입력을 대조한다. 다른 세대, live RunInput, 잘못된 예산은 거절한다. 예산이나 정책을 기본값으로 채우지 않는다. 새 경로의 `research` stage는 위 명시적 CompanyResearch seed를 반환해야 한다. 기존 opaque 연구 context는 legacy 경로에서 계속 사용할 수 있다.

seed revision은 원래 Eligibility revision과 같아야 한다. 원래 Eligibility의 run/candidate/schema/policy/as_of와 근거 Evidence의 참조 폐쇄성을 검증한다. 이후 State revision이 증가해도 저장된 Eligibility의 근거 ID와 초기 revision을 다시 쓰지 않는다.

seed는 성공 또는 empty인 원래 record만 전달하는 제한된 artifact 계약이다. `CompanyResearchArtifactsV3`에는 errors/status/calls envelope가 없으므로 failed/unavailable record나 `error_id`가 있는 seed는 거절한다. CompanyResearch의 optional/required 오류를 seed에서 손실 없이 보존하는 기능은 제공하지 않는다. 아래 원래 오류 보존 규칙은 초기·추가 `ResearchOutcome`에 적용하며 seed DTO를 확장하지 않는다.

### 세대와 선택적 record 선언

Source/Chunk/Evidence/record 및 실제 중첩 `Contract`의 schema는 binding과 같아야 한다. `Evidence.provenance[*]`와 `RetrievalRecord.cost`도 검사한다. `Chunk.locator` 등 locator는 현재 계약의 문자열 타입이지 중첩 DTO가 아니다. `bibliographic_metadata`와 `arguments_without_secrets`의 opaque JSON 안에 있는 `schema_version` 키를 DTO 버전으로 해석하지 않는다.

record가 `execution_mode`를 명시하면 정확한 문자열 `fixture`여야 한다. 누락은 허용하지만 null/boolean/object/list/다른 문자열은 거절한다. corpus/index/as_of도 기존 선택적 선언 비교를 유지한다. 과거 seed/manual/API record와 metadata가 없는 retriever record에 새 필수키를 요구하지 않는다.

`pin`은 실제 `IndexedRetriever`일 때 `runtime.policy.execution_mode`가 정확히 `fixture`인지 discovery 등 callback 전에 확인한다. 유효한 deadline과 가상 승인 참조가 있어도 live runtime을 거절하며 ledger를 예약하지 않는다. 초기·추가 producer 호출과 실제 retrieve 호출 직전에도 같은 mode를 확인한다. producer나 RunInput을 fixture로 다시 쓰지 않는다.

기존 `index_identity(retrieve._snapshot)`을 계산해 adapter의 원래 `_identity`와 corpus/index를 대조하고 binding 내부에 고정한다. producer의 snapshot/identity는 바꾸지 않는다. record가 `index_identity`를 명시하면 해당 adapter의 tool name과 고정 identity가 모두 일치해야 한다. 원래 index를 확인할 수 없는 callable이나 다른 tool의 선언은 `declared but unverifiable`로 거절한다. 선언이 없는 경로는 계속 지원하며, 이를 index identity가 검증됐다는 주장으로 바꾸지 않는다. 이 일관성 검사는 semantic authority나 actual runtime 승인이 아니다.

opaque retrieve, Web/API channel, StructuredLLM callable의 내부 실행 모드는 이 binding으로 증명할 수 없다. 호출자가 offline fixture 구현을 주입할 책임이 있다. 선언이 없다는 이유로 live가 승인되거나 외부 요청 차단이 보장되는 것은 아니다. CompanyResearch seed의 status/errors envelope 제한도 그대로다. 이 경로를 full-live 통합으로 표시하지 않는다.

## 보존과 채택의 구별

`batches`에는 status, initial, Source, Chunk, RetrievalRecord, Evidence, gaps, calls, 원래 errors, rejected reason, skipped count를 원래 ID와 함께 보존한다. tuple 등은 JSON 배열로 직렬화한다.

- `admission="rejected_input"`: 검증 전 입력 또는 실패 batch의 진단 자료다. 성공 부분 Evidence가 있어도 State의 채택된 사실과 snapshot에 병합하지 않는다.
- `admission="admitted"`: 타입, 세대, carrier, 허용 출처, cutoff, provenance 폐쇄성을 통과한 batch다. 기존 Source/Evidence reducer와 동일 ID 결과 충돌 검사를 거쳐 State에 반영한다.

원래 failed/unavailable outcome은 producer의 검증된 원래 `WorkflowError`를 public run과 후보 archive에 유지한다. 성공 부분 자료를 보존하되 evaluator와 aggregate는 호출하지 않는다. failed status에 terminal errors가 없거나, 원래 record/call carrier가 끊기거나, 같은 error ID의 내용이 충돌하면 `UPSTREAM_INVALID`로 거절한다. 누락된 원래 error를 만들어 채우지 않는다. 파손된 입력의 errors는 rejected batch에서 supplied input으로 확인할 수 있으며 검증된 원래 오류로 승격하지 않는다.

실제 runtime은 retry 성공에 `errors=[]`, terminal 실패에 마지막 오류만 반환하면서 모든 attempt record를 유지한다. 이를 누락 carrier로 취급하지 않는다. pin된 실제 `IndexedRetriever`가 이번 producer 호출 중 반환한 원래 record 묶음과 terminal 오류를 binding 내부에서 관찰하고, 해당 record의 원래 오류를 `runtime.error_history`와 대조한다. error ID, record payload, 후보/run/schema/tool, call 소유와 record 순서, runtime call ID, attempt, timestamp, status, retryable이 일치해야 한다. 과거 호출이나 같은 batch의 다른 호출에서 빌린 error ID는 채택하지 않는다. 관찰 자료가 없는 opaque 도구의 누락 오류는 계속 거절하며 terminal 오류 누락도 history로 보충하지 않는다.

검증된 historical attempt 오류는 기존 batch envelope의 `attempt_errors`에 원래 전체 DTO로 보존한다. producer의 `errors`, status와 원래 rich payload는 바꾸지 않는다. 회복된 과거 오류를 public run/State의 terminal errors로 승격해 정상 성공을 실패로 바꾸지 않는다. 성공·empty는 detached candidate State에서 오류 병합, Source/reducer 충돌과 snapshot provenance까지 검증한 뒤 한 번에 채택한다. 채택 실패 시 owned State는 그대로이며 입력과 오류는 rejected batch에만 남는다. failed/unavailable은 carrier 검증을 통과한 원래 terminal 오류만 기존 방식으로 보존한다.

이 관찰은 두 consumer의 동기 fixture 호출에 한정한다. 원래 retrieve 호출을 잠시 감싸고 `finally`에서 원래 객체를 복원한다. producer/runtime 구현, runtime policy·ledger·예산, index와 DTO 계약을 바꾸지 않는다. 동일 producer 객체의 동시 호출을 지원한다는 뜻은 아니다.

선택적 도구의 unavailable/failed는 후보 전체 실패로 바꾸지 않는다. 원래 error와 실패 record를 State 이력과 public run에 남긴다. empty는 부정 사실도 오류도 아니다. 실제 snapshot에는 채택된 Evidence가 참조하는 원래 성공 record와 Chunk만 들어간다. 사용하지 않은 실패/empty record도 전체 State와 batch 이력에는 남는다.

정상 `ok/empty` outcome의 모든 record는 정확히 하나의 call에 속해야 하며 call이 참조하는 record 전체와 outcome record 집합이 같아야 한다. 성공/empty call의 마지막 record status도 call의 terminal status와 같아야 한다. 한 call의 여러 record는 허용하며 record 수를 call 수나 budget으로 바꾸지 않는다. required failed/unavailable outcome에는 집합 동등 조건을 적용하지 않는다. 실제 LLM extraction 실패는 record 저장 뒤 call append 전에 발생할 수 있으므로 그 부분 이력과 원래 오류를 rejected batch에 유지한다. 이때 retry 이력은 관찰된 원래 adapter 호출 묶음으로 귀속을 검증하며 성공한 연구 call을 합성하지 않는다.

정상 정정 `supersedes`와 그 근거에 의존하는 파생 이력은 State/batches에서 삭제하지 않는다. 기존 `_build_snapshot`이 계산한 active Evidence만 Coverage, 추가 요청의 Evidence, freeze admission과 evaluator에 전달한다. superseded/파생 무효화 항목도 Source allowlist/cutoff, Evidence event_date/value_as_of와 record/Chunk provenance 검증을 통과해야 한다. inactive라는 이유로 파손·미래 자료를 채택하지 않는다. 원래 Eligibility 근거가 무효화되면 기존 freeze 거절을 유지하며 적격성을 자동 재판정하지 않는다.

동일 Source/Chunk/record ID의 core·payload 충돌, Evidence 식별 core 충돌, 누락된 Source/Chunk/record, 다른 후보·schema·index·as_of, 미래 Source·Evidence는 채택하지 않는다. seed의 사용하지 않은 Source도 allowlist/cutoff를 통과해야 한다. 기존 `_build_snapshot`과 실제 `freeze_snapshot`의 provenance 검증을 재사용한다. Source만으로 Evidence나 rating을 만들지 않는다.

## revision과 유한 조사

초기 batch는 추가 조사 횟수가 아니다. 추가 요청은 기존 research gate에서 요청 전에 차감하며 현재 policy의 후보별 상한 2를 유지한다. Coverage가 생성한 open gap이 없으면 보강 요청을 initial plan으로 잘못 재실행하지 않고 거절한다.

State의 Evidence payload가 실제로 바뀐 batch에서만 revision을 한 번 증가시킨다. 새 Evidence뿐 아니라 기존 Evidence의 새 provenance도 변경이다. empty나 실패 batch는 revision을 올리지 않는다. Source·Chunk·이력만 추가된 경우에도 Evidence가 같으면 revision은 같다. 실제 freeze가 evaluation round를 증가시키고 JSON snapshot을 저장한다. 평가 branch에는 동일한 원래 frozen generation의 독립 사본을 전달한다.

binding의 명시 batch `ToolBudget`은 매 요청에 사본으로 전달한다. 이를 새 전역 호출 예산이나 runtime ledger 충전으로 해석하지 않는다. 추가 transport retry, 후보 간 예산 reset, policy/profile 값 변경은 하지 않는다.

새 artifact 경로의 required tool/LLM 실패는 추가 요청 중에도 원래 오류로 후보를 종료하고 평가하지 않는다. 이는 `RecoverableResearchFailure`를 처리하는 legacy callback 경로의 재시도 계약과 다르며 #211의 명시적 실패 보존·평가 0회 요구를 따른다. legacy 경로나 실제 transport retry 정책은 변경하지 않는다. `ToolBudget`은 caller 주입값이며 baseline 8회 이력을 새 기본값이나 누적 ledger로 설치하지 않는다.

## offline fixture 검증

`tests/integration/test_v3_evidence_snapshot_consumer.py`는 가상 Source/Chunk, 기존 `IndexedRetriever`의 synthetic backend, fixture StructuredLLM을 사용한다. 초기와 실제 Coverage gap 보강 모두 기존 `EvidenceResearch`를 실행한다. 기존 Technology callable은 fixture `FakeLLM`의 구조화 응답을 받고 기존 adapter와 five-way join에 참여한다. 나머지 Founder/Market/Moat/Business & Deal branch는 명시적 synthetic 평가다. 별도 Market Draft의 코드를 사용하지 않는다.

다음은 환경 설치가 끝난 저장소 루트에서 실행한다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. UV_OFFLINE=1 \
  uv run --no-sync python -B -m pytest \
  tests/integration/test_v3_evidence_snapshot_consumer.py \
  tests/integration/test_v3_research_loop.py \
  tests/integration/test_v3_outer_graph.py \
  tests/integration/test_v3_source_only_controller.py \
  tests/integration/test_evaluation_v3_adapter.py \
  tests/integration/test_evidence_research_graph.py \
  tests/unit/test_snapshot.py -q -p no:cacheprovider
```

새 테스트는 두 public consumer의 초기/보강 경로, 원래 오류 보존, optional empty/unavailable, partial failure, malformed provenance/carrier, 요청 상한, revision, JSON State, 다후보 격리와 원래 snapshot 사본을 확인한다. oracle/outer parity 테스트는 fixture runtime의 무작위 요청 ID 생성만 결정적으로 고정한다. production ID나 payload를 정규화해 차이를 숨기지 않는다.

이 검증은 실제 수집·embedding·모델 품질·비용 실측이 아니다. 네트워크와 유료 모델 없이 수행한 fixture wiring 검증이다. source-only의 eligible-not-ready, 평가 0회, actual Coverage/evaluation/scorer/selector 거절과 승인 consumer gate는 바꾸지 않는다. D05/D06의 실제 authority/verifier, 승인된 corpus/provider/실행 예산 및 full Discovery 선행 조건도 여전히 별도 차단 조건이다.
