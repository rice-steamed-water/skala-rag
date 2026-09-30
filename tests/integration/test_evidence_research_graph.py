"""#55 T07·T13·T25(fixture 부분): 후보 Graph collect = 단일 Evidence Research.

실제 LangGraph와 #25 research_gate, #54 IndexedRetriever(가상 backend), #50 추출
(가상 LLM), #21 freeze_snapshot을 쓴다. 자료·평가는 가상이며 live 증거가 아니다.
"""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from tests.fixtures.evidence_research import (
    SCHEMA,
    LineLLM,
    chunk,
    indexed_retriever,
    transport_failure,
    utc,
)
from tests.integration.test_candidate_graph import harness  # noqa: F401

from skala_rag.agents.evidence_research import (
    EvidenceResearch,
    evidence_research_stage,
)
from skala_rag.contracts.coverage import ResearchGap
from skala_rag.contracts.evaluation import EvaluationSnapshot
from skala_rag.contracts.sources import Source
from skala_rag.contracts.state import create_initial_state
from skala_rag.contracts.tools import ToolBudget
from skala_rag.fakes import FakeClock
from skala_rag.graph.snapshot import freeze_snapshot

BASE = json.loads((Path(__file__).parents[1] / "fixtures/contracts.json").read_text())
CORPUS, INDEX = "synthetic-corpus", "synthetic-index"
INITIAL, GAP = "founder.expertise", "technology.integration"
SOURCE = Source.model_validate(
    BASE["Source"], context={"execution_mode": "fixture"}
).model_dump(mode="json")
BUDGET = ToolBudget(
    schema_version=SCHEMA, max_calls=4, max_retries=0, timeout_seconds=1
)


def _gap(cid, criterion, status="open"):
    return ResearchGap(
        schema_version=SCHEMA,
        gap_id=f"gap:{cid}:{criterion}",
        candidate_id=cid,
        criterion_id=criterion,
        missing_fields=["claim"],
        reason="가상 gap",
        suggested_queries=[criterion],
        attempted_retrieval_ids=[],
        status=status,
    )


def _research_node(state):
    """Company Research 가상 결과: 적격성 근거(web) 하나. RAG 경로와 무관하다."""
    cid = state["current_candidate_id"]
    ev = deepcopy(BASE["Evidence"])
    ev.update(evidence_id=f"ev-{cid}", candidate_id=cid, criterion_ids=[])
    ev["provenance"] = [
        dict(
            schema_version=SCHEMA,
            retrieval_id=f"ret-{cid}",
            method="web",
            chunk_id=None,
        )
    ]
    record = deepcopy(BASE["RetrievalRecord"])
    record.update(
        retrieval_id=f"ret-{cid}",
        candidate_id=cid,
        status="ok",
        source_ids=["src-synthetic"],
        evidence_ids=[ev["evidence_id"]],
    )
    return {
        "evidence": {ev["evidence_id"]: ev},
        "retrieval_history": [*state["retrieval_history"], record],
    }


def _coverage(state):
    """가상 Coverage: RAG 근거가 GAP criterion을 덮지 못하면 open gap."""
    cid = state["current_candidate_id"]
    covered = any(
        e["candidate_id"] == cid
        and GAP in e["criterion_ids"]
        and any(p["method"] == "rag" for p in e["provenance"])
        for e in state["evidence"].values()
    )
    gap = _gap(cid, GAP, "resolved" if covered else "open")
    return {"research_gaps": {cid: [gap.model_dump(mode="json")]}}


def _evaluate(original):
    """harness 평가 결과를 현재 snapshot의 evidence_revision에 맞춘다."""

    def evaluate(state):
        cid = state["current_candidate_id"]
        revision = state["evidence_revisions"][cid]
        delta = original(state)
        for result in delta["evaluation_results"].values():
            result["evidence_revision"] = revision
            result["evaluation"]["evidence_revision"] = revision
        return delta

    return evaluate


