"""Offline synthetic inputs through real tools and the existing outer graph."""

import hashlib
import json
import socket
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from skala_rag import run_settings
from skala_rag.contracts import (
    Candidate,
    DiscoveryBundle,
    RunInput,
    Source,
    ToolBudget,
    ToolResult,
)
from skala_rag.fakes import FakeClock
from skala_rag.graph import candidate_workflow_v3 as outer
from skala_rag.tools.source_fetch import FetchPolicy, SafeFetcher, snapshot_source_id

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("source-only test attempted network")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)


@pytest.fixture
def inputs():
    clock = FakeClock(datetime(2026, 9, 30, tzinfo=UTC))
    run = RunInput(
        schema_version="test",
        investment_theme="Synthetic robotics",
        countries=["US"],
        languages=["en"],
        as_of=date(2026, 9, 30),
        policy_version="v3-operational-1.0.0",
        corpus_version="synthetic-no-corpus-used",
        execution_mode="live",
    )
    profile = run_settings.recommended_profile(
        run_id="run-209",
        selection_source="synthetic-discovery-bundle",
        authority_reference="synthetic-not-approval",
        policy_references=(run.policy_version,),
        code_version=None,
    )
    url = "https://discovery.example/robots"
    digest = "sha256:" + hashlib.sha256(b"Synthetic discovery").hexdigest()
    source = Source(
        schema_version="test",
        source_id=snapshot_source_id(url, digest),
        url=url,
        local_path=None,
        title="Synthetic discovery",
        publisher=None,
        author=None,
        published_at=None,
        retrieved_at=clock.now(),
        source_kind="web",
        language="en",
        content_hash=digest,
        access_notes="Synthetic offline input",
        bibliographic_metadata={},
    )
    candidates = [
        Candidate(
            schema_version="test",
            candidate_id=f"co-{i}",
            canonical_name=f"Synthetic {i}",
            aliases=[],
            country="US",
            homepage_url=f"https://robot{i}.example/",
            legal_identifiers={},
            discovery_source_ids=[source.source_id],
        )
        for i in range(7)
    ]
    discovery = ToolResult[DiscoveryBundle](
        schema_version="test",
        status="ok",
        data=DiscoveryBundle(
            schema_version="test",
            candidates=candidates,
            sources={source.source_id: source},
        ),
        retrieval_records=[],
        errors=[],
    )
    budget = ToolBudget(
        schema_version="test", max_calls=1, max_retries=0, timeout_seconds=30
    )
    configured = []
    requests = []

    def fetcher_factory():
        configured.append("official-homepage")

        def handler(request):
            requests.append(str(request.url))
            return httpx.Response(200, text="Synthetic source without facts")

        return SafeFetcher(
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

    return (
        dict(
            run_input=run,
            run_profile=profile,
            run_id="run-209",
            discovery_result=discovery,
            budget=budget,
            fetcher_factory=fetcher_factory,
            clock=clock,
            provider="official-homepage",
            replay_scope="discovery_bundle_replay",
        ),
        configured,
        requests,
    )


def source_api():
    import importlib.util

    assert importlib.util.find_spec("skala_rag.source_only_v3") is not None, (
        "missing source-only consumer API"
    )
    from skala_rag import source_only_v3

    return source_only_v3


def test_real_tool_unknowns_archive_all_in_existing_outer_without_evaluator_build(
    inputs, tmp_path, monkeypatch
):
    api = source_api()
    options, configured, requests = inputs
    original = outer.run_candidate_workflow_v3
    calls = []

    def callthrough(*args, **kwargs):
        calls.append("outer")
        return original(*args, **kwargs)

    monkeypatch.setattr(outer, "run_candidate_workflow_v3", callthrough)

    def forbidden(*args, **kwargs):
        raise AssertionError("source-only must not build dormant evaluation graph")

    monkeypatch.setattr(outer, "build_evaluation_graph_v3", forbidden)
    boundary = api.prepare_source_only_v3(**options)
    events = []
    trace = []
    result = api.run_source_only_v3(
        boundary, output_dir=tmp_path / "run", graph_events=events, trace_events=trace
    )
    assert calls == ["outer"]
    receipt = result.selection_receipt
    assert receipt.execution_mode == result.execution_mode == "live"
    assert len(receipt.selected_ids) == result.candidate_index == 5
    assert tuple(result.outcomes) == receipt.selected_ids
    assert all(
        outcome.status == "eligibility_unknown" for outcome in result.outcomes.values()
    )
    assert result.status == "no_eligible_candidates"
    assert result.selection.selected_candidate_id is None
    assert result.selection.reason == "NO_ELIGIBLE_RESULTS"
    assert result.scores == result.decisions == result.coverage_results == {}
    assert not result.errors
    assert configured == ["official-homepage"]
    assert len(requests) == 5
    nodes = Counter(
        name
        for ns, updates in events
        if not ns
        for name in updates
        if name != "__interrupt__"
    )
    assert nodes == Counter(
        discover=1,
        normalize=1,
        candidate_iterator=6,
        research=5,
        eligibility=5,
        archive=5,
        advance=5,
        selector=1,
    )
    assert all(ns == () for ns, _ in events)
    assert Counter(e["step"] for e in trace)["selector"] == 1
    assert all(e["execution_mode"] == "live" for e in trace)
    for cid in receipt.selected_ids:
        detail = result.source_only_detail["research"][cid]
        state = detail["state"]
        assert len(state["sources"]) == 1
        assert state["evidence"] == {}
        assert state["eligibility_results"][cid]["status"] == "unknown"
        assert state["eligibility_results"][cid]["checks"]
        assert state["retrieval_history"]
        assert result.research_retry_count[cid] == 0
    saved = json.loads((tmp_path / "run" / "candidate-run.json").read_text())
    assert saved["source_only_detail"]["discovery"]["result"] == options[
        "discovery_result"
    ].model_dump(mode="json")
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text())
    assert "manifest.json" not in manifest["artifacts"]
    for name, digest in manifest["artifacts"].items():
        assert (
            hashlib.sha256((tmp_path / "run" / name).read_bytes()).hexdigest() == digest
        )
    assert manifest["execution_scope"] == "source_only"
    assert manifest["replay_scope"] == "discovery_bundle_replay"
    assert manifest["semantic_review"] == "unreviewed"
    assert manifest["evaluation"] == "not_started"
    assert manifest["publication_allowed"] is False
    persisted_nodes = json.loads((tmp_path / "run" / "graph-events.json").read_text())
    assert Counter(item["node"] for item in persisted_nodes) == nodes
    assert all(item["namespace"] == [] for item in persisted_nodes)
    steps = Counter(e["step"] for e in trace)
    assert steps["research"] == steps["eligibility"] == 5
    assert manifest["budget_scope"] == "per_candidate"
    assert (
        manifest["replay_budget_scope"]
        == "no_new_network_not_charged_to_fresh_fetch_deadline"
    )
    assert manifest["usage"]["physical_http_requests"] == "unmeasured"
    assert manifest["usage"]["provider_configuration_attempts"] == 1
    assert manifest["usage"]["provider_company_research_calls"] == 5
    assert set(manifest["usage"]["per_candidate"]) == set(receipt.selected_ids)
    assert all(
        row == dict(provider_company_research_calls=1, captured_replays=0)
        for row in manifest["usage"]["per_candidate"].values()
    )
    assert manifest["usage"]["captured_replays"] == 0
    assert manifest["past_paid_ledger"] == "not_supplied_unverified"


