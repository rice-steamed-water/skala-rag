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

## Minimal opt-in receipt runner

`discovery_smoke.run_discovery_smoke` is a small callable, not a CLI, Graph
controller, provider selector, extractor or complete M2 trace. It reuses
`search_with_receipt` and the actual `TavilyDiscovery` / `TavilyRuntimeBridge` /
`AdapterRuntime`, rejecting callback lookalikes and inconsistent run/schema/tool/
node/mode context. Live still requires explicit `adapter.allow_live=True`; shared
runtime approval, readiness, deadline, priced allowance and campaign ledger gates
remain unchanged. An admitted live mode is not evidence that an HTTP call occurred.

Caller supplies the complete explicit `RunInput`, `ToolBudget`, configured adapter
(including approved public theme, bounds, extractor, IDs, clock, readiness,
transport, credential, allowance and shared campaign ledger), and an **existing,
approved, dedicated empty output directory**. No environment, `.env`, default
output path, prices, candidates, provider fallback or semantic extractor is read
or created. Use a fresh adapter/retrieval ID per invocation and exclusive
synchronous use of its HTTP client; the runner temporarily attaches a request
observation hook and removes it in `finally`, preserving caller hooks.

```python
from skala_rag.tools.discovery_smoke import run_discovery_smoke

# All four objects below are explicitly prepared/approved by the caller.
receipt_path = run_discovery_smoke(
    adapter=configured_adapter,
    request=approved_request,
    budget=approved_budget,
    output_directory=approved_empty_directory,
)
```

`receipt.json` preserves the exact detached ToolResult (successful, empty or failed),
all physical and normalization records, terminal redacted errors, referenced
runtime attempt errors (including recovered retries), and exact observed Source
snippet snapshots even after extractor failure. It records the supplied request/
budget and versions, plus observed transport-bound method/URL/allowlisted JSON
request parameters, **never headers, credentials or adapter/runtime configuration**.
The public query therefore appears in this caller-approved receipt, unlike the
redacted retrieval logs. Unknown costs remain null in the actual records, not zero
or the allowance ceiling. Provider request IDs and runtime implementation version
are null because the existing bridge/runtime do not expose them; bridge version
and runtime policy schema version are recorded. There is no invented provider
response, HTTP success, company fact, fetched article, Chunk or Evidence.

The output directory rejects symlinks, existing contents and reuse across runs.
An exclusive `.discovery-smoke` reservation prevents competing invocations;
the receipt is fsynced and atomically published with a no-replacement hard link
(the existing writers' exclusive-create convention is preserved). Receipt files
are mode 0600; the reservation is mode 0700. The reservation stays after failures,
so do not retry into the same directory. On filesystem publication failure,
`SmokePersistenceError.receipt` retains the detached observation for caller recovery;
the exception message does not expose filesystem/transport exception text.

A safety gate rejects the configured credential, recognizable credential/token
patterns and credential-bearing URLs anywhere in the output before writing bytes.
Unexpected outgoing JSON fields fail closed. Unsafe observed content is **not
silently altered or discarded**: `UnsafeSmokeReceipt.receipt` retains the detached
observation in memory and no receipt bytes are written. The caller must handle
that object securely, not log its repr. This is a bounded safety check, not a claim
to detect arbitrary secrets inside public text; authorizing truly public inputs
and reviewing permitted output remain caller responsibilities.

Offline regression uses the real shared runtime with `httpx.MockTransport`,
synthetic snippets and explicitly injected fixture-only extractor observations.
Successful candidate, empty search, post-extractor and transport failures, retry
error preservation, unsafe-output rejection, publication failure recovery, client
hook cleanup and output/context guards are covered. No real API call, credential
lookup or model download is part of this regression.

```sh
uv run --offline pytest tests/unit/test_discovery_smoke.py tests/unit/test_live_discovery.py tests/unit/test_discovery_runtime.py -q
uv run --offline ruff check src/skala_rag/tools/discovery_smoke.py tests/unit/test_discovery_smoke.py
uv run --offline ruff format --check src/skala_rag/tools/discovery_smoke.py tests/unit/test_discovery_smoke.py
```

Prepared runner code and fixture receipts do not close #48. Actual price/readiness
observations, verified production extractor, approved public input/output location
and real provider smoke remain caller/parent gates. This receipt is not full M2
execution, discovery Graph persistence, or proof of recall/accuracy/cost.

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
