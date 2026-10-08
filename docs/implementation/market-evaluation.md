# Market PR #134 integration boundary

`agents.market.evaluate_market` reuses the existing `market-evaluation-v2` prompt and
#22 wrapper. The caller supplies a frozen snapshot, reviewed target market and
Evidence → MarketLink attribution; this module does not search or authenticate
those upstream facts. Market definition, geography, currency, reference year,
forecast/actual basis, TAM cap and conflicting CAGR bands remain checked.

Core `core-0.1.0` is approved by #59 comment5904859865 (2026-09-30).
Only its status/header changes; approved numerical bands, anchors and minimum
Evidence rules are unchanged. Finance has its separate #61 approval. Neither
approval grants runtime permission or a new live budget.

## Caller contract errors

For admitted TAM/SAM figures, the monetary observation's `Evidence.value_as_of`
must be present and its year must equal `MarketLink.reference_year` (the exact
calendar date need not match). Publication/retrieval dates, `event_date` and
`period` are not substitutes. The Evidence DTO already requires this monetary
date; the Market boundary also guards instances copied or mutated without DTO
revalidation. CAGR retains its existing start/end-year contract.

Malformed caller input raises `ValueError` before any LLM call: contradictory
figure links/criteria/units, missing monetary dates, incompatible reference years,
or values outside the existing rubric bands. This is a technical input/contract
error, not an `EvaluationResult` failure from the model, missing evidence or a
negative market fact. Callers must correct the input/configuration rather than
convert it into a missing criterion. Unlinked or wrong-target/currency evidence
retains the existing exclusion behavior. No band, cap, minimum-source rule or
missing threshold is added or changed.

## Compatibility and remaining integration

- The common wrapper retains `system_prompt`, `user_prompt`, `prompt_context`
  and `extra_validator`. An explicit user prompt takes precedence; otherwise
  the deterministic snapshot prompt includes the supplied context. Both paths
  retain validation, bounded structural repair and diagnostic redaction.
- Founder and Market accept the exact approved `core-0.1.0` version, not future
  versions merely labelled approved. Founder's legacy entry remains draft-policy;
  its additive approved fixture consumer is exercised in the new five-implementation
  matrix below, without altering the legacy guard.
- Market returns baseline `EvaluationResult`; the existing
  `bind_baseline_evaluator_v3("market", ...)` converts its original terminal result
  to `EvaluationBranchResult` without re-running the evaluator. Its baseline
  `ScoringPolicy` annotation is unchanged, but the offline integration below
  exercises the loader-backed `ApprovedScoringPolicy` catalog/version view.
  This observed fixture compatibility is not an actual-runtime admission API.
  The existing opt-in live smoke's historical manual snapshot is not a whole-v3
  evaluation trace.
- Moat's checked-in approved Core artifact still requires explicit artifact
  approval and snapshot-bound reviewed anchors. Its legacy proposed-only
  patent/comparison tests use a separately labelled synthetic historical fixture.
  Boolean reviews are not promoted to approved anchors; actual runtime stays blocked.
- Finance semantic verification and approved policy admission are unchanged.
  No new actual API call, provider selection, policy threshold or runtime permission
  is introduced by this integration.

## Offline Market → binder → five-way graph regression

`tests/integration/test_evaluation_v3_adapter.py::test_actual_market_approved_binder_five_way_offline`
executes the real `evaluate_market` implementation, its existing #22 wrapper and
Market semantic validator, the unchanged baseline binder, and the installed
`build_evaluation_graph_v3(...).compile()` five-way LangGraph barrier.
No new production adapter, evaluator bridge or policy defaults are required.

The fixture resolves `pinned_approval_registry` against this checkout and loads
`configs/scoring.v3.json` with `load_approved_policy(..., execution_mode="fixture")`.
`validate_core_artifact` binds the exact parsed owner-approved Core artifact to
that registry before calling Market. A fresh **synthetic** frozen input is
prepared with the operational `v3-operational-1.0.0` policy version before the
call; on the success path, snapshot, Evidence, corpus metadata and all five
terminals retain the original `synthetic-common-1` schema. No returned draft
evaluation is relabelled
as operational, and no policy/result generation is stamped after evaluation.