def failure_capture(options, candidate, *, code="LLM_TIMEOUT"):
    from skala_rag.contracts.error_codes import ErrorCode
    from skala_rag.contracts.interfaces import LLMError
    from skala_rag.tools.company_research import LiveResearchCompany
    from skala_rag.tools.official_homepage import OfficialHomepage

    class Failure:
        version = "synthetic-error-only-no-model"

        def __call__(self, *args):
            raise LLMError(ErrorCode(code), "must-not-persist-raw-exception")

    tool = LiveResearchCompany(
        [
            OfficialHomepage(
                options["fetcher_factory"](),
                schema_version="test",
                clock=options["clock"],
                extractor=Failure(),
            )
        ],
        run_id=options["run_id"],
        schema_version="test",
        as_of=options["run_input"].as_of,
        clock=options["clock"],
    )
    return tool(candidate, options["budget"])


def test_captured_required_error_preserves_original_source_then_advances(
    inputs, tmp_path
):
    api = source_api()
    options, configured, requests = inputs
    candidate = options["discovery_result"].data.candidates[0]
    captured = failure_capture(options, candidate)
    original = captured.model_dump(mode="json")
    configured.clear()
    requests.clear()
    boundary = api.prepare_source_only_v3(
        **options, research_replays={candidate.candidate_id: captured}
    )
    events = []
    result = api.run_source_only_v3(
        boundary, output_dir=tmp_path / "mixed", graph_events=events
    )
    assert result.status == "source_only_partial_failure"
    assert result.selection.reason == "SOURCE_ONLY_PARTIAL_FAILURE"
    assert result.outcomes[candidate.candidate_id].status == "failed"
    assert result.candidate_index == 5
    assert len(requests) == 4
    assert [e.error_code for e in result.errors] == ["LLM_TIMEOUT"]
    original_error = original["retrieval_records"][-1]["arguments_without_secrets"][
        "extractor_error"
    ]
    error = result.errors[0]
    for field in (
        "error_id",
        "run_id",
        "candidate_id",
        "node",
        "attempt",
        "timestamp",
        "retryable",
    ):
        assert error.model_dump(mode="json")[field] == original_error[field]
    detail = result.source_only_detail["research"][candidate.candidate_id]
    assert detail["input_scope"] == "captured_result_replay"
    assert detail["result"] == original
    assert len(detail["state"]["sources"]) == 1
    assert detail["state"]["retrieval_history"][0]["status"] == "ok"
    assert detail["state"]["evidence"] == {}
    assert detail["state"]["eligibility_results"] == {}
    assert captured.model_dump(mode="json") == original
    assert result.scores == result.decisions == {}
    nodes = Counter(
        name
        for ns, updates in events
        if not ns
        for name in updates
        if name != "__interrupt__"
    )
    assert (
        nodes["research"]
        == nodes["eligibility"]
        == nodes["archive"]
        == nodes["advance"]
        == 5
    )
    assert nodes["selector"] == 1
    assert (
        "must-not-persist-raw-exception"
        not in (tmp_path / "mixed" / "candidate-run.json").read_text()
    )


@pytest.mark.parametrize(
    "field", ["run_id", "schema_version", "policy_version", "corpus_version", "as_of"]
)
def test_research_replay_declared_generation_mismatch_is_denied_before_factory(
    inputs, field
):
    api = source_api()
    options, configured, requests = inputs
    candidate = options["discovery_result"].data.candidates[0]
    captured = failure_capture(options, candidate)
    captured.retrieval_records[-1].arguments_without_secrets[field] = (
        "different-generation"
    )
    configured.clear()
    requests.clear()
    with pytest.raises(ValueError):
        api.prepare_source_only_v3(
            **options, research_replays={candidate.candidate_id: captured}
        )
    assert configured == requests == []


