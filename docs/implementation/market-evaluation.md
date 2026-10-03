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
- Market still takes the baseline `ScoringPolicy` and returns baseline
  `EvaluationResult`, not v3 `EvaluationBranchResult`. The existing live smoke is
  opt-in and its historical manual snapshot is not a whole-v3 evaluation trace.
- Moat's checked-in approved Core artifact still requires explicit artifact
  approval and snapshot-bound reviewed anchors. Its legacy proposed-only
  patent/comparison tests use a separately labelled synthetic historical fixture.
  Boolean reviews are not promoted to approved anchors; actual runtime stays blocked.
- Finance semantic verification and approved policy admission are unchanged.
  No new actual API call, provider selection, policy threshold or runtime permission
  is introduced by this integration.

The baseline → five-branch approved v3 evaluator adapter, authoritative review
registry and real runtime admission remain separate work. Passing offline tests
is not evidence of those paths being complete.
