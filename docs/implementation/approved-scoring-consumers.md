# Approved scoring consumers — #187 / #168

## Boundary

`skala_rag.scoring.approved_consumers` adds an explicit **offline fixture** route
through approved outer-policy loading, real v3 arithmetic, decision and selector.
It does not enable actual evaluation, a live controller, providers or publication.
#168 remains open. Core artifact propagation stays with #134; this slice neither
loads nor copies its proposed rubric artifact.

The existing `aggregate_scores_v3`, `decide_v3` and `select_best_v3` still require
fixture `V3Policy` and reject `ApprovedScoringPolicy`. The original outer-rejection
test remains unchanged. No numeric, Missing/N/A, ranking, budget, provider,
minimum-Evidence or RNG policy changes accompany this route.

## Python integration API

Import these names directly from `skala_rag.scoring.approved_consumers`:

```python
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
in tests; there is no production registry or default accepting callback.

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
