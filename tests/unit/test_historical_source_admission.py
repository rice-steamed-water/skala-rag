"""Synthetic historical admission across backend, fixture and frozen snapshot."""

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from skala_rag.contracts.bundles import RetrievalBundle
from skala_rag.contracts.inputs import RunInput
from skala_rag.contracts.retrieval import RetrievalRequest
from skala_rag.contracts.sources import Chunk, Source
from skala_rag.contracts.state import create_initial_state
from skala_rag.fakes import FakeClock
from skala_rag.graph.snapshot import SnapshotInvalid, freeze_snapshot
from skala_rag.rag.retrieval import GuardedRetriever
from skala_rag.tools.fixture_retrieve import fixture_search


@pytest.mark.parametrize("path", ["guard", "fixture", "snapshot"])
@pytest.mark.parametrize(
    "published,retrieved,admitted",
    [
        ("2026-05-01", "2026-09-30T00:00:00Z", False),
        ("2026-09-02", "2026-08-01T00:00:00Z", False),
        (None, "2026-09-02T00:00:00Z", False),
        (None, "2026-09-01T23:59:59Z", True),
        ("2026-09-01", "2026-09-01T23:59:59Z", True),
        ("2026-09-01T23:59:59-10:00", "2026-09-01T23:59:59-10:00", True),
    ],
)
def test_historical_admission(path, published, retrieved, admitted):
    payloads = json.loads(
        (Path(__file__).parents[1] / "fixtures/contracts.json").read_text()
    )
    run = RunInput.model_validate(payloads["RunInput"])
    source_payload = {
        **payloads["Source"],
        "published_at": published,
        "retrieved_at": retrieved,
    }
    source = Source.model_validate(source_payload)
    chunk = Chunk.model_validate(payloads["Chunk"])
    request = RetrievalRequest(
        schema_version=run.schema_version,
        query="synthetic historical source",
        candidate_id="co-synthetic",
        corpus_version=run.corpus_version,
        index_version="synthetic-index",
        as_of=run.as_of,
        top_k=1,
        allowed_source_ids=[source.source_id],
    )
    if path != "snapshot":
        bundle = RetrievalBundle(
            schema_version=run.schema_version,
            chunks=[chunk],
            sources={source.source_id: source},
        )
        search = (
            (lambda _: bundle)
            if path == "guard"
            else fixture_search(
                {chunk.chunk_id: chunk},
                bundle.sources,
                index_version=request.index_version,
                schema_version=run.schema_version,
            )
        )
        retriever = GuardedRetriever(
            search,
            run_id="run-synthetic",
            tool_name="synthetic",
            clock=FakeClock(datetime(2026, 9, 1, tzinfo=timezone.utc)),
            schema_version=run.schema_version,
            execution_mode="fixture",
        )
        result = retriever(request)
        assert result.status == (
            "ok" if admitted else "failed" if path == "guard" else "empty"
        )
        if not admitted:
            assert result.retrieval_records[0].chunk_ids == []
            if path == "guard":
                assert retriever.cache_keys == []
                assert "AFTER_AS_OF" in result.errors[0].message_redacted
        return
    state = create_initial_state(run.model_dump(mode="json"))
    state["sources"] = {source.source_id: source_payload}
    state["chunks"] = {chunk.chunk_id: deepcopy(payloads["Chunk"])}
    state["evidence"] = {"ev-synthetic": deepcopy(payloads["Evidence"])}
    record = deepcopy(payloads["RetrievalRecord"])
    record.update(
        status="ok",
        candidate_id="co-synthetic",
        source_ids=[source.source_id],
        chunk_ids=[chunk.chunk_id],
        evidence_ids=["ev-synthetic"],
    )
    state["retrieval_history"] = [record]
    eligibility = deepcopy(payloads["EligibilityResult"])
    eligibility.update(status="eligible", evidence_ids=["ev-synthetic"])
    state["eligibility_results"] = {"co-synthetic": eligibility}
    kwargs = dict(
        run_id="run-synthetic",
        index_version=request.index_version,
        schema_version=run.schema_version,
        allowed_source_ids=request.allowed_source_ids,
        industry_evidence_ids=set(),
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    if admitted:
        assert freeze_snapshot("co-synthetic", state, run, **kwargs).evidence_ids == [
            "ev-synthetic"
        ]
    else:
        with pytest.raises(
            SnapshotInvalid, match="Eligibility evidence invalidated or unavailable"
        ):
            freeze_snapshot("co-synthetic", state, run, **kwargs)
        assert state["snapshots"] == {} and state["evaluation_rounds"] == {}
        assert state["errors"][-1]["error_code"] == "SNAPSHOT_INVALID"
