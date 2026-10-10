"""#55 live smoke 스크립트의 흐름만 가상 index로 확인한다.

#145 산출물(모델·SQLite index) 로딩은 #54 경로 그대로라 여기서는 가상
IndexedRetriever로 바꿔 끼운다. 실제 검색·LLM 결과가 아니다.
"""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from tests.fixtures.evidence_research import (
    LineLLM,
    chunk,
    indexed_retriever,
    utc,
    web_page,
)

from skala_rag.fakes import FakeClock
from skala_rag.rag import evidence_research_validation as smoke
from skala_rag.settings import load_runtime_document
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt

CID = smoke.CANDIDATE_ID
TECH = "Physical Intelligence trains technology.maturity flow models on robots."
RELIABILITY = "Physical Intelligence reports technology.reliability across tasks."


@pytest.fixture
def patched(monkeypatch, tmp_path):
    _, source = web_page(
        "https://fixture.invalid/pi0",
        f"{TECH}\n{RELIABILITY}",
        retrieved_at=utc(2026, 9, 1),
    )
    chunks = [
        chunk("c-tech", source, TECH, candidate_ids=[CID], corpus="corpus-live"),
        chunk("c-rel", source, RELIABILITY, candidate_ids=[CID], corpus="corpus-live"),
    ]
    retriever, backend = indexed_retriever(
        chunks,
        {source.source_id: source},
        corpus="corpus-live",
        index="index-live",
        clock=FakeClock(utc(2026, 9, 30), timedelta(milliseconds=1)),
        run_id=smoke.RUN_ID,
    )
    snapshot = SimpleNamespace(corpus_version="corpus-live", index_version="index-live")
    runtime = retriever._runtime

    def fake_retriever(*args, **kwargs):
        return (
            retriever,
            snapshot,
            {source.source_id: source},
            runtime,
            SimpleNamespace(token_lengths=[]),
        )

    monkeypatch.setattr(smoke, "_retriever", fake_retriever)
    (tmp_path / "outputs").mkdir()
    return tmp_path, backend


def run(root, **changes):
    return smoke.run(
        root=root,
        model_path=root,
        store_path=root,
        receipt_path=root,
        output_dir=changes.get("output_dir", root / "outputs/issue55"),
        timeout_seconds=changes.get("timeout_seconds", 60),
        llm=changes.get("llm", "none"),
        initial_criterion=changes.get("initial_criterion", "technology.maturity"),
        initial_query=changes.get("initial_query", "technology.maturity"),
        gap_criterion=changes.get("gap_criterion", "technology.reliability"),
        gap_query=changes.get("gap_query", "technology.reliability"),
        top_k=changes.get("top_k", 2),
        max_segment_bytes=changes.get("max_segment_bytes", 4000),
        runtime_document=changes.get("runtime_document"),
    )


def test_no_llm_mode_traces_retrieval_without_evidence(patched):
    root, backend = patched
    summary = run(root)
    assert summary["batches"] == [
        ("initial", "no_evidence"),
        ("gap_retry", "no_evidence"),
    ]
    assert backend.calls == ["technology.maturity", "technology.reliability"]
    report = json.loads((root / "outputs/issue55/validation.json").read_text())
    assert report["llm_calls"] == 2
    assert [(r["initial"], r["gap_id"]) for r in report["records"]] == [
        (True, "initial:technology.maturity"),
        (False, "gap:technology.reliability"),
    ]


def test_extraction_trace_reaches_evidence(patched, monkeypatch):
    root, _ = patched
    monkeypatch.setattr(smoke, "NoClaimsLLM", LineLLM)
    monkeypatch.setattr(LineLLM, "calls", 0, raising=False)
    summary = run(root)
    assert summary["batches"] == [("initial", "ok"), ("gap_retry", "ok")]
    report = json.loads((root / "outputs/issue55/validation.json").read_text())
    assert [b["evidence_revision"] for b in report["batches"]] == [1, 2]
    for key, item in report["evidence"].items():
        [path] = item["provenance"]
        record = next(
            r for r in report["records"] if r["retrieval_id"] == path["retrieval_id"]
        )
        assert path["method"] == "rag" and key in record["evidence_ids"]
        assert path["chunk_id"] in [c[0] for c in record["chunk_pages"]]


@pytest.mark.parametrize(
    "changes", [dict(top_k=5), dict(timeout_seconds=0), dict(llm="openai")]
)
def test_rejects_unbounded_or_unconfigured_runs(patched, monkeypatch, changes):
    root, _ = patched
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError):
        run(root, **changes)


def test_refuses_output_outside_outputs(patched):
    root, _ = patched
    with pytest.raises(ValueError):
        run(root, output_dir=root / "elsewhere")


def test_validation_composition_loads_once_for_retrieval_and_no_claims(
    patched, monkeypatch
):
    root, _ = patched
    document = load_runtime_document()
    loads = []

    def load():
        loads.append(True)
        return document

    monkeypatch.setattr(smoke, "load_runtime_document", load)
    result = run(root)
    assert result["evidence"] == 0
    assert loads == [True]


def test_openai_validation_uses_snapshot_limits_and_common_pricing():
    document = load_runtime_document()
    profile = document.profiles.m2_evidence_validation.model_copy(
        update={"request_output_tokens": 100, "max_calls": 2, "timeout_seconds": 10.0}
    )
    document = document.model_copy(
        update={
            "profiles": document.profiles.model_copy(
                update={"m2_evidence_validation": profile}
            )
        }
    )
    clock = FakeClock(utc(2026, 9, 30))
    llm = smoke._openai_llm("synthetic-key", clock, runtime_document=document)
    bound = llm.allowance_for("s", "u", smoke.ExtractionOutput)

    assert bound.output_tokens == 100
    assert bound.max_cost_usd == (
        bound.input_tokens * document.llm.usd_per_input_token
        + 100 * document.llm.usd_per_output_token
    )
    assert llm.budget.timeout_seconds == 10
    assert isinstance(llm.transport, OpenAIResponsesAttempt)
    assert llm.transport.llm_settings is document.llm
