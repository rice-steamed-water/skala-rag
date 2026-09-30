# Approved-value scoring contract — #168

## Scope and approval boundary

The separate `skala_rag.scoring.approved_policy` module admits the approved
`v3-operational-1.0.0` values from #82 and explicit approval records for
`core-0.1.0` and `finance-0.1.0`. #168 approves this compatibility boundary, not
new weights, thresholds, anchors, ranking, RNG, minimum-Evidence gates, budgets,
provider calls or M3 execution. Policy approval is not semantic verification.

`ScoringPolicy` / `load_policy` remain draft/fixture-only.
`V3Policy` / `load_v3_policy` and `configs/scoring.v3.json` remain fixture-only.
The config's historical `rubric_status=not_approved`, `live_readiness=not_approved`
and `live_budget=not_approved` are **not** silently promoted. The new contract's
separate, externally verified rubric records supersede only the historical
rubric metadata for this explicitly loaded contract. Neither rubric file is
modified or loaded by this module. Consumers must separately verify the actual
rubric artifact against the approved version/content; a version string is not
proof that arbitrary anchors have been approved. The Core file's proposed status
must be resolved by its owner (#134), not rewritten here.

## Exact caller API

```python
load_approved_policy(
    path: str | Path,
    *,
    approvals: PolicyApprovals,
    approval_verifier: Callable[[ApprovalEvidence, V3Policy], bool],
    execution_mode: Literal["fixture", "live"] = "fixture",
    live_gates: LiveScoringGates | None = None,
    live_gate_verifier: Callable[[LiveGate, LiveScoringGates], bool] | None = None,
) -> ApprovedScoringPolicy
```

`PolicyApprovals` requires:

- `contract_reference="rice-steamed-water/skala-rag#168"`;
- `operational`: scope `operational`, version `v3-operational-1.0.0`, reference
  `rice-steamed-water/skala-rag#82`;
- `core`: scope `core`, version `core-0.1.0`, explicit nonblank reference;
- `finance`: scope `finance`, version `finance-0.1.0`, explicit nonblank reference.

All three records must be resolved by the **trusted controller's external
approval verifier** against authoritative approval evidence. The callback also
receives the validated operational policy. Return exactly `True` to accept;
`1`, prose such as `"approved"`, false, missing verifier or exceptions do not
approve anything. There is no default verifier, registry, credential lookup,
provider fallback or provider call. Provider/model output must never define
these callbacks or serve as their authoritative registry. Tests use an explicitly
synthetic registry and are not evidence of actual approval/readiness.

## Independent live gates

`execution_mode="live"` additionally requires `LiveScoringGates` and a trusted
`live_gate_verifier`. Every field is explicit:

- `run_id`, exact `policy_version`, canonical `provider`, and `open_decisions`;
- `readiness: tools.runtime.Readiness`;
- `limits: tools.runtime.RuntimeLimits` and `allowance: tools.runtime.Allowance`;
- separate `runtime_readiness_reference`, `call_budget_reference`, and
  `cost_budget_reference`.

Local structural checks reject known open decisions, excluded providers,
missing readiness, zero/unavailable provider call capacity, absent token/cost
caps and an allowance exceeding the supplied caps. Zero cost is preserved when
both the explicit cost bound and allowance are zero; no new positive-cost policy
is introduced. The existing runtime numeric validators reject invalid counts,
nonfinite/negative amounts and coerced booleans.

The external callback is then called independently for each `LiveGate`:
`runtime_readiness`, `call_budget`, `cost_budget`. Each must return exactly `True`
for the **same complete run/provider/policy/limits/allowance inputs**. It must
resolve the appropriate reference, approved provider identity and actual current
readiness or remaining shared-ledger budget; recording a nonblank reference is
not verification. The controller must enumerate unresolved decisions, not clear
them to obtain admission. Empty `open_decisions` in a synthetic test does not
approve D05/D06 minimum Evidence or D08 RNG for a real controller.

This is a non-executing preflight snapshot, not an execution capability or a
budget reservation. Recheck current runtime timing/approval/readiness and use
`AdapterRuntime` / `BudgetLedger.reserve` and `settle` at each actual request.
The loader neither reserves nor consumes budget. It validates copies of caller
inputs, but nested runtime maps remain DTO data, not tamper-proof tokens. Never
reuse an admission object after input/state changes without fresh verification.

#158's existing KIPRIS/`kipris`, KRX/`krx`, 중기부 and Tavily/`tavily` exclusions
are reused directly. The external verifier must resolve canonical identities;
an alias is not a new approved provider. Keep `invoke_current_scope` around the
whole adapter call. This module does not wire or bypass that guard, allow a paid
fallback or grant new provider approval.

## Consumer compatibility and remaining work

The returned object exposes `policy_version: str` and
`criteria: tuple[V3Criterion, ...]`. Those are the catalog attributes used by
`agents.evaluation.build_user_prompt`, `validate_output`, `assemble_evaluation`
and `agents.business_deal.evaluate_business_deal`. A regression exercises the
real prompt/assembly helpers with this object, including policy-generation
mismatch rejection. Existing evaluator annotations still say `ScoringPolicy`;
owners should explicitly adopt the new type/catalog interface when integrating
live controllers. No evaluator signature or runtime behavior is changed here.

The object deliberately does **not** subclass the legacy draft policy or expose
legacy `thresholds`/`budgets`. Do not convert it into a draft policy or apply the
baseline fixed-100/three-label scorer. Its `operational` member preserves all
existing v3 numeric/ranking/research/report settings and original fixture mode.
The existing v3 arithmetic/decision fixture consumers accept that member;
`aggregate_scores_v3` still rejects the new outer contract. That is a preserved
fixture-only boundary, **not** a newly enabled live aggregator.

Still independently required before real evaluation/controller execution:

1. Approved artifact/content checks for Core/Finance, actual semantic rubric and
   applicability verifiers, authoritative Finance metric-role/round/period/unit/
   accounting-subject facts (#60/#61). The actual Business & Deal gate is unchanged.
2. Frozen snapshot provenance, matching run/candidate/generation/policy/rubric,
   atomic five-branch join, and each N/A rule's actual reason/evidence verification.
3. Explicit controller resolution of minimum Evidence/Coverage, candidate/RNG
   choices and other still-OPEN decisions. #168 does not close these.
4. Runtime provider/timing/credential/index readiness, per-attempt call/token/cost
   reservation and usage settlement; current-run exclusions and no paid fallback.
5. Separate owner integration of live aggregation, selector, Warning/final
   publication and M3 validation. This module does not assert any are complete.

## Offline verification

`tests/unit/test_approved_policy.py` covers external approvals and version binding,
independent live gates, exclusions, unknown/draft/modified policies, invalid
observations, fixture default, nested-input revalidation, real shared catalog
helper compatibility, and unchanged v3 fixture arithmetic. Existing catalog,
v3 policy/scoring, Finance actual-mode refusal and runtime tests remain regression
gates. All successful live-mode tests use synthetic in-memory inputs only.
