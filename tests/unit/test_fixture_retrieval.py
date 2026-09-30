"""T12/T13 fixture 검색 격리와 실제 반환 Chunk의 provenance 연결."""

import itertools
from datetime import datetime, timezone
from pathlib import Path

import pytest
from tests.fixtures.loader import load_common_fixtures

from skala_rag.contracts import RetrievalBundle, RetrievalRequest, ToolBudget
from skala_rag.contracts.interfaces import CollectEvidence, Retrieve
from skala_rag.fakes import FakeClock, FakeTool
from skala_rag.rag.fixture import FixtureRetriever, cache_key, validate_bundle
from skala_rag.scoring.catalog import load_policy
from skala_rag.tools.fixture_collector import FixtureEvidenceCollector

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def data():
    return load_common_fixtures(
        load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
    )


@pytest.fixture
def clock():
    return FakeClock(datetime(2026, 9, 30, tzinfo=timezone.utc))


def request(data, **updates):
    payload = dict(
        schema_version="synthetic-1",
        query="가상 로봇 기술",
        candidate_id=data.cases["eligible"],
        corpus_version="corpus-fixture-v1",
        index_version="index-fixture-v1",
        as_of="2026-09-30",
        top_k=4,
        allowed_source_ids=list(data.sources),
    )
    payload.update(updates)
    return RetrievalRequest.model_validate(payload)


def retriever(data, clock):
    counter = itertools.count()
    bundle = RetrievalBundle.model_validate(
        dict(
            schema_version="synthetic-1",
            chunks=list(data.chunks.values()),
            sources=data.sources,
        ),
        context={"execution_mode": "fixture"},
    )
    return FixtureRetriever(
        bundle,
        index_version="index-fixture-v1",
        run_id="run-test",
        schema_version="synthetic-1",
        clock=clock,
        retrieval_id_factory=lambda: f"retrieval-test-{next(counter)}",
        execution_mode="fixture",
    )


def collector(data, clock, retrieve, req=None, extractor=None, chunk_store=None):
    def extract(chunk):
        return [
            e.model_copy(deep=True)
            for e in data.evidence.values()
            if any(p.chunk_id == chunk.chunk_id for p in e.provenance)
        ]

    return FixtureEvidenceCollector(
        retrieve=retrieve,
        chunk_store={} if chunk_store is None else chunk_store,
        request_builder=lambda candidate, gaps: [req or request(data)],
        extract=extractor or extract,
        run_id="run-test",
        schema_version="synthetic-1",
        clock=clock,
        error_id_factory=lambda: "error-test",
        execution_mode="fixture",
    )


def budget(calls=1):
    return ToolBudget(
        schema_version="synthetic-1", max_calls=calls, max_retries=0, timeout_seconds=1
    )


def test_candidate_filter_and_protocol(data, clock):
    retrieve = retriever(data, clock)
    assert isinstance(retrieve, Retrieve)
    result = retrieve(request(data))
    assert result.status == "ok"
    assert len(result.data.chunks) == 1
    assert result.data.chunks[0].candidate_ids == [data.cases["eligible"]]
    assert result.retrieval_records[0].cache_hit is False


def test_as_of_cache_separation_and_empty(data, clock):
    retrieve = retriever(data, clock)
    old = request(data, as_of="2026-09-28")
    current = request(data)
    assert retrieve(old).status == "empty"
    assert retrieve(current).status == "ok"
    assert len(retrieve.cache) == 2
    again = retrieve(current)
    assert again.retrieval_records[0].cache_hit is True
    assert again.retrieval_records[0].retrieval_id == "retrieval-test-2"


@pytest.mark.parametrize(
    "updates",
    [
        {"query": "다른 질문"},
        {"candidate_id": "co-other"},
        {"corpus_version": "different"},
        {"index_version": "different"},
        {"allowed_source_ids": []},
        {"as_of": "2026-09-29"},
        {"top_k": 1},
    ],
)
def test_cache_key_has_all_dimensions(data, updates):
    assert cache_key(request(data)) != cache_key(request(data, **updates))


def test_allowed_source_set_order_and_cache_copy(data, clock):
    original = request(data)
    reversed_ids = request(
        data, allowed_source_ids=list(reversed(original.allowed_source_ids))
    )
    assert cache_key(original) == cache_key(reversed_ids)
    retrieve = retriever(data, clock)
    result = retrieve(original)
    result.data.chunks[0].text = "외부 변경"
    assert retrieve(reversed_ids).data.chunks[0].text != "외부 변경"
    assert next(iter(data.chunks.values())).text != "외부 변경"


