"""Synthetic Company Research admission/persistence boundaries; no network."""

import asyncio
import hashlib
import json
import socket
import stat
from datetime import UTC, date, datetime
from unittest.mock import Mock

import httpx
import pytest

from skala_rag.agents.eligibility_extraction import LLMEligibilityExtractor
from skala_rag.agents.m2_research import (
    assemble_research_state,
    run_research_to_trace,
    save_research_state,
)
from skala_rag.agents.m2_trace import TraceInvalid
from skala_rag.contracts import Candidate, RunInput, ToolBudget
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode
from skala_rag.contracts.interfaces import LLMError
from skala_rag.fakes import FakeClock
from skala_rag.graph.snapshot import freeze_snapshot
from skala_rag.tools.company_research import (
    FieldObservation,
    LiveResearchCompany,
    StageObservation,
)
from skala_rag.tools.official_homepage import ExtractedFacts, OfficialHomepage
from skala_rag.tools.source_fetch import FetchPolicy, SafeFetcher


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("offline research State test attempted network access")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)


@pytest.fixture
def research():
    clock = FakeClock(datetime(2026, 9, 30, tzinfo=UTC))
    candidate = Candidate(
        schema_version="test",
        candidate_id="co-synthetic",
        canonical_name="Synthetic Robot",
        aliases=[],
        country="US",
        homepage_url="https://robot.example/",
        legal_identifiers={},
        discovery_source_ids=[],
    )
    run = RunInput(
        schema_version="test",
        investment_theme="Robotics",
        countries=["US"],
        languages=["en"],
        as_of=date(2026, 9, 30),
        policy_version="test-policy",
        corpus_version="test-corpus",
        execution_mode="fixture",
    )

    class Extractor:
        version = "synthetic"

        def __call__(self, candidate, source, content, content_type=None):
            return ExtractedFacts(
                observations=tuple(
                    FieldObservation(
                        field=field,
                        value=value,
                        source_id=source.source_id,
                        locator=source.url + "#fact",
                        claim="Synthetic " + field,
                        excerpt="Synthetic observation",
                        identity_basis="official_domain",
                    )
                    for field, value in [
                        ("identity", None),
                        ("business", None),
                        ("domain_match", True),
                        ("is_listed", False),
                        ("exit_completed", False),
                        ("stage", StageObservation("Series A", "series_a", "explicit")),
                    ]
                )
            )

    fetcher = SafeFetcher(
        FetchPolicy(
            allowed_schemes=frozenset({"https"}),
            allowed_hosts=None,
            max_bytes=10000,
            timeout_seconds=30,
            max_redirects=0,
        ),
        clock=clock,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="Synthetic observation")
        ),
        resolve=lambda host: ["93.184.216.34"],
    )
    tool = LiveResearchCompany(
        [
            OfficialHomepage(
                fetcher, schema_version="test", clock=clock, extractor=Extractor()
            )
        ],
        run_id="run-test",
        schema_version="test",
        as_of=run.as_of,
        clock=clock,
    )
    budget = ToolBudget(
        schema_version="test", max_calls=1, max_retries=0, timeout_seconds=30
    )
    return candidate, run, tool, budget, clock


def test_research_links_actual_paths_and_is_freezable(research):
    candidate, run, tool, budget, clock = research
    result = tool(candidate, budget)
    before = result.model_dump(mode="json")
    built = assemble_research_state(
        candidate=candidate, run_input=run, run_id="run-test", result=result
    )
    assert result.model_dump(mode="json") == before
    assert built.receipt["eligibility_status"] == "eligible"
    snapshot = freeze_snapshot(
        candidate.candidate_id,
        built.state,
        run,
        run_id="run-test",
        index_version="test-index",
        schema_version="test",
        allowed_source_ids=list(built.state["sources"]),
        industry_evidence_ids=[],
        clock=clock.now,
    )
    assert len(snapshot.evidence) == 6