Only **Market's evaluator implementation** is exercised. Founder, Technology,
Moat and Business & Deal are four explicitly injected synthetic terminal
branches, with atomic traction/deal_terms data in the last branch. They are not
four real evaluator runs. The Market facts, reviewed-target/link observations
and FakeLLM responses are also synthetic, reused from the existing Market test
fixture; they are not current authenticated Web/RAG facts or a reviewed corpus.
The test denies socket connections and invokes no live/paid provider.

| Case | Observed contract asserted by regression |
| --- | --- |
| Success | Each callback once; one Market FakeLLM call; one join; six dimensions promoted. Original Market fields, ratings and citations are preserved; v3-only N/A slots remain empty. |
| Original Market timeout | One FakeLLM call; the real evaluator returns a terminal failure; every original redacted WorkflowError field and failure ID survives unchanged. |
| Wrong requested schema | Real Market produces its original different-schema result; binder rejects rather than rewriting it. |
| Wrong original generation | Real Market evaluates a separately prepared matching draft input/policy; binder rejects that original draft result against the operational frozen input. |
| Unauthorized industry Evidence | Real Market succeeds on synthetic industry facts, but binder rejects when its explicit industry permission is absent. |
| Wrong criterion citation | Market size cites the synthetic CAGR Evidence; existing wrapper rejects attribution through its bounded repair path (two FakeLLM calls), then preserves the original terminal failure. |
| Earlier-year monetary Evidence | SAM `value_as_of` year 2019 versus link year 2025 is rejected before FakeLLM (zero calls). |
| Live preflight | Graph denies execution before all five callbacks (zero callbacks and zero FakeLLM calls). |

Every failure promotes zero dimensions, archives the candidate and advances
exactly once; repeating the existing failure-only archive controller is a no-op.
No scoring/selection/report or PDF completion is claimed by this evaluation-stage
regression. Scoped offline verification can be reproduced using the installed
checkout environment:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider \
  tests/integration/test_evaluation_v3_adapter.py \
  tests/unit/test_evaluation_v3_adapter.py tests/unit/test_approval_registry.py \
  tests/unit/test_market.py tests/unit/test_evaluate_dimension.py -q -ra
