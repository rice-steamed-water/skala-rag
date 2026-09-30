"""T12·T13 fixture 부분: retrieve 범위·cache 격리와 retrieval → Chunk → Evidence trace.

공통 가상 fixture(#12)만 쓴다. 실제 embedding·index·corpus 검색이 아니다.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from tests.fixtures.loader import load_common_fixtures

from skala_rag.agents.evidence_collector import (
    EvidenceCollector,
    EvidenceExtractionError,
    ExtractedClaim,
    fixture_extract_claims,
)
from skala_rag.contracts import ResearchGap, RetrievalRequest
from skala_rag.contracts.bundles import RetrievalBundle
from skala_rag.contracts.interfaces import CollectEvidence, Retrieve
from skala_rag.contracts.tools import ToolBudget
from skala_rag.fakes import FakeClock
from skala_rag.rag.retrieval import GuardedRetriever, retrieval_cache_key
from skala_rag.scoring.catalog import load_policy
from skala_rag.tools.fixture_retrieve import TOOL_NAME, fixture_search

SCHEMA = "synthetic-common-1"
CORPUS = "corpus-fixture-v1"
INDEX = "index-fixture-v1"
ELIGIBLE = "co-fixture-eligible"
AS_OF = date(2026, 9, 30)


@pytest.fixture
def fx():
    policy = load_policy("configs/scoring.draft.json", execution_mode="fixture")
    data = load_common_fixtures(policy)
    base = data.sources["src-fixture-eligible"]
    chunk = data.chunks["chunk-fixture-eligible"]
    # 기준일 검사용 추가 가상 자료: 미래 발행, 날짜 미상·미래 확보, 산업 공통.
    extra = {
        "src-future": dict(published_at=date(2026, 10, 5)),
        "src-undated-future": dict(
            published_at=None,
            retrieved_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
        ),
        "src-industry": dict(),
    }
    for sid, changes in extra.items():
        data.sources[sid] = base.model_copy(update={"source_id": sid, **changes})
        data.chunks[f"chunk-{sid}"] = chunk.model_copy(
            update={
                "chunk_id": f"chunk-{sid}",
                "source_id": sid,
                "text": f"{sid} technology.integration 가상 관측",
                "scope": "industry" if sid == "src-industry" else "company",
                "candidate_ids": [] if sid == "src-industry" else [ELIGIBLE],
            }
        )
    return data


def make_retriever(fx, search=None):
    return GuardedRetriever(
        search
        or fixture_search(
            fx.chunks, fx.sources, index_version=INDEX, schema_version=SCHEMA
        ),
        run_id="run-test",
        tool_name=TOOL_NAME,
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=timezone.utc), timedelta(1)),
        schema_version=SCHEMA,
        execution_mode="fixture",
    )


def request(**changes):
    payload = dict(
        schema_version=SCHEMA,
        query="가상 로봇 알파 기술 통합",
        candidate_id=ELIGIBLE,
        corpus_version=CORPUS,
        index_version=INDEX,
        as_of=AS_OF,
        top_k=10,
        allowed_source_ids=sorted(
            [
                "src-fixture-eligible",
                "src-fixture-same_name",
                "src-future",
                "src-undated-future",
                "src-industry",
            ]
        ),
    )
    payload.update(changes)
    return RetrievalRequest(**payload)


def gap(criterion_id="technology.integration", queries=("기술 통합",), status="open"):
    return ResearchGap(
        schema_version=SCHEMA,
        gap_id=f"gap:{criterion_id}",
        candidate_id=ELIGIBLE,
        criterion_id=criterion_id,
        missing_fields=["claim"],
        reason="가상 gap",
        suggested_queries=list(queries),
        attempted_retrieval_ids=[],
        status=status,
    )


def make_collector(retriever, extract=fixture_extract_claims, index_version=INDEX):
    return EvidenceCollector(
        retriever,
        extract,
        run_id="run-test",
        corpus_version=CORPUS,
        index_version=index_version,
        as_of=AS_OF,
        top_k=10,
        allowed_source_ids=request().allowed_source_ids,
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=timezone.utc)),
        schema_version=SCHEMA,
        execution_mode="fixture",
    )


BUDGET = ToolBudget(
    schema_version=SCHEMA, max_calls=5, max_retries=0, timeout_seconds=1
)


def test_protocols(fx):
    retriever = make_retriever(fx)
    assert isinstance(retriever, Retrieve)
    assert isinstance(make_collector(retriever), CollectEvidence)


# --- T12: 귀속·출처·기준일 필터, cache 격리 ---


def test_retrieve_applies_company_source_and_as_of(fx):
    result = make_retriever(fx)(request())
    assert result.status == "ok"
    ids = {c.chunk_id for c in result.data.chunks}
    # 다른 기업(same_name), 미래 발행, 날짜 미상·미래 확보는 빠진다.
    assert ids == {"chunk-fixture-eligible", "chunk-src-industry"}
    record = result.retrieval_records[0]
    assert record.chunk_ids == [c.chunk_id for c in result.data.chunks]
    assert record.arguments_without_secrets["as_of"] == "2026-09-30"


def test_later_as_of_admits_later_sources(fx):
    result = make_retriever(fx)(request(as_of=date(2026, 10, 10)))
    ids = {c.chunk_id for c in result.data.chunks}
    assert {"chunk-src-future", "chunk-src-undated-future"} <= ids
    assert "chunk-fixture-same_name" not in ids


def test_unallowed_source_excluded_and_empty_is_not_error(fx):
    result = make_retriever(fx)(request(allowed_source_ids=["src-fixture-ineligible"]))
    assert result.status == "empty"
    assert result.data.chunks == [] and result.errors == []


def test_cache_key_includes_every_scope_field():
    base = retrieval_cache_key(request())
    for changes in [
        dict(query="다른 질의"),
        dict(candidate_id="co-fixture-same_name"),
        dict(corpus_version="corpus-2"),
        dict(index_version="index-2"),
        dict(as_of=date(2026, 9, 1)),
        dict(top_k=3),
        dict(allowed_source_ids=["src-fixture-eligible"]),
    ]:
        assert retrieval_cache_key(request(**changes)) != base, changes
    # 허용 목록 순서는 의미가 없다.
    shuffled = list(reversed(request().allowed_source_ids))
    assert retrieval_cache_key(request(allowed_source_ids=shuffled)) == base


def test_different_as_of_is_a_separate_cache_entry(fx):
    calls = []
    search = fixture_search(
        fx.chunks, fx.sources, index_version=INDEX, schema_version=SCHEMA
    )

    def counting(req):
        calls.append(req.as_of)
        return search(req)

    retriever = make_retriever(fx, counting)
    first = retriever(request())
    again = retriever(request())
    later = retriever(request(as_of=date(2026, 10, 10)))

    assert calls == [AS_OF, date(2026, 10, 10)]
    assert len(retriever.cache_keys) == 2
    assert [r.retrieval_records[0].cache_hit for r in (first, again, later)] == [
        False,
        True,
        False,
    ]
    assert again.data == first.data
    assert later.data != first.data
    ids = {r.retrieval_records[0].retrieval_id for r in (first, again, later)}
    assert len(ids) == 3


@pytest.mark.parametrize(
    "bad_chunk, reason",
    [
        ("chunk-fixture-same_name", "OTHER_COMPANY_CHUNK"),
        ("chunk-src-future", "AFTER_AS_OF"),
        ("chunk-src-undated-future", "AFTER_AS_OF"),
        ("chunk-fixture-ineligible", "SOURCE_NOT_ALLOWED"),
    ],
)
def test_violating_backend_result_is_rejected(fx, bad_chunk, reason):
    def leaky(req):
        hits = [fx.chunks["chunk-fixture-eligible"], fx.chunks[bad_chunk]]
        return RetrievalBundle.model_construct(
            schema_version=SCHEMA,
            chunks=hits,
            sources={c.source_id: fx.sources[c.source_id] for c in hits},
        )

    retriever = make_retriever(fx, leaky)
    result = retriever(request())
    assert result.status == "failed" and result.data is None
    assert result.errors[0].error_code == "TOOL_RESPONSE_INVALID"
    assert reason in result.errors[0].message_redacted
    record = result.retrieval_records[0]
    assert record.chunk_ids == [] and record.error_id == result.errors[0].error_id
    assert retriever.cache_keys == []  # 실패는 cache하지 않는다


def test_unconfigured_index_is_unavailable(fx):
    result = make_retriever(fx)(request(index_version="index-other"))
    assert result.status == "unavailable"
    assert result.errors[0].error_code == "TOOL_NOT_CONFIGURED"


# --- T13: retrieval_id → chunk → rag provenance Evidence ---


def test_collect_evidence_traces_retrieval_to_evidence(fx):
    retriever = make_retriever(fx)
    candidate = fx.candidates[ELIGIBLE]
    result = make_collector(retriever)(candidate, [gap()], BUDGET)

    assert result.status == "ok"
    (record,) = result.retrieval_records
    assert record.tool_name == TOOL_NAME and record.status == "ok"
    evidence = result.data.evidence
    assert evidence and set(record.evidence_ids) == set(evidence)
    for item in evidence.values():
        (path,) = item.provenance
        assert path.method == "rag"
        assert path.retrieval_id == record.retrieval_id
        assert path.chunk_id in record.chunk_ids
        chunk = fx.chunks[path.chunk_id]
        assert item.source_id == chunk.source_id
        assert item.locator == chunk.locator
        assert item.excerpt in chunk.text
        assert item.criterion_ids == ["technology.integration"]
        assert item.source_id in result.data.sources
        if chunk.scope == "company":
            assert item.candidate_id == ELIGIBLE
        else:
            assert item.candidate_id is None and item.scope == "industry"
    # 다른 기업·기준일 이후 자료에서는 Evidence가 생기지 않는다.
    used = {p.chunk_id for e in evidence.values() for p in e.provenance}
    assert used == {"chunk-fixture-eligible", "chunk-src-industry"}


def test_repeat_retrieval_merges_provenance_not_evidence(fx):
    retriever = make_retriever(fx)
    candidate = fx.candidates[ELIGIBLE]
    result = make_collector(retriever)(
        candidate, [gap(queries=("질의 A", "질의 B"))], BUDGET
    )
    first, second = result.retrieval_records
    assert first.retrieval_id != second.retrieval_id
    for item in result.data.evidence.values():
        assert {p.retrieval_id for p in item.provenance} == {
            first.retrieval_id,
            second.retrieval_id,
        }
    assert set(first.evidence_ids) == set(second.evidence_ids)


def test_excerpt_not_in_chunk_is_rejected(fx):
    def invent(chunk, gap):
        return [ExtractedClaim(claim="지어낸 주장", excerpt="원문에 없는 문장")]

    collector = make_collector(make_retriever(fx), invent)
    with pytest.raises(EvidenceExtractionError):
        collector(fx.candidates[ELIGIBLE], [gap()], BUDGET)


def test_retrieval_failure_is_propagated_with_records(fx):
    collector = make_collector(make_retriever(fx), index_version="index-other")
    result = collector(fx.candidates[ELIGIBLE], [gap()], BUDGET)
    assert result.status == "unavailable" and result.data is None
    assert result.errors[0].error_code == "TOOL_NOT_CONFIGURED"
    assert len(result.retrieval_records) == 1


def test_budget_limits_calls_and_zero_budget_fails(fx):
    retriever = make_retriever(fx)
    candidate = fx.candidates[ELIGIBLE]
    gaps = [gap(queries=("a", "b", "c"))]
    two = BUDGET.model_copy(update={"max_calls": 2})
    assert len(make_collector(retriever)(candidate, gaps, two).retrieval_records) == 2

    zero = BUDGET.model_copy(update={"max_calls": 0})
    result = make_collector(retriever)(candidate, gaps, zero)
    assert result.status == "failed"
    assert result.errors[0].error_code == "BUDGET_EXHAUSTED"


def test_closed_or_other_company_gap_is_empty_without_calls(fx):
    def never(req):
        raise AssertionError("retrieve must not be called")

    collector = make_collector(make_retriever(fx, never))
    other = gap().model_copy(update={"candidate_id": "co-fixture-same_name"})
    result = collector(fx.candidates[ELIGIBLE], [gap(status="resolved"), other], BUDGET)
    assert result.status == "empty" and result.retrieval_records == []