@pytest.mark.parametrize("status", ["unknown", "ineligible", "eligible"])
def test_saved_state_precedes_gated_technology(research, tmp_path, status):
    candidate, run, tool, budget, _ = research
    result = tool(candidate, budget)
    if status == "unknown":
        result.data.profile.is_listed = None
    elif status == "ineligible":
        result.data.profile.is_listed = True
    target = tmp_path / "outputs" / "research"

    def technology(state):
        assert (
            json.loads((target / "research-state.json").read_text())[
                "eligibility_results"
            ][candidate.candidate_id]["status"]
            == "eligible"
        )
        assert (
            state["eligibility_results"][candidate.candidate_id]["status"] == "eligible"
        )
        return "synthetic-trace"

    callback = Mock(side_effect=technology)
    out = run_research_to_trace(
        candidate=candidate,
        run_input=run,
        run_id="run-test",
        research_tool=Mock(return_value=result),
        budget=budget,
        root=tmp_path,
        output_dir=target,
        secret_values=["fake-secret"],
        technology=callback,
    )
    assert callback.call_count == (1 if status == "eligible" else 0)
    if status != "eligible":
        assert out.receipt["technology_status"] == "blocked_admission"
    assert stat.S_IMODE((target / "research-state.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(target.stat().st_mode) == 0o700


@pytest.mark.parametrize("mutation", ["candidate", "future", "history", "source"])
def test_bad_research_cannot_publish_state(research, mutation):
    candidate, run, tool, budget, _ = research
    result = tool(candidate, budget)
    if mutation == "candidate":
        result.data.profile.candidate_id = "another"
    elif mutation == "future":
        next(iter(result.data.sources.values())).retrieved_at = datetime(
            2026, 10, 1, tzinfo=UTC
        )
    elif mutation == "history":
        result.retrieval_records[0].run_id = "another"
    else:
        result.retrieval_records[0].source_ids = []
    with pytest.raises(TraceInvalid):
        assemble_research_state(
            candidate=candidate, run_input=run, run_id="run-test", result=result
        )


def test_secret_and_overwrite_are_rejected_before_publication(research, tmp_path):
    candidate, run, tool, budget, _ = research
    built = assemble_research_state(
        candidate=candidate,
        run_input=run,
        run_id="run-test",
        result=tool(candidate, budget),
    )
    built.receipt["sentinel"] = "configured-secret"
    target = tmp_path / "outputs" / "research"
    with pytest.raises(TraceInvalid):
        save_research_state(
            built, root=tmp_path, output_dir=target, secret_values=["configured-secret"]
        )
    assert not target.exists()
    built.receipt.pop("sentinel")
    save_research_state(built, root=tmp_path, output_dir=target, secret_values=[])
    with pytest.raises(TraceInvalid):
        save_research_state(built, root=tmp_path, output_dir=target, secret_values=[])


def test_provider_failure_is_saved_without_admission_or_technology(research, tmp_path):
    candidate, run, _, budget, clock = research
    fetcher = SafeFetcher(
        FetchPolicy(
            allowed_schemes=frozenset({"https"}),
            allowed_hosts=None,
            max_bytes=10000,
            timeout_seconds=30,
            max_redirects=0,
        ),
        clock=clock,
        resolve=lambda host: ["93.184.216.34"],
        transport=httpx.MockTransport(lambda request: httpx.Response(429)),
    )
    tool = LiveResearchCompany(
        [OfficialHomepage(fetcher, schema_version="test", clock=clock, extractor=None)],
        run_id="run-test",
        schema_version="test",
        as_of=run.as_of,
        clock=clock,
    )
    callback = Mock()
    target = tmp_path / "outputs" / "failed"
    out = run_research_to_trace(
        candidate=candidate,
        run_input=run,
        run_id="run-test",
        research_tool=tool,
        budget=budget,
        root=tmp_path,
        output_dir=target,
        secret_values=[],
        technology=callback,
    )
    callback.assert_not_called()
    assert out.receipt["research_status"] == "unavailable"
    assert out.receipt["eligibility_status"] is None
    assert out.receipt["error_codes"] == ["TOOL_RATE_LIMITED"]
    assert out.state["eligibility_results"] == {}
    assert (
        json.loads((target / "research-state.json").read_text())["workflow_status"]
        == "failed"
    )


@pytest.mark.parametrize("mutation", ["output", "country"])
def test_invalid_input_is_rejected_before_research_request(
    research, tmp_path, mutation
):
    candidate, run, tool, budget, _ = research
    target = (
        tmp_path / "outside"
        if mutation == "output"
        else tmp_path / "outputs" / "research"
    )
    if mutation == "country":
        run.countries = ["KR"]
    with pytest.raises(TraceInvalid):
        run_research_to_trace(
            candidate=candidate,
            run_input=run,
            run_id="run-test",
            research_tool=tool,
            budget=budget,
            root=tmp_path,
            output_dir=target,
            secret_values=[],
            technology=None,
        )
    assert tool.calls == 0


def test_failed_technology_keeps_saved_admission_state(research, tmp_path):
    candidate, run, tool, budget, _ = research
    target = tmp_path / "outputs" / "admitted"

    def fail(state):
        state["eligibility_results"].clear()
        raise TraceInvalid("technology unavailable")

    with pytest.raises(TraceInvalid):
        run_research_to_trace(
            candidate=candidate,
            run_input=run,
            run_id="run-test",
            research_tool=tool,
            budget=budget,
            root=tmp_path,
            output_dir=target,
            secret_values=[],
            technology=fail,
        )
    assert (
        json.loads((target / "research-state.json").read_text())["eligibility_results"][
            candidate.candidate_id
        ]["status"]
        == "eligible"
    )
    assert (
        json.loads((target / "research-receipt.json").read_text())["technology_status"]
        == "ready"
    )


@pytest.mark.parametrize(
    ("code", "boundary_code"),
    [
        (ErrorCode.LLM_TIMEOUT, ErrorCode.TOOL_TIMEOUT),
        (ErrorCode.LLM_OUTPUT_INVALID, ErrorCode.TOOL_RESPONSE_INVALID),
        (ErrorCode.TOOL_AUTH_FAILED, ErrorCode.TOOL_AUTH_FAILED),
        (ErrorCode.LLM_FAILED, ErrorCode.TOOL_FAILED),
    ],
)
def test_required_extractor_failure_persists_technical_failure(
    research, tmp_path, code, boundary_code
):
    candidate, run, tool, budget, _ = research
    llm = Mock()
    llm.generate.side_effect = LLMError(code, "fake-secret-provider-detail")
    tool._providers[0]._extractor = LLMEligibilityExtractor(
        llm, as_of=run.as_of, domain_definition="Robotics", max_input_chars=1000
    )
    captured = []

    def actual_research(candidate, budget):
        result = tool(candidate, budget)
        captured.append((result, result.model_dump(mode="json")))
        return result

    callback = Mock()
    target = tmp_path / "outputs" / "extractor-failed"
    out = run_research_to_trace(
        candidate=candidate,
        run_input=run,
        run_id="run-test",
        research_tool=actual_research,
        budget=budget,
        root=tmp_path,
        output_dir=target,
        secret_values=["fake-secret-provider-detail"],
        technology=callback,
    )
    assert tool.calls == 1
    ((result, before),) = captured
    assert result.status == ERROR_SPECS[boundary_code].tool_status
    assert result.data is None
    assert result.errors[0].error_code == boundary_code.value
    assert result.errors[0].retryable == ERROR_SPECS[boundary_code].retryable
    llm.generate.assert_called_once()
    fetch, summary = result.retrieval_records
    assert fetch.status == "ok" and len(fetch.source_ids) == 1
    assert fetch.error_id is None and fetch.evidence_ids == []
    assert summary.arguments_without_secrets["requests_used"] == 1
    assert (
        summary.arguments_without_secrets["providers"]["official-homepage"]["status"]
        == result.status
    )
    archived = summary.arguments_without_secrets["retained_sources"]
    assert set(archived) == set(fetch.source_ids)
    sid = fetch.source_ids[0]
    assert archived[sid]["content_hash"] == (
        "sha256:" + hashlib.sha256(b"Synthetic observation").hexdigest()
    )
    callback.assert_not_called()
    assert result.model_dump(mode="json") == before
    saved = json.loads((target / "research-state.json").read_text())
    receipt = json.loads((target / "research-receipt.json").read_text())
    assert saved == out.state and receipt == out.receipt
    assert saved["run_outcome"] == "technical_failure"
    assert saved["workflow_status"] == "failed"
    assert saved["candidate_status"][candidate.candidate_id] == "failed"
    for name in (
        "eligibility_results",
        "company_profiles",
        "evidence",
        "evaluations",
        "evaluation_results",
    ):
        assert saved[name] == {}
    assert saved["evidence_revisions"][candidate.candidate_id] == 0
    assert saved["research_retry_count"][candidate.candidate_id] == 0
    assert saved["sources"] == archived
    assert saved["retrieval_history"][0] == fetch.model_dump(mode="json")
    assert saved["errors"][0]["error_code"] == code.value
    assert saved["errors"][0]["retryable"] == ERROR_SPECS[code].retryable
    assert receipt["error_codes"] == [code.value]
    assert receipt["eligibility_status"] is None
    assert receipt["technology_status"] == "blocked_admission"
    assert receipt["source_ids"] == [sid]
    assert "fake-secret-provider-detail" not in result.model_dump_json()
    assert "fake-secret-provider-detail" not in json.dumps(saved)


@pytest.mark.parametrize(
    "mutation",
    [
        "map",
        "shape",
        "extra",
        "hash",
        "snapshot",
        "future",
        "no_fetch",
        "fetch_status",
        "fetch_time",
        "run",
        "candidate",
        "summary_tool",
        "summary_as_of",
        "archive_record",
        "error_shape",
        "error_code",
        "error_retry",
        "error_run",
        "error_candidate",
        "error_id",
        "providers_shape",
        "provider_shape",
        "provider_optional",
    ],
)
def test_malformed_retention_cannot_publish_state(research, tmp_path, mutation):
    candidate, run, tool, budget, _ = research
    extractor = Mock(version="synthetic-failure")
    extractor.side_effect = LLMError(ErrorCode.LLM_TIMEOUT, "fake-secret")
    tool._providers[0]._extractor = extractor
    result = tool(candidate, budget)
    fetch, summary = result.retrieval_records
    args = summary.arguments_without_secrets
    sid = fetch.source_ids[0]
    payload = args["retained_sources"][sid]
    if mutation == "map":
        args["retained_sources"] = {"wrong": payload}
    elif mutation == "shape":
        args["retained_sources"] = []
    elif mutation == "extra":
        payload["injected"] = True
    elif mutation == "hash":
        payload["content_hash"] = "not-a-hash"
    elif mutation == "snapshot":
        payload["content_hash"] = "sha256:" + "0" * 64
    elif mutation == "future":
        payload["retrieved_at"] = "2026-10-01T00:00:00Z"
    elif mutation == "no_fetch":
        fetch.source_ids = []
    elif mutation == "fetch_status":
        fetch.status = "empty"
    elif mutation == "fetch_time":
        payload["retrieved_at"] = "2026-09-29T00:00:00Z"
    elif mutation == "run":
        fetch.run_id = "another-run"
    elif mutation == "candidate":
        fetch.candidate_id = "another-candidate"
    elif mutation == "summary_tool":
        summary.tool_name = "untrusted-tool"
    elif mutation == "summary_as_of":
        args["as_of"] = "2025-01-01"
    elif mutation == "archive_record":
        fetch.arguments_without_secrets["retained_sources"] = args["retained_sources"]
    elif mutation == "error_shape":
        args["extractor_error"] = []
    elif mutation == "error_code":
        args["extractor_error"]["error_code"] = "UNRECOGNIZED"
    elif mutation == "error_retry":
        args["extractor_error"]["retryable"] = False
    elif mutation == "error_run":
        args["extractor_error"]["run_id"] = "another-run"
    elif mutation == "error_candidate":
        args["extractor_error"]["candidate_id"] = "another-candidate"
    elif mutation == "error_id":
        args["extractor_error"]["error_id"] = "another-error"
    elif mutation == "providers_shape":
        args["providers"] = []
    elif mutation == "provider_shape":
        args["providers"]["official-homepage"] = []
    else:
        args["providers"]["official-homepage"]["required"] = False
    target = tmp_path / "outputs" / "invalid-retention"
    callback = Mock()
    with pytest.raises(TraceInvalid):
        run_research_to_trace(
            candidate=candidate,
            run_input=run,
            run_id="run-test",
            research_tool=Mock(return_value=result),
            budget=budget,
            root=tmp_path,
            output_dir=target,
            secret_values=[],
            technology=callback,
        )
    assert not target.exists()
    callback.assert_not_called()


@pytest.mark.parametrize(
    "omission",
    ["extractor_error", "retained_sources", "both", "retained_sources_and_ids"],
)
def test_required_extractor_metadata_omission_cannot_publish_state(
    research, tmp_path, omission
):
    candidate, run, tool, budget, _ = research
    extractor = Mock(version="synthetic-failure")
    extractor.side_effect = LLMError(ErrorCode.LLM_TIMEOUT, "fake-secret")
    tool._providers[0]._extractor = extractor
    result = tool(candidate, budget)
    summary = result.retrieval_records[-1]
    args = summary.arguments_without_secrets
    if omission in ("extractor_error", "both"):
        del args["extractor_error"]
    if omission in ("retained_sources", "both", "retained_sources_and_ids"):
        del args["retained_sources"]
    if omission == "retained_sources_and_ids":
        summary.source_ids = []
    assert args["providers"]["official-homepage"]["notes"] == [
        "EXTRACTOR_FAILED:LLM_TIMEOUT"
    ]
    before = result.model_dump(mode="json")
    target = tmp_path / "outputs" / "missing-extractor-metadata"
    callback = Mock()
    with pytest.raises(TraceInvalid):
        run_research_to_trace(
            candidate=candidate,
            run_input=run,
            run_id="run-test",
            research_tool=Mock(return_value=result),
            budget=budget,
            root=tmp_path,
            output_dir=target,
            secret_values=[],
            technology=callback,
        )
    assert result.model_dump(mode="json") == before
    assert not target.exists()
    callback.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("notes", "EXTRACTOR_FAILED:LLM_TIMEOUT", id="notes-string"),
        pytest.param("notes", {}, id="notes-object"),
        pytest.param("notes", None, id="notes-null"),
        pytest.param("notes", 1, id="notes-int"),
        pytest.param("notes", [None], id="notes-list-null"),
        pytest.param("notes", ["unmarked", 1], id="notes-mixed"),
        pytest.param("notes", ["unmarked", {}], id="notes-unmarked-nontext"),
        pytest.param("providers", [], id="providers-list"),
        pytest.param("providers", "unmarked", id="providers-string"),
        pytest.param("providers", None, id="providers-null"),
        pytest.param("providers", 1, id="providers-int"),
        pytest.param("provider", [], id="provider-list"),
        pytest.param("provider", "unmarked", id="provider-string"),
        pytest.param("provider", None, id="provider-null"),
        pytest.param("provider", 1, id="provider-int"),
    ],
)
def test_malformed_provider_metadata_without_carriers_cannot_publish_state(
    research, tmp_path, field, value
):
    candidate, run, tool, budget, _ = research
    extractor = Mock(version="synthetic-failure")
    extractor.side_effect = LLMError(ErrorCode.LLM_TIMEOUT, "fake-secret")
    tool._providers[0]._extractor = extractor
    result = tool(candidate, budget)
    args = result.retrieval_records[-1].arguments_without_secrets
    del args["extractor_error"]
    del args["retained_sources"]
    if field == "providers":
        args["providers"] = value
    elif field == "provider":
        args["providers"]["official-homepage"] = value
    else:
        args["providers"]["official-homepage"]["notes"] = value
    before = result.model_dump(mode="json")
    callback = Mock()
    target = tmp_path / "outputs" / "invalid-provider-metadata"
    with pytest.raises(TraceInvalid):
        run_research_to_trace(
            candidate=candidate,
            run_input=run,
            run_id="run-test",
            research_tool=Mock(return_value=result),
            budget=budget,
            root=tmp_path,
            output_dir=target,
            secret_values=[],
            technology=callback,
        )
    assert result.model_dump(mode="json") == before
    assert result.data is None
    assert not (tmp_path / "outputs").exists()
    callback.assert_not_called()