@pytest.mark.parametrize(
    "field,value",
    [("corpus_version", "other"), ("as_of", "2026-09-29"), ("countries", ["KR"])],
)
def test_pinned_boundary_tampering_is_not_new_admission(inputs, tmp_path, field, value):
    from dataclasses import replace

    api = source_api()
    options, configured, requests = inputs
    boundary = api.prepare_source_only_v3(**options)
    payload = json.loads(boundary.run_input_json)
    payload[field] = value
    forged = replace(boundary, run_input_json=json.dumps(payload))
    with pytest.raises(ValueError):
        api.run_source_only_v3(forged, output_dir=tmp_path / "denied")
    assert configured == requests == []
    assert not (tmp_path / "denied").exists()


@pytest.mark.parametrize(
    "mode", ["no_homepage", "normal_empty_discovery", "failed_discovery", "all_failed"]
)
def test_normal_absence_and_technical_failure_remain_distinct(inputs, tmp_path, mode):
    from skala_rag.contracts.errors import WorkflowError

    api = source_api()
    options, configured, requests = inputs
    candidates = options["discovery_result"].data.candidates
    if mode == "no_homepage":
        for candidate in candidates:
            candidate.homepage_url = None
    elif mode == "normal_empty_discovery":
        options["discovery_result"].status = "empty"
        options["discovery_result"].data.candidates = []
    elif mode == "failed_discovery":
        options["discovery_result"] = ToolResult[DiscoveryBundle](
            schema_version="test",
            status="failed",
            data=None,
            retrieval_records=[],
            errors=[
                WorkflowError(
                    schema_version="test",
                    error_id="original-discovery-timeout",
                    run_id="run-209",
                    candidate_id=None,
                    node="discovery",
                    error_code="TOOL_TIMEOUT",
                    message_redacted="Synthetic timeout",
                    retryable=True,
                    attempt=1,
                    timestamp=options["clock"].now(),
                )
            ],
        )
    captures = (
        {c.candidate_id: failure_capture(options, c) for c in candidates}
        if mode == "all_failed"
        else {}
    )
    configured.clear()
    requests.clear()
    events = []
    result = api.run_source_only_v3(
        api.prepare_source_only_v3(**options, research_replays=captures),
        output_dir=tmp_path / mode,
        graph_events=events,
    )
    assert requests == []
    assert configured == (["official-homepage"] if mode == "no_homepage" else [])
    assert result.selection.selected_candidate_id is None
    assert result.scores == result.decisions == {}
    assert (
        Counter(name for ns, updates in events if not ns for name in updates)[
            "selector"
        ]
        == 1
    )
    if mode == "normal_empty_discovery":
        assert result.status == "no_candidates" and not result.errors
        assert result.candidate_index == 0
    elif mode == "failed_discovery":
        assert result.status == "discovery_failed"
        assert result.selection.reason == "SOURCE_ONLY_DISCOVERY_FAILED"
        assert [e.error_id for e in result.errors] == ["original-discovery-timeout"]
        assert result.source_only_detail["discovery"]["result"] == options[
            "discovery_result"
        ].model_dump(mode="json")
    elif mode == "all_failed":
        assert result.status == "source_only_technical_failure"
        assert result.selection.reason == "SOURCE_ONLY_TECHNICAL_FAILURE"
        assert result.candidate_index == len(result.errors) == 5
        assert {e.error_code for e in result.errors} == {"LLM_TIMEOUT"}
    else:
        assert result.status == "no_eligible_candidates"
        assert all(
            d["state"]["sources"] == {}
            for d in result.source_only_detail["research"].values()
        )
        assert all(
            d["receipt"]["providers"]["official-homepage"]["skipped"] == "NO_HOMEPAGE"
            for d in result.source_only_detail["research"].values()
        )


@pytest.mark.parametrize(
    "poison",
    [
        "kipris",
        "krx",
        "중기부",
        "tavily",
        "unknown-provider",
        "new_discovery",
        "paid_calls",
        "paid_cost",
        "retry",
        "budget",
        "budget_schema",
        "profile_run",
        "live_mode",
        "policy",
        "schema",
        "country",
        "fixture_homepage",
        "fixture_source",
        "source_closure",
        "nested_json",
    ],
)
def test_preconfiguration_guards_deny_bad_scope_without_callbacks(
    inputs, tmp_path, poison
):
    api = source_api()
    options, configured, requests = inputs
    if poison in {"kipris", "krx", "중기부", "tavily", "unknown-provider"}:
        options["provider"] = poison
    elif poison == "new_discovery":
        options["replay_scope"] = "new_discovery"
    elif poison in {"paid_calls", "paid_cost"}:
        object.__setattr__(
            options["run_profile"],
            "paid_call_allowance" if poison == "paid_calls" else "paid_cost_usd",
            1,
        )
    elif poison in {"retry", "budget", "budget_schema"}:
        setattr(
            options["budget"],
            {
                "retry": "max_retries",
                "budget": "max_calls",
                "budget_schema": "schema_version",
            }[poison],
            {"retry": 1, "budget": 0, "budget_schema": "other"}[poison],
        )
    elif poison == "profile_run":
        options["run_id"] = "wrong-run"
    elif poison in {"live_mode", "policy", "country"}:
        setattr(
            options["run_input"],
            {
                "live_mode": "execution_mode",
                "policy": "policy_version",
                "country": "countries",
            }[poison],
            {"live_mode": "fixture", "policy": "other", "country": ["KR"]}[poison],
        )
    elif poison == "schema":
        options["discovery_result"].data.candidates[0].schema_version = "wrong"
    elif poison == "fixture_homepage":
        options["discovery_result"].data.candidates[0].homepage_url = "fixture://poison"
    elif poison == "fixture_source":
        next(
            iter(options["discovery_result"].data.sources.values())
        ).url = "fixture://poison"
    elif poison == "source_closure":
        options["discovery_result"].data.sources = {}
    else:
        next(
            iter(options["discovery_result"].data.sources.values())
        ).bibliographic_metadata["nested"] = {"bad": object()}
    with pytest.raises((ValueError, TypeError)):
        api.run_source_only_v3(
            api.prepare_source_only_v3(**options), output_dir=tmp_path / "denied"
        )
    assert configured == requests == []
    assert not (tmp_path / "denied").exists()


