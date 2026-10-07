"""#219: public synthetic demo, original artifacts and real offline PDF."""

import hashlib
import json
import re
import runpy
import socket
from pathlib import Path

import pytest
from pypdf import PdfReader


def test_offline_demo_preserves_public_results_and_pdf(tmp_path, monkeypatch):
    attempts = []

    def deny(*args, **kwargs):
        attempts.append(True)
        pytest.fail("offline demo attempted network/DNS")

    for target, name in (
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (socket, "create_connection"),
        (socket, "getaddrinfo"),
    ):
        monkeypatch.setattr(target, name, deny)
    demo = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "examples/v3_offline_demo.py")
    )
    # Fixture settings must resolve even when the caller is outside the checkout.
    monkeypatch.chdir(tmp_path)
    out = demo["run_offline_demo"](tmp_path / "fresh")
    assert Path.cwd() == tmp_path
    manifest = json.loads((out / "demo-manifest.json").read_text())
    assert manifest["execution_mode"] == "fixture"
    assert not manifest["final_allowed"] and not manifest["publication_allowed"]
    assert manifest["usage"] is None
    assert manifest["observed_calls"] == {
        "fixture_backend": 46,
        "fixture_evaluator": 10,
        "fixture_generator": 1,
        "fixture_judge": 1,
        "product": 0,
    }
    for artifact in manifest["artifacts"]:
        path = out / artifact["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
    result = json.loads((out / "candidates.json").read_text())
    context = json.loads((out / "context.json").read_text())
    report = json.loads((out / "report.json").read_text())
    events = json.loads((out / "graph-events.json").read_text())
    assert result["status"] == "ready_for_v3_reporting"
    assert result["candidate_index"] == 2 and set(result["scores"]) == {"co-0", "co-1"}
    assert not result["errors"]
    joins = [
        u["evaluation_join"]["data"]
        for ns, u in events
        if not ns and "evaluation_join" in u
    ]
    assert len(joins) == 2
    for cid, score in result["scores"].items():
        original = result["research_artifacts"][cid]["state"]["snapshots"][
            score["snapshot_id"]
        ]
        assert original == context["snapshots"][cid]
        join = next(j for j in joins if j["snapshot"]["candidate_id"] == cid)
        assert join["snapshot"] == original
        assert len(join["evaluated"]["evaluations_v3"]) == 6
    context_dto = json.loads((out / "context-dto.json").read_text())
    assert json.loads(context_dto["payload"]) == context
    assert context_dto["context_id"] == report["context_id"]
    assert report["status"] == "completed" and not report["warning"]
    assert report["validation"]["valid"] and report["pdf_validation"]["valid"]
    assert not report["final_allowed"]
    markdown = (out / "report-demo.md").read_text()
    assert markdown == report["draft"]["markdown"]
    assert re.findall(r"^## (.+)$", markdown, re.M) == [
        "SUMMARY",
        "COMPANY & TEAM",
        "TECHNOLOGY",
        "MARKET",
        "INVESTMENT ASSESSMENT & RISKS",
        "REFERENCE",
    ]
    pdf = out / manifest["pdf"]["path"]
    assert pdf.read_bytes().startswith(b"%PDF-")
    pages = PdfReader(pdf).pages
    assert len(pages) == manifest["pdf"]["page_count"] <= 5
    assert manifest["pdf"]["summary_fraction"] <= 0.5
    text = "\n".join(p.extract_text() for p in pages)
    for eid in report["draft"]["cited_evidence_ids"]:
        assert f"[@evidence:{eid}]" in text
    for sid in report["draft"]["reference_source_ids"]:
        assert f"[@source:{sid}]" in text
    assert "가상" in text
    assert json.loads((out / "trace.json").read_text())
    assert attempts == []


def test_offline_demo_refuses_existing_directory(tmp_path):
    demo = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "examples/v3_offline_demo.py")
    )
    marker = tmp_path / "keep.txt"
    marker.write_text("original")
    with pytest.raises(FileExistsError):
        demo["run_offline_demo"](tmp_path)
    assert marker.read_text() == "original"
    assert list(tmp_path.iterdir()) == [marker]
