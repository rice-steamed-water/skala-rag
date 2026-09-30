"""#62 offline boundary integration; every source, model and rating is synthetic."""

import socket
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from tests.fixtures.loader import load_common_fixtures

from skala_rag.agents.evaluation import evaluate_dimension
from skala_rag.agents.evidence_extraction import (
    SegmentError,
    extract_evidence,
    link_record,
    rag_segment,
    verify_provenance,
)
from skala_rag.contracts import RetrievalRequest, RunInput
from skala_rag.contracts.state import create_initial_state
from skala_rag.fakes import FakeClock, FakeLLM
from skala_rag.graph.snapshot import SnapshotInvalid, freeze_snapshot
from skala_rag.rag.retrieval import GuardedRetriever
from skala_rag.scoring.catalog import load_policy
from skala_rag.tools.fixture_retrieve import fixture_search

SCHEMA = "synthetic-common-1"
CID = "co-fixture-eligible"
NOW = datetime(2026, 9, 30, tzinfo=UTC)


@pytest.fixture
def trace(monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("offline M2 boundary test attempted network access")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setenv("SKALA_FAKE_API_KEY", "sk-synthetic-secret-never-forward")
    policy = load_policy("configs/scoring.draft.json", execution_mode="fixture")
    fx = load_common_fixtures(policy)
    candidate = fx.candidates[CID]
    chunk = fx.chunks["chunk-fixture-eligible"]
    text = f"{candidate.canonical_name}의 로봇 통합이 가상 환경에서 확인됐다."
    chunk = chunk.model_copy(update={"text": text})
    src = fx.sources[chunk.source_id]
    retrieve = GuardedRetriever(
        fixture_search(
            {chunk.chunk_id: chunk},
            {src.source_id: src},
            index_version="index-fixture-v1",
            schema_version=SCHEMA,
        ),
        run_id="run-62",
        tool_name="fixture-retrieve",
        clock=FakeClock(NOW),
        schema_version=SCHEMA,
        execution_mode="fixture",
    )
    result = retrieve(
        RetrievalRequest(
            schema_version=SCHEMA,
            query="로봇 통합",
            candidate_id=CID,
            corpus_version=chunk.corpus_version,
            index_version="index-fixture-v1",
            as_of=NOW.date(),
            top_k=1,
            allowed_source_ids=[src.source_id],
        )
    )
    assert result.status == "ok"
    record = result.retrieval_records[0]
    segment = rag_segment(
        result.data.chunks[0], result.data, record, schema_version=SCHEMA
    )
    llm = FakeLLM(
        [
            {
                "claims": [
                    {
                        "claim": text,
                        "excerpt": text,
                        "subject": candidate.canonical_name,
                    }
                ]
            }
        ]
    )
    extraction = extract_evidence(
        segment,
        llm=llm,
        candidate=candidate,
        criterion_ids=["technology.integration"],
        as_of=NOW.date(),
        schema_version=SCHEMA,
        execution_mode="fixture",
    )
    linked = link_record(record, extraction, segment)
    run = RunInput(
        schema_version=SCHEMA,
        investment_theme="가상 로봇",
        countries=["KR"],
        languages=["ko"],
        as_of=NOW.date(),
        policy_version=policy.policy_version,
        corpus_version=chunk.corpus_version,
        execution_mode="fixture",
    )
    state = create_initial_state(run.model_dump(mode="json"))
    state["sources"] = {src.source_id: src.model_dump(mode="json")}
    state["chunks"] = {chunk.chunk_id: chunk.model_dump(mode="json")}
    state["evidence"] = {
        k: e.model_dump(mode="json") for k, e in extraction.evidence.items()
    }
    state["retrieval_history"] = [linked.model_dump(mode="json")]
    state["evidence_revisions"] = {CID: 1}
    eligibility = next(
        e for e in fx.eligibility_results.values() if e.candidate_id == CID
    )
    # Synthetic admission only; this does not establish real eligibility.
    state["eligibility_results"] = {
        CID: eligibility.model_copy(
            update={
                "run_id": "run-62",
                "evidence_ids": list(extraction.evidence),
            }
        ).model_dump(mode="json")
    }
    kwargs = dict(
        run_id="run-62",
        index_version="index-fixture-v1",
        schema_version=SCHEMA,
        allowed_source_ids=[src.source_id],
        industry_evidence_ids=[],
        clock=lambda: NOW,
    )
    return state, run, kwargs, policy, llm, result


def evaluate(snapshot, policy, evidence_id):
    criteria = []
    for c in policy.criteria:
        if c.dimension != "technology":
            continue
        observed = c.criterion_id == "technology.integration"
        criteria.append(
            dict(
                criterion_id=c.criterion_id,
                status="observed" if observed else "missing",
                rating=3 if observed else None,
                evidence_ids=[evidence_id] if observed else [],
                rationale="가상 판단",
                missing_reason=None if observed else "가상 자료 없음",
            )
        )
    llm = FakeLLM([{"criteria": criteria}])
    result = evaluate_dimension(
        "technology",
        snapshot,
        {"rubric_version": "fixture-62"},
        llm=llm,
        policy=policy,
        clock=FakeClock(NOW),
        schema_version=SCHEMA,
        max_repairs=0,
    )
    return result, llm


def test_retrieval_extraction_freeze_and_technology_citation(trace):
    state, run, kwargs, policy, extractor, retrieval = trace
    snapshot = freeze_snapshot(CID, state, run, **kwargs)
    (eid,) = snapshot.evidence
    item = snapshot.evidence[eid]
    assert (
        verify_provenance(
            item, records=snapshot.retrieval_records, chunks=snapshot.chunks
        )
        == []
    )
    evaluation, evaluator = evaluate(snapshot, policy, eid)
    assert evaluation.status == "success"
    assert evaluation.evaluation.snapshot_id == snapshot.snapshot_id
    used = next(
        c
        for c in evaluation.evaluation.criteria
        if c.criterion_id == "technology.integration"
    )
    assert used.evidence_ids == [eid]
    path = item.provenance[0]
    assert path.retrieval_id == retrieval.retrieval_records[0].retrieval_id
    chunk = snapshot.chunks[path.chunk_id]
    assert chunk.source_id == item.source_id and chunk.page_start == chunk.page_end == 1
    assert chunk.locator == item.locator and item.excerpt in chunk.text
    saved = deepcopy(state["snapshots"])
    state["evidence"][eid]["claim"] = "후속 변경"
    assert state["snapshots"] == saved and snapshot.evidence[eid].claim != "후속 변경"
    for llm in (extractor, evaluator):
        assert len(llm.calls) == 1
        assert "sk-synthetic-secret-never-forward" not in str(llm.calls)


@pytest.mark.parametrize(
    "mutation", ["unlinked", "other_company", "future", "changed_chunk"]
)
def test_broken_trace_cannot_freeze_or_evaluate(trace, mutation):
    state, run, kwargs, _, _, _ = trace
    (eid,) = state["evidence"]
    if mutation == "unlinked":
        state["retrieval_history"][0]["evidence_ids"] = []
    elif mutation == "other_company":
        state["chunks"]["chunk-fixture-eligible"]["candidate_ids"] = ["other-company"]
    elif mutation == "future":
        next(iter(state["sources"].values()))["retrieved_at"] = "2026-10-01T00:00:00Z"
    else:
        state["chunks"]["chunk-fixture-eligible"]["text"] = "다른 원문"
    with pytest.raises(SnapshotInvalid):
        freeze_snapshot(CID, state, run, **kwargs)
    assert state["snapshots"] == {} and state["evaluation_rounds"] == {}
    assert state["errors"][-1]["error_code"] == "SNAPSHOT_INVALID"


def test_forged_chunk_and_postfreeze_citation_are_rejected(trace):
    state, run, kwargs, policy, _, retrieval = trace
    forged = retrieval.data.chunks[0].model_copy(update={"text": "조작된 원문"})
    with pytest.raises(SegmentError):
        rag_segment(
            forged,
            retrieval.data,
            retrieval.retrieval_records[0],
            schema_version=SCHEMA,
        )
    snapshot = freeze_snapshot(CID, state, run, **kwargs)
    result, _ = evaluate(snapshot, policy, "evidence-invented")
    assert result.status == "failure" and result.evaluation is None


def test_live_m2_trace_not_available():
    pytest.skip(
        "#62 실제 경로 미연결: 승인 corpus/index 및 #54/#55/#57 필요; "
        "fixture로 대체하지 않음"
    )