@pytest.mark.parametrize(
    "poison", ["map", "missing_fetch", "future", "provider_notes", "error_id"]
)
def test_malformed_failure_retention_is_not_admitted(inputs, tmp_path, poison):
    api = source_api()
    options, configured, requests = inputs
    candidate = options["discovery_result"].data.candidates[0]
    captured = failure_capture(options, candidate)
    args = captured.retrieval_records[-1].arguments_without_secrets
    if poison == "map":
        args["retained_sources"] = []
    elif poison == "missing_fetch":
        captured.retrieval_records[0].source_ids = []
    elif poison == "future":
        next(iter(args["retained_sources"].values()))["retrieved_at"] = (
            "2026-10-01T00:00:00Z"
        )
    elif poison == "provider_notes":
        args["providers"]["official-homepage"]["notes"] = [None]
    else:
        args["extractor_error"]["error_id"] = "wrong"
    configured.clear()
    requests.clear()
    result = api.run_source_only_v3(
        api.prepare_source_only_v3(
            **options, research_replays={candidate.candidate_id: captured}
        ),
        output_dir=tmp_path / "rejected",
    )
    assert result.outcomes[candidate.candidate_id].status == "failed"
    assert "UPSTREAM_INVALID" in {e.error_code for e in result.errors}
    assert candidate.candidate_id not in result.source_only_detail["research"]
    assert result.candidate_index == 5
    assert result.scores == result.decisions == {}
    manifest = json.loads((tmp_path / "rejected" / "manifest.json").read_text())
    assert manifest["usage"]["captured_replays"] == 1
    assert manifest["usage"]["provider_company_research_calls"] == 4


def test_existing_synthetic_eligible_capture_is_explicitly_denied_before_downstream(
    inputs, tmp_path, monkeypatch
):
    from tests.integration import test_m2_research_state as fixtures

    api = source_api()
    options, configured, requests = inputs
    candidate, _, tool, budget, _ = fixtures.research.__wrapped__()
    tool._run_id = options["run_id"]
    candidate.discovery_source_ids = list(options["discovery_result"].data.sources)
    options["discovery_result"].data.candidates = [candidate]
    captured = tool(candidate, budget)

    def deny(*args, **kwargs):
        raise AssertionError("source-only eligible must not build evaluator")

    monkeypatch.setattr(outer, "build_evaluation_graph_v3", deny)
    events = []
    result = api.run_source_only_v3(
        api.prepare_source_only_v3(
            **options, research_replays={candidate.candidate_id: captured}
        ),
        output_dir=tmp_path / "eligible-denied",
        graph_events=events,
    )
    assert result.outcomes[candidate.candidate_id].status == "failed"
    assert result.status == "source_only_technical_failure"
    assert result.selection.reason == "SOURCE_ONLY_TECHNICAL_FAILURE"
    assert [e.error_code for e in result.errors] == ["SOURCE_ONLY_EVALUATION_NOT_READY"]
    assert (
        result.source_only_detail["research"][candidate.candidate_id]["state"][
            "eligibility_results"
        ][candidate.candidate_id]["status"]
        == "eligible"
    )
    assert configured == requests == []
    assert result.scores == result.decisions == {}
    nodes = Counter(name for ns, updates in events if not ns for name in updates)
    assert (
        nodes["eligibility"]
        == nodes["archive"]
        == nodes["advance"]
        == nodes["selector"]
        == 1
    )
    assert (
        not {"collect", "coverage", "freeze", "evaluation_join", "score", "decision"}
        & nodes.keys()
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("eligibility_status", "eligible"),
        ("status", "evaluated"),
        ("normalized_score", "90"),
        ("decision_id", "fake"),
        ("scores", {"fake": 90}),
    ],
)
def test_source_only_no_selection_guard_cannot_open_general_live_selector(field, value):
    from skala_rag.scoring.selector_v3 import select_best_v3
    from skala_rag.scoring.v3_policy import load_v3_policy

    api = source_api()
    policy = load_v3_policy(ROOT / "configs/scoring.v3.json", execution_mode="fixture")
    row = dict(
        candidate_id="co-0",
        eligibility_status="unknown",
        status="eligibility_unknown",
        label=None,
        normalized_score=None,
        weighted_missing_pct=None,
        applicable_weight=None,
        score_summary_id=None,
    )
    row[field] = value
    with pytest.raises(ValueError):
        api.select_source_only_terminal_v3(
            [row], policy, run_id="run", schema_version="test"
        )
    with pytest.raises(ValueError, match="fixture V3Policy required"):
        select_best_v3(
            [],
            policy.model_copy(update={"execution_mode": "live"}),
            run_id="run",
            schema_version="test",
        )


