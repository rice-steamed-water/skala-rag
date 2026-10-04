# Approved Founder fixture consumer (#197)

## Scope and compatibility

`skala_rag.agents.founder.evaluate_founder_approved_fixture` is an additive,
loader-backed **offline fixture** consumer. It returns the existing terminal
`EvaluationResult`, not a new wrapper/container, trace format, investment label,
or execution capability. It calls the real common `evaluate_dimension` with
`max_repairs=0`; there are no new model retries or repairs.

`evaluate_founder_fixture` retains its existing proposed-rubric/draft-policy
contract, including its guard, typing and repair behavior. This change does not
copy PR134's unmerged guard or configs. Main's `configs/rubrics/core.yaml` remains
`proposed`. An integration with PR134 must preserve both the owner's legacy
guard change and this additive entry, not replace one with the other.

The new entry requires `actual_runtime is False` and `type(llm) is FakeLLM`.
`True`, `0`, a provider object or a FakeLLM subclass is rejected **before** any
policy path access, approval resolver, review callback or model call. No real-mode
compatibility bypass exists. #168/#96, D05/D06/D08, provider/corpus readiness,
budget and campaign gates are not advanced by a successful fixture.

## Policy and Core boundary

Supply the policy path and explicit `PolicyApprovals` plus a trusted external
`ApprovalVerifier`; the consumer independently invokes `load_approved_policy`
with `execution_mode="fixture"`. It does not inspect a nonexistent legacy
`ApprovedScoringPolicy.status`, accept a constructed policy as proof, change
policy versions in returned results, or modify scoring/configuration files.

The supplied Core must have `status="approved"`. `validate_core_artifact` from
Moat binds the supplied version and whole canonical content to an explicit
`CoreArtifactApproval` and a separate artifact resolver. Its reference must equal
the Core reference in the policy approvals. The rubric and policy must cover
exactly `founder.expertise`, `founder.industry`, and `founder.execution`.

For trusted local content commitments, the existing APIs are:

```python
registry = pinned_approval_registry(ROOT)
approvals = registry.policy_approvals()
policy = load_approved_policy(
    ROOT / "configs/scoring.v3.json",
    approvals=approvals,
    approval_verifier=registry.verify_policy,
    execution_mode="fixture",
)
artifact_approval = registry.core_approval()
artifact_verifier = registry.verify_core
```

This registry resolves **historical content commitments**, not production
semantic authority or runtime admission. Core's existing canonical digest
intentionally ignores only the top-level status label. Tests therefore use an
explicit synthetic `approved` copy of the unchanged proposed main artifact,
with real pinned content/version/reference verification. This is not evidence
that main Core's status has been approved or that approval has propagated from
PR134. All person facts and observation reviews in these tests are synthetic.
The independently loaded policy shown above prepares the test's operational
snapshot generation **before** evaluation; the entry itself reloads the policy.

## Original frozen snapshot and person scope

Preflight detaches and revalidates the **whole original** snapshot, including
Source–Chunk–RetrievalRecord–Evidence attribution, schemas, corpus, run,
candidate and provenance closure. The existing Technology helpers
`checked_snapshot`, `checked_fixture_output` and `_exact_tree` are reused for
these generic DTO checks, not Technology criterion selection. Original evidence
support/conflict/supersession references must also close within the snapshot.
Nested `model_copy` extras, mixed schemas, stale retrieval identity and ghost
records are rejected rather than silently normalized. Snapshot policy and schema
must match the loaded policy and supplied schema.

`founder_person_ids` must be a nonempty `Collection` of explicit, exact, nonblank
`str` IDs with no duplicates. Lists, tuples and sets can be used; a bare string,
bytes or an iterator is not a person collection. `verified_person_by_evidence_id`
must be a mapping of exact nonblank string evidence/person IDs; every key must
exist in the original snapshot. Mapping an evidence item to another person is
allowed as input, but excludes that item from Founder evaluation.

The attribution mapping is **trusted caller-owned research output**. This
consumer does not derive person truth from names, company-name matching,
keywords, an LLM rationale, or retrieved text. Caller research must establish
same-person and same-company attribution, role, employment periods, domain work
and prior commercialization identity as appropriate to the approved anchor.

Only mapped founder evidence of the current candidate, company scope, and a
Founder criterion enters the prompt and output validation. Industry,
non-Founder, other-person and unmapped evidence do not enter the prompt. They
remain in the original snapshot/digest; filtering is not a new frozen generation.
A model citing excluded evidence produces the common terminal failure, not a
fabricated zero or Missing result. With genuinely absent attribution, a valid
model output may report `missing`/`attribution_unverified` without a rating.

