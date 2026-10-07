"""Offline live-boundary tests: real DemoLLM, synthetic HTTP and local corpus."""

import json
import shutil
from pathlib import Path

import httpx
import pytest
from tests.unit.test_demo_budget import approve
from tests.unit.test_demo_scoring import missing_review
from tests.unit.test_local_demo import retrieval_fixture

from skala_rag.demo_context import SCHEMA, research_material
from skala_rag.demo_scoring import load_demo_rubric
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt
from skala_rag.tools.structured_llm import APPROVED_MODEL


def setup_boundary(root, monkeypatch, *, verdict="pass", instructions=None):
    import skala_rag.local_demo as demo

    approve(root)
    shutil.copytree(Path(__file__).resolve().parents[2] / "configs", root / "configs")
    rubric = load_demo_rubric(root)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-credential")

    def retrieve(**kwargs):
        bundle, records = retrieval_fixture(root)
        records[0].run_id = kwargs["run_id"]
        return research_material(
            root=root, bundle=bundle, records=records, run_id=kwargs["run_id"]
        )

    def respond(request):
        payload = json.loads(request.content)
        # Keep a durable count across the runner process boundary.
        with (root / "requests.jsonl").open("a") as stream:
            stream.write(json.dumps({"model": payload["model"]}) + "\n")
        user = json.loads(payload["input"][1]["content"])
        if "role" in user:
            assert "CriterionRating" in json.dumps(payload["text"]["format"]["schema"])
            assert "No ratings" not in payload["input"][0]["content"]
            assert user["rubric"]["criteria"]
            data = dict(
                schema_version=SCHEMA,
                criteria=missing_review(user["role"], rubric)["criteria"],
                observations=[],
                interpretations=[],
                missing=["Synthetic only"],
            )
        elif "draft" in user:
            assert (
                "independently check every observed criterion"
                in payload["input"][0]["content"]
            )
            assert user["context"]["scoring"]["rubric"]["criteria"]
            data = dict(
                schema_version=SCHEMA,
                verdict=verdict,
                context_id=user["context_id"],
                judged_artifact_hash=user["artifact_hash"],
                findings=[],
                revision_instructions=instructions or [],
            )
        else:
            eid = next(iter(user["context"]["evidence"]))
            section = dict(
                facts=[dict(text="합성 관측", evidence_ids=[eid])],
                interpretation="합성 해석",
                unknown="미확인",
            )
            data = dict(
                schema_version=SCHEMA,
                summary=section,
                company_team=section,
                technology=section,
                market=dict(
                    facts=[],
                    interpretation="합성 시장 해석",
                    unknown="시장 자료 미확인",
                ),
                assessment_risks=section,
                limitations=["Synthetic only"],
            )
        return httpx.Response(
            200,
            json={
                "model": APPROVED_MODEL,
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(data)}],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 30},
            },
        )

    def transport(**kwargs):
        return OpenAIResponsesAttempt(
            **kwargs, http_transport=httpx.MockTransport(respond)
        )

    monkeypatch.setattr(demo, "_retrieve", retrieve)
    monkeypatch.setattr(demo, "OpenAIResponsesAttempt", transport)
    return demo


def test_revision_required_is_explicit_and_never_retries(tmp_path, monkeypatch):
    demo = setup_boundary(
        tmp_path, monkeypatch, verdict="revise", instructions=["Fix narrative"]
    )
    output = demo.run_demo(
        root=tmp_path, company="Physical Intelligence", progress=lambda _: None
    )
    receipt = json.loads((output / "run-result.json").read_text())
    assert receipt["error_code"] == "REPORT_REVISION_REQUIRED", receipt
    assert receipt["status"] == "failed"
    assert len((tmp_path / "requests.jsonl").read_text().splitlines()) == 7
    pipeline = json.loads((output / "report-pipeline.json").read_text())
    assert pipeline["revisions"] == 0
    assert pipeline["judgement"]["verdict"] == "revise"
    assert not (output / "report.pdf").exists()


@pytest.mark.browser
def test_real_demo_llm_mocked_http_to_actual_pdf(tmp_path, monkeypatch):
    demo = setup_boundary(tmp_path, monkeypatch)
    stages = []
    output = demo.run_demo(
        root=tmp_path, company="Physical Intelligence", progress=stages.append
    )
    receipt = json.loads((output / "run-result.json").read_text())
    assert receipt["status"] == "completed", receipt
    assert receipt["campaign"]["calls"] == 7
    assert receipt["campaign"]["reapproval_required"] is False
    assert len(receipt["llm_calls"]) == 7
    assert receipt["publication_allowed"] is False
    assert (output / "report.pdf").read_bytes().startswith(b"%PDF")
    assert "[@evidence:" in (output / "report.md").read_text()
    assert stages == ["local_rag", *demo.ROLES, "generator", "judge", "pdf"]


