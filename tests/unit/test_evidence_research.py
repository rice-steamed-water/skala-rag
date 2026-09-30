"""#55 단일 Evidence Research: 최초 계획·gap 재조사·도구 필수/선택·예산·trace.

RAG는 #54 IndexedRetriever(가상 backend), 추출은 #50 extract_evidence(가상 LLM)다.
실제 모델·index·API 호출이 아니며 실측 결과가 아니다.
"""

from datetime import date, timedelta

import pytest
from tests.fixtures.evidence_research import (
    SCHEMA,
    LineLLM,
    StubWeb,
    chunk,
    indexed_retriever,
    llm_error,
    transport_failure,
    utc,
    web_page,
)

from skala_rag.agents.evidence_extraction import verify_provenance
from skala_rag.agents.evidence_research import (
    EvidenceResearch,
    ResearchFailure,
    WebChannel,
)
from skala_rag.contracts.candidates import Candidate
from skala_rag.contracts.coverage import ResearchGap
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import CollectEvidence
from skala_rag.contracts.tools import ToolBudget
from skala_rag.fakes import FakeClock

AS_OF = date(2026, 9, 30)
CORPUS, INDEX = "corpus-t", "index-t"
ALPHA, BETA = "co-alpha", "co-beta"
TECH = "알파로보틱스는 2025년 휴머노이드 제어 모델 technology.integration을 공개했다."
REVENUE = "알파로보틱스의 2025년 market.size 매출은 12억원이다."
ODD_UNIT = "알파로보틱스의 2025년 market.size 예약금은 3조각 원이다."
BETA_TECH = "베타로봇은 2024년 technology.integration 물류 로봇을 출시했다."


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("external network forbidden")

    monkeypatch.setattr("socket.socket.connect", denied)


def candidate(cid, name):
    return Candidate(
        schema_version=SCHEMA,
        candidate_id=cid,
        canonical_name=name,
        aliases=[],
        country="KR",
        homepage_url=None,
        legal_identifiers={},
        discovery_source_ids=[],
    )


def gap(cid, criterion, queries, status="open", gap_id=None):
    return ResearchGap(
        schema_version=SCHEMA,
        gap_id=gap_id or f"gap:{cid}:{criterion}",
        candidate_id=cid,
        criterion_id=criterion,
        missing_fields=["claim"],
        reason="가상 gap",
        suggested_queries=list(queries),
        attempted_retrieval_ids=[],
        status=status,
    )


def budget(n):
    return ToolBudget(
        schema_version=SCHEMA, max_calls=n, max_retries=0, timeout_seconds=1
    )


@pytest.fixture
def world():
    clock = FakeClock(utc(2026, 9, 30), timedelta(seconds=1))
    retrieved = utc(2026, 9, 1)
    alpha_raw, alpha = web_page(
        "https://fixture.invalid/alpha",
        f"{TECH}\n{REVENUE}\n{ODD_UNIT}",
        retrieved_at=retrieved,
    )
    _, beta = web_page(
        "https://fixture.invalid/beta", BETA_TECH, retrieved_at=retrieved
    )
    chunks = [
        chunk("c-tech", alpha, TECH, candidate_ids=[ALPHA], corpus=CORPUS),
        chunk(
            "c-fin",
            alpha,
            f"{REVENUE}\n{ODD_UNIT}",
            candidate_ids=[ALPHA],
            corpus=CORPUS,
        ),
        chunk("c-beta", beta, BETA_TECH, candidate_ids=[BETA], corpus=CORPUS),
    ]
    sources = {alpha.source_id: alpha, beta.source_id: beta}
    retriever, backend = indexed_retriever(
        chunks, sources, corpus=CORPUS, index=INDEX, clock=clock
    )
    plans = []

    def plan(c):
        plans.append(c.candidate_id)
        return [
            gap(c.candidate_id, "technology.integration", ["technology.integration"])
        ]

    def build(
        *, llm=None, web=(), rag_required=True, initial_plan=plan, max_bytes=None
    ):
        return EvidenceResearch(
            retrieve=retriever,
            rag_required=rag_required,
            llm=llm or LineLLM(),
            initial_plan=initial_plan,
            web=web,
            run_id="run-synthetic",
            corpus_version=CORPUS,
            index_version=INDEX,
            as_of=AS_OF,
            top_k=5,
            allowed_source_ids=sorted(sources),
            clock=clock,
            schema_version=SCHEMA,
            execution_mode="fixture",
            max_segment_bytes=max_bytes,
        )

    return dict(
        clock=clock,
        alpha=alpha,
        alpha_raw=alpha_raw,
        chunks={c.chunk_id: c for c in chunks},
        backend=backend,
        plans=plans,
        build=build,
        a=candidate(ALPHA, "알파로보틱스"),
        b=candidate(BETA, "베타로봇"),
    )