def test_callback_mutation_cannot_change_pinned_inputs_or_selection(
    inputs, tmp_path, monkeypatch
):
    from skala_rag.tools.company_research import LiveResearchCompany

    api = source_api()
    options, _, requests = inputs
    boundary = api.prepare_source_only_v3(**options)
    original = LiveResearchCompany.__call__

    def mutate(self, candidate, budget):
        result = original(self, candidate, budget)
        candidate.aliases.append("callback-mutation")
        candidate.discovery_source_ids.clear()
        options["run_input"].corpus_version = "callback-mutation"
        object.__setattr__(options["run_profile"], "seed", 9)
        return result

    monkeypatch.setattr(LiveResearchCompany, "__call__", mutate)
    result = api.run_source_only_v3(boundary, output_dir=tmp_path / "pinned")
    assert result.selection_receipt.profile.seed == 42
    assert "callback-mutation" not in result.selection_receipt.to_json()
    assert (
        result.source_only_detail["run_input"]["corpus_version"]
        == "synthetic-no-corpus-used"
    )
    assert len(requests) == result.candidate_index == 5


def test_control_cancellation_propagates_without_terminal_artifacts(inputs, tmp_path):
    import asyncio

    api = source_api()
    options, configured, requests = inputs

    def cancel():
        raise asyncio.CancelledError()

    options["fetcher_factory"] = cancel
    with pytest.raises(asyncio.CancelledError):
        api.run_source_only_v3(
            api.prepare_source_only_v3(**options), output_dir=tmp_path / "cancelled"
        )
    assert configured == requests == []
    assert not (tmp_path / "cancelled").exists()


def test_dedup_excluded_never_researched_or_refilled(inputs, tmp_path):
    from copy import deepcopy

    api = source_api()
    options, _, requests = inputs
    duplicate = deepcopy(options["discovery_result"].data.candidates[0])
    duplicate.candidate_id = "duplicate"
    options["discovery_result"].data.candidates.append(duplicate)
    result = api.run_source_only_v3(
        api.prepare_source_only_v3(**options), output_dir=tmp_path / "dedup"
    )
    receipt = result.selection_receipt
    assert receipt.merges[0].merged_candidate_id == "duplicate"
    assert set(receipt.excluded_ids) | {"duplicate"} <= set(receipt.discovered_ids)
    assert not (set(receipt.excluded_ids) | {"duplicate"}) & result.outcomes.keys()
    assert len(requests) == result.candidate_index == 5
    assert all(count == 0 for count in result.research_retry_count.values())


@pytest.mark.parametrize(
    "rows,reason",
    [
        ([], "NO_ELIGIBLE_RESULTS"),
        ([("unknown", "eligibility_unknown")], "NO_ELIGIBLE_RESULTS"),
        ([("ineligible", "ineligible")], "NO_ELIGIBLE_RESULTS"),
        ([("unknown", "failed")], "SOURCE_ONLY_TECHNICAL_FAILURE"),
        ([("eligible", "failed")], "SOURCE_ONLY_TECHNICAL_FAILURE"),
        (
            [("eligible", "failed"), ("unknown", "eligibility_unknown")],
            "SOURCE_ONLY_PARTIAL_FAILURE",
        ),
        (
            [("unknown", "failed"), ("ineligible", "ineligible")],
            "SOURCE_ONLY_PARTIAL_FAILURE",
        ),
    ],
)
def test_restricted_selector_preserves_failure_semantics(rows, reason):
    from skala_rag.scoring.v3_policy import load_v3_policy

    api = source_api()
    policy = load_v3_policy(ROOT / "configs/scoring.v3.json", execution_mode="fixture")
    selection = api.select_source_only_terminal_v3(
        [
            dict(candidate_id=f"co-{i}", eligibility_status=e, status=s)
            for i, (e, s) in enumerate(rows)
        ],
        policy,
        run_id="run-209",
        schema_version="test",
    )
    assert selection.reason == reason
    assert selection.selected_candidate_id is None
    assert selection.considered_candidate_ids == ()
    assert selection.compared_score_summary_ids == ()


@pytest.mark.parametrize("mixed", [False, True])
def test_eligible_failed_outer_row_keeps_actual_eligibility(
    inputs, tmp_path, monkeypatch, mixed
):
    from tests.integration import test_m2_research_state as fixtures

    api = source_api()
    options, configured, requests = inputs
    candidate, _, tool, budget, _ = fixtures.research.__wrapped__()
    tool._run_id = options["run_id"]
    candidate.discovery_source_ids = list(options["discovery_result"].data.sources)
    unknown = options["discovery_result"].data.candidates[0]
    options["discovery_result"].data.candidates = (
        [candidate, unknown] if mixed else [candidate]
    )
    captured = tool(candidate, budget)
    rows = []
    select = outer.select_source_only_terminal_v3

    def observe(observed_rows, *args, **kwargs):
        rows.extend(observed_rows)
        return select(observed_rows, *args, **kwargs)

    monkeypatch.setattr(outer, "select_source_only_terminal_v3", observe)
    result = api.run_source_only_v3(
        api.prepare_source_only_v3(
            **options, research_replays={candidate.candidate_id: captured}
        ),
        output_dir=tmp_path / "eligible-rows",
    )
    row = next(r for r in rows if r["candidate_id"] == candidate.candidate_id)
    assert row["eligibility_status"] == "eligible"
    assert row["status"] == "failed"
    assert row["normalized_score"] is None
    assert result.selection.reason == (
        "SOURCE_ONLY_PARTIAL_FAILURE" if mixed else "SOURCE_ONLY_TECHNICAL_FAILURE"
    )
    assert result.scores == result.decisions == {}
    assert len(result.outcomes) == result.candidate_index == (2 if mixed else 1)
    assert configured == (["official-homepage"] if mixed else [])
    assert len(requests) == (1 if mixed else 0)


