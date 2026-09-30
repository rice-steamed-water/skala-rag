"""Synthetic Company Research admission/persistence boundaries; no network."""

import json
import socket
import stat
from datetime import UTC, date, datetime
from unittest.mock import Mock

import httpx
import pytest

from skala_rag.agents.m2_research import (
    assemble_research_state,
    run_research_to_trace,
    save_research_state,
)
from skala_rag.agents.m2_trace import TraceInvalid
from skala_rag.contracts import Candidate, RunInput, ToolBudget
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
    monkeypatch.setattr(socket, "create_connection", deny)


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