@pytest.fixture
def research_graph(harness):  # noqa: F811
    make, execute, calls, run, policy = harness
    clock = FakeClock(utc(2026, 9, 1), timedelta(seconds=1))
    source = Source.model_validate(
        BASE["Source"], context={"execution_mode": "fixture"}
    )
    texts = {
        "co-0": [f"Synthetic 0 {INITIAL} 관측", f"Synthetic 0 {GAP} 관측"],
        "co-1": [f"Synthetic 1 {INITIAL} 관측"],
    }
    chunks = [
        chunk(
            f"chunk-{cid}-{i}",
            source,
            text,
            candidate_ids=[cid],
            locator=f"{source.url}#page=1",
            corpus=CORPUS,
        )
        for cid, lines in texts.items()
        for i, text in enumerate(lines)
    ]
    retriever, backend = indexed_retriever(
        chunks, {source.source_id: source}, corpus=CORPUS, index=INDEX, clock=clock
    )
    plans, batches = [], []

    def plan(candidate):
        plans.append(candidate.candidate_id)
        return [_gap(candidate.candidate_id, INITIAL)]

    research = EvidenceResearch(
        retrieve=retriever,
        rag_required=True,
        llm=LineLLM(),
        initial_plan=plan,
        run_id="run-synthetic",
        corpus_version=CORPUS,
        index_version=INDEX,
        as_of=date(2026, 9, 1),
        top_k=5,
        allowed_source_ids=[source.source_id],
        clock=clock,
        schema_version=SCHEMA,
        execution_mode="fixture",
    )
    stage = evidence_research_stage(research, budget=BUDGET)

    def collect(state):
        cid = state["current_candidate_id"]
        batches.append((cid, state["research_retry_count"][cid]))
        return stage(state)

    def build(cases):
        nodes = make(cases)
        discover = nodes.discover

        def normalized(state):
            # Index snapshot과 같은 Source payload로 발견 출처를 저장한다.
            return {**discover(state), "sources": {source.source_id: SOURCE}}

        return replace(
            nodes,
            discover=normalized,
            research=_research_node,
            collect=collect,
            coverage=_coverage,
            evaluate=_evaluate(nodes.evaluate),
        )

    return dict(
        build=build,
        execute=execute,
        backend=backend,
        plans=plans,
        batches=batches,
        run=run,
        limit=policy.budgets.max_research_retries_per_candidate,
    )


def _rag_evidence(result, cid):
    return {
        k: e
        for k, e in result["evidence"].items()
        if e["candidate_id"] == cid and e["provenance"][0]["method"] == "rag"
    }


def test_initial_then_gap_research_resolves_same_candidate(research_graph):
    g = research_graph
    result = g["execute"](g["build"](["recommend"]))
    # 최초 수집은 계획(필수 근거), 재조사 1회는 Coverage gap을 같은 진입점에서 쓴다.
    assert g["batches"] == [("co-0", 0), ("co-0", 1)]
    assert g["plans"] == ["co-0"] and g["backend"].calls == [INITIAL, GAP]
    assert result["research_retry_count"]["co-0"] == 1
    assert result["research_gaps"]["co-0"][0]["status"] == "resolved"
    assert result["evidence_revisions"]["co-0"] == 2
    evidence = _rag_evidence(result, "co-0")
    assert sorted(c for e in evidence.values() for c in e["criterion_ids"]) == [
        INITIAL,
        GAP,
    ]
    # 실제 검색 이력 → Chunk → Evidence → 평가 snapshot까지 이어진다.
    [snapshot] = result["snapshots"].values()
    history = {r["retrieval_id"]: r for r in result["retrieval_history"]}
    for key, item in evidence.items():
        [path] = item["provenance"]
        assert key in snapshot["evidence"]
        assert path["chunk_id"] in snapshot["chunks"]
        assert key in history[path["retrieval_id"]]["evidence_ids"]
    gaps = [
        r["arguments_without_secrets"].get("research_gap_id") for r in history.values()
    ]
    assert f"gap:co-0:{GAP}" in gaps and f"gap:co-0:{INITIAL}" in gaps
    assert result["candidate_outcomes"]["co-0"]["status"] == "recommend"


