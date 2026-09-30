"""#30: four terminal CLI paths and one baseline research Graph handoff.

The graph-only retry case records its trace but is not a v3 CLI terminal run.
"""

import hashlib
import json
import socket
from dataclasses import replace
from pathlib import Path

import pytest
from tests.integration.test_candidate_graph import (
    _always_gap,
    _tracked_collect,
)

from skala_rag.cli import run
from skala_rag.contracts.manifest import RunManifest

CONFIG = Path(__file__).parents[1] / "fixtures/cli-input.json"
pytest_plugins = ["tests.integration.test_candidate_graph"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("fixture E2E attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", blocked)


def run_cli(tmp_path, *, ratings=(5, 4), verdict="pass"):
    destination = run(
        "Physical AI robotics",
        output_dir=tmp_path,
        policy_path="configs/scoring.v3.json",
        catalog_path="configs/scoring.draft.json",
        config_path=CONFIG,
        fixture_ratings=ratings,
        fixture_judge_verdict=verdict,
    )
    manifest = RunManifest.model_validate_json(
        (destination / "manifest.json").read_text()
    )
    receipt = json.loads((destination / "run-result.json").read_text())
    result = json.loads((destination / "candidate-result.json").read_text())
    trace = json.loads((destination / "trace.json").read_text())
    assert manifest.run_input.execution_mode == "fixture"
    assert manifest.usage["external_requests"] == 0
    assert manifest.usage["llm_tokens"] == 0
    assert manifest.usage["cost_usd"] == "0"
    assert all(t["execution_mode"] == "fixture" for t in trace)
    assert all(t["run_id"] == manifest.run_id for t in trace)
    for artifact in manifest.artifacts.values():
        assert (
            hashlib.sha256(Path(artifact.artifact_path).read_bytes()).hexdigest()
            == artifact.artifact_hash
        )
    assert not receipt["publication_allowed"]
    assert not (destination / "report.md").exists()
    return destination, manifest, receipt, result, trace


def test_normal_recommendation_cli_trace_and_manifest(tmp_path):
    _, manifest, receipt, result, trace = run_cli(tmp_path)
    assert receipt["workflow_status"] == "completed"
    assert receipt["run_outcome"] == "recommended"
    assert receipt["exit_code"] == 0
    assert result["selection"]["selected_candidate_id"] == "company-0"
    assert manifest.run_outcome == "recommended"
    assert {t["step"] for t in trace} >= {
        "evaluation_join",
        "selector",
        "report_context",
        "report_generate",
        "report_validate",
        "report_judge",
        "report_pdf",
    }


def test_watchlist_then_next_candidate_cli_trace_and_manifest(tmp_path):
    _, manifest, receipt, result, trace = run_cli(tmp_path, ratings=(3, 5))
    assert result["selection"]["selected_candidate_id"] == "company-1"
    assert result["decisions"]["company-0"]["label"] == "WATCHLIST"
    assert result["decisions"]["company-1"]["label"].startswith("RECOMMEND")
    assert set(result["scores"]) == {"company-0", "company-1"}
    assert receipt["run_outcome"] == manifest.run_outcome == "recommended"
    assert {t["candidate_id"] for t in trace if t["step"] == "evaluation_join"} == {
        "company-0",
        "company-1",
    }


def test_all_non_recommendation_cli_trace_and_manifest(tmp_path):
    destination, manifest, receipt, result, trace = run_cli(tmp_path, ratings=(3, 3))
    assert result["selection"]["selected_candidate_id"] is None
    assert receipt["run_outcome"] == manifest.run_outcome == "no_recommendation"
    assert receipt["workflow_status"] == "completed" and receipt["exit_code"] == 0
    context = json.loads((destination / "report-context.json").read_text())
    assert context["mode"] == "no_recommendation"
    assert any(t["step"] == "selector" for t in trace)


def test_research_exhaustion_terminates_installed_graph(harness, tmp_path):
    make, execute, calls, _, policy = harness
    nodes = make(["recommend"])
    nodes = replace(nodes, coverage=_always_gap, collect=_tracked_collect(nodes, calls))
    result = execute(nodes)
    limit = policy.budgets.max_research_retries_per_candidate
    trace = [c for c in calls if c[0] == "collect"]
    (tmp_path / "research-trace.json").write_text(json.dumps(trace))
    assert trace == [("collect", "co-0", n) for n in range(limit + 1)]
    assert result["research_retry_count"]["co-0"] == limit
    assert result["research_gaps"]["co-0"][0]["status"] == "exhausted"
    assert result["workflow_status"] == "running"  # report handoff, not final
    assert result["report_input"] is not None


def test_report_revision_exhaustion_cli_warning_trace_and_manifest(tmp_path):
    destination, manifest, receipt, _, trace = run_cli(tmp_path, verdict="revise")
    assert receipt["workflow_status"] == manifest.workflow_status == "completed"
    assert receipt["exit_code"] == 2 and receipt["warnings"]
    assert receipt["acceptance"] == "warning"
    assert manifest.usage["report_revisions"] == 2
    assert manifest.validation_results["semantic"].verdict == "revise"
    assert len([t for t in trace if t["step"] == "report_generate"]) == 3
    assert len([t for t in trace if t["step"] == "report_judge"]) == 3
    assert not [t for t in trace if t["step"] == "report_pdf"]
    assert (destination / "draft.md").exists()
