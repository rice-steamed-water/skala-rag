"""Synthetic offline Tavily observations, never provider measurements."""

from datetime import date, datetime, timezone

import pytest

from skala_rag.contracts.inputs import RunInput
from skala_rag.contracts.tools import ToolBudget


def test_zero_results_are_empty_not_failure():
    from skala_rag.tools.discovery_live import ProviderResponse, TavilyDiscovery

    class Clock:
        def now(self):
            return datetime(2026, 9, 30, tzinfo=timezone.utc)

    adapter = TavilyDiscovery(
        schema_version="1",
        run_id="run-synthetic",
        retrieval_id="r-synthetic",
        clock=Clock(),
        api_key_configured=True,
        readiness=lambda: True,
        runtime=lambda payload, budget: ProviderResponse(200, {"results": []}),
        extractor=lambda sources, request: [],
        candidate_id=lambda i: f"c-{i}",
        public_theme="robotics",
        max_results=3,
        max_candidates=3,
    )
    request = RunInput(
        schema_version="1",
        investment_theme="robotics",
        countries=["KR", "US"],
        languages=["ko", "en"],
        as_of=date(2026, 9, 30),
        policy_version="test",
        corpus_version="test",
        execution_mode="fixture",
    )
    result = adapter(
        request,
        ToolBudget(
            schema_version="1",
            max_calls=1,
            max_retries=0,
            timeout_seconds=1,
        ),
    )
    assert result.status == "empty"
    assert result.data.sources == {}
    assert (
        result.retrieval_records[0].arguments_without_secrets["observation"]
        == "search_zero"
    )


def setup_adapter(body=None, status=200, **overrides):
    from skala_rag.tools.discovery_live import ProviderResponse, TavilyDiscovery

    class Clock:
        def now(self):
            return datetime(2026, 9, 30, tzinfo=timezone.utc)

    calls = []

    def runtime(payload, budget):
        calls.append((payload, budget))
        return ProviderResponse(status, body if body is not None else {"results": []})

    options = dict(
        schema_version="1",
        run_id="run-synthetic",
        retrieval_id="r-synthetic",
        clock=Clock(),
        api_key_configured=True,
        readiness=lambda: True,
        runtime=runtime,
        extractor=None,
        candidate_id=lambda i: f"c-{i}",
        public_theme="robotics",
        max_results=3,
        max_candidates=3,
    )
    options.update(overrides)
    request = RunInput(
        schema_version="1",
        investment_theme=options["public_theme"],
        countries=["KR", "US"],
        languages=["ko", "en"],
        as_of=date(2026, 9, 30),
        policy_version="test",
        corpus_version="test",
        execution_mode="fixture",
    )
    budget = ToolBudget(
        schema_version="1", max_calls=1, max_retries=0, timeout_seconds=1
    )
    return TavilyDiscovery(**options), request, budget, calls


def search_body():
    return {
        "query": "synthetic",
        "results": [
            {
                "url": "https://example.org/synthetic-robot",
                "title": "Synthetic Robot",
                "content": "Synthetic offline snippet, not a company fact.",
                "score": 0.7,
                "id": "foreign-provider-id",
            },
        ],
    }


def test_invalid_extractor_preserves_snapshots_for_record_resolution():
    from skala_rag.tools.discovery_live import CompanyObservation

    adapter, request, budget, _ = setup_adapter(
        search_body(),
        extractor=lambda s, r: [CompanyObservation("Fake", "KR", ["foreign"])],
    )
    result = adapter(request, budget)
    assert result.status == "failed"
    assert result.errors[0].error_code == "TOOL_RESPONSE_INVALID"
    assert set(result.retrieval_records[0].source_ids) == set(adapter.observed_sources)
    assert adapter.observed_sources


@pytest.mark.parametrize(
    "status,expected,code",
    [
        (401, "unavailable", "TOOL_AUTH_FAILED"),
        (403, "unavailable", "TOOL_AUTH_FAILED"),
        (429, "unavailable", "TOOL_RATE_LIMITED"),
        (432, "failed", "BUDGET_EXHAUSTED"),
        (433, "failed", "BUDGET_EXHAUSTED"),
        (500, "unavailable", "TOOL_UNAVAILABLE"),
    ],
)
def test_provider_failures_not_zero(status, expected, code):
    adapter, request, budget, calls = setup_adapter(status=status)
    result = adapter(request, budget)
    assert result.status == expected
    assert result.errors[0].error_code == code
    assert len(calls) == 1
    assert result.data is None