.venv/bin/ruff check tests/integration/test_evaluation_v3_adapter.py
.venv/bin/ruff format --check tests/integration/test_evaluation_v3_adapter.py
```

The worker's initial integrated run returned **162 passed** for consumer/wrapper
coverage and **262 passed, 1 failed** including registry coverage. The incoming
registry test still expected historical-main Core `proposed` status and a PR134
owner dependency. The parent reconciled those two assertions with this owner's
approved Core status; content-binding and all non-admission assertions remain.
The subsequent parent full suite returned **3240 passed, 3 skipped**, with Ruff,
format (332 files) and sdist/wheel build passing. This is offline integration
evidence, not actual Market/provider execution or publication acceptance.

## Separate offline Market + approved Technology → five-way graph regression

`test_actual_market_and_approved_technology_same_frozen_graph` adds **two real
evaluator implementations**, not two actual-runtime calls: Market and
`evaluate_technology_approved_fixture`. The original eight Market/four-synthetic
cases and earlier draft-generation cases remain separate and unchanged.
Founder, Moat and Business & Deal are **three explicitly synthetic** sibling
terminals; Business & Deal still contains both traction and deal_terms.

The combined fixture reuses `SyntheticMarketCase` facts/output and the existing
Technology unit-case output/typed-receipt pattern. Before evaluation, it closes
its synthetic Source → Chunk → RetrievalRecord → Evidence attribution (including
Market's allowed industry evidence) and freezes the full operational input.
Both evaluators and every sibling receive detached copies of that identical
snapshot, catalog and corpus generation. This preparation is not RAG retrieval:
no document download, embedding, provider request or live search occurs. It does
not stamp returned policy/schema/generation fields or change production code.
The approved Core artifact is read unchanged; policy and content are verified
with `pinned_approval_registry.verify_policy` / `verify_core`, not synthetic
approval verifiers.

Technology's external review registry, `ReviewedTechnologyAnchor` receipts and
all four reviewed flags are **synthetic caller-owned assertions**, not trusted
semantic review or actual review authority. The successful path authenticates
four exact typed receipts through that fixture resolver against
`frozen_snapshot_digest` of the original **whole** input and
`core_artifact_digest` of the pinned Core. The test keeps the returned
`TechnologyEvaluation` container, prompt version, allowed Evidence IDs and
criterion → Evidence → retrieval/chunk → snapshot trace outside graph State;
only its explicit `.result` enters the existing baseline binder. Both distinct
FakeLLMs and both evaluators run once; all five callbacks run once, one join
atomically promotes six dimensions, and the original baseline fields/citations
remain exact. State and frozen storage remain unchanged.

| Combined case | Regression boundary |
| --- | --- |
| Success | Two real implementations, two distinct FakeLLM calls, four resolved synthetic Technology review receipts, three synthetic siblings, six-or-zero promotion at every streamed State. |
| Market terminal timeout | Original Market WorkflowError and failure IDs preserved; Technology still executes/reviews once; no binder/graph retry. |
| Technology terminal timeout | One Technology FakeLLM call, zero review/resolver calls, original terminal WorkflowError preserved. |
| Stale whole-snapshot review | Change only a non-Technology Market claim before the run; stale original full-snapshot receipt rejected, not validated against a Technology-only projection. |
| Wrong review Evidence/reference | Wrong citation binding or unregistered external review reference rejects technically without model repair. |
| Mixed nested generation | Non-Technology Evidence with a stale schema rejected by Technology full-snapshot preflight before its FakeLLM/review. |
| Reviewer exception | Redacted `TechnologyReviewError` raises from the evaluator; no terminal model result or fabricated Missing/investment rejection. |
| Graph live preflight | Zero callbacks, FakeLLM or reviewer calls. |
| Technology actual-runtime preflight | Separate test passes `actual_runtime=True` and a nonexistent policy path; denial occurs before path access, any verifier/reviewer or FakeLLM call. |

The current graph's `branch_node` catches evaluator exceptions and returns a
redacted technical `WorkflowError(error_code="UPSTREAM_INVALID")`. The regression
observes both the raised Technology review/preflight exception and that returned
graph error; it does not invent an evaluator terminal receipt. For these returned
graph failures, zero dimensions promote and the existing failure controller
archives/advances exactly once; replay is a no-op. Nothing is scored or classified
as an investment rejection. Technology model timeout is tested separately from
post-model review rejection.

Scoped verification (not a full-suite or parent acceptance receipt):

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider \
  tests/integration tests/unit/test_technology.py \
  tests/unit/test_technology_approved.py tests/unit/test_market.py \
  tests/unit/test_evaluation_v3_adapter.py tests/unit/test_approval_registry.py \
  tests/unit/test_evaluate_dimension.py -q -ra
.venv/bin/ruff check tests/integration/test_evaluation_v3_adapter.py
.venv/bin/ruff format --check tests/integration/test_evaluation_v3_adapter.py
git diff --check
```

Worker execution returned **655 passed, 3 skipped** (only the three opt-in live
smokes skipped). A red test first kept Technology as the old synthetic sibling
and failed on zero versus one required Technology FakeLLM call; enabling the
approved evaluator made all nine combined graph cases pass. A separate
process-local API-removal sabotage failed all ten new tests and restored the
function in `finally`; no production file was patched for that probe. Ruff,
format and diff checks are recorded in the worker receipt. A bounded seven-file
graphify AST-only extraction ran offline in scratch (173 nodes, 447 edges), not
semantic extraction or a repository graph rebuild. The parent subsequently ran
the integrated full suite: **3319 passed, 3 opt-in live skipped**, zero failures
or errors; Ruff check, format (335 files) and sdist/wheel build passed. Sonnet
independently ran all 28 integration cases; Terra ran the 10 new cases, 69 approved
Technology, 14 legacy Technology and 101 registry cases with no reproducible
P1/P2 finding. Terminal timeout errors remain original; only raised review or
preflight exceptions follow the graph's `UPSTREAM_INVALID` path. These are offline
acceptance receipts, not actual RAG/runtime or publication acceptance.

