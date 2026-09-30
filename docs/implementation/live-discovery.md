# #48 Tavily Discovery — offline boundary

## Delivery status

This increment is an **offline interface/response-normalization implementation**,
not a live provider integration or proof of M2 completion. Tavily Web is the sole
approved pilot; supported request scope is KR/US and ko/en. There is no provider
fallback. #45 shared runtime (PR #100) is merged and reused via `discovery_runtime.py`.
Live is now an explicit adapter capability, not observed provider execution.
`allow_live=False` remains the default. Live requires the actual
`TavilyRuntimeBridge` (not a callback advertising a version), matching live policy,
run/schema/tool/discovery context, an explicit extractor and exact approved public
theme. Shared runtime still requires observed readiness, approval references,
deadline, priced per-request allowance and bounded campaign ledger before sending.
No alternate retry or ledger is created.
No live smoke, provider/paid request, credential read, or model download was run.
Do not close #48 on the strength of synthetic fixtures.

Only new issue-owned files are introduced. Contracts, Graph, manifests, existing
Normalize, shared runtime, and other issue owners' files are unchanged.

## Official provider shape

Reference retrieved from Tavily's official Search endpoint documentation:
`https://docs.tavily.com/documentation/api-reference/endpoint/search.md`
(the same page is available without `.md`). Retrieval of public documentation is
not a search API request or live integration evidence.

The documented operation is `POST https://api.tavily.com/search`, JSON `query`
with Bearer authentication. **This adapter does not implement or send that HTTP
operation itself.** The new bridge performs one injected-client POST per attempt,
with the shared runtime owning all retries, timeout and ledger accounting.
No SDK is required. The legacy injected offline callable returns
`ProviderResponse(status_code, body)`; the consumed response projection is
`results: [{title: string, url: string, content: string, ...}]`. Provider fields
such as `answer`, `score`, `id`, `raw_content`, and estimated `published_date`
are not adopted as candidate IDs, Evidence, publication facts, or company facts.
This validates the consumed projection, not every optional field of the complete
provider OpenAPI response.

Request parameters are explicit: `topic=general`, `search_depth=basic`, caller
`max_results` (1..20), caller `as_of` as `end_date`, and false
`include_answer/include_raw_content/include_images/auto_parameters`. The caller's
approved public theme, country codes and language codes appear as literal query
data. With a single country, `country` is `south korea` or `united states`; with a
single language, `language` is `ko` or `en`. Official country/language parameters
are ranking boosts, not verified corporate domicile/language filters. Multi-scope
requests remain one offline invocation, with scope hints in the query; this is not
proof of geographic completeness or bilingual recall. An explicit extractor must
attribute every candidate to admitted returned Sources and requested countries.

## Injection and ownership

`TavilyDiscovery` implements the existing `SearchCandidates.__call__(RunInput,
ToolBudget) -> ToolResult[DiscoveryBundle]` shape. Supply:

- Explicit schema/run/retrieval IDs, aware `Clock`, result and candidate bounds.
  Create a fresh adapter and unique retrieval ID for each logical invocation;
  there is no controller/run lifecycle management in this class.
- `api_key_configured: bool`: an offline readiness observation, **not a key**.
  No environment, `.env`, vault, secret, or default credential path is consulted.
- An explicit `readiness` callable returning exactly `True`, and an
  `OfflineRuntime(payload, budget)` callable. Missing either fails closed. These
  legacy fixtures alone are not runtime integration evidence. The versioned
  `TavilyRuntimeBridge` calls `AdapterRuntime.execute` with explicit `CallContext`,
  `Readiness`, `Allowance`, and `TavilySingleAttempt.retry_owner="runtime"`.
  Caller supplies the HTTP client (single-attempt transport, no SDK retries) and
  explicit key; neither is created or read from environment here. Budget is passed through unchanged;
  runtime owns timeout/retry/cost accounting. No default HTTP client, independent retry,
  backoff, default timeout, or parallel runtime implementation exists here.
- `public_theme`: text the caller explicitly authorized as public. It must match
  `request.investment_theme` exactly. No document/private body is accepted or
  transferred. Query text cannot override JSON parameters or execute code.
  There is no NLP prompt-injection detector or claim of search-operator isolation.
- An optional explicit extractor of
  `CompanyObservation(name, country, source_ids, homepage, aliases, identifiers)`.
  Without an extractor, snippets do **not** become invented companies. The
  extractor has no candidate-ID field; IDs come from `candidate_id(index)`, owned
  by the caller/controller. Its output is revalidated, duplicate IDs and
  missing/duplicate/foreign source attribution are rejected.
- Existing #17 `normalize_candidates` and `CandidateLimitPolicy` are reused.
  Same-name foreign entities remain separate; duplicate entities have merge
  records. Candidate bounds are explicit; an over-limit result without a policy
  is unavailable, not silently truncated. No RNG algorithm/seed/default limit
  is introduced. Inject the separately approved pre-evaluation selection policy.

Fixture execution is explicitly marked in Sources and tool name; external-looking
`example.org` URLs in tests are **synthetic locators**, never fetched.

## Source and retrieval semantics

#46 `RawSnapshot/to_source/check_as_of` helpers are reused. The exact UTF-8 returned
snippet bytes are hashed, and locator+hash produces controller-owned Source IDs.
This snapshot is a **search snippet representation**, not full article acquisition.
Its metadata labels `provider=tavily`, `representation=search_snippet`,
`execution_mode=fixture`; missing publisher/publication/language remain unknown.
Later full-source acquisition and claim verification belong to subsequent stages.
The provider's estimated publication date is not enough to prove a historical
edition. Current undated snippets are excluded from historical candidate
extraction by `check_as_of`, while their discovery snapshots are retained.