@pytest.mark.parametrize(
    "invalid", ["late_clock", "expired_deadline", "deadline_equal_now"]
)
def test_fresh_admission_denied_before_configuration(inputs, tmp_path, invalid):
    api = source_api()
    options, configured, requests = inputs
    if invalid == "late_clock":
        options["clock"].current = datetime(2026, 10, 6, tzinfo=UTC)
    else:
        options["budget"].deadline = datetime(
            2026, 9, 29 if invalid == "expired_deadline" else 30, tzinfo=UTC
        )
    with pytest.raises(ValueError, match="source-only fresh"):
        api.run_source_only_v3(
            api.prepare_source_only_v3(**options), output_dir=tmp_path / "denied"
        )
    assert configured == requests == []
    assert not (tmp_path / "denied").exists()


def test_fresh_admission_rechecked_after_selection(inputs, tmp_path, monkeypatch):
    api = source_api()
    options, configured, requests = inputs
    normalize = run_settings.normalize_and_select

    def advance(*args, **kwargs):
        selected = normalize(*args, **kwargs)
        options["clock"].current = datetime(2026, 10, 1, tzinfo=UTC)
        return selected

    monkeypatch.setattr(run_settings, "normalize_and_select", advance)
    with pytest.raises(ValueError, match="source-only fresh"):
        api.run_source_only_v3(
            api.prepare_source_only_v3(**options),
            output_dir=tmp_path / "late-selection",
        )
    assert configured == requests == []
    assert not (tmp_path / "late-selection").exists()


@pytest.mark.parametrize("selected_only", [False, True])
@pytest.mark.parametrize("expired_budget", [False, True])
def test_late_clock_all_captures_replay_without_fresh_deadline(
    inputs, tmp_path, expired_budget, selected_only
):
    api = source_api()
    options, configured, requests = inputs
    candidates = options["discovery_result"].data.candidates
    captures = {c.candidate_id: failure_capture(options, c) for c in candidates}
    if selected_only:
        selected = run_settings.normalize_and_select(
            candidates, profile=options["run_profile"], execution_mode="live"
        ).receipt.selected_ids
        captures = {cid: value for cid, value in captures.items() if cid in selected}
    originals = {cid: value.model_dump(mode="json") for cid, value in captures.items()}
    configured.clear()
    requests.clear()
    options["clock"].current = datetime(2026, 10, 6, tzinfo=UTC)
    if expired_budget:
        options["budget"].deadline = datetime(2026, 9, 29, tzinfo=UTC)
    result = api.run_source_only_v3(
        api.prepare_source_only_v3(**options, research_replays=captures),
        output_dir=tmp_path / "late-replay",
    )
    assert result.status == "source_only_technical_failure"
    assert result.selection.reason == "SOURCE_ONLY_TECHNICAL_FAILURE"
    assert configured == requests == []
    assert result.source_only_detail["usage"]["captured_replays"] == 5
    assert result.source_only_detail["usage"]["provider_company_research_calls"] == 0
    for cid, detail in result.source_only_detail["research"].items():
        assert detail["result"] == originals[cid]
        assert detail["state"]["sources"]


@pytest.mark.parametrize("expires", ["deadline", "as_of"])
def test_fresh_admission_rechecked_each_candidate(
    inputs, tmp_path, monkeypatch, expires
):
    from skala_rag.tools.company_research import LiveResearchCompany

    api = source_api()
    options, configured, requests = inputs
    if expires == "deadline":
        options["budget"].deadline = datetime(2026, 9, 30, 0, 1, tzinfo=UTC)
    call = LiveResearchCompany.__call__
    calls = []

    def advance(self, *args):
        result = call(self, *args)
        calls.append("provider")
        options["clock"].current = (
            datetime(2026, 9, 30, 0, 2, tzinfo=UTC)
            if expires == "deadline"
            else datetime(2026, 10, 1, tzinfo=UTC)
        )
        return result

    monkeypatch.setattr(LiveResearchCompany, "__call__", advance)
    events = []
    result = api.run_source_only_v3(
        api.prepare_source_only_v3(**options),
        output_dir=tmp_path / "midloop",
        graph_events=events,
    )
    assert calls == ["provider"]
    assert configured == ["official-homepage"]
    assert len(requests) == 1
    assert result.status == "source_only_partial_failure"
    assert result.selection.reason == "SOURCE_ONLY_PARTIAL_FAILURE"
    assert result.candidate_index == len(result.outcomes) == 5
    assert Counter(o.status for o in result.outcomes.values()) == {
        "eligibility_unknown": 1,
        "failed": 4,
    }
    assert result.source_only_detail["usage"]["provider_company_research_calls"] == 1
    assert len(result.source_only_detail["research"]) == 1
    nodes = Counter(
        name
        for ns, updates in events
        if not ns
        for name in updates
        if name != "__interrupt__"
    )
    assert nodes["archive"] == nodes["advance"] == 5
    assert nodes["selector"] == 1
    assert result.scores == result.decisions == {}


def test_valid_future_deadline_is_per_candidate_not_campaign_limit(inputs, tmp_path):
    api = source_api()
    options, configured, requests = inputs
    options["budget"].deadline = datetime(2026, 9, 30, 1, tzinfo=UTC)
    result = api.run_source_only_v3(
        api.prepare_source_only_v3(**options), output_dir=tmp_path / "valid-deadline"
    )
    assert configured == ["official-homepage"]
    assert len(requests) == 5
    assert result.status == "no_eligible_candidates"
    assert not result.errors


