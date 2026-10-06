# Approved scoring consumers (#187 / #168 / #217)

## Boundary

`skala_rag.scoring.approved_consumers` adds an explicit **offline fixture** route
through approved outer-policy loading, real v3 arithmetic, decision and selector.
It does not enable actual evaluation, a live controller, providers or publication.
#168 remains open. Core artifact propagation stays with #134; this slice does
not provide an actual Core producer or propagate its proposed status. Explicit
historical content resolution is separate from that propagation.

The existing `aggregate_scores_v3`, `decide_v3` and `select_best_v3` still require
fixture `V3Policy` and reject `ApprovedScoringPolicy`. The original outer-rejection
test remains unchanged. No numeric, Missing/N/A, ranking, budget, provider,
minimum-Evidence or RNG policy changes accompany this route.

## Python integration API

Import these names directly from `skala_rag.scoring.approved_consumers`:

```text
ApprovedPolicySource(
    *,
    path: str | Path,
    approvals: PolicyApprovals,
    approval_verifier: ApprovalVerifier,
    execution_mode: Literal["fixture", "live"],
    live_gates: LiveScoringGates | None = None,
    live_gate_verifier: LiveGateVerifier | None = None,
)

aggregate_scores_approved(
    evaluations: Sequence[Evaluation],
    source: ApprovedPolicySource,
    *,
    snapshot: EvaluationSnapshot,
    applicability_verifier: ApplicabilityVerifier | None,
) -> ScoreSummary

decide_approved(
    summary: ScoreSummary,
    source: ApprovedPolicySource,
    *,
    evidence_ids: tuple[str, ...] = (),
    rationale: str = "Deterministic v3 policy decision",
    risks: tuple[str, ...] = (),
    limitations: tuple[str, ...] = (),
) -> InvestmentDecision

select_best_approved(
    candidates: Sequence[Mapping[str, object]],
    source: ApprovedPolicySource,
    *,
    run_id: str,
    schema_version: str,
) -> SelectionResultV3
```

`ApprovedPolicySource` is frozen loader configuration, **not authority or a cached
approval token**. It requires an explicit mode. Every consumer independently
reloads the path, revalidates `PolicyApprovals` and invokes all three supplied
approval verifiers through `load_approved_policy`. An arbitrary constructed outer
DTO is not accepted. Changed source contents, scope/version/artifact references,
non-exact-True results and verifier exceptions fail closed before the numeric
core or candidate inputs are consumed. Synthetic approval registries exist only
in tests. The code-owned `pinned_approval_registry(root)` resolves historical
approval content; it does not authorize semantic or actual execution, and no
default accepting callback is installed.

The loaded outer remains at the admission boundary. Consumers pass its validated
catalog/version and exact numeric settings to private pure helpers extracted from
the existing v3 modules. `approved.operational.numeric` is **numeric data**, not
an execution policy/capability: the operational object is never passed to a
fixture entry point, and a live outer is never stripped or relabeled to execute.
The formulas and ordering have one implementation shared by old and new entries.
Private helpers are not policy-admission APIs.

The aggregate caller must supply the six evaluations promoted by the upstream
atomic five-branch join, plus its frozen snapshot. The new aggregate entry
revalidates a detached snapshot copy and the shared core checks all six dimensions,
23 criteria and run/candidate/round/snapshot/revision/policy/schema identity.
This is not new provenance or semantic verification: the upstream join/controller
must already establish Evidence/Source/Chunk/Record closure and rubric validity.
N/A still requires a separate applicability verifier receiving the detached
snapshot; a missing observation remains Missing, not N/A or negative evidence.
A zero applicable denominator still raises `ZeroDenominatorV3` without a score.

Decision returns the existing `InvestmentDecision` DTO, retaining exact
cross-product Missing and Market/Technology guards and unrounded thresholds.
Selector accepts the existing terminal row shape (`candidate_id`,
`eligibility_status`, `status`, `label`, `normalized_score`,
`weighted_missing_pct`, `applicable_weight`, `score_summary_id`) and returns the
existing `SelectionResultV3`. It preserves eligible/evaluated filtering,
label/score/Missing priority, exact decimals, raw-ID tie-breaks, permutation
invariance and all-WATCHLIST/PASS/no-eligible no-selection results. Upstream
controllers retain responsibility for row/score/decision reference closure.

## Actual execution remains denied

All three consumers reject `execution_mode="live"` with
`actual approved registry/runtime unavailable; live denied` **before** reading
the policy path, invoking any verifier or consuming evaluations/summary/rows.
Even a successful synthetic live contract with all three gates exactly True
cannot authorize these consumers. There is no injected registry/runtime override,
reservation, network client, credential lookup, model load or paid fallback.
Fixture sources also reject live admission inputs rather than ignore them.

The existing loader can still validate non-executing live-compatible inputs for
interface tests. Independent readiness/call/cost gate denial, OPEN decisions and
#158's excluded providers remain enforced there. This is not budget reservation
or a production approval resolver. Later actual admission needs authoritative
rubric artifact/content and semantic/applicability checks (including #134's Core
artifact), resolved controller policy, provenance and atomic join validation,
current runtime readiness, and per-request reserve/settle. None is supplied here.