Every successful/empty bundle keeps all discovered Sources, including excluded,
merged and candidate-limit-dropped results. Existing `accept_discovery` and State
patch propagation can preserve them when later Company Research fails. The
adapter never creates funding, eligibility, stage, Chunk, or Evidence claims.
RetrievalRecord has no candidate assignment and empty chunk/evidence ID lists.
It records scope/as_of, status, Source IDs, error link, merge/drop/exclusion
observations, injected timestamps; cost remains unknown, not fabricated zero.
Raw query is intentionally omitted from logs (`query=None`); query content is
visible only to the injected offline runtime. No failure body or exception text
is logged.

`ToolResult` forbids `data` on failure. For post-search extractor/policy failure,
`adapter.observed_sources` is a defensive-copy snapshot receipt resolving the
failed record's Source IDs. The caller must preserve it beside the failed record;
`search_with_receipt(adapter, request, budget)` in `discovery_receipt.py` captures
an invocation's detached `result` (including failed records/errors) and
`observed_sources` together, before adapter reuse can erase the snapshots.
This is **not** automatic Graph integration or #55 completion. No shared DTO was changed to smuggle
failure payloads into a successful bundle.

| Observation | Tool status / code | Distinction |
| --- | --- | --- |
| Search `results=[]` | empty, no errors | `observation=search_zero` |
| Hits but insufficient/admissibility-excluded company data | empty, no errors | `observation=information_insufficient`, Sources retained |
| Explicit attributable candidates | ok | Normalize, Source closure and controller IDs validated |
| Key observation/runtime/readiness missing, unsupported scope, live request, absent limit policy | unavailable / TOOL_NOT_CONFIGURED | not successful zero results |
| HTTP 401/403 | unavailable / TOOL_AUTH_FAILED | no retry |
| HTTP 429 / 5xx or transport failure | unavailable / TOOL_RATE_LIMITED or TOOL_UNAVAILABLE | legacy callable has no retry; bridge retries only through shared runtime |
| HTTP 432/433 / caller zero calls or expired deadline | failed / BUDGET_EXHAUSTED | budget failure, not candidate absence |
| Timeout | failed / TOOL_TIMEOUT | no synthetic empty result |
| Invalid response/extractor attribution or duplicate IDs | failed / TOOL_RESPONSE_INVALID | redacted technical failure; valid collected snapshot receipt retained |
| Explicit extractor RuntimeError | failed / TOOL_FAILED | source receipt retained, not factual absence |

## Shared runtime limitations and receipts

`bridge_version="tavily-runtime-v1"` returns the actual runtime ToolResult and
attempt records. Discovery preserves those records, adding one normalization
record (not another physical request or ledger debit). Physical response records
have no invented Source IDs; snippet Source closure appears in the normalization
record. Unknown usage/cost remains unknown. The shared ledger can be reused across
all tools; adapters do not construct a second ledger.

The approved transient retry backoff is 1/2 seconds with at most two additional
attempts; authentication never retries, even with malformed Retry-After.
Merged #122 supplies `parse_retry_after` and frozen `RetryAfter` metadata. The
bridge passes the same injected `runtime.clock` to its single-attempt transport;
the bridge constructor remains compatible, while direct `TavilySingleAttempt`
construction now requires an explicit clock (no default clock is created).
On retryable 429/5xx responses it captures delta-seconds/HTTP-date at response
arrival, before body processing, and passes normalized metadata to `http_failure`.
Malformed retryable headers fail closed as TOOL_RESPONSE_INVALID without raw
headers or exception text. Runtime alone waits for the maximum of policy backoff
and remaining provider minimum, subtracts processing elapsed time, checks deadline
before/after sleep, and accounts once per physical request. The bridge has no
sleep or retry loop. Explicit opt-in Live Discovery inherits runtime gates:
approvals, deadline, bounded ledger and
priced allowance. Missing prices/readiness are not supplied by fixtures.

## Verification and outstanding gates

Tests contain original inline synthetic fixtures (not recorded provider responses
or real-company data). Targeted command:

```sh
uv run --offline pytest tests/unit/test_live_discovery.py tests/unit/test_discovery_runtime.py -q
uv run --offline ruff check src/skala_rag/tools/discovery_live.py src/skala_rag/tools/discovery_runtime.py tests/unit/test_live_discovery.py tests/unit/test_discovery_runtime.py
uv run --offline ruff format --check src/skala_rag/tools/discovery_live.py src/skala_rag/tools/discovery_runtime.py tests/unit/test_live_discovery.py tests/unit/test_discovery_runtime.py
```

Tests exercise zero/auth/timeout/transport/unready/live/unsupported/invalid payloads,
foreign/duplicate IDs, query parameter injection, historical undated snippets,
Normalize/dedup, injected selection, deterministic receipts and preservation after
Company Research failure. MockTransport tests exercise opt-in live admission and post-search failure receipts;
these are not opt-in provider smoke or readiness observations. Actual final command receipts belong to the parent verification report.

Remaining: separately approved live
readiness/budget configuration and transport, explicit public-text policy in the
controller, production extractor selection/verification, controller receipt
persistence, opt-in live smoke and full-project regression/review. The parent owns
full gates and any later PR. This increment makes no live accuracy/recall/cost,
issue-completion, CI, PR, commit, push, or merge claim.
