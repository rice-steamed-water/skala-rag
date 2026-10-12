"""Paid execution must fail closed and preserve redacted failure receipts."""

import json

import pytest

from skala_rag import live_exploration as live


def test_missing_key_creates_no_artifacts_or_requests(tmp_path):
    (tmp_path / ".env").write_text("OTHER_SETTING=value\n")
    with pytest.raises(ValueError, match="OPENAI_API_KEY missing"):
        live.run(root=tmp_path, artifacts_root=tmp_path, approval_reference="test")
    assert not (tmp_path / "outputs").exists()


def test_missing_approval_is_rejected_before_reading_credentials(tmp_path):
    with pytest.raises(ValueError, match="approval required"):
        live.run(root=tmp_path, artifacts_root=tmp_path, approval_reference=" ")
    assert not (tmp_path / "outputs").exists()


def test_model_preflight_failure_has_no_paid_calls_or_secret(tmp_path, monkeypatch):
    secret = "synthetic-private-key"
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={secret}\n")

    def unavailable(**kwargs):
        raise ValueError("local model integrity failure")

    monkeypatch.setattr(live, "LocalRAG", unavailable)
    destination = live.run(
        root=tmp_path, artifacts_root=tmp_path, approval_reference="synthetic-test"
    )
    receipt = json.loads((destination / "run-result.json").read_text())
    assert receipt["ledger"]["calls"] == 0
    assert receipt["workflow_status"] == receipt["acceptance"] == "failed"
    assert receipt["publication_allowed"] is False
    assert receipt["llm_usage"] == {}
    assert all(secret not in p.read_text() for p in destination.iterdir())


def test_previous_campaign_exhaustion_rejects_new_attempt(tmp_path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=synthetic-key\n")
    prior = tmp_path / "outputs/live-exploration-prior"
    prior.mkdir(parents=True)
    (prior / "run-result.json").write_text(
        json.dumps(
            {
                "approval_reference": "same-campaign",
                "ledger": {
                    "calls": 50,
                    "tool_calls": {"openai": 30},
                    "cost_usd_accounted": "3",
                },
                "elapsed_seconds": 0,
            }
        )
    )
    with pytest.raises(ValueError, match="campaign budget exhausted"):
        live.run(
            root=tmp_path, artifacts_root=tmp_path, approval_reference="same-campaign"
        )
    assert list((tmp_path / "outputs").iterdir()) == [prior]


def test_html_parser_removes_scripts_and_styles():
    parser = live.VisibleText()
    parser.feed("<style>hidden</style><script>secret()</script><p>Real text</p>")
    assert parser.parts == ["Real text"]