## Five real Python implementations, one pinned offline generation

`test_five_real_implementations_one_pinned_frozen_graph` adds a separate bounded
12-case matrix. Every callback invokes its existing evaluator implementation:

- Founder: `evaluate_founder_approved_fixture`, original baseline result.
- Market: `evaluate_market`, original baseline result.
- Technology: `evaluate_technology_approved_fixture`, retained container/trace;
  only explicit `.result` enters the baseline binder.
- Moat: `evaluate_moat_approved_fixture`, original native v3 branch (not rebound).
- Business & Deal: `evaluate_business_deal`, original atomic native v3 branch.

There are **no injected terminal sibling envelopes** in this new matrix. Prior
Market/four-synthetic, Market+Technology/three-synthetic and draft compatibility
cases stay separate and unchanged. All five module paths are asserted to belong
to this checkout. A five-party barrier starts the callbacks in parallel, each
receiving a detached copy of the identical frozen operational snapshot.

The fixture reuses the earlier common corpus/Market/Technology preparation and
common Founder/Moat observations. It reads the exact approved Core and Finance
artifacts without modifying status/version/content. `pinned_approval_registry`
verifies policy and all artifact content through the approved-policy loader in
fixture mode; Core uses the registry's explicit artifact receipt and resolver.
Source/Chunk/RetrievalRecord/Evidence closure is prepared before any evaluator.
The original schema, policy, corpus/index, run/candidate, snapshot, round and
revision are not rewritten on returned results.

Founder and Technology use exact typed synthetic reviewed anchors with external
fixture resolution; Moat uses its implemented `ReviewedMoatAnchor` binding.
All three bind the **whole original snapshot digest**, exact Core digest,
criterion/rating/cited IDs; Founder additionally binds the explicit synthetic
person set and Evidence/person map. Those caller-owned flags and resolvers are
not authenticated human/semantic authority.

Finance is not all-Missing: the existing approved gross-margin unit example is
reused with synthetic reported FY2025 revenue 100 and cost 50, same entity/KRW/unit
and period. Two `ReviewedFinancialFact` receipts are prepared before execution,
with exact frozen Evidence and original identity. The actual Finance helper
validates their metric roles, accounting entity, period/currency/unit and the
approved **50% gross margin → rating 5** band. Other financial criteria remain
honestly Missing; no N/A rule or fabricated investment finding is introduced.
`ApprovedVerifiers` are explicitly synthetic caller assertions, not a semantic
review service. The existing Business & Deal verifier API supplies a Finance-only
Evidence projection; the test checks that exact projection and its unchanged
original identity/source/chunk/record storage rather than inventing a whole-input
verifier API. This is offline numeric validation, not authentic financial proof.

The success case observes five distinct FakeLLMs generating once each (**five
simulated calls, zero provider requests**), all five terminal results, one join
and six dimensions. Original criterion IDs, statuses, ratings, rationales and
Evidence references survive; Technology's full trace remains outside State.
Every streamed State has six-or-zero promoted dimensions and frozen input/storage
are unchanged.

The matrix also covers terminal timeout from **each** of the five implementations,
individually stale Founder/Technology/Moat whole-snapshot anchors after an
unrelated Market claim change, valid traction plus invalid deal_terms output,
Market's actual wrong-requested-schema response rejected by the binder without
relabeling, and graph live preflight with zero callbacks/models/reviews. Every
returned terminal error retains all original fields/IDs. Founder/Technology stale
review raises and is observed as the graph's redacted `UPSTREAM_INVALID`; Moat's
implemented stale-review path returns its own original terminal failure. No
synthetic terminal is substituted for either contract. All returned graph failures
promote zero dimensions and archive/advance exactly once; archive replay is a
no-op. No scoring, investment rejection or report/PDF acceptance is claimed.