def test_source_and_zero_top_k_filter(data, clock):
    retrieve = retriever(data, clock)
    assert retrieve(request(data, allowed_source_ids=[])).status == "empty"
    assert retrieve(request(data, top_k=0)).status == "empty"
    with pytest.raises(ValueError, match="index"):
        retrieve(request(data, index_version="wrong"))


def test_collector_provenance_and_record_trace(data, clock):
    retrieve = retriever(data, clock)
    chunks = {}
    collect = collector(data, clock, retrieve, chunk_store=chunks)
    assert isinstance(collect, CollectEvidence)
    result = collect(data.candidates[data.cases["eligible"]], [], budget())
    assert result.status == "ok"
    assert len(result.data.evidence) == 23
    record = result.retrieval_records[0]
    assert set(record.evidence_ids) == set(result.data.evidence)
    for evidence in result.data.evidence.values():
        path = evidence.provenance[0]
        assert path.method == "rag"
        assert path.retrieval_id == record.retrieval_id
        assert path.chunk_id in record.chunk_ids
        assert chunks[path.chunk_id].source_id == evidence.source_id
        assert evidence.source_id in record.source_ids
    again = collect(data.candidates[data.cases["eligible"]], [], budget())
    assert again.retrieval_records[0].cache_hit is True
    assert set(again.data.evidence) == set(result.data.evidence)
    assert (
        next(iter(again.data.evidence.values())).provenance[0].retrieval_id
        != record.retrieval_id
    )


def test_budget_exhaustion_prevents_search(data, clock):
    retrieve = retriever(data, clock)
    result = collector(data, clock, retrieve)(
        data.candidates[data.cases["eligible"]], [], budget(0)
    )
    assert result.status == "failed"
    assert result.errors[0].error_code == "BUDGET_EXHAUSTED"
    assert retrieve.cache == {}
    assert result.retrieval_records == []


def test_out_of_scope_injected_result_rejected(data, clock):
    returned = retriever(data, clock)(
        request(data, candidate_id=data.cases["same_name"])
    )
    fake = FakeTool([returned])
    result = collector(data, clock, fake)(
        data.candidates[data.cases["eligible"]], [], budget()
    )
    assert result.status == "failed"
    assert result.errors[0].error_code == "TOOL_RESPONSE_INVALID"
    assert result.data is None
    assert len(result.retrieval_records) == 1


def test_bad_extracted_evidence_rejected(data, clock):
    foreign = next(
        e for e in data.evidence.values() if e.candidate_id == data.cases["same_name"]
    )
    collect = collector(
        data, clock, retriever(data, clock), extractor=lambda chunk: [foreign]
    )
    result = collect(data.candidates[data.cases["eligible"]], [], budget())
    assert result.status == "failed"
    assert result.errors[0].error_code == "TOOL_RESPONSE_INVALID"
    assert result.retrieval_records[0].evidence_ids == []


def test_unknown_publication_date_is_not_invented(data, clock):
    data.sources[next(iter(data.sources))].published_at = None
    assert retriever(data, clock)(request(data)).status == "empty"


def test_injected_future_source_rejected(data, clock):
    bundle = retriever(data, clock)(request(data)).data
    next(iter(bundle.sources.values())).published_at = datetime(
        2026, 10, 1, tzinfo=timezone.utc
    )
    with pytest.raises(ValueError):
        validate_bundle(bundle, request(data))


def test_extraction_failure_stays_technical_failure(data, clock):
    def broken(chunk):
        raise RuntimeError("가상 추출 실패")

    collect = collector(data, clock, retriever(data, clock), extractor=broken)
    result = collect(data.candidates[data.cases["eligible"]], [], budget())
    assert result.status == "failed"
    assert result.errors[0].error_code == "TOOL_FAILED"
    assert len(result.retrieval_records) == 1
    assert result.data is None


def test_industry_claim_cannot_replace_company_scope(data, clock):
    item = next(iter(data.evidence.values())).model_copy(deep=True)
    item.scope = "industry"
    item.candidate_id = None
    collect = collector(
        data, clock, retriever(data, clock), extractor=lambda chunk: [item]
    )
    result = collect(data.candidates[data.cases["eligible"]], [], budget())
    assert result.status == "failed"
    assert result.errors[0].error_code == "TOOL_RESPONSE_INVALID"
