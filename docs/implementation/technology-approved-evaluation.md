# Technology approved fixture consumer (#195)

## Scope and authority

`evaluate_technology_approved_fixture` in `agents/technology.py` is an additive,
**offline FakeLLM-only** entry connected to the existing Technology prompt,
evidence selection, trace builder and `evaluate_dimension` evaluator. It loads
`ApprovedScoringPolicy` inside the call; a caller-constructed policy cannot
substitute for that load. It does not use legacy `execution_mode="real"` to avoid
legacy `.status` validation.

The legacy `evaluate_technology` API, including its historical real-mode behavior,
is unchanged. Its status check alone is **not authoritative actual admission**.
This new entry rejects any `actual_runtime` value other than exact `False`, and
any LLM other than an exact `FakeLLM`, before reading a policy path or invoking
approval/review callbacks. No provider, dependency, config, graph or shared
wrapper was changed. D05/D06/D08 and current campaign admission remain HOLD.

Approved Core values already exist; approval propagation is separate. The new
entry requires supplied Core `status="approved"`, version `core-0.1.0`, exact
content digest and authoritative artifact callback, with the same Core reference
as the policy approvals. Canonical Core content intentionally excludes only the
top-level status label. This does **not** allow proposed Core at this entry.
Main's proposed Core remains unchanged; PR134 owns propagation. The positive
tests use an explicitly synthetic approved-status copy, not production approval.

## Callable boundary

The keyword-only inputs after the original `EvaluationSnapshot` are:

- `policy_path`, `approvals: PolicyApprovals`, `approval_verifier: ApprovalVerifier`;
- `rubric`, `artifact_approval: CoreArtifactApproval`, `artifact_verifier`;
- `llm`, `clock`, `schema_version` (must equal frozen snapshot schema);
- `verify_observation(criterion, cited_evidence) -> ReviewedTechnologyAnchor`;
- `review_verifier(receipt) -> bool`, and optional `actual_runtime=False`.

Reuse `CoreArtifactApproval`, `validate_core_artifact`, `core_artifact_digest`,
`frozen_snapshot_digest`, and `load_approved_policy`. The controller owns external
registry resolution; neither a reference string nor construction is approval.

Preflight detaches the entire snapshot and supplied rubric. It revalidates exact
DTO types, map IDs, declared fields, nested schema, policy identity, all record
source/chunk/evidence references, run/candidate attribution, corpus metadata,
RAG chunk membership and excerpt containment. These checks represent frozen
attribution **only**: they do not authenticate source bytes or actual retrieval.
The four rubric/policy Technology IDs must agree before the model request.

## Reviewed-anchor claims and remaining obligations

`ReviewedTechnologyAnchor` binds:

- an external `review_reference`;
- exact Core content and **whole original frozen snapshot** digests, including
  generation and non-Technology evidence (not the filtered prompt snapshot);
- Technology-only criterion, exact integer rating 1–5 and exact distinct cited
  Evidence set;
- explicit exact-boolean assertions `anchor_facts_reviewed`,
  `minimum_evidence_reviewed`, `direct_negative_facts_reviewed`, and
  `independent_corroboration_reviewed`.

These flags assert that the reviewer checked the **applicable approved rules**;
they do not say every rating needs negative facts or independent sources. They
are not new Evidence-count, rating-cap or N/A policies. The reviewer must check
approved anchors/minimum lists, direct negative facts where the chosen anchor
requires them, the self-claim cap, and independent corroboration for rating 5.
TRL events, environment/duration/results, internal component ownership and
integration, and commercial contract/PoC correspondence must be established from
reviewed facts. No keyword, free-text rationale, similarity or publisher-name
heuristic supplies these facts; absence of search hits is not a negative fact.

Each observed assessment must have a correctly typed receipt **and** a separate
external resolver accepting that exact claim/reference. Bare `True`, unregistered
references, stale snapshots, wrong citations, malformed fields and subclasses
are rejected. A callback accepting all claims does not become trusted semantic
authority: callers must implement authentic external review and source-fact
resolution. This slice does not implement that production authority.

Review runs after baseline output validation, against the preserved whole
snapshot, before the consumer returns. Callback assessment/Evidence arguments
are detached. Review failure or ordinary callback exception raises the redacted
`TechnologyReviewError("TECHNOLOGY_REVIEW_REJECTED")`; it is not converted to
Missing, rating zero or an investment rejection. Process controls propagate.
Preflight failures raise a redacted `ValueError`. There are no added model
repairs/retries: this entry fixes `max_repairs=0`. Original baseline terminal
failures return unchanged without invoking semantic review. Core N/A fails the
existing output schema; Missing stays Missing with no fabricated score.

An entry-local structural LLM proxy revalidates exact FakeLLM output instances
before baseline assembly, including nested `model_copy` extras that Pydantic
instance pass-through could otherwise omit. Invalid instances become the existing
redacted `LLM_OUTPUT_INVALID` terminal failure, with zero repairs. This proxy adds
no semantic callback, request, provider access or shared-wrapper hook.

## Adapter and evidence

The result is the existing `TechnologyEvaluation` container, retaining `.result`,
`prompt_version`, `allowed_evidence_ids` and `trace`. An existing #191 binder
caller explicitly returns `container.result` and retains the container separately.
The binder validates the original input generation; no policy/schema/generation
stamping or replacement adapter is added.

`tests/unit/test_technology_approved.py` exercises the real new callable and real
shared evaluator/adapter with synthetic provenance-closed mock data and a
caller-owned synthetic review registry. These are structural positive fixtures,
not actual source truth, retrieval, semantic review or investment evaluations.
It separately exercises existing approved local two-PDF byte/text preflight
negatives using issue184 assets when available (without embedding or retrieval).
A tampered source hash and extracted text fail existing verification; no fake
RetrievalRecord is represented as actual search.

Observed bounded verification: 356 scoped tests passed, including 69 new tests,
legacy Technology, evaluator, adapter/integration, registry and Moat regressions.
Both real-PDF negatives executed; no skips in this run. RED evidence captured
missing entry, full-record closure, mixed-schema and forged-output failures before
their fixes.
AST-only graphify output is scratch-only with no external LLM use. Independent
review and latest-main full integration remain parent-owned; this document does
not claim actual Technology readiness or whole #57/#168/#96 completion.