@pytest.mark.parametrize("mode", ["fetch", "budget"])
def test_unmarked_failure_without_retention_metadata_still_assembles(research, mode):
    candidate, run, tool, budget, clock = research
    if mode == "budget":
        budget.max_calls = 0
    else:
        tool._providers[0]._fetcher = SafeFetcher(
            FetchPolicy(
                allowed_schemes=frozenset({"https"}),
                allowed_hosts=None,
                max_bytes=10000,
                timeout_seconds=30,
                max_redirects=0,
            ),
            clock=clock,
            transport=httpx.MockTransport(lambda request: httpx.Response(429)),
            resolve=lambda host: ["93.184.216.34"],
        )
    result = tool(candidate, budget)
    args = result.retrieval_records[-1].arguments_without_secrets
    assert "extractor_error" not in args
    assert args.pop("retained_sources") == {}
    if mode == "budget":
        assert args["providers"] == {}
    else:
        assert args["providers"]["official-homepage"]["notes"] == []
    before = result.model_dump(mode="json")
    out = assemble_research_state(
        candidate=candidate, run_input=run, run_id="run-test", result=result
    )
    assert out.receipt["error_codes"] == [
        "BUDGET_EXHAUSTED" if mode == "budget" else "TOOL_RATE_LIMITED"
    ]
    assert out.state["run_outcome"] == "technical_failure"
    assert out.state["sources"] == {} and out.state["eligibility_results"] == {}
    assert result.model_dump(mode="json") == before


