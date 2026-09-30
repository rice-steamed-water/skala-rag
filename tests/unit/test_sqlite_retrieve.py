"""Mock HF query HTTP + temporary SQLite + Retrieve; no live inference."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from tests.unit.test_hf_index import api_setup
from tests.unit.test_index_v3 import FakeEmbedder, chunk, corpus, document, source
from tests.unit.test_text_index_review import reviewed

from skala_rag.contracts import RetrievalRequest, ToolBudget
from skala_rag.fakes import FakeClock
from skala_rag.rag.adapter import IndexedRetriever
from skala_rag.rag.corpus import manifest_hash
from skala_rag.rag.dense import snapshot_from_plan
from skala_rag.rag.hf_embedding import HFEmbeddingEncoder
from skala_rag.rag.index_v3 import build_index_plan, write_index
from skala_rag.rag.query_hf import HFQueryEncoder
from skala_rag.rag.sqlite_index import SQLiteIndexStore
from skala_rag.rag.sqlite_retrieve import SQLiteDenseSearch
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    BudgetLedger,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("external network forbidden")

    monkeypatch.setattr("socket.socket.connect", reject)


def runtime():
    return AdapterRuntime(
        policy=RuntimePolicy(
            schema_version="synthetic-1",
            execution_mode="fixture",
            retry_delays_seconds=(),
            live_approval_reference=None,
            timing_approval_reference=None,
        ),
        ledger=BudgetLedger(
            RuntimeLimits(
                schema_version="synthetic-1",
                max_calls=2,
                tool_max_calls={"retrieve": 2},
                max_input_tokens=0,
                max_output_tokens=0,
                max_cost_usd=Decimal(1),
            )
        ),
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=UTC)),
        sleep=lambda _: None,
    )


def retrieve(snapshot, backend, run):
    return IndexedRetriever(
        snapshot=snapshot,
        backend=backend,
        runtime=run,
        readiness=Readiness(
            schema_version="synthetic-1",
            required=True,
            configured=True,
            credential_required=True,
            credential_present=True,
            model_required=True,
            model_available=True,
            index_required=True,
            index_available=True,
        ),
        budget=ToolBudget(
            schema_version="synthetic-1", max_calls=1, max_retries=0, timeout_seconds=1
        ),
        allowance=Allowance(
            schema_version="synthetic-1",
            input_tokens=0,
            output_tokens=0,
            max_cost_usd=Decimal("0.1"),
        ),
        run_id="synthetic-run",
        schema_version="synthetic-1",
        tool_name="retrieve",
    )


@pytest.fixture
def prepared(tmp_path):
    deployment, config = api_setup()
    docs = (document(), document("b", candidate_ids=("other",)))
    manifest = corpus(*docs)
    plan = build_index_plan(
        manifest=manifest,
        expected_corpus_hash=manifest_hash(manifest),
        sources={d.source_id: source(d) for d in docs},
        chunks=[chunk(d) for d in docs],
        settings=config,
    )
    path = tmp_path / "synthetic.sqlite"
    write_index(plan, embedder=FakeEmbedder(), store=SQLiteIndexStore(path, plan=plan))
    store = SQLiteIndexStore(path)
    snapshot = snapshot_from_plan(
        plan,
        manifest=manifest,
        reopened_metadata=store.read_metadata(plan.metadata.index_version),
        search_settings={"metric": "cosine"},
        execution_mode="fixture",
    )
    request = RetrievalRequest(
        schema_version="synthetic-1",
        query="synthetic robot",
        candidate_id="co-a",
        corpus_version=snapshot.corpus_version,
        index_version=snapshot.index_version,
        as_of="2026-09-30",
        top_k=1,
        allowed_source_ids=list(snapshot.bundle.sources),
    )
    return deployment, config, store, plan, snapshot, request


def test_http_query_reopened_sqlite_retrieve_cache(prepared):
    deployment, _, store, plan, snapshot, request = prepared
    calls = []

    def handler(req):
        calls.append(req)
        assert json.loads(req.content) == {
            "inputs": [request.query],
            "normalize": True,
            "truncate": False,
        }
        assert req.extensions["timeout"]["read"] == 1
        return httpx.Response(200, json=[[3, 4]])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        encoder = HFQueryEncoder(
            HFEmbeddingEncoder(
                deployment=deployment,
                token="synthetic-token",
                client=client,
                timeout_seconds=10,
            )
        )
        backend = SQLiteDenseSearch(
            store=store, metadata=plan.metadata, snapshot=snapshot, encoder=encoder
        )
        run = runtime()
        adapter = retrieve(snapshot, backend, run)
        first = adapter(request)
        assert first.status == "ok"
        assert first.data.chunks[0].chunk_id == "chunk-a"
        assert first.data.chunks[0].page_start == 1
        assert adapter(request).retrieval_records[0].cache_hit
        assert len(calls) == run.ledger.snapshot()["calls"] == 1
        assert "synthetic-token" not in first.model_dump_json()
        assert request.query not in first.retrieval_records[0].model_dump_json()


@pytest.mark.parametrize(
    "code,expected",
    [
        (401, "TOOL_AUTH_FAILED"),
        (403, "TOOL_AUTH_FAILED"),
        (429, "TOOL_RATE_LIMITED"),
        (503, "TOOL_UNAVAILABLE"),
    ],
)
def test_hf_status_survives_to_runtime(prepared, code, expected):
    deployment, _, store, plan, snapshot, req = prepared
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(code, text="secret response")
        )
    ) as client:
        backend = SQLiteDenseSearch(
            store=store,
            metadata=plan.metadata,
            snapshot=snapshot,
            encoder=HFQueryEncoder(
                HFEmbeddingEncoder(
                    deployment=deployment,
                    token="synthetic-token",
                    client=client,
                    timeout_seconds=10,
                )
            ),
        )
        result = retrieve(snapshot, backend, runtime())(req)
        assert result.errors[0].error_code == expected
        assert "secret response" not in result.model_dump_json()


@pytest.mark.parametrize("timeout", [True, False])
def test_hf_timeout_and_schema_rejected(prepared, timeout):
    deployment, _, store, plan, snapshot, req = prepared

    def handler(request):
        if timeout:
            raise httpx.ReadTimeout("private", request=request)
        return httpx.Response(200, json=[[0, 0]])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        backend = SQLiteDenseSearch(
            store=store,
            metadata=plan.metadata,
            snapshot=snapshot,
            encoder=HFQueryEncoder(
                HFEmbeddingEncoder(
                    deployment=deployment,
                    token="synthetic-token",
                    client=client,
                    timeout_seconds=10,
                )
            ),
        )
        result = retrieve(snapshot, backend, runtime())(req)
        assert result.errors[0].error_code == (
            "TOOL_TIMEOUT" if timeout else "TOOL_RESPONSE_INVALID"
        )


def test_missing_store_prevents_http(prepared):
    deployment, _, store, plan, snapshot, req = prepared
    store.path.unlink()

    def forbidden(_):
        raise AssertionError("missing index cannot call API")

    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        backend = SQLiteDenseSearch(
            store=store,
            metadata=plan.metadata,
            snapshot=snapshot,
            encoder=HFQueryEncoder(
                HFEmbeddingEncoder(
                    deployment=deployment,
                    token="synthetic-token",
                    client=client,
                    timeout_seconds=10,
                )
            ),
        )
        run = runtime()
        assert retrieve(snapshot, backend, run)(req).status == "unavailable"
        assert run.ledger.snapshot()["calls"] == 0


def test_text_only_bridge_requires_extraction_and_preserves_limits():
    doc, c, extracted = reviewed()
    from tests.unit.test_text_index_review import plan as reviewed_plan

    p = reviewed_plan(doc, c, extracted)
    kwargs = dict(
        manifest=corpus(doc),
        reopened_metadata=p.metadata,
        search_settings={"metric": "cosine"},
        execution_mode="fixture",
        source_inputs={doc.source_id: source(doc)},
    )
    with pytest.raises(ValueError, match="actual extraction"):
        snapshot_from_plan(p, **kwargs)
    snapshot = snapshot_from_plan(
        p, extraction_results={doc.document_id: extracted}, **kwargs
    )
    assert "text_only" in snapshot.bundle.sources[doc.source_id].access_notes
    assert snapshot.bundle.sources[doc.source_id].bibliographic_metadata[
        "text_index_review"
    ]["limitations"]
    assert doc.extraction_status == "partial"


def test_store_removed_after_cached_success_is_not_served(prepared):
    deployment, _, store, plan, snapshot, req = prepared
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=[[3, 4]])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        backend = SQLiteDenseSearch(
            store=store,
            metadata=plan.metadata,
            snapshot=snapshot,
            encoder=HFQueryEncoder(
                HFEmbeddingEncoder(
                    deployment=deployment,
                    token="synthetic-token",
                    client=client,
                    timeout_seconds=10,
                )
            ),
        )
        run = runtime()
        adapter = retrieve(snapshot, backend, run)
        assert adapter(req).status == "ok"
        store.path.unlink()
        result = adapter(req)
        assert result.status == "unavailable"
        assert result.data is None
        assert len(calls) == run.ledger.snapshot()["calls"] == 1
