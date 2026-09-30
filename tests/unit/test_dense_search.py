"""Synthetic 2D vectors verify ranking/plan integration, not BGE-M3 quality."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.unit.test_index_v3 import chunk, corpus, document, settings, source

from skala_rag.contracts import RetrievalRequest, ToolBudget
from skala_rag.fakes import FakeClock
from skala_rag.rag.adapter import IndexedRetriever
from skala_rag.rag.corpus import manifest_hash
from skala_rag.rag.dense import DenseVectorSearch, QueryVector, snapshot_from_plan
from skala_rag.rag.index_v3 import EmbeddingVector, build_index_plan
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    BudgetLedger,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
    TransportFailure,
)


class Encoder:
    retry_owner = "runtime"

    def __init__(self):
        self.calls = []
        self.vector = QueryVector(
            settings().model_id, settings().model_revision, (1.0, 0.0)
        )

    def encode_once(self, query, *, settings, timeout_seconds):
        self.calls.append((query, settings.snapshot(), timeout_seconds))
        return self.vector


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("no network in synthetic dense search")

    monkeypatch.setattr("socket.socket.connect", reject)


@pytest.fixture
def inputs():
    a, b, c = document(), document("b"), document("c", candidate_ids=("other",))
    manifest = corpus(a, b, c)
    plan = build_index_plan(
        manifest=manifest,
        expected_corpus_hash=manifest_hash(manifest),
        sources={d.source_id: source(d) for d in (a, b, c)},
        chunks=[chunk(d) for d in (a, b, c)],
        settings=settings(),
    )
    snapshot = snapshot_from_plan(
        plan,
        manifest=manifest,
        reopened_metadata=plan.metadata,
        search_settings={"metric": "cosine"},
        execution_mode="fixture",
    )
    vectors = [
        EmbeddingVector(
            "chunk-a", settings().model_id, settings().model_revision, (0.0, 1.0)
        ),
        EmbeddingVector(
            "chunk-b", settings().model_id, settings().model_revision, (0.6, 0.8)
        ),
        EmbeddingVector(
            "chunk-c", settings().model_id, settings().model_revision, (1.0, 0.0)
        ),
    ]
    encoder = Encoder()
    backend = DenseVectorSearch(
        snapshot=snapshot, vectors=vectors, encoder=encoder, execution_mode="fixture"
    )
    return manifest, plan, snapshot, vectors, encoder, backend


def request(snapshot, **updates):
    return RetrievalRequest(
        schema_version="synthetic-1",
        query="synthetic query",
        candidate_id="co-a",
        corpus_version=snapshot.corpus_version,
        index_version=snapshot.index_version,
        **(
            {
                "as_of": "2026-09-30",
                "top_k": 1,
                "allowed_source_ids": list(snapshot.bundle.sources),
            }
            | updates
        ),
    )


def search(inputs, *, ids=("chunk-a", "chunk-b"), **updates):
    _, _, snapshot, _, _, backend = inputs
    return backend.search_once(
        request(snapshot, **updates),
        snapshot=snapshot,
        allowed_chunk_ids=ids,
        timeout_seconds=2.5,
    )


def test_rank_after_filter_excludes_globally_best_other_company(inputs):
    result = search(inputs)
    assert [c.chunk_id for c in result.chunks] == ["chunk-b"]
    assert result.chunks[0].page_start == 1
    assert set(result.sources) == {"src-b"}
    encoder = inputs[4]
    assert encoder.calls == [("synthetic query", settings().snapshot(), 2.5)]


def test_top_k_and_stable_tie_order(inputs):
    _, _, snapshot, vectors, encoder, _ = inputs
    vectors[1] = replace(vectors[1], values=(0.0, 1.0))
    inputs = (
        *inputs[:-1],
        DenseVectorSearch(
            snapshot=snapshot,
            vectors=vectors,
            encoder=encoder,
            execution_mode="fixture",
        ),
    )
    assert [
        c.chunk_id for c in search(inputs, ids=("chunk-b", "chunk-a"), top_k=2).chunks
    ] == ["chunk-a", "chunk-b"]


@pytest.mark.parametrize("options", [dict(ids=()), dict(top_k=0)])
def test_no_query_encoding_when_no_search_work(inputs, options):
    assert search(inputs, **options).chunks == []
    assert inputs[4].calls == []


@pytest.mark.parametrize(
    "change",
    [
        dict(model_id="other"),
        dict(model_revision="other"),
        dict(values=(1.0,)),
        dict(values=(float("nan"), 0.0)),
        dict(values=(0.0, 0.0)),
    ],
)
def test_invalid_query_vector_rejected(inputs, change):
    inputs[4].vector = replace(inputs[4].vector, **change)
    with pytest.raises(TransportFailure) as exc:
        search(inputs)
    assert exc.value.code.value == "TOOL_RESPONSE_INVALID"


@pytest.mark.parametrize(
    "change",
    [
        dict(model_id="other"),
        dict(model_revision="other"),
        dict(values=(1.0,)),
        dict(values=(float("inf"), 0.0)),
        dict(values=(0.0, 0.0)),
    ],
)
def test_invalid_reopened_vector_rejected(inputs, change):
    _, _, snapshot, vectors, encoder, _ = inputs
    vectors[0] = replace(vectors[0], **change)
    with pytest.raises(ValueError):
        DenseVectorSearch(
            snapshot=snapshot,
            vectors=vectors,
            encoder=encoder,
            execution_mode="fixture",
        )
    assert encoder.calls == []


def test_missing_duplicate_vector_and_reopened_metadata(inputs):
    _, _, snapshot, vectors, encoder, _ = inputs
    for invalid in (vectors[:-1], [*vectors, vectors[0]]):
        with pytest.raises(ValueError):
            DenseVectorSearch(
                snapshot=snapshot,
                vectors=invalid,
                encoder=encoder,
                execution_mode="fixture",
            )


@pytest.mark.parametrize(
    "field", ["index_version", "model_revision", "dimension", "corpus_hash"]
)
def test_readback_metadata_mismatch(inputs, field):
    manifest, plan, *_ = inputs
    metadata = replace(plan.metadata, **{field: 7 if field == "dimension" else "wrong"})
    with pytest.raises(ValueError, match="identity"):
        snapshot_from_plan(
            plan,
            manifest=manifest,
            reopened_metadata=metadata,
            search_settings={"metric": "cosine"},
            execution_mode="fixture",
        )


def test_forged_plan_and_partial_manifest_rejected(inputs):
    manifest, plan, *_ = inputs
    forged = replace(plan, metadata=replace(plan.metadata, index_version="forged"))
    with pytest.raises(ValueError):
        snapshot_from_plan(
            forged,
            manifest=manifest,
            reopened_metadata=forged.metadata,
            search_settings={"metric": "cosine"},
            execution_mode="fixture",
        )
    rejected = manifest.model_copy(
        update={"documents": (document(extraction_status="partial"),)}
    )
    with pytest.raises(ValueError):
        snapshot_from_plan(
            plan,
            manifest=rejected,
            reopened_metadata=plan.metadata,
            search_settings={"metric": "cosine"},
            execution_mode="fixture",
        )


def test_replaced_search_snapshot_rejected_before_encoder(inputs):
    snapshot = inputs[2].model_copy(deep=True)
    snapshot.bundle.chunks[0].text = "replaced"
    with pytest.raises(TransportFailure):
        inputs[5].search_once(
            request(snapshot),
            snapshot=snapshot,
            allowed_chunk_ids=("chunk-a",),
            timeout_seconds=1,
        )
    assert inputs[4].calls == []


def test_returned_mutation_and_extreme_vectors(inputs):
    _, _, snapshot, vectors, encoder, _ = inputs
    vectors[0] = replace(vectors[0], values=(1e308, 1e308))
    encoder.vector = replace(encoder.vector, values=(1e-300, 1e-300))
    backend = DenseVectorSearch(
        snapshot=snapshot, vectors=vectors, encoder=encoder, execution_mode="fixture"
    )
    args = dict(snapshot=snapshot, allowed_chunk_ids=("chunk-a",), timeout_seconds=1)
    result = backend.search_once(request(snapshot), **args)
    result.chunks[0].text = "external mutation"
    assert (
        backend.search_once(request(snapshot), **args).chunks[0].text
        != "external mutation"
    )


@pytest.mark.parametrize(
    "options",
    [dict(ids=("chunk-c",)), dict(allowed_source_ids=[]), dict(as_of="2026-08-01")],
)
def test_direct_backend_scope_cannot_bypass_request(inputs, options):
    with pytest.raises(TransportFailure):
        search(inputs, **options)
    assert inputs[4].calls == []


def test_plan_dense_retrieve_runtime_cache_end_to_end(inputs):
    _, _, snapshot, _, encoder, backend = inputs
    runtime = AdapterRuntime(
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
                max_calls=1,
                tool_max_calls={"retrieve": 1},
                max_input_tokens=0,
                max_output_tokens=0,
                max_cost_usd=Decimal(1),
            )
        ),
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=UTC)),
        sleep=lambda _: None,
    )
    retrieve = IndexedRetriever(
        snapshot=snapshot,
        backend=backend,
        runtime=runtime,
        readiness=Readiness(
            schema_version="synthetic-1",
            required=True,
            configured=True,
            credential_required=False,
            credential_present=False,
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
    first = retrieve(request(snapshot))
    assert first.status == "ok"
    assert [c.chunk_id for c in first.data.chunks] == ["chunk-b"]
    assert first.retrieval_records[0].source_ids == ["src-b"]
    assert first.retrieval_records[0].chunk_ids == ["chunk-b"]
    assert retrieve(request(snapshot)).retrieval_records[0].cache_hit
    empty = retrieve(request(snapshot, allowed_source_ids=[]))
    assert empty.status == "empty"
    assert not empty.retrieval_records[0].cache_hit
    assert len(encoder.calls) == runtime.ledger.snapshot()["calls"] == 1
    # A distinct actual query cannot bypass exhausted physical request budget.
    other = request(snapshot).model_copy(update={"query": "other query"})
    assert retrieve(other).errors[0].error_code == "BUDGET_EXHAUSTED"
    assert len(encoder.calls) == 1
