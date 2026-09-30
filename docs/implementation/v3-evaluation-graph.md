# v3 parallel evaluation stage (#24)

이 모듈은 **fixture 기반 versioned 평가 subgraph와 실패 handoff**다.
기존 #23 baseline `CandidateNodes.evaluate` writer/API는 바꾸지 않는다.
기존 controller는 baseline DTO를 검증하므로 이 subgraph 결과를 baseline
`evaluation_results`/`evaluations`로 변환하거나 그대로 넣으면 안 된다.
완전한 v3 후보 runner·selector·scoring·재조사·report 구현이 아니다.

## 후속 #23의 연결 API

```python
from skala_rag.graph.evaluation_v3 import build_evaluation_graph_v3
from skala_rag.contracts.state_v3 import EvaluationStateV3
from skala_rag.contracts.v3 import EvaluationBranchResult

compiled = build_evaluation_graph_v3(
    evaluators,  # exactly founder/market/technology/moat/business_deal callbacks
    criteria=explicit_catalog.criteria,  # supplied complete 23-criterion catalog
    policy_version=explicit_catalog.policy_version,
    run_id=run_id,
    schema_version=snapshot.schema_version,
    industry_evidence_dimensions=explicit_allowed_dimensions,
    applicability_validator=approved_applicability_validator,  # or None: reject N/A
    clock=clock,  # Callable[[], aware datetime]
).compile()
result = compiled.invoke(state)
```

각 evaluator의 signature는 `callback(detached_snapshot: EvaluationSnapshot)`이고
반환은 **명시 import한 v3 `EvaluationBranchResult` 또는 그 JSON payload**다.
callback은 State/controller/scoring을 쓰지 않는다. provider·LLM·rubric은 구현하지
않으며 주입한 evaluator가 책임진다. `business_deal`은 `traction`과 `deal_terms`를
둘 다 포함한 하나의 성공 envelope 또는 평가가 없는 실패 envelope를 반환한다.
한쪽 성공만 반환하면 전체 branch가 실패한다.

입력은 JSON `EvaluationStateV3`다. baseline 상태에 다음을 명시적으로 준비한다:

- `snapshot_v3`: 기존 `freeze_snapshot` 반환의 JSON payload.
- `snapshots[snapshot_id]`: 실제 freeze controller가 저장한 동일 payload.
- `run_input`: fixture execution mode 및 동일 policy version.
- `current_candidate_id`, `evaluation_rounds`, `evidence_revisions`: 동일 세대.
- `candidates`, `candidate_index`, `candidate_outcomes`, `candidate_status`, `errors`:
  현재 후보를 가리키는 기존 후보 controller 상태.

`EvaluationStateV3`는 baseline State를 상속하며 baseline 초기값은 변경하지 않는다.
새 결과 이름은 `branch_results_v3`, `evaluations_v3`, `evaluation_status_v3`,
`evaluation_failure_ids_v3`다. 마지막 필드는 이번 invocation의 실패 ID만
담으며 기존 State.errors의 과거 동일 후보 오류를 archive에 끌어오지 않는다.
`branch_results_v3`는 run/candidate/round/snapshot/branch JSON-array key를 가진
terminal-envelope 저장소다. `evaluations_v3`는 **이번 stage의 원자적 성공 결과**
(기존 `evaluation_key` 형식의 6개 v3 Evaluation)이며 매 invocation에서 초기화한다.
후속 runner가 후보별 영구 이력을 원하면 성공 시 별도 versioned 저장소로 옮긴다.

### 성공

실제 LangGraph fan-out으로 5개 node를 실행하고
`add_edge(list(BRANCH_DIMENSIONS), "join_v3")`가 모든 branch를 기다린다.
join의 단일 writer가 6dimension을 모두 검증한 뒤 한 번에 promotion한다.
`evaluation_status_v3 == "success"`; 후보 index/current ID는 그대로다.
후속 v3 aggregate는 **이 상태를 확인한 후에만** v3 DTO를 읽는다.

### 실패와 archive → advance

callback exception·invalid/partial result는 redacted `UPSTREAM_INVALID` terminal
branch로 변환한다. supplied failure envelope의 기존 오류는 보존한다.
실패하면 `evaluations_v3 == {}`이며 scoring/decision을 생성하지 않는다.
preflight의 frozen storage/controller mismatch도 callback 실행 전에 실패한다.

`archive_advance_failure_v3(state)`가 단일 재사용 controller adapter다.
현재 후보의 오류 ID를 담은 기존 `CandidateOutcome(status="failed")`를 archive하고
index를 정확히 1 증가시킨 다음 current ID를 None으로 만든다. Graph가 이 adapter를
실제로 실행하며 완료 후 반복 adapter 호출은 no-op다. conflicting outcome은 거절한다.

후속 #23은 실패 시 **이미 증가한 index에서 다음 후보를 선택**해야 한다.
기존 archive/advance node를 또 호출하지 않는다. baseline candidate graph에 바로
꽂는 Stage adapter는 제공하지 않으며 baseline `StageFailure`/DTO 경계는 유지한다.

## 검증 경계

- run/candidate/round/snapshot/revision/policy identity가 모두 일치해야 한다.
- 각 dimension criterion은 명시 주입 catalog에 정확히 한 번씩 존재해야 한다.
- 실제 snapshot Evidence ID, Evidence map key, criterion attribution,
  company candidate attribution 및 명시 industry dimension 허용을 확인한다.
- gap도 현재 후보/영역 criterion만 허용한다.
- N/A는 v3 구조 계약의 rule ID·reason·applicability Evidence가 필수다.
  해당 Evidence도 동일 attribution gate를 통과한 뒤, 주입 validator가
  승인 rule·reason·근거의 의미를 확인하고 정확히 `True`를 반환해야 한다.
  validator는 순수/결정적이어야 한다(개별 branch와 join에서 재검증).
  승인 applicability rule 목록이나 D14 rubric의 암묵적 기본값은 없다.
- 기존 저장소의 다른 candidate/round envelope는 join에서 무시한다.
  현재 evaluator가 다른 candidate/round를 반환하는 것은 실패다.
  같은 candidate/round의 run/snapshot/revision/policy 손상은 무시하지 않고 거절한다.
- 같은 key/같은 payload replay는 멱등, 다른 payload는 conflict다.
  branch callback replay conflict는 실패 handoff로 연결하며 기존 payload를 덮어쓰지 않는다.

## 실행 증거와 남은 범위

`tests/integration/test_v3_parallel_evaluation.py`의 T06은 실제 installed LangGraph와
5-party `threading.Barrier`로 동시 fan-out, 단일 join, 6dimension promotion을 검증한다.
T22는 branch 실패·business_deal half-failure·identity/catalog/Evidence 손상,
원본 오류 보존, archive/index 1회 증가, 부분 promotion 금지를 검증한다.
실제 `freeze_snapshot` → subgraph 경로와 replay/conflict도 실행한다.
unit tests는 completion-order 독립, stale envelope, Missing/N/A 및 attribution을 검증한다.
모두 synthetic/offline이며 live 실측이나 정책 승인 증거가 아니다.

#82 정책 파일은 이 subgraph에서 import/copy하지 않는다. 평가 catalog는 명시 fixture
catalog로만 주입한다. sequential #23 후속의 v3 all-candidate selector/scoring,
#25 조사 loop, 실제 LLM adapter·승인 rubric/applicability rule catalog·live 예산은
별도 작업이다. graphify graph는 이 worktree에 없으며 scope 밖 파일은 생성하지 않는다.