def test_initial_call_uses_plan_and_real_retrieval_trace(world):
    research = world["build"]()
    assert isinstance(research, CollectEvidence)
    out = research.run(world["a"], [], budget(4))
    assert out.initial and out.status == "ok" and world["plans"] == [ALPHA]
    [item] = out.evidence.values()
    [path] = item.provenance
    record = next(r for r in out.records if r.retrieval_id == path.retrieval_id)
    # 실제 검색 반환 Chunk → rag provenance → 검색 이력의 evidence_ids.
    assert path.method == "rag" and path.chunk_id == "c-tech"
    assert record.chunk_ids == ["c-tech"] and item.evidence_id in record.evidence_ids
    assert record.arguments_without_secrets["research_initial"] is True
    assert item.criterion_ids == ["technology.integration"]
    assert item.candidate_id == ALPHA and item.excerpt == TECH
    assert set(out.chunks) == {"c-tech"} and world["alpha"].source_id in out.sources
    assert (
        verify_provenance(
            item, records={r.retrieval_id: r for r in out.records}, chunks=out.chunks
        )
        == []
    )
    assert out.gaps[0].attempted_retrieval_ids == [record.retrieval_id]


def test_gap_call_uses_only_own_open_gaps(world):
    research = world["build"]()
    gaps = [
        gap(ALPHA, "market.size", ["market.size"]),
        gap(ALPHA, "technology.integration", ["technology.integration"], "resolved"),
        gap(BETA, "technology.integration", ["technology.integration"]),
    ]
    out = research.run(world["a"], gaps, budget(5))
    assert not out.initial and world["plans"] == []
    assert world["backend"].calls == ["market.size"]
    assert [g.gap_id for g in out.gaps] == [f"gap:{ALPHA}:market.size"]
    assert {e.criterion_ids[0] for e in out.evidence.values()} == {"market.size"}


def test_other_candidate_documents_are_isolated(world):
    out = world["build"]().run(world["b"], [], budget(2))
    assert {e.candidate_id for e in out.evidence.values()} == {BETA}
    assert set(out.chunks) == {"c-beta"}


def test_finance_evidence_goes_through_unit_check(world):
    llm = LineLLM(
        {
            REVENUE: dict(
                value=12, unit="억원", currency="KRW", value_as_of="2025-12-31"
            ),
            ODD_UNIT: dict(
                value=3, unit="조각", currency="KRW", value_as_of="2025-12-31"
            ),
        }
    )
    out = world["build"](llm=llm).run(
        world["a"], [gap(ALPHA, "market.size", ["market.size"])], budget(1)
    )
    assert [e.excerpt for e in out.evidence.values()] == [REVENUE]
    assert out.rejected == ["finance:unknown_unit"]
    [record] = out.records
    assert record.evidence_ids == list(out.evidence)


def test_empty_is_not_failure_or_negative_fact(world):
    out = world["build"]().run(
        world["a"], [gap(ALPHA, "moat.ip", ["없는 질의"])], budget(1)
    )
    assert out.status == "empty" and out.evidence == {} and out.errors == []
    assert [r.status for r in out.records] == ["empty"]


def test_budget_is_shared_across_tools_and_stops(world):
    web = StubWeb({}, clock=world["clock"])
    research = world["build"](web=[WebChannel("web", "web", False, web)])
    gaps = [gap(ALPHA, "market.size", ["market.size", "technology.integration"])]
    out = research.run(world["a"], gaps, budget(3))
    # (질의 2 × 도구 2) 중 3회만 실행하고 남은 1회는 실행하지 않는다.
    assert [(c.tool, c.status) for c in out.calls] == [
        ("retrieve", "ok"),
        ("web", "empty"),
        ("retrieve", "ok"),
    ]
    assert out.skipped == 1 and web.calls == ["market.size"]


def test_zero_budget_fails_before_any_call(world):
    out = world["build"]().run(world["a"], [], budget(0))
    assert out.status == "failed" and world["backend"].calls == []
    assert [e.error_code for e in out.errors] == [ErrorCode.BUDGET_EXHAUSTED]