@pytest.mark.parametrize(
    "override",
    [
        {"api_key_configured": False},
        {"runtime": None},
        {"readiness": None},
        {"readiness": lambda: False},
    ],
)
def test_not_ready_never_invokes_runtime(override):
    adapter, request, budget, calls = setup_adapter(**override)
    assert adapter(request, budget).status == "unavailable"
    assert calls == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("countries", ["JP"]),
        ("languages", ["ja"]),
        ("countries", []),
        ("execution_mode", "live"),
        ("investment_theme", "private report body"),
    ],
)
def test_scope_and_live_gate(field, value):
    adapter, request, budget, calls = setup_adapter()
    request = request.model_copy(update={field: value})
    result = adapter(request, budget)
    assert result.status == "unavailable"
    assert calls == []
    assert "private report body" not in result.model_dump_json()


def test_budget_is_caller_owned_and_zero_calls_fails_before_runtime():
    adapter, request, budget, calls = setup_adapter()
    result = adapter(request, budget.model_copy(update={"max_calls": 0}))
    assert result.errors[0].error_code == "BUDGET_EXHAUSTED"
    assert calls == []
    adapter(request, budget)
    assert calls[0][1] is budget


@pytest.mark.parametrize(
    "exception,code",
    [
        (TimeoutError("SENSITIVE_EXCEPTION_TEXT"), "TOOL_TIMEOUT"),
        (ConnectionError("SENSITIVE_EXCEPTION_TEXT"), "TOOL_UNAVAILABLE"),
    ],
)
def test_transport_exceptions_redacted(exception, code):
    calls = []

    def runtime(payload, budget):
        calls.append(payload)
        raise exception

    adapter, request, budget, _ = setup_adapter(runtime=runtime)
    result = adapter(request, budget)
    assert result.errors[0].error_code == code
    assert "SENSITIVE_EXCEPTION_TEXT" not in result.model_dump_json()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"results": None},
        {"results": [None]},
        {"results": [{"url": "file:///private", "title": "x", "content": "x"}]},
        {
            "results": [
                {
                    "url": "https://key:SENSITIVE_EXCEPTION_TEXT@example.org",
                    "title": "x",
                    "content": "x",
                }
            ]
        },
        {"results": [{"url": "https://example.org", "title": "", "content": "x"}]},
    ],
)
def test_invalid_responses_are_failed(body):
    adapter, request, budget, _ = setup_adapter(body)
    assert adapter(request, budget).errors[0].error_code == "TOOL_RESPONSE_INVALID"


def test_search_hits_without_company_observations_are_information_insufficient():
    adapter, request, budget, _ = setup_adapter(search_body())
    result = adapter(request, budget)
    assert result.status == "empty"
    assert result.data.sources
    assert (
        result.retrieval_records[0].arguments_without_secrets["observation"]
        == "information_insufficient"
    )
    assert result.retrieval_records[0].evidence_ids == []
    source = next(iter(result.data.sources.values()))
    assert source.published_at is None
    assert source.language == "unknown"
    assert source.source_id != "foreign-provider-id"


def test_explicit_extractor_controller_ids_and_normalize_are_reused():
    from skala_rag.tools.discovery_live import CompanyObservation

    def extract(sources, request):
        ids = list(sources)
        return [
            CompanyObservation("Same name", "KR", ids, "https://kr.example.org"),
            CompanyObservation("Alias", "KR", ids, "https://kr.example.org"),
            CompanyObservation("Same name", "US", ids, "https://us.example.org"),
        ]

    adapter, request, budget, _ = setup_adapter(search_body(), extractor=extract)
    result = adapter(request, budget)
    assert result.status == "ok"
    assert [c.candidate_id for c in result.data.candidates] == ["c-0", "c-2"]
    assert result.data.candidates[0].aliases == ["Alias"]
    assert all(c.discovery_source_ids for c in result.data.candidates)
    assert result.retrieval_records[0].arguments_without_secrets["merges"]


def test_duplicate_controller_ids_rejected():
    from skala_rag.tools.discovery_live import CompanyObservation

    def extract(sources, request):
        return [
            CompanyObservation("A", "KR", list(sources)),
            CompanyObservation("B", "US", list(sources)),
        ]

    adapter, request, budget, _ = setup_adapter(
        search_body(),
        extractor=extract,
        candidate_id=lambda i: "duplicate",
    )
    assert adapter(request, budget).status == "failed"