Three additional parametrized actual-entry preflight cases cover Founder, Moat
and Business & Deal; the existing Technology preflight remains. Approved entries
deny before a deliberately nonexistent policy path, approval/review callbacks or
FakeLLM; Business & Deal denies `execution_mode="real"` before verifier/model.
No production retry/controller/policy changes were required.

New coverage initially exposed test assumptions, not an implementation defect:
Business & Deal passes the documented Finance projection, and common `LLMError`
text is already redacted by its caller. The fixture/assertions were aligned with
those existing APIs; no production workaround or manufactured RED is claimed.

Scoped worker verification returned **15 passed, 28 deselected** for this increment
and **894 passed, 3 opt-in live skipped** for all integrations plus relevant
Founder (approved/legacy), Market, Technology (approved/legacy), Moat, Business &
Deal, Finance verification, registry/policy, binder and common wrapper suites.
This is not a full-suite/build or independent parent acceptance receipt.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider \
  tests/integration/test_evaluation_v3_adapter.py \
  -k 'five_real or five_implementation_actual' -q --tb=short
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider \
  tests/integration tests/unit/test_founder_approved.py tests/unit/test_founder.py \
  tests/unit/test_market.py tests/unit/test_technology.py \
  tests/unit/test_technology_approved.py tests/unit/test_moat.py \
  tests/unit/test_business_deal.py tests/unit/test_finance_verification.py \
  tests/unit/test_approval_registry.py tests/unit/test_approved_policy.py \
  tests/unit/test_evaluation_v3_adapter.py tests/unit/test_evaluate_dimension.py -q -ra
```

**Parent offline acceptance of this increment passed:** full suite **3406 passed,
3 opt-in live skipped**, zero failures/errors; Ruff, format (338 files) and
sdist/wheel build passed. Sonnet independently executed the 15 new cases; Terra
executed all 43 integration cases and the 15-case selection, with no reproduced
P1/P2 findings. Parent also reran the 15-case selection successfully. Earlier
counts above remain historical. This completes five-implementation offline
consumer-wiring coverage, not actual-live evaluation, authenticated RAG/financial
or person review, M3, publication or #59/#96/#168 completion.

## Preserved actual-execution gates

Registry content matching is not runtime semantic acceptance: fixture diagnostics
remain `semantic_review=unreviewed`, `runtime_admission=not_admitted` and
`campaign_approval=unapproved`. Market's numerical/link validation does not
establish authentic demand anchors, minimum-evidence adequacy or trusted current
review of upstream target/link observations. Core values already have approval;
that does not resolve remaining D05/D06/D08 admission/readiness/campaign choices.

Founder's legacy fixture still depends on `policy.status`, which the approved
policy view does not expose. The merged additive
`evaluate_founder_approved_fixture` now supplies the loader-backed offline path
exercised by the five-implementation matrix; the legacy guard is unchanged.
Legacy `evaluate_technology(..., execution_mode="fixture")` retains its
draft-policy contract, while its approved fixture entry supplies loader-backed
compatibility. Both approved entries reject `actual_runtime=True`; neither is
runtime admission. Moat's reviewed-anchor and Finance's semantic gates remain
separate unchanged requirements. The existing outer workflow is fixture-only;
these tests do not expand its policy admission or authorize new
corpus/model/policy/provider/budget choices.

Keep existing #59 / PR134 **Draft**, and #59/#96/#168 OPEN pending their actual
acceptance work. This regression is five-implementation offline consumer wiring,
not M3, parent acceptance of this increment, or an authenticated
research → RAG → freeze → evaluation → report → PDF trace. No new live run is
authorized or reported.