def test_optional_tool_unavailable_is_recorded_not_fatal(world):
    web = StubWeb({}, clock=world["clock"], error=ErrorCode.TOOL_NOT_CONFIGURED)
    out = world["build"](web=[WebChannel("web", "web", False, web)]).run(
        world["a"], [], budget(4)
    )
    assert out.status == "ok" and out.evidence
    [record] = [r for r in out.records if r.tool_name == "web"]
    assert record.status == "unavailable" and record.error_id is not None
    assert record.arguments_without_secrets["research_required"] is False
    assert record.arguments_without_secrets["research_error_codes"] == [
        "TOOL_NOT_CONFIGURED"
    ]


def test_required_tool_failure_stops_batch(world):
    web = StubWeb({}, clock=world["clock"], error=ErrorCode.TOOL_UNAVAILABLE)
    out = world["build"](web=[WebChannel("web", "web", True, web)]).run(
        world["a"],
        [gap(ALPHA, "market.size", ["market.size", "technology.integration"])],
        budget(10),
    )
    assert out.status == "unavailable" and web.calls == ["market.size"]
    assert [e.error_code for e in out.errors] == ["TOOL_UNAVAILABLE"]
    # 실패 전 RAG 결과와 이력은 버리지 않는다.
    assert [r.tool_name for r in out.records] == ["retrieve", "web"]


def test_required_rag_unavailable_keeps_reason(world):
    world["backend"].failure = transport_failure()
    research = world["build"]()
    out = research.run(world["a"], [], budget(2))
    assert out.status == "unavailable"
    assert {e.error_code for e in out.errors} == {"TOOL_UNAVAILABLE"}
    result = world["build"]()(world["a"], [], budget(2))
    assert result.status == "unavailable" and result.data is None


def test_llm_failure_is_not_a_tool_result(world):
    research = world["build"](llm=LineLLM(error=llm_error()))
    out = research.run(world["a"], [], budget(2))
    assert out.status == "failed" and out.evidence == {}
    assert [e.error_code for e in out.errors] == ["LLM_TIMEOUT"]
    with pytest.raises(ResearchFailure):
        research(world["a"], [], budget(2))


def test_web_and_rag_rediscovery_merge_provenance(world):
    raw, alpha = world["alpha_raw"], world["alpha"]
    locator = world["chunks"]["c-tech"].locator
    web = StubWeb(
        {"technology.integration": [(raw, alpha, locator, TECH)]}, clock=world["clock"]
    )
    out = world["build"](web=[WebChannel("web", "web", False, web)]).run(
        world["a"], [], budget(2)
    )
    [item] = out.evidence.values()
    assert sorted(p.method for p in item.provenance) == ["rag", "web"]
    records = {r.retrieval_id: r for r in out.records}
    assert all(
        item.evidence_id in records[p.retrieval_id].evidence_ids
        for p in item.provenance
    )
    assert verify_provenance(item, records=records, chunks=out.chunks) == []


def test_web_source_after_as_of_is_excluded(world):
    raw, late = web_page(
        "https://fixture.invalid/late", TECH, retrieved_at=utc(2026, 10, 2)
    )
    web = StubWeb(
        {"technology.integration": [(raw, late, raw.locator, None)]},
        clock=world["clock"],
    )
    out = world["build"](
        web=[WebChannel("web", "web", False, web)], rag_required=False
    ).run(world["a"], [], budget(2))
    assert late.source_id not in out.sources
    assert "as_of:UNDATED_RETRIEVED_AFTER_AS_OF" in out.rejected


def test_initial_plan_for_other_candidate_is_rejected(world):
    research = world["build"](
        initial_plan=lambda c: [gap(BETA, "technology.integration", ["x"])]
    )
    with pytest.raises(ValueError):
        research.run(world["a"], [], budget(1))


def test_long_segment_is_split_before_extraction(world):
    # c-fin(두 줄)을 줄 단위로 나눠 LLM에 한 줄씩 보낸다(#163).
    llm = LineLLM()
    limit = max(len(REVENUE.encode()), len(ODD_UNIT.encode())) + 1
    research = world["build"](llm=llm, max_bytes=limit)
    out = research.run(
        world["a"], [gap(ALPHA, "market.size", ["market.size"])], budget(1)
    )
    assert llm.calls == 2
    assert sorted(e.excerpt for e in out.evidence.values()) == sorted(
        [REVENUE, ODD_UNIT]
    )
    [record] = out.records
    assert set(record.evidence_ids) == set(out.evidence)
    with pytest.raises(ValueError):
        world["build"](max_bytes=0)
