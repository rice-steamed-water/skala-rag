"""#30: four v3 CLI paths and one complete baseline Graph fixture path."""

import hashlib
import json
import socket
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.integration.test_candidate_graph import (
    _always_gap,
    _tracked_collect,
)

from skala_rag.cli import run
from skala_rag.contracts.manifest import ArtifactMetadata, RunManifest
from skala_rag.graph.report import ReportNodes, build_report_graph
from skala_rag.reporting.generator import generate_fixture_report
from skala_rag.reporting.stubs import StubJudge, StubLayout, StubRenderer

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
    make, execute, calls, run_input, policy = harness
    nodes = make(["recommend"])
    original_evaluate = nodes.evaluate

    def evaluate(state):
        # The candidate harness normally returns only the parallel result map;
        # the report controller also needs the successful Evaluation payloads.
        delta = original_evaluate(state)
        delta["evaluations"] = {
            key: value["evaluation"]
            for key, value in delta["evaluation_results"].items()
        }
        return delta

    nodes = replace(
        nodes,
        coverage=_always_gap,
        collect=_tracked_collect(nodes, calls),
        evaluate=evaluate,
    )
    result = execute(nodes)
    limit = policy.budgets.max_research_retries_per_candidate
    research_calls = [c for c in calls if c[0] == "collect"]
    assert research_calls == [("collect", "co-0", n) for n in range(limit + 1)]
    assert result["research_retry_count"]["co-0"] == limit
    assert result["research_gaps"]["co-0"][0]["status"] == "exhausted"
    assert result["workflow_status"] == "running"
    assert result["report_input"] is not None

    report_graph = build_report_graph(
        ReportNodes(
            generate=generate_fixture_report,
            judge=StubJudge(("pass",)),
            render=StubRenderer((True,)),
            layout=StubLayout((True,)),
        ),
        policy,
        run_id="run-synthetic",
        schema_version="synthetic-1",
        clock=lambda: datetime(2026, 9, 1, tzinfo=UTC),
        template="fixture-template",
    ).compile()
    final = report_graph.invoke(result, {"recursion_limit": 50})
    assert final["workflow_status"] == "completed"
    assert final["run_outcome"] == "recommended"
    assert final["report"] is not None
    assert final["pdf_validation"]["checks"]["pdf_verified"] is False

    trace = [
        {
            "step": step,
            "candidate_id": candidate_id,
            "retry_count": count,
            "execution_mode": "fixture",
        }
        for step, candidate_id, count in research_calls
    ]
    trace.append(
        {
            "step": "report_complete",
            "workflow_status": final["workflow_status"],
            "run_outcome": final["run_outcome"],
            "execution_mode": "fixture",
        }
    )
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(json.dumps(trace))
    report_path = tmp_path / "report.md"
    report_path.write_text(final["report"])
    artifacts = {}
    for path in (trace_path, report_path):
        artifacts[path.name] = ArtifactMetadata(
            schema_version="synthetic-1",
            artifact_path=str(path),
            artifact_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, check=False
    )
    manifest = RunManifest(
        schema_version="synthetic-1",
        run_id="run-synthetic",
        run_input=run_input,
        code_revision=revision.stdout.strip() if revision.returncode == 0 else None,
        uncommitted=bool(dirty.stdout)
        or dirty.returncode != 0
        or revision.returncode != 0,
        policy_version=policy.policy_version,
        corpus_version=run_input.corpus_version,
        corpus_hash=hashlib.sha256(
            (CONFIG.parent / "contracts.json").read_bytes()
        ).hexdigest(),
        prompt_versions={"report_generator": "fixture-template"},
        model_versions={"report_generator": "fixture-template", "report_judge": "stub"},
        tool_status={"execution_mode": "fixture", "pdf": "stub_unverified"},
        budgets={"research_retries_per_candidate": limit},
        usage={"external_requests": 0, "llm_tokens": 0, "cost_usd": "0"},
        artifacts=artifacts,
        validation_results={
            "structural": final["report_validation"],
            "semantic": final["report_judgement"],
            "pdf": final["pdf_validation"],
        },
        workflow_status=final["workflow_status"],
        run_outcome=final["run_outcome"],
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2))
    saved = RunManifest.model_validate_json(manifest_path.read_text())
    assert saved.workflow_status == "completed"
    assert saved.run_outcome == "recommended"
    assert saved.run_input.execution_mode == "fixture"
    assert all(event["execution_mode"] == "fixture" for event in trace)
    assert saved.usage["external_requests"] == 0
    for artifact in saved.artifacts.values():
        assert (
            hashlib.sha256(Path(artifact.artifact_path).read_bytes()).hexdigest()
            == artifact.artifact_hash
        )


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
