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
    assert manifest.workflow_status == "running"
    assert manifest.run_outcome is None
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