@pytest.mark.parametrize(
    "verdict,instructions,code",
    [
        ("pass", ["Fix narrative"], "JUDGE_PASS_CONTRADICTORY"),
        ("fail", [], "REPORT_REJECTED"),
    ],
)
def test_judge_failure_is_persisted_and_blocks_next_run(
    tmp_path, monkeypatch, verdict, instructions, code
):
    demo = setup_boundary(
        tmp_path, monkeypatch, verdict=verdict, instructions=instructions
    )
    output = demo.run_demo(
        root=tmp_path, company="Physical Intelligence", progress=lambda _: None
    )
    receipt = json.loads((output / "run-result.json").read_text())
    assert receipt["error_code"] == code
    assert receipt["campaign"]["reapproval_required"] is True
    with pytest.raises(ValueError, match="REAPPROVAL_REQUIRED"):
        demo.run_demo(
            root=tmp_path, company="Physical Intelligence", progress=lambda _: None
        )
    assert len((tmp_path / "requests.jsonl").read_text().splitlines()) == 7


def test_worker_crash_is_failed_not_running_and_budget_stays_spent(
    tmp_path, monkeypatch
):
    import os

    from skala_rag.demo_budget import Campaign

    demo = setup_boundary(tmp_path, monkeypatch)
    monkeypatch.setattr(
        OpenAIResponsesAttempt, "generate_once", lambda *a, **kw: os._exit(17)
    )
    output = demo.run_demo(
        root=tmp_path, company="Physical Intelligence", progress=lambda _: None
    )
    receipt = json.loads((output / "run-result.json").read_text())
    assert receipt["error_code"] == "DEMO_WORKER_EXITED"
    assert receipt["status"] == "failed"
    assert receipt["campaign"]["calls"] == 1
    assert receipt["campaign"]["cost_usd_accounted"] != "0"
    with pytest.raises(ValueError, match="REAPPROVAL_REQUIRED"):
        with Campaign(tmp_path):
            pass


def test_deadline_kills_renderer_descendants(tmp_path, monkeypatch):
    import subprocess
    import sys
    import time
    from datetime import UTC, datetime, timedelta

    from skala_rag.demo_budget import Campaign

    demo = setup_boundary(tmp_path, monkeypatch)
    marker = tmp_path / "descendant-survived"

    def blocked_pdf(*args):
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import pathlib,sys,time;time.sleep(1.2);"
                "pathlib.Path(sys.argv[1]).touch()",
                str(marker),
            ]
        )
        (tmp_path / "renderer-started").touch()
        time.sleep(3)

    monkeypatch.setattr(demo, "build_korean_report_pdf", blocked_pdf)
    with Campaign(tmp_path) as campaign:
        campaign.state["deadline"] = (
            datetime.now(UTC) + timedelta(seconds=0.6)
        ).isoformat()
        campaign.save()
    output = demo.run_demo(
        root=tmp_path, company="Physical Intelligence", progress=lambda _: None
    )
    assert (tmp_path / "renderer-started").exists()
    assert (
        json.loads((output / "run-result.json").read_text())["error_code"]
        == "CAMPAIGN_EXPIRED"
    )
    time.sleep(1.3)
    assert not marker.exists()


@pytest.mark.parametrize("stage", ["retrieval", "model", "pdf"])
def test_deadline_terminates_blocked_operations_and_persists_gate(
    tmp_path, monkeypatch, stage
):
    import os
    import time
    from datetime import UTC, datetime, timedelta

    from skala_rag.demo_budget import Campaign

    demo = setup_boundary(tmp_path, monkeypatch)

    def block(*args, **kwargs):
        (tmp_path / "blocked-pid").write_text(str(os.getpid()))
        time.sleep(2)
        (tmp_path / "escaped-deadline").touch()
        raise ValueError("LOCAL_RETRIEVAL_FAILED")

    if stage == "retrieval":
        monkeypatch.setattr(demo, "_retrieve", block)
    elif stage == "model":
        monkeypatch.setattr(OpenAIResponsesAttempt, "generate_once", block)
    else:
        monkeypatch.setattr(demo, "build_korean_report_pdf", block)
    with Campaign(tmp_path) as campaign:
        campaign.state["deadline"] = (
            datetime.now(UTC) + timedelta(seconds=0.6)
        ).isoformat()
        campaign.save()
    start = time.monotonic()
    output = demo.run_demo(
        root=tmp_path, company="Physical Intelligence", progress=lambda _: None
    )
    assert time.monotonic() - start < 1.5
    receipt = json.loads((output / "run-result.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == "CAMPAIGN_EXPIRED"
    assert receipt["campaign"]["reapproval_required"] is True
    assert receipt["campaign"]["calls"] == {"retrieval": 0, "model": 1, "pdf": 7}[stage]
    assert not (output / "report.pdf").exists()
    assert not (tmp_path / "escaped-deadline").exists()
    pid = int((tmp_path / "blocked-pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    with pytest.raises(ValueError, match="REAPPROVAL_REQUIRED"):
        with Campaign(tmp_path):
            pass
