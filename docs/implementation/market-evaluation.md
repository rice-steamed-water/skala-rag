# Market approved connection

`src/skala_rag/agents/market.py` keeps `evaluate_market` as the draft-only
compatibility path. Approved policy objects still fail that guard before any
model request. The separate entrypoint is:

```python
def evaluate_market_approved(
    snapshot: EvaluationSnapshot,
    *,
    target_market: MarketTarget | None,
    market_links: Mapping[str, MarketLink],
    rubric: Mapping[str, JsonValue],
    llm: StructuredLLM,
    actual_admission: ActualAdmissionV3 | None = None,
    reviewed_observations: Mapping[str, MarketObservationReview] | None = None,
) -> EvaluationResult: ...
```

The controller supplies an exact `ActualAdmissionV3`, pinned Core rubric and
`RuntimeStructuredLLM`/`OpenAIResponsesAttempt` bound to the admission's runtime,
clock, ledger, run, candidate and `market_evaluation` call context. No admission
means denial, including a JSON object containing an approval flag. Native
transport is required for `execution_scope="actual"`; an exact
`httpx.MockTransport` is required for `"controlled_response"`.

## Independent review inputs

The domain request and review input have these public signatures:

```python
MarketObservationReviewRequest(
    *,
    target: MarketTarget,
    links: dict[str, MarketLink],
    criterion: Literal["market.size", "market.growth", "market.demand"],
    excerpts: dict[str, str],
)

MarketObservationReview(
    request: bytes,
    subject: str,
    receipt: CriterionAssessment,
)
```

`MarketObservationReviewRequest` is defined in the Market module. The shared
resolver currently consumes the structured `target`/`links`/`criterion`/
`excerpts` JSON grammar, rather than exporting a named Market request class.
The controller retains the exact bytes reviewed through its independent
review channel and supplies the existing **baseline** `CriterionAssessment`.
The evaluation wrapper never creates positive review records. Every observed
criterion, including demand, requires a supplied receipt and accepted review.

Before resolving that review, the wrapper checks the request's entire target,
all cited links and exact excerpt map against the original controller inputs.
The receipt's schema version, criterion, rating, citations, rationale, missing
reason and applicability note must match the model output. The resolver API is
used without modifying the shared contract:

```python
actual_admission.resolve_review(snapshot, rubric, request, receipt, subject=subject)
```

That resolver independently closes the original Source bytes, source metadata,
quote spans, snapshot/cutoff/rubric, request digest and receipt digest.
Rejected, unresolved or absent reviews do not authorize observed output.
Post-wire source/subject integrity failures return a failed `EvaluationResult`
with `MARKET_SOURCE_REVIEW_INVALID`, never Missing.

## Missing, filtering and numerical checks

Unknown target (`None`) or no links (`{}`) admits only all-Missing output with
valid rubric missing reasons. No segment, country, link or numerical context
is invented. The prompt excludes unattributed evidence while validation and
the returned envelope preserve the original snapshot and caller call context.
Excluded citations are rejected even if present in the original snapshot.

Both paths share the existing segment/geography attribution and numerical
filter: source currency/unit/value date, reference year, actual/forecast basis,
CAGR period, comparable contexts, rubric bands, conflicts and TAM-only cap.
Industry evidence additionally needs the explicit market permission in the
exact pinned rubric's common rules. Permission never admits another company's
evidence or industry evidence bearing another candidate ID.

The current pinned Core content still has `status: proposed`. The shared
`SourceBoundReviewResolver` requires `status == "approved"` in addition to the
explicit `[market]` common rule, so industry receipts currently fail closed.
The mock-wire tests reproduce this mismatch even with a synthetic accepted
industry review. No rubric/config/shared-resolver change is made in this lane.
The positive numerical wire controls therefore use company-attributed evidence.

The new path uses the existing `evaluate_dimension` output validation and
`extra_validator`, with `max_repairs=0`. It adds no retry loop. The admitted
runtime retains responsibility for transport accounting and its retry policy.
Malformed output returns a failure `EvaluationResult`; preflight integrity
errors can raise, as in the existing source-bound contracts.

## Offline evidence

`tests/unit/test_market.py` preserves legacy denials and numerical checks.
`tests/integration/test_market_actual_connection.py` drives the real
`RuntimeStructuredLLM` and `OpenAIResponsesAttempt` through controlled
`httpx.MockTransport`. It blocks external socket connections and checks that
each response consumes one shared-ledger call, its input/output usage and the
test-only cost allowance.

**All semantic decisions, target attribution, numerical observations, readiness,
token bounds and cost budgets in these tests are SYNTHETIC controls.** Local
source byte/quote checks execute, but are not market truth or actual-provider
evidence. No existing real candidate becomes eligible through these tests.
No paid request, download, credential lookup or product API is required.

Run from the authorized connections worktree without dependency installation:

```bash
UV_OFFLINE=1 uv run --no-sync pytest -q \
  tests/unit/test_market.py \
  tests/integration/test_market_actual_connection.py \
  tests/unit/test_actual_admission_v3.py \
  tests/unit/test_source_bound_review.py
```
