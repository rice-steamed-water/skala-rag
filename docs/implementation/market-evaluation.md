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
  versions merely labelled approved. Founder remains a draft-policy fixture entry.
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

## Preserved actual-execution gates

Registry content matching is not runtime semantic acceptance: fixture diagnostics
remain `semantic_review=unreviewed`, `runtime_admission=not_admitted` and
`campaign_approval=unapproved`. Market's numerical/link validation does not
establish authentic demand anchors, minimum-evidence adequacy or trusted current
review of upstream target/link observations. Core values already have approval;
that does not resolve remaining D05/D06/D08 admission/readiness/campaign choices.

Founder's fixture path still depends on legacy `policy.status`, which the
approved policy view does not expose; that separately owned compatibility gap
remains a follow-up. Legacy `evaluate_technology(..., execution_mode="fixture")`
also retains its draft-policy contract, but the newly merged approved fixture
entry now supplies the loader-backed compatibility exercised above. Neither
that entry nor Technology `real` mode is actual-runtime admission: the approved
entry explicitly rejects `actual_runtime=True`. Moat's reviewed-anchor and
Finance's semantic gates remain separate unchanged requirements. The existing
outer workflow is fixture-only; this test does not
expand its policy admission or authorize new corpus/model/policy/provider/budget
choices.

Keep existing #59 / PR134 **Draft**, and #59/#96/#168 OPEN pending their actual
acceptance work. This regression is one offline consumer-wiring increment, not
M3, all-five real-evaluator acceptance, or an authenticated research → RAG →
freeze → evaluation → report → PDF trace. No new live run is authorized or
reported.
