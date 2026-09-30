"""Offline Tavily response boundary with merged #45 shared-runtime bridge.

No credentials, HTTP client, retry or runtime defaults are provided here.
The injected runtime MUST be offline. execution_mode=live always fails closed.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from skala_rag.agents.discovery import (
    CandidateLimitPolicy,
    CandidateLimitUnresolved,
    normalize_candidates,
)
from skala_rag.contracts.bundles import DiscoveryBundle
from skala_rag.contracts.candidates import Candidate
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode
from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.inputs import RunInput
from skala_rag.contracts.interfaces import Clock
from skala_rag.contracts.retrieval import RetrievalRecord
from skala_rag.contracts.sources import Source
from skala_rag.contracts.tools import ToolBudget, ToolResult
from skala_rag.tools.source_fetch import RawSnapshot, check_as_of, to_source

TOOL_NAME = "tavily-search-candidates-offline"


@dataclass(frozen=True)
class ProviderResponse:
    status_code: int
    body: object


class OfflineRuntime(Protocol):
    """Caller-owned, offline single invocation; budget is passed through unchanged."""

    def __call__(
        self, payload: dict[str, Any], budget: ToolBudget
    ) -> ProviderResponse | ToolResult[ProviderResponse]: ...


@dataclass(frozen=True)
class CompanyObservation:
    """Explicit extractor observations, no generated candidate IDs or stage claims."""

    canonical_name: str
    country: str
    source_ids: Sequence[str]
    homepage_url: str | None = None
    aliases: Sequence[str] = ()
    legal_identifiers: Mapping[str, str] | None = None


Extractor = Callable[[Mapping[str, Source], RunInput], Sequence[CompanyObservation]]


class TavilyDiscovery:
    """SearchCandidates-compatible offline pilot for KR/US and ko/en.

    public_theme is explicitly authorized public text, not a private document.
    IDs and selection policy belong to the caller/controller. Instances represent
    one logical invocation; caller supplies a fresh retrieval_id per invocation.
    """

    def __init__(
        self,
        *,
        schema_version: str,
        run_id: str,
        retrieval_id: str,
        clock: Clock,
        api_key_configured: bool,
        readiness: Callable[[], bool] | None,
        runtime: OfflineRuntime | None,
        extractor: Extractor | None,
        candidate_id: Callable[[int], str],
        public_theme: str,
        max_results: int,
        max_candidates: int,
        limit_policy: CandidateLimitPolicy | None = None,
    ) -> None:
        if type(max_results) is not int or not 1 <= max_results <= 20:
            raise ValueError("max_results must be 1..20")
        if type(max_candidates) is not int or max_candidates < 1:
            raise ValueError("max_candidates must be positive")
        if not public_theme.strip():
            raise ValueError("public_theme must be explicit public text")
        self.schema_version, self.run_id, self.retrieval_id = (
            schema_version,
            run_id,
            retrieval_id,
        )
        self.clock, self.api_key_configured = clock, api_key_configured
        self.readiness, self.runtime, self.extractor = readiness, runtime, extractor
        self.candidate_id, self.public_theme = candidate_id, public_theme
        self.max_results, self.max_candidates = max_results, max_candidates
        self.limit_policy = limit_policy
        self._observed_sources: dict[str, Source] = {}

    @property
    def observed_sources(self) -> dict[str, Source]:
        """Snapshot receipt even when extractor validation fails; defensive copy.

        ToolResult forbids data on failure. Caller must persist this receipt
        beside failed retrieval records, not infer Evidence from source_ids.
        """
        return {
            sid: s.model_copy(deep=True) for sid, s in self._observed_sources.items()
        }

    def __call__(
        self, request: RunInput, budget: ToolBudget
    ) -> ToolResult[DiscoveryBundle]:
        self._observed_sources = {}
        self._runtime_records = []
        started = self.clock.now()
        args = {
            "execution_mode": request.execution_mode,
            "countries": list(request.countries),
            "languages": list(request.languages),
            "as_of": request.as_of.isoformat(),
            "live_integration": "gated_pending_readiness_and_smoke",
        }
        code = self._preflight(request, budget, started)
        if code is not None:
            return self._result(request, started, args, code=code)
        # Theme is data in a JSON query string, never interpolated into parameters,
        # prompts, URL, headers or executable code. Public authorization is exact.
        payload = {
            "query": self.public_theme
            + " startups "
            + " ".join(request.countries)
            + " languages "
            + " ".join(request.languages),
            "topic": "general",
            "search_depth": "basic",
            "max_results": self.max_results,
            "end_date": request.as_of.isoformat(),
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "auto_parameters": False,
        }
        if len(set(request.countries)) == 1:
            payload["country"] = {"KR": "south korea", "US": "united states"}[
                request.countries[0]
            ]
        if len(set(request.languages)) == 1:
            payload["language"] = request.languages[0]
        # These documented provider parameters are ranking boosts, not company
        # domicile or verified source-language facts. Multi-scope stays one call.
        assert self.runtime is not None  # preflight rejects missing runtime
        try:
            response = self.runtime(payload, budget)
        except (TimeoutError, httpx.TimeoutException):
            return self._result(request, started, args, code=ErrorCode.TOOL_TIMEOUT)
        except (ConnectionError, httpx.TransportError):
            return self._result(request, started, args, code=ErrorCode.TOOL_UNAVAILABLE)
        except Exception:
            return self._result(request, started, args, code=ErrorCode.TOOL_FAILED)
        if isinstance(response, ToolResult):
            self._runtime_records = response.retrieval_records
            if response.status not in {"ok", "empty"}:
                return ToolResult[DiscoveryBundle](
                    schema_version=self.schema_version,
                    status=response.status,
                    data=None,
                    retrieval_records=response.retrieval_records,
                    errors=response.errors,
                )
            response = response.data
        if (
            not isinstance(response, ProviderResponse)
            or type(response.status_code) is not int
        ):
            return self._result(
                request, started, args, code=ErrorCode.TOOL_RESPONSE_INVALID
            )
        if response.status_code != 200:
            code = _http_error(response.status_code)
            return self._result(request, started, args, code=code)
        try:
            sources, admitted = self._sources(response.body, request)
        except (ValueError, TypeError, KeyError):
            return self._result(
                request, started, args, code=ErrorCode.TOOL_RESPONSE_INVALID
            )
        self._observed_sources = sources
        args["excluded_source_ids"] = [sid for sid in sources if sid not in admitted]
        observations = "search_zero" if not sources else "information_insufficient"
        try:
            candidates = []
            if admitted and self.extractor is not None:
                extracted = self.extractor(
                    {
                        sid: source.model_copy(deep=True)
                        for sid, source in admitted.items()
                    },
                    request.model_copy(deep=True),
                )
                ids = set()
                for index, item in enumerate(extracted):
                    sid = list(item.source_ids)
                    if (
                        not sid
                        or len(set(sid)) != len(sid)
                        or not set(sid) <= set(admitted)
                        or item.country not in request.countries
                    ):
                        raise ValueError("invalid extractor attribution or country")
                    cid = self.candidate_id(index)
                    if cid in ids:
                        raise ValueError("duplicate controller ID")
                    ids.add(cid)
                    candidates.append(
                        Candidate(
                            schema_version=self.schema_version,
                            candidate_id=cid,
                            canonical_name=item.canonical_name,
                            aliases=list(item.aliases),
                            country=item.country,
                            homepage_url=item.homepage_url,
                            legal_identifiers=dict(item.legal_identifiers or {}),
                            discovery_source_ids=sid,
                        )
                    )
            normalized = normalize_candidates(
                candidates,
                max_candidates=self.max_candidates,
                limit_policy=self.limit_policy,
            )
            args["merges"] = [
                {
                    "kept": m.kept_candidate_id,
                    "merged": m.merged_candidate_id,
                    "matched_on": m.matched_on,
                }
                for m in normalized.merges
            ]
            args["dropped_candidate_ids"] = normalized.dropped_candidate_ids
            data = DiscoveryBundle(
                schema_version=self.schema_version,
                candidates=normalized.candidates,
                sources=sources,
            )
        except CandidateLimitUnresolved:
            return self._result(
                request,
                started,
                args,
                code=ErrorCode.TOOL_NOT_CONFIGURED,
                source_ids=list(sources),
            )
        except RuntimeError:
            return self._result(
                request,
                started,
                args,
                code=ErrorCode.TOOL_FAILED,
                source_ids=list(sources),
            )
        except (ValueError, TypeError, AttributeError):
            # Invalid extractor output is technical failure, not a factual absence.
            return self._result(
                request,
                started,
                args,
                code=ErrorCode.TOOL_RESPONSE_INVALID,
                source_ids=list(sources),
            )
        args["observation"] = "candidates_found" if data.candidates else observations
        return self._result(request, started, args, data=data)

    def _preflight(self, request, budget, started):
        if request.execution_mode != "fixture":
            return ErrorCode.TOOL_NOT_CONFIGURED
        if getattr(self.runtime, "bridge_version", None) is not None:
            if self.runtime.runtime.policy.execution_mode != "fixture":
                return ErrorCode.TOOL_NOT_CONFIGURED
        if (
            not self.api_key_configured
            or self.runtime is None
            or self.readiness is None
            or self.readiness() is not True
        ):
            return ErrorCode.TOOL_NOT_CONFIGURED
        if (
            not request.countries
            or not set(request.countries) <= {"KR", "US"}
            or not request.languages
            or not set(request.languages) <= {"ko", "en"}
        ):
            return ErrorCode.TOOL_NOT_CONFIGURED
        if request.investment_theme != self.public_theme:
            return ErrorCode.TOOL_NOT_CONFIGURED
        if budget.max_calls < 1 or (
            budget.deadline is not None and started >= budget.deadline
        ):
            return ErrorCode.BUDGET_EXHAUSTED
        return None

    def _sources(self, body, request):
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise ValueError("missing results")
        if len(body["results"]) > self.max_results:
            raise ValueError("result limit violated")
        sources = {}
        for item in body["results"]:
            if not isinstance(item, dict):
                raise ValueError("invalid result")
            url, title, text = item["url"], item["title"], item["content"]
            if not all(isinstance(v, str) and v.strip() for v in (url, title, text)):
                raise ValueError("invalid result fields")
            parts = urlsplit(url)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.username
                or parts.password
            ):
                raise ValueError("invalid result URL")
            # Snapshot is exactly the returned UTF-8 snippet, NOT fetched web bytes.
            raw = RawSnapshot(
                url,
                url,
                "web",
                (),
                text.encode("utf-8"),
                "text/plain",
                self.clock.now(),
            )
            source = to_source(
                raw,
                schema_version=self.schema_version,
                title=title,
                source_kind="web",
                language="unknown",
                access_notes=(
                    "Synthetic/offline Tavily search snippet; "
                    "not fetched article or Evidence"
                ),
                bibliographic_metadata={
                    "provider": "tavily",
                    "representation": "search_snippet",
                    "execution_mode": "fixture",
                    "snippet": text,
                },
            )
            # Provider IDs, score, answer and undemonstrated publication dates are
            # not controller IDs or trusted facts. Missing publication stays None.
            sources.setdefault(source.source_id, source)
        admitted = {
            sid: s
            for sid, s in sources.items()
            if check_as_of(s, request.as_of).admitted
        }
        return sources, admitted

    def _result(self, request, started, args, *, code=None, data=None, source_ids=()):
        errors = []
        status = "ok" if data is not None and data.candidates else "empty"
        error_id = None
        if code is not None:
            spec = ERROR_SPECS[code]
            status = spec.tool_status
            error_id = f"error-{self.retrieval_id}"
            errors = [
                WorkflowError(
                    schema_version=self.schema_version,
                    error_id=error_id,
                    run_id=self.run_id,
                    node="discovery",
                    error_code=code.value,
                    message_redacted=f"offline Tavily boundary: {code.value}",
                    retryable=spec.retryable,
                    attempt=1,
                    timestamp=started,
                )
            ]
        record = RetrievalRecord(
            schema_version=self.schema_version,
            retrieval_id=self.retrieval_id,
            run_id=self.run_id,
            tool_name=TOOL_NAME,
            query=None,
            arguments_without_secrets=args,
            started_at=started,
            finished_at=self.clock.now(),
            status=status,
            source_ids=list(data.sources) if data is not None else list(source_ids),
            chunk_ids=[],
            evidence_ids=[],
            error_id=error_id,
            cost=None,
            cache_hit=False,
        )
        return ToolResult[DiscoveryBundle](
            schema_version=self.schema_version,
            status=status,
            data=data,
            retrieval_records=[*self._runtime_records, record],
            errors=errors,
        )


def _http_error(status: int) -> ErrorCode:
    if status in (401, 403):
        return ErrorCode.TOOL_AUTH_FAILED
    if status == 429:
        return ErrorCode.TOOL_RATE_LIMITED
    if status in (432, 433):
        return ErrorCode.BUDGET_EXHAUSTED
    if status >= 500:
        return ErrorCode.TOOL_UNAVAILABLE
    return ErrorCode.TOOL_FAILED
