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

## Source-backed `technology.integration` adjudication (#201)

`agents/source_fact_verification.py` implements a bounded, rule-supported slice
for **one** criterion, not arbitrary natural-language entailment or production
Technology readiness. Two separate checks are mandatory:

- **Source proof** (`verify_original_source`, `verify_span`): a controller-chosen
  `TrustedSource` path inside an approved root → raw SHA-256 equal to Source and
  manifest → existing local `extract_pdf` → `verify_text_review` → exact approved
  Chunk equality → page, raw `[start,end)` offsets, quote and page text hash.
  Source/manifest ID, schema, local path, title and language must agree; URL and
  publication date agree when supplied by the manifest. Manifest publisher labels
  can differ from Source publisher labels; they are not ownership proof.
  Full Source metadata (including author, publisher, dates and bibliography) must
  equal the original frozen snapshot Source, not merely share a bytes digest.
  Corpus identity, existing `check_as_of` and cited Evidence date checks apply.
  Nested DTO/dataclass field types are checked before interpretation, including
  exact integers rather than booleans and tuples rather than mutable lists.
  Source proof outputs and captured resolver inputs are detached copies.
- **Source correspondence** (`assess_fact`): a controller-owned registry must
  accept the exact fact digest **and** the whole raw source sentence must assert
  that subject/relation/object in one of the forms below. Registry membership,
  author lists, a bare component name, arXiv publication or a literal number are
  not entailment. Unsupported wording, wrong subjects/objects, negation and
  relabeled metric roles fail closed. Unregistered proposals remain `unreviewed`.

`verify_span` and `assess_fact` are lower-level checks that take a
controller-generated `SourceProof` from `verify_original_source`. Freely
constructing a proof and registry does not make these semantic authority APIs.
The authority path is `adjudicate_technology_integration` and its captured-input
resolver, which re-read the approved original bytes before accepting facts.

### Exact supported sentence forms and limits

Here `S` is the declared system, `X`/`Y` are exact component names, and `N` is a
literal unsigned integer or decimal. Matching is case-sensitive, preserves raw
text and does not infer aliases, translations, pronouns or missing context:

| Predicate | Affirmative source sentence |
| --- | --- |
| `component_of_system` | `X is a component of S.` or `X is a core component of S.` |
| `component_self_developed` | `X is a self-developed core component of S.` |
| `component_external_platform` | `X is an external robot platform of S.` |
| `directed_connection` | `X conditions Y.`, `X sends targets to Y.` or `X sends targets to Y at N Hz.` |
| `independent_integration_confirmation` | `Independent testing confirms integration outcome of S: X->Y->Z.` |

A quote must be an entire sentence at a raw source boundary, not a positive-looking
clause cut from a negated/contextual statement. A supplied Measurement is supported
only for the target-command sentence: exact `N`, `Hz`, `command_frequency`, and
`conditions=None`. Other units/roles/conditions are unsupported, even if a review
accepted the fact. Confirmation paths use distinct ASCII letter/digit/underscore/
hyphen component names joined by `->`; arbitrary outcome wording is unsupported.
No general cross-sentence or cross-system contradiction discovery, language
understanding, empirical replication or automated independence authentication is
implemented. The controller must ensure same-system component-name attribution
across sources: identical names alone do not prove component identity. It still
owns source independence and genuine review authority; neither is inferred from
publisher names or a reviewer string.

### Approved anchors and resolver

`adjudicate_technology_integration` binds the whole original snapshot (including
non-Technology facts), rubric content, run/candidate/round/revision/policy and the
verbatim minimum/anchor texts. Minimum evidence requires an admitted directed
connection between admitted components of the declared system. Ratings use the
existing anchors without adding policy or thresholds:

- **3:** a reviewed self-developed **core** component and an external platform
  participate in the same demonstrated connected structure. An isolated owned
  component plus an unrelated external edge is insufficient.
- **4:** at least three reviewed self-developed core components participate in
  the same connected structure. Generic membership or disconnected owned
  components cannot substitute for the anchor's core/integration requirements.
- **5:** 4 plus source-backed independent confirmation about this system's
  demonstrated directed path, covering its self-developed core structure.
  Another system's confirmation, an unestablished path or a company self-source
  cannot raise the rating. Citations include only the supporting structure and
  qualifying confirmation, not unrelated admitted claims.

Owned core counting uses the existing `(own - external) & core` rule for the
declared system. If both exact self-developed and external-platform claims are
admitted for one component, it is excluded from the owned core count; this is not
a generic contradiction detector or a reason to reject both facts.

Ratings 1–2 remain unsupported; undecidable yields `rating=None` / `unsupported`,
not a fabricated negative rating. Source/binding/type failures are redacted
`SourceFactError` technical failures, never Missing. There are no fetch, download,
model, encoder, provider or LLM calls in this slice.

`anchor_from_integration_adjudication(result, reference, *, rubric=...)` formats
an **untrusted assertion**, requiring coherent rating 3–5, minimum, exact anchor,
digests and distinct Evidence/fact sets. It does not authenticate a public DTO.
`integration_adjudication_resolver(snapshot, rubric, artifact, *, sources, registry)`
captures detached original controller inputs and re-runs real source-backed
adjudication on each `validate_technology_anchor` verification. Fabricating or
rebinding a result DTO cannot supply authority. Local source tampering remains a
technical `TechnologyReviewError` at that boundary; all reviewed flags and the
existing `actual_runtime` denial remain intact.

**No real semantic-positive review artifact exists yet.** The actual-data test
(`tests/integration/test_technology_actual_source_slice.py`; env
`SKALA_APPROVED_SOURCE_ROOT`, `SKALA_APPROVED_INDEX`, skips when absent) verifies
approved π0.5 p4/p7 source spans and unreviewed denial only. Its wrong-claim denials
are caused by missing review authority, not independently meaningful semantic
refutation. It does not construct a real full EvaluationSnapshot or run a real
semantic-positive evaluator. Synthetic tests exercise the correspondence rules,
ratings 3/4, a two-source rating 5, wrong-subject/self-source/disconnected negatives,
and the real anchor validator with the captured-input resolver. They are generated
PDFs and synthetic registries, not human reviews or actual company evaluations.
Remaining 22 criteria, actual snapshot construction and Eligibility/controller
integration remain outside this bounded issue; full gates/reviews are parent-owned.