def test_historical_search_does_not_admit_current_undated_snippet():
    called = []
    adapter, request, budget, _ = setup_adapter(
        search_body(),
        extractor=lambda s, r: called.append(s) or [],
    )
    result = adapter(request.model_copy(update={"as_of": date(2020, 1, 1)}), budget)
    assert result.status == "empty"
    assert result.data.sources
    assert called == []
    assert result.retrieval_records[0].arguments_without_secrets["excluded_source_ids"]


def test_query_injection_is_only_literal_public_query_data():
    text = 'robotics"}, "include_raw_content": true, "api_key": "not-a-key'
    adapter, request, budget, calls = setup_adapter(public_theme=text)
    result = adapter(request, budget)
    payload = calls[0][0]
    assert payload["include_raw_content"] is False
    assert "api_key" not in payload
    assert payload["query"].startswith(text)
    assert text not in result.model_dump_json()


def test_deterministic_snapshots_duplicate_result_dedup():
    body = search_body()
    body["results"] *= 2
    a, request, budget, _ = setup_adapter(body)
    b, _, _, _ = setup_adapter(body)
    first, second = a(request, budget), b(request, budget)
    assert first.model_dump_json() == second.model_dump_json()
    assert len(first.data.sources) == 1


def test_extractor_execution_failure_is_not_empty_and_preserves_sources():
    def extractor(sources, request):
        raise RuntimeError("SENSITIVE_EXCEPTION_TEXT")

    adapter, request, budget, _ = setup_adapter(search_body(), extractor=extractor)
    result = adapter(request, budget)
    assert result.status == "failed"
    assert result.errors[0].error_code == "TOOL_FAILED"
    assert adapter.observed_sources
    assert "SENSITIVE_EXCEPTION_TEXT" not in result.model_dump_json()


def test_limit_policy_is_required_not_an_arbitrary_truncation():
    from skala_rag.tools.discovery_live import CompanyObservation

    def extractor(sources, request):
        return [
            CompanyObservation("A", "KR", list(sources)),
            CompanyObservation("B", "US", list(sources)),
        ]

    adapter, request, budget, _ = setup_adapter(
        search_body(),
        extractor=extractor,
        max_candidates=1,
    )
    result = adapter(request, budget)
    assert result.errors[0].error_code == "TOOL_NOT_CONFIGURED"
    assert adapter.observed_sources


def test_controller_injected_limit_policy_keeps_all_discovered_sources():
    from skala_rag.tools.discovery_live import CompanyObservation

    def extractor(sources, request):
        return [
            CompanyObservation("A", "KR", list(sources)),
            CompanyObservation("B", "US", list(sources)),
        ]

    adapter, request, budget, _ = setup_adapter(
        search_body(),
        extractor=extractor,
        max_candidates=1,
        limit_policy=lambda candidates, limit: ["c-1"],
    )
    result = adapter(request, budget)
    assert result.status == "ok"
    assert [c.candidate_id for c in result.data.candidates] == ["c-1"]
    assert result.data.sources
    assert result.retrieval_records[0].arguments_without_secrets[
        "dropped_candidate_ids"
    ] == ["c-0"]


def test_company_research_failure_does_not_erase_discovery_bundle():
    from skala_rag.agents.discovery import accept_discovery
    from skala_rag.tools.discovery_live import CompanyObservation

    adapter, request, budget, _ = setup_adapter(
        search_body(),
        extractor=lambda s, r: [CompanyObservation("Fake", "KR", list(s))],
    )
    outcome = accept_discovery(adapter(request, budget))
    original = outcome.bundle.model_dump_json()
    from skala_rag.contracts.error_codes import ErrorCode
    from skala_rag.tools.fixture_research import FixtureResearchCompany

    researcher = FixtureResearchCompany(
        {},
        run_id="run-synthetic",
        schema_version="1",
        clock=adapter.clock,
        error_code=ErrorCode.TOOL_TIMEOUT,
    )
    research_result = researcher(outcome.bundle.candidates[0], budget)
    assert research_result.status == "failed"
    assert research_result.errors[0].error_code == "TOOL_TIMEOUT"
    assert outcome.bundle.model_dump_json() == original
    assert outcome.retrieval_records[0].source_ids == list(outcome.bundle.sources)