def test_unresolved_gap_exhausts_at_limit_without_revision_change(research_graph):
    g = research_graph
    result = g["execute"](g["build"](["watchlist", "recommend"]))
    limit = g["limit"]
    # co-1은 GAP 자료가 없어 empty 재조사만 한도까지 소비한다(요청 전 차감).
    assert [b for b in g["batches"] if b[0] == "co-1"] == [
        ("co-1", n) for n in range(limit + 1)
    ]
    assert result["research_retry_count"] == {"co-0": 1, "co-1": limit}
    assert result["research_gaps"]["co-1"][0]["status"] == "exhausted"
    # empty 재조사는 부정 사실이 아니며 Evidence·revision을 바꾸지 않는다.
    assert result["evidence_revisions"]["co-1"] == 1
    assert list(_rag_evidence(result, "co-1")) and all(
        GAP not in e["criterion_ids"] for e in _rag_evidence(result, "co-1").values()
    )
    # 후보 A의 사용량·이력은 B와 섞이지 않고 보존된다.
    by_candidate = {r["candidate_id"] for r in result["retrieval_history"]}
    assert by_candidate == {"co-0", "co-1"}
    assert result["candidate_outcomes"]["co-1"]["status"] == "recommend"


def test_required_rag_failure_initial_fails_candidate(research_graph):
    g = research_graph
    g["backend"].failure = transport_failure()
    result = g["execute"](g["build"](["recommend"]))
    assert result["candidate_outcomes"]["co-0"]["status"] == "failed"
    assert result["research_retry_count"]["co-0"] == 0
    assert {e["error_code"] for e in result["errors"]} == {"TOOL_UNAVAILABLE"}


def test_required_rag_failure_on_retry_is_spent_not_fatal(research_graph):
    g = research_graph
    backend = g["backend"]
    original = backend.search_once

    def fail_on_gap(request, **kwargs):
        if request.query == GAP:
            raise transport_failure()
        return original(request, **kwargs)

    backend.search_once = fail_on_gap
    result = g["execute"](g["build"](["recommend"]))
    assert result["research_retry_count"]["co-0"] == g["limit"]
    assert result["research_gaps"]["co-0"][0]["status"] == "exhausted"
    assert result["candidate_outcomes"]["co-0"]["status"] == "recommend"
    assert result["evidence_revisions"]["co-0"] == 1


def test_new_evidence_does_not_mutate_frozen_snapshot(research_graph):
    """T25: 이전 snapshot은 불변이고 새 근거는 새 세대에서만 보인다."""
    g = research_graph
    nodes = g["build"](["recommend"])
    run = g["run"]
    state = create_initial_state(run.model_dump(mode="json"))
    state["candidates"] = [c for c in nodes.discover(state)["candidates"]][:1]
    state["sources"] = {"src-synthetic": SOURCE}
    state.update(
        current_candidate_id="co-0",
        research_retry_count={"co-0": 0},
        evidence_revisions={"co-0": 0},
        evaluation_rounds={"co-0": 0},
    )
    state.update(_research_node(state))
    state.update(nodes.eligibility(state))

    def apply(delta):
        state["evidence"] = {**state["evidence"], **delta.get("evidence", {})}
        state["chunks"] = {**state["chunks"], **delta.get("chunks", {})}
        state["sources"] = {**state["sources"], **delta.get("sources", {})}
        state["retrieval_history"] = delta["retrieval_history"]
        state["evidence_revisions"].update(delta.get("evidence_revisions", {}))

    def freeze():
        return freeze_snapshot(
            "co-0",
            state,
            run,
            run_id="run-synthetic",
            index_version=INDEX,
            schema_version="synthetic-1",
            allowed_source_ids={"src-synthetic"},
            industry_evidence_ids=set(),
            clock=lambda: utc(2026, 9, 1),
        )

    apply(nodes.collect(state))
    first = freeze()
    frozen = first.model_dump(mode="json")
    state["evaluation_rounds"]["co-0"] = first.evaluation_round
    state["research_retry_count"]["co-0"] = 1
    state["research_gaps"] = {"co-0": [_gap("co-0", GAP).model_dump(mode="json")]}
    apply(nodes.collect(state))
    second = freeze()
    assert first.model_dump(mode="json") == frozen
    assert EvaluationSnapshot.model_validate(
        frozen, context={"execution_mode": "fixture"}
    )
    assert second.evidence_revision == first.evidence_revision + 1
    assert set(first.evidence) < set(second.evidence)
    assert second.snapshot_id != first.snapshot_id