@pytest.mark.parametrize(
    "poison",
    [
        "status",
        "label",
        "normalized_score",
        "weighted_missing_pct",
        "applicable_weight",
        "score_summary_id",
        "decision_id",
        "scores",
    ],
)
def test_eligible_failed_guard_rejects_evaluated_or_scored_rows(poison):
    from skala_rag.scoring.v3_policy import load_v3_policy

    api = source_api()
    policy = load_v3_policy(ROOT / "configs/scoring.v3.json", execution_mode="fixture")
    row = dict(candidate_id="co-0", eligibility_status="eligible", status="failed")
    row[poison] = (
        "evaluated"
        if poison == "status"
        else {"fake": 90}
        if poison == "scores"
        else "fake"
    )
    with pytest.raises(ValueError):
        api.select_source_only_terminal_v3(
            [row], policy, run_id="run-209", schema_version="test"
        )


@pytest.mark.parametrize("status", ["ineligible", "unknown"])
def test_normal_replayed_eligibility_kept_without_failure_reason(
    inputs, tmp_path, monkeypatch, status
):
    from tests.integration import test_m2_research_state as fixtures

    api = source_api()
    options, configured, requests = inputs
    candidate, _, tool, budget, _ = fixtures.research.__wrapped__()
    candidate.discovery_source_ids = list(options["discovery_result"].data.sources)
    options["discovery_result"].data.candidates = [candidate]
    tool._run_id = options["run_id"]
    captured = tool(candidate, budget)
    captured.data.profile.is_listed = True if status == "ineligible" else None
    original = captured.model_dump(mode="json")
    rows = []
    select = outer.select_source_only_terminal_v3

    def observe(observed_rows, *args, **kwargs):
        rows.extend(observed_rows)
        return select(observed_rows, *args, **kwargs)

    monkeypatch.setattr(outer, "select_source_only_terminal_v3", observe)
    result = api.run_source_only_v3(
        api.prepare_source_only_v3(
            **options, research_replays={candidate.candidate_id: captured}
        ),
        output_dir=tmp_path / "normal-replay",
    )
    assert configured == requests == []
    assert result.status == "no_eligible_candidates"
    assert result.selection.reason == "NO_ELIGIBLE_RESULTS"
    assert rows[0]["eligibility_status"] == status
    assert (
        result.source_only_detail["research"][candidate.candidate_id]["result"]
        == original
    )
    assert not result.errors


def test_late_replay_published_source_preserved_under_existing_cutoff(inputs, tmp_path):
    api = source_api()
    options, configured, requests = inputs
    candidate = options["discovery_result"].data.candidates[0]
    options["discovery_result"].data.candidates = [candidate]
    captured = failure_capture(options, candidate)
    source = next(
        iter(
            captured.retrieval_records[-1]
            .arguments_without_secrets["retained_sources"]
            .values()
        )
    )
    source["published_at"] = "2026-09-29"
    # Replay happens late; the original captured edition remains before as_of.
    original = captured.model_dump(mode="json")
    options["clock"].current = datetime(2026, 10, 6, tzinfo=UTC)
    configured.clear()
    requests.clear()
    result = api.run_source_only_v3(
        api.prepare_source_only_v3(
            **options, research_replays={candidate.candidate_id: captured}
        ),
        output_dir=tmp_path / "dated-replay",
    )
    assert configured == requests == []
    assert (
        result.source_only_detail["research"][candidate.candidate_id]["result"]
        == original
    )
    assert result.source_only_detail["research"][candidate.candidate_id]["state"][
        "sources"
    ]
    assert [e.error_code for e in result.errors] == ["LLM_TIMEOUT"]


@pytest.mark.parametrize(
    "declaration", ["canonical_name", "homepage_url", "summary_query", "official_url"]
)
def test_declared_capture_candidate_identity_mismatch_denied_before_factory(
    inputs, declaration
):
    api = source_api()
    options, configured, requests = inputs
    candidate = options["discovery_result"].data.candidates[0]
    captured = failure_capture(options, candidate)
    if declaration == "summary_query":
        captured.retrieval_records[-1].query = "Other candidate"
    elif declaration == "official_url":
        captured.retrieval_records[0].arguments_without_secrets["url"] = (
            "https://other.example/"
        )
    else:
        captured.retrieval_records[-1].arguments_without_secrets[declaration] = (
            "Other candidate"
            if declaration == "canonical_name"
            else "https://other.example/"
        )
    configured.clear()
    requests.clear()
    with pytest.raises(ValueError, match="attribution"):
        api.prepare_source_only_v3(
            **options, research_replays={candidate.candidate_id: captured}
        )
    assert configured == requests == []