## Offline evidence

`tests/unit/test_approved_scoring_consumers.py` exercises the real loader and
shared numeric cores, with network connection attempts forbidden. Tests compare
fixture/new results for complete evaluation, Missing/N/A, exact guards and
thresholds, zero denominator, stale/partial generation, raw-ID ties and selector
permutations. It also exercises the complete aggregate → decision → selector
chain and fresh verification at each stage. Failure tests assert zero numeric
entry, loader or callback use where the relevant denial must precede it.

The synthetic snapshot is schema-valid test input, not proof of an actual
upstream join, provenance closure, rubric semantics or live readiness.

Scoped development verification (no live API or full-project gate):

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/unit/test_approved_policy.py tests/unit/test_scoring_v3.py \
  tests/unit/test_select_v3.py tests/unit/test_approved_scoring_consumers.py
```

Parent integration owns independent review, full-project tests/build, commits,
PR and merge. This isolated writer creates no commits or GitHub mutations.

## Public outer 연결 (#217)

`graph.candidate_workflow_v3.build_candidate_workflow_v3`는 선택 인자
`approved_policy_source: ApprovedPolicySource | None = None`을 받는다.
`run_candidate_workflow_v3`와 기존 `run_candidate_report_v3`도 같은 인자를
`**options`로 전달한다. 생략하면 기존 fixture 점수 경로가 그대로 실행된다.
새 runner나 oracle, 승인 registry를 만들지 않는다.

기존 코드 소유 승인 기록을 명시하려면 저장소 루트에서 다음 구성을 사용한다.
이 구성만으로 평가나 보고서가 실행되지는 않는다. stage와 evaluator,
applicability verifier, Generator/Judge는 호출자가 별도로 공급한다.

```python
from pathlib import Path

from skala_rag.scoring.approval_registry import pinned_approval_registry
from skala_rag.scoring.approved_consumers import ApprovedPolicySource

root = Path.cwd()
registry = pinned_approval_registry(root)
approved_policy_source = ApprovedPolicySource(
    path=root / "configs/scoring.v3.json",
    approvals=registry.policy_approvals(),
    approval_verifier=registry.verify_policy,
    execution_mode="fixture",
)
```

builder는 첫 callback 전에 기존 loader로 승인 입력을 확인한다.
source의 live 모드, live 부가 입력, 잘못된 source와 source-only 혼용을 거절한다.
호출자가 준 `V3Policy` 전체가 source에서 읽은 operational 정책과 같아야 한다.
버전이나 criterion ID만 같은 입력은 충분하지 않다. run은 normalize 전후 두
compilation에 같은 분리된 정책과 source 구성을 전달한다. mutable 승인 데이터는
복사하지만 trusted verifier 객체는 복제하지 않아 현재 승인 철회 상태를 보존한다.

score 노드는 원래 five-branch atomic join의 여섯 Evaluation과 그 실행에서 고정한
snapshot을 `aggregate_scores_approved`에 전달한다. decision은 `decide_approved`,
모든 후보 처리 뒤 selector는 `select_best_approved`를 한 번 호출한다.
재평가, 재freeze, 보조 점수 계산이나 두 번째 selector는 없다.
각 consumer는 source를 새로 읽고 승인 내용을 다시 검증한다. builder는 그 검증의
trusted callback 전후에 전체 operational 정책을 대조하므로 callback이나 파일 변경을
이용해 실행 중 숫자, 조사 또는 선정 정책을 바꿀 수 없다. 검증된 outer DTO를
capability로 캐시하지 않으며 verifier의 정확한 True/거절/예외 의미도 유지한다.
후속 점수나 판단 실패는 기존 오류와 archive/advance 경로를 따른다.
selector의 fresh 검증 실패는 예외로 종료해 보고서 callback에 도달하지 않는다.

`tests/integration/test_v3_approved_outer.py`는 public outer와 report composite에서
코드 소유 pinned approval 내용을 소비한다. 합성 후보 두 개의 원래 join과 snapshot,
State/context를 대조하고 승인 경로와 생략 경로의 결과가 같은지 검증한다.
이는 실제 outer 노드의 연결 증거지만 기업 관측과 평가, Generator/Judge 응답은
합성 입력이다. 보고서 구조 검증 성공을 실제 semantic 승인이나 PDF 완료로
표시하지 않으며 `final_allowed=False`를 유지한다.

source-only의 `SOURCE_ONLY_EVALUATION_NOT_READY`, actual 평가 거절과 기존
report fixture guard는 그대로다. 실제 기업 조사 원본과 적격성 근거,
D05/D06 등의 검토 권위, Core 상태 전파와 Market callable, 실제 모델 실행 결정과
누적 예산 reserve/settle은 별도 입력과 승인 대상이다. 역사적 승인 내용의 일치가
그 권한을 대신하지 않는다. 전체 품질 게이트와 독립 리뷰는 부모 통합 단계에서 실행한다.