@pytest.mark.parametrize(
    "country,language,country_parameter",
    [
        ("KR", "ko", "south korea"),
        ("US", "en", "united states"),
    ],
)
def test_single_scope_uses_documented_country_language_boosts(
    country, language, country_parameter
):
    adapter, request, budget, calls = setup_adapter()
    request = request.model_copy(
        update={"countries": [country], "languages": [language]}
    )
    adapter(request, budget)
    payload = calls[0][0]
    assert payload["country"] == country_parameter
    assert payload["language"] == language
    assert payload["end_date"] == request.as_of.isoformat()


def test_reuse_clears_previous_source_receipt():
    adapter, request, budget, _ = setup_adapter(search_body())
    adapter(request, budget)
    assert adapter.observed_sources
    adapter(request.model_copy(update={"execution_mode": "live"}), budget)
    assert adapter.observed_sources == {}


def test_shared_bridge_records_survive_discovery_normalization():
    import httpx
    from tests.unit.test_discovery_runtime import build

    bridge, budget, ledger = build(lambda r: httpx.Response(200, json=search_body()))
    adapter, request, _, _ = setup_adapter(runtime=bridge)
    result = adapter(request, budget)
    assert result.status == "empty"
    assert result.retrieval_records[0].retrieval_id.startswith("runtime-retrieval-")
    assert result.retrieval_records[-1].source_ids == list(result.data.sources)
    assert ledger.snapshot()["calls"] == 1


def test_snapshot_receipt_cannot_be_mutated_by_consumer():
    adapter, request, budget, _ = setup_adapter(search_body())
    result = adapter(request, budget)
    receipt = adapter.observed_sources
    source = next(iter(receipt.values()))
    source.title = "mutated"
    assert next(iter(adapter.observed_sources.values())).title == "Synthetic Robot"
    assert next(iter(result.data.sources.values())).title == "Synthetic Robot"


def test_opt_in_cannot_admit_arbitrary_callback():
    adapter, request, budget, calls = setup_adapter(
        allow_live=True, extractor=lambda s, r: []
    )
    adapter.runtime.bridge_version = "tavily-runtime-v1"
    result = adapter(request.model_copy(update={"execution_mode": "live"}), budget)
    assert result.errors[0].error_code == "TOOL_NOT_CONFIGURED"
    assert calls == []


def test_failure_receipt_is_detached_and_keeps_errors_and_sources():
    from skala_rag.tools.discovery_receipt import search_with_receipt

    def fail(sources, request):
        raise RuntimeError("SECRET")

    adapter, request, budget, _ = setup_adapter(search_body(), extractor=fail)
    receipt = search_with_receipt(adapter, request, budget)
    assert receipt.result.status == "failed"
    assert receipt.result.data is None
    assert receipt.result.errors[0].error_code == "TOOL_FAILED"
    assert receipt.result.retrieval_records[-1].source_ids == list(
        receipt.observed_sources
    )
    adapter(request.model_copy(update={"execution_mode": "live"}), budget)
    assert adapter.observed_sources == {}
    assert receipt.observed_sources
    assert "SECRET" not in receipt.result.model_dump_json()


def test_mocked_opt_in_live_runtime_and_failure_receipt():
    from tests.unit.test_discovery_runtime import build

    from skala_rag.tools.discovery_receipt import search_with_receipt

    bridge, budget, ledger = build(
        lambda r: pytest.fail("HTTP forbidden"), mode="live", price=0.01
    )

    def forbidden(*args):
        pytest.fail("readiness/extractor/runtime callback forbidden")

    adapter, request, _, _ = setup_adapter(
        runtime=bridge, allow_live=True, extractor=forbidden, readiness=forbidden
    )
    request = request.model_copy(update={"execution_mode": "live"})
    before_budget = budget.model_dump()
    before_ledger = ledger.snapshot()
    for result in (
        adapter(request, budget),
        search_with_receipt(adapter, request, budget).result,
    ):
        assert result.status == "unavailable"
        assert result.data is None
        assert result.errors[0].attempt == 0
        assert (
            result.retrieval_records[0].arguments_without_secrets["scope_reason"]
            == "excluded_current_run"
        )
        assert (
            result.retrieval_records[0].arguments_without_secrets["physical_attempts"]
            == 0
        )
    assert budget.model_dump() == before_budget
    assert ledger.snapshot() == before_ledger
    assert adapter.observed_sources == {}