def test_unused_captures_preserved_with_excluded_and_dedup_ids(inputs, tmp_path):
    from copy import deepcopy

    api = source_api()
    options, configured, requests = inputs
    duplicate = deepcopy(options["discovery_result"].data.candidates[0])
    duplicate.candidate_id = "duplicate"
    options["discovery_result"].data.candidates.append(duplicate)
    captures = {
        c.candidate_id: failure_capture(options, c)
        for c in options["discovery_result"].data.candidates
    }
    originals = {cid: value.model_dump(mode="json") for cid, value in captures.items()}
    configured.clear()
    requests.clear()
    result = api.run_source_only_v3(
        api.prepare_source_only_v3(**options, research_replays=captures),
        output_dir=tmp_path / "capture-audit",
    )
    assert configured == requests == []
    receipt = result.selection_receipt
    manifest = json.loads((tmp_path / "capture-audit" / "manifest.json").read_text())
    audit = manifest["capture_replay"]
    assert set(audit["provided_ids"]) == set(captures)
    assert set(audit["attempted_ids"]) == set(receipt.selected_ids)
    assert set(audit["unused_ids"]) == set(captures) - set(receipt.selected_ids)
    assert set(audit["excluded_ids"]) == set(receipt.excluded_ids) & set(captures)
    assert audit["dedup_merged_ids"] == ["duplicate"]
    assert (
        audit["attribution_limits"]
        == "declared_identity_checked_opaque_metadata_not_identity_proof"
    )
    assert result.source_only_detail["capture_replay_inputs"] == originals
    canonical = json.dumps(
        originals, ensure_ascii=False, sort_keys=True, allow_nan=False
    )
    assert audit["input_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()


def test_opaque_capture_metadata_preserved_not_promoted_to_identity_proof(
    inputs, tmp_path
):
    api = source_api()
    options, configured, requests = inputs
    candidate = options["discovery_result"].data.candidates[0]
    options["discovery_result"].data.candidates = [candidate]
    captured = failure_capture(options, candidate)
    captured.retrieval_records[0].query = "opaque query NOT candidate attribution"
    captured.retrieval_records[0].arguments_without_secrets["opaque_extra"] = {
        "unverified_name": "Other candidate"
    }
    configured.clear()
    requests.clear()
    result = api.run_source_only_v3(
        api.prepare_source_only_v3(
            **options, research_replays={candidate.candidate_id: captured}
        ),
        output_dir=tmp_path / "opaque",
    )
    assert configured == requests == []
    assert result.source_only_detail["research"][candidate.candidate_id][
        "result"
    ] == captured.model_dump(mode="json")
    manifest = json.loads((tmp_path / "opaque" / "manifest.json").read_text())
    assert (
        manifest["capture_replay"]["attribution_limits"]
        == "declared_identity_checked_opaque_metadata_not_identity_proof"
    )
    assert manifest["semantic_review"] == "unreviewed"


@pytest.mark.parametrize("failure", ["exception", "wrong_fetcher"])
@pytest.mark.parametrize("with_replay", [False, True])
def test_failed_configuration_attempted_once_preserves_archives_and_replay(
    inputs, tmp_path, failure, with_replay
):
    api = source_api()
    options, configured, requests = inputs
    captures = {}
    if with_replay:
        candidate = options["discovery_result"].data.candidates[0]
        captures[candidate.candidate_id] = failure_capture(options, candidate)
    original = {cid: value.model_dump(mode="json") for cid, value in captures.items()}
    configured.clear()
    requests.clear()
    attempts = []

    def fail():
        attempts.append("factory")
        if failure == "exception":
            raise RuntimeError("must-not-persist-configuration-secret")
        return object()

    options["fetcher_factory"] = fail
    events = []
    result = api.run_source_only_v3(
        api.prepare_source_only_v3(**options, research_replays=captures),
        output_dir=tmp_path / "failed-configuration",
        graph_events=events,
    )
    assert attempts == ["factory"]
    assert configured == requests == []
    assert result.status == "source_only_technical_failure"
    assert result.selection.reason == "SOURCE_ONLY_TECHNICAL_FAILURE"
    assert len(result.outcomes) == result.candidate_index == 5
    assert all(
        o.status == "failed" and len(o.failure_ids) == 1
        for o in result.outcomes.values()
    )
    usage = result.source_only_detail["usage"]
    assert usage["provider_configuration_attempts"] == 1
    assert usage["provider_company_research_calls"] == 0
    assert usage["captured_replays"] == int(with_replay)
    assert (
        sum(
            row["provider_company_research_calls"]
            for row in usage["per_candidate"].values()
        )
        == 0
    )
    if with_replay:
        cid = next(iter(captures))
        assert result.source_only_detail["research"][cid]["result"] == original[cid]
        assert result.source_only_detail["research"][cid]["state"]["sources"]
        assert "LLM_TIMEOUT" in {e.error_code for e in result.errors}
    nodes = Counter(
        name
        for ns, updates in events
        if not ns
        for name in updates
        if name != "__interrupt__"
    )
    assert nodes["archive"] == nodes["advance"] == 5
    assert nodes["selector"] == 1
    assert result.scores == result.decisions == {}
    for artifact in (tmp_path / "failed-configuration").glob("*.json"):
        assert "must-not-persist-configuration-secret" not in artifact.read_text()


@pytest.mark.parametrize("control", [KeyboardInterrupt, SystemExit])
def test_configuration_process_control_propagates(inputs, tmp_path, control):
    api = source_api()
    options, configured, requests = inputs

    def stop():
        raise control()

    options["fetcher_factory"] = stop
    with pytest.raises(control):
        api.run_source_only_v3(
            api.prepare_source_only_v3(**options), output_dir=tmp_path / "stopped"
        )
    assert configured == requests == []
    assert not (tmp_path / "stopped").exists()


def test_factory_clock_rollover_does_not_count_uninvoked_provider(inputs, tmp_path):
    api = source_api()
    options, configured, requests = inputs
    factory = options["fetcher_factory"]

    def advance():
        fetcher = factory()
        options["clock"].current = datetime(2026, 10, 1, tzinfo=UTC)
        return fetcher

    options["fetcher_factory"] = advance
    result = api.run_source_only_v3(
        api.prepare_source_only_v3(**options), output_dir=tmp_path / "factory-rollover"
    )
    assert configured == ["official-homepage"]
    assert requests == []
    assert result.source_only_detail["usage"]["provider_company_research_calls"] == 0
    assert result.source_only_detail["usage"]["provider_configuration_attempts"] == 1
    assert result.candidate_index == 5
    assert result.status == "source_only_technical_failure"