@pytest.mark.parametrize("mode", ["source_only", "empty_facts", "no_homepage"])
def test_normal_absence_still_persists_unknown(research, tmp_path, mode):
    candidate, run, tool, budget, _ = research
    extractor = Mock(version="synthetic-empty", return_value=ExtractedFacts(()))
    tool._providers[0]._extractor = None if mode == "source_only" else extractor
    if mode == "no_homepage":
        candidate.homepage_url = None
    callback = Mock()
    target = tmp_path / "outputs" / mode
    out = run_research_to_trace(
        candidate=candidate,
        run_input=run,
        run_id="run-test",
        research_tool=tool,
        budget=budget,
        root=tmp_path,
        output_dir=target,
        secret_values=[],
        technology=callback,
    )
    saved = json.loads((target / "research-state.json").read_text())
    assert saved == out.state
    assert saved["candidate_status"][candidate.candidate_id] == "eligibility_unknown"
    assert saved.get("run_outcome") != "technical_failure"
    assert saved["errors"] == [] and saved["evidence"] == {}
    assert out.receipt["eligibility_status"] == "unknown"
    assert len(saved["sources"]) == (0 if mode == "no_homepage" else 1)
    assert extractor.call_count == (1 if mode == "empty_facts" else 0)
    callback.assert_not_called()