## Typed review and independent resolver

`verify_observation(criterion, cited_evidence)` receives detached copies after a
successful common evaluation, once per observed criterion. It must return the
exact `ReviewedFounderAnchor` type. The receipt contains:

- nonblank external `review_reference`;
- canonical whole Core `artifact_sha256` and **whole original** `snapshot_sha256`;
- exact Founder `criterion_id`, integer rating in 1..5 (not bool), and a nonempty,
  duplicate-free tuple containing exactly the criterion's cited evidence IDs;
- a duplicate-free tuple binding the complete supplied founder-person set;
- `person_by_evidence_id`: tuple of unique `(evidence_id, person_id)` string pairs
  covering **exactly** the cited IDs, matching the supplied mapping, with every
  cited person in the founder set and every evidence item in the candidate scope;
- exact-True `anchor_facts_reviewed`, `minimum_evidence_reviewed`,
  `person_identity_reviewed`, `employment_identity_reviewed`, and
  `independent_corroboration_reviewed` flags.

Flags mean that the external reviewer checked the **applicable approved rule**.
They do not introduce a universal independent-source requirement, person-count
rule, evidence-count rule or rating cap. The external reviewer must check anchor
facts, minimum evidence, direct negative facts when required, person/employment
identity and independent corroboration wherever the approved anchor requires it.
The consumer does not replace that review with counting or string matching.

A separate `review_verifier(receipt)` must resolve the exact claim against trusted
external review authority and return **literal True**. A bare True receipt, a
reference string, a truthy resolver result, substituted people/sources/IDs,
stale digest or unreviewed flags are rejected. Resolver arguments are detached;
changing bound membership/flags during resolution is rejected. Detached review
arguments cannot rewrite the returned assessment or original snapshot.

**A malicious accept-all fixture resolver still cannot be semantic proof.** A
structurally valid receipt with invented facts can be accepted by a dishonest
resolver. The test explicitly demonstrates this boundary; there is no person or
semantic verification implementation here and no claim of actual Founder/M3
success. Trusted controller code and honest external semantic review remain
requirements. Structural binding prevents replay/substitution; it cannot make a
caller's attribution mapping true.

## Errors, Missing and adapter handoff

- Ordinary local preflight errors become redacted `ValueError("Founder approved
  fixture preflight rejected")` before the model.
- Forged structured outputs retain the common terminal `LLM_OUTPUT_INVALID`
  contract, one model call and no repair; no payload secrets enter messages.
- Common terminal failures (including timeout) are returned unchanged. Missing
  assessments and rejected Core N/A outputs preserve the common contract.
  Missing and failure paths invoke no observation review.
- Technical post-evaluation review errors raise redacted
  `FounderReviewError("FOUNDER_REVIEW_REJECTED")`; they do not become Missing,
  zero scores, investment labels or fabricated DTOs.
- Upstream LLM behavior stays outside local redaction: common `LLMError` handling
  is preserved and other upstream exceptions retain their identity. Process
  controls/cancellation propagate through preflight, model and review boundaries.

The existing #191 `bind_baseline_evaluator_v3` accepts a callback returning this
terminal DTO directly. Supply its complete 23-criterion catalog and explicit
industry-evidence dimensions. Tests invoke the real Founder callback exactly
once with the original operational generation and compare successful evaluation
fields and terminal failure/errors losslessly after the existing V3 conversion
(which adds its absent applicability fields). There is no returned-generation
stamping, hidden result unwrapping, new export, or new binder.

## Verification and remaining gates

All validation is offline and synthetic. Scoped verification covers the new
consumer, legacy Founder, #191 adapter, Technology, Moat, pinned registry and
approved-policy consumers. The two existing Technology local-PDF tests are
explicitly deselected: this child neither loads nor modifies another worktree's
local assets. Dependency setup uses `uv sync --frozen --offline` only.

AST-only Graphify navigation is captured in scratch, with no semantic extraction,
LLM/network call or repository graph write. It is navigation evidence only,
not a full semantic graph refresh. Exact command receipts/counts are recorded in
`/Users/luk/.hermes/cache/scratch/skala-issue197-worker.md` for the parent.
Full-suite gating, independent review, PR134 integration and commit/PR/merge
remain with the parent; this child does not stage, commit, push or write GitHub.
