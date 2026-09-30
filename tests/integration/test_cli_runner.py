"""CLI artifact integrity and zero-call live refusal."""

import hashlib
import json
from pathlib import Path

import pytest

from skala_rag.cli import main, run
from skala_rag.contracts.manifest import RunManifest

CONFIG = Path(__file__).parents[1] / "fixtures/cli-input.json"


def test_fixture_artifacts_have_valid_hashes_and_no_final(tmp_path):
    destination = run(
        "robotics",
        output_dir=tmp_path,
        policy_path="configs/scoring.v3.json",
        catalog_path="configs/scoring.draft.json",
        config_path=CONFIG,
    )
    manifest = RunManifest.model_validate_json(
        (destination / "manifest.json").read_text()
    )
    assert manifest.run_input.execution_mode == "fixture"
    assert manifest.usage["external_requests"] == 0
    assert manifest.usage["fixture_evaluation_branches"] == 10
    assert manifest.workflow_status == "failed"
    assert manifest.run_outcome == "technical_failure"
    assert not manifest.validation_results
    assert not (destination / "report.md").exists()
    for artifact in manifest.artifacts.values():
        assert (
            hashlib.sha256(Path(artifact.artifact_path).read_bytes()).hexdigest()
            == artifact.artifact_hash
        )
    result = json.loads((destination / "candidate-result.json").read_text())
    assert result["selection"]["selected_candidate_id"] == "company-0"
    assert result["scores"]["company-0"]["normalized_score"] == "100"
    trace = json.loads((destination / "trace.json").read_text())
    assert all(
        t["run_id"] == manifest.run_id and t["duration_seconds"] >= 0 for t in trace
    )
    assert all(t["output_ids"] for t in trace if t["step"] == "freeze")


def test_live_refuses_before_runner_or_config_read(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("runner called for live request")

    monkeypatch.setattr("skala_rag.cli.run", forbidden)
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--theme",
                "robotics",
                "--mode",
                "live",
                "--config",
                "missing",
                "--output-dir",
                str(tmp_path),
            ]
        )
    assert exc.value.code == 1
    assert not list(tmp_path.iterdir())


def test_live_config_and_policy_mismatch_refuse_without_artifacts(tmp_path):
    config = json.loads(CONFIG.read_text())
    path = tmp_path / "config.json"
    config["execution_mode"] = "live"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="live execution"):
        run(
            "robotics",
            output_dir=tmp_path / "outputs",
            policy_path="missing",
            catalog_path="missing",
            config_path=path,
        )
    config["execution_mode"] = "fixture"
    config["policy_version"] = "wrong"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="policy version"):
        run(
            "robotics",
            output_dir=tmp_path / "outputs",
            policy_path="configs/scoring.v3.json",
            catalog_path="missing",
            config_path=path,
        )
    assert not (tmp_path / "outputs").exists()


def test_injected_warning_persists_draft_findings_and_terminal_manifest(tmp_path):
    from tests.unit.test_run_finalization import completion

    destination = run(
        "robotics",
        output_dir=tmp_path,
        policy_path="configs/scoring.v3.json",
        catalog_path="configs/scoring.draft.json",
        config_path=CONFIG,
        report_adapter=lambda result: completion(revisions=2, verdict="revise"),
    )
    manifest = RunManifest.model_validate_json(
        (destination / "manifest.json").read_text()
    )
    receipt = json.loads((destination / "run-result.json").read_text())
    assert manifest.workflow_status == "completed"
    assert manifest.usage["report_revisions"] == 2
    assert manifest.validation_results["semantic"].verdict == "revise"
    assert receipt["exit_code"] == 2 and receipt["warnings"]
    assert (
        not receipt["publication_allowed"] and not (destination / "report.md").exists()
    )
    assert (destination / "draft.md").read_text() == "Fixture draft"


def test_report_adapter_failure_is_redacted_and_terminal(tmp_path):
    def broken(result):
        raise RuntimeError("secret-provider-token")

    destination = run(
        "robotics",
        output_dir=tmp_path,
        policy_path="configs/scoring.v3.json",
        catalog_path="configs/scoring.draft.json",
        config_path=CONFIG,
        report_adapter=broken,
    )
    receipt = json.loads((destination / "run-result.json").read_text())
    assert receipt["reason"] == "REPORT_ADAPTER_FAILED"
    assert receipt["workflow_status"] == "failed"
    assert all(
        "secret-provider-token" not in p.read_text() for p in destination.iterdir()
    )


def test_manifest_versions_hashes_and_controller_trace_match_artifacts(tmp_path):
    destination = run(
        "robotics",
        output_dir=tmp_path,
        policy_path="configs/scoring.v3.json",
        catalog_path="configs/scoring.draft.json",
        config_path=CONFIG,
    )
    manifest = RunManifest.model_validate_json(
        (destination / "manifest.json").read_text()
    )
    result = json.loads((destination / "candidate-result.json").read_text())
    trace = json.loads((destination / "trace.json").read_text())
    assert manifest.tool_status["index_version"]
    assert (
        manifest.tool_status["policy_hash"]
        == hashlib.sha256(Path("configs/scoring.v3.json").read_bytes()).hexdigest()
    )
    assert (
        manifest.tool_status["effective_input_hash"]
        == hashlib.sha256(
            json.dumps(
                manifest.run_input.model_dump(mode="json"), sort_keys=True
            ).encode()
        ).hexdigest()
    )
    for cid, summary in result["scores"].items():
        steps = {item["step"]: item for item in trace if item["candidate_id"] == cid}
        assert steps["coverage"]["input_ids"]
        assert len(steps["evaluation_join"]["output_ids"]) == 6
        assert steps["score"]["input_ids"] == [summary["snapshot_id"]]
        assert summary["score_summary_id"] in steps["score"]["output_ids"]
        assert steps["decision"]["input_ids"] == [summary["score_summary_id"]]
        assert (
            result["decisions"][cid]["decision_id"] in steps["decision"]["output_ids"]
        )
        assert all(
            steps[step]["status"] == "ok"
            for step in ("coverage", "evaluation_join", "score", "decision")
        )
    selector = next(item for item in trace if item["step"] == "selector")
    assert selector["output_ids"] == [result["selection"]["selected_candidate_id"]]


def test_failed_branch_trace_contains_only_ids_and_redacted_status():
    from skala_rag.fixture_runner import run_fixture

    trace = []
    result, _ = run_fixture(broken={"company-0"}, trace=trace)
    failed = next(
        t
        for t in trace
        if t["candidate_id"] == "company-0" and t["step"] == "evaluation_join"
    )
    assert failed["status"] == "failed"
    assert set(failed["output_ids"]) == set(result.outcomes["company-0"].failure_ids)
    assert not any(
        t["step"] in ("score", "decision") and t["candidate_id"] == "company-0"
        for t in trace
    )
    assert "secret synthetic provider error" not in json.dumps(trace)
    assert result.selection.selected_candidate_id == "company-1"