def test_required_extractor_cancellation_is_not_swallowed(research, tmp_path):
    candidate, run, tool, budget, _ = research
    extractor = Mock(version="synthetic-cancellation")
    extractor.side_effect = asyncio.CancelledError()
    tool._providers[0]._extractor = extractor
    callback = Mock()
    target = tmp_path / "outputs" / "cancelled"
    with pytest.raises(asyncio.CancelledError):
        run_research_to_trace(
            candidate=candidate,
            run_input=run,
            run_id="run-test",
            research_tool=tool,
            budget=budget,
            root=tmp_path,
            output_dir=target,
            secret_values=[],
            technology=callback,
        )
    assert not target.exists()
    callback.assert_not_called()


def test_failure_retains_previous_successful_provider_sources(research, tmp_path):
    candidate, run, tool, budget, clock = research
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=f"Synthetic snapshot {len(requests)}")

    fetcher = SafeFetcher(
        FetchPolicy(
            allowed_schemes=frozenset({"https"}),
            allowed_hosts=None,
            max_bytes=10000,
            timeout_seconds=30,
            max_redirects=0,
        ),
        clock=clock,
        transport=httpx.MockTransport(handler),
        resolve=lambda host: ["93.184.216.34"],
    )
    prior = OfficialHomepage(
        fetcher, schema_version="test", clock=clock, extractor=None
    )
    prior.name = "prior-source-only"
    prior.required = False
    tool._providers.insert(0, prior)
    tool._providers[1]._fetcher = fetcher
    extractor = Mock(version="synthetic-failure")
    extractor.side_effect = LLMError(ErrorCode.LLM_OUTPUT_INVALID, "fake-secret")
    tool._providers[1]._extractor = extractor
    budget.max_calls = 2
    result = tool(candidate, budget)
    target = tmp_path / "outputs" / "previous-sources"
    callback = Mock()
    out = run_research_to_trace(
        candidate=candidate,
        run_input=run,
        run_id="run-test",
        research_tool=Mock(return_value=result),
        budget=budget,
        root=tmp_path,
        output_dir=target,
        secret_values=["fake-secret"],
        technology=callback,
    )
    assert result.data is None and len(requests) == 2
    assert len(out.state["sources"]) == 2
    assert out.state["evidence"] == {} and out.state["eligibility_results"] == {}
    assert out.state["run_outcome"] == "technical_failure"
    assert out.state["errors"][0]["error_code"] == "LLM_OUTPUT_INVALID"
    assert out.state["errors"][0]["retryable"] is True
    for fetch in result.retrieval_records[:-1]:
        assert fetch.status == "ok"
        assert set(fetch.source_ids) <= set(out.state["sources"])
    assert result.retrieval_records[-1].arguments_without_secrets["requests_used"] == 2
    assert json.loads((target / "research-state.json").read_text()) == out.state
    callback.assert_not_called()
