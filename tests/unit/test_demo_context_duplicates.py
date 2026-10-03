"""Offline provenance collision regressions; no live provider calls."""

import pytest
from tests.unit.test_local_demo import retrieval_fixture

from skala_rag.demo_context import build_research_context, research_material


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("as_dict", [False, True])
def test_conflicting_retrieval_ids_are_rejected(tmp_path, reverse, as_dict):
    bundle, records = retrieval_fixture(tmp_path)
    records.append(
        records[0].model_copy(
            update={"status": "empty", "chunk_ids": [], "source_ids": []}
        )
    )
    if reverse:
        records.reverse()
    if as_dict:
        bundle = bundle.model_dump(mode="json")
        records = [record.model_dump(mode="json") for record in records]
    with pytest.raises(ValueError, match="RETRIEVAL_ID_CONFLICT"):
        research_material(
            root=tmp_path, bundle=bundle, records=records, run_id="test-run"
        )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("as_dict", [False, True])
@pytest.mark.parametrize("field,value", [("text", "Conflicting text"), ("page_end", 2)])
def test_conflicting_chunk_ids_are_rejected(tmp_path, reverse, as_dict, field, value):
    bundle, records = retrieval_fixture(tmp_path)
    bundle.chunks.append(bundle.chunks[0].model_copy(update={field: value}))
    if reverse:
        bundle.chunks.reverse()
    if as_dict:
        bundle = bundle.model_dump(mode="json")
    with pytest.raises(ValueError, match="CHUNK_ID_CONFLICT"):
        research_material(
            root=tmp_path, bundle=bundle, records=records, run_id="test-run"
        )


def test_identical_duplicates_export_one_closed_provenance_path(tmp_path):
    bundle, records = retrieval_fixture(tmp_path)
    bundle.chunks.append(bundle.chunks[0].model_copy(deep=True))
    records.append(records[0].model_copy(deep=True))
    material = research_material(
        root=tmp_path, bundle=bundle, records=records, run_id="test-run"
    )
    exported = build_research_context(
        run_id="test-run", material=material, reviews={}
    ).snapshot()
    trace = exported["research_trace"]
    assert len(exported["evidence"]) == len(trace["chunks"]) == 1
    assert len(trace["retrieval_records"]) == 1
    for evidence in exported["evidence"].values():
        assert len(evidence["provenance"]) == 1
        for path in evidence["provenance"]:
            record = trace["retrieval_records"][path["retrieval_id"]]
            chunk = trace["chunks"][path["chunk_id"]]
            assert record["status"] == "ok"
            assert chunk["chunk_id"] in record["chunk_ids"]
            assert evidence["source_id"] == chunk["source_id"]
            assert chunk["source_id"] in record["source_ids"]
            assert chunk["source_id"] in exported["sources"]
            assert evidence["excerpt"] == chunk["text"]
            assert evidence["locator"] == chunk["locator"]


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("retrieval_records", "status", "empty"),
        ("retrieval_records", "chunk_ids", []),
        ("retrieval_records", "source_ids", []),
        ("retrieval_records", "retrieval_id", "other"),
        ("retrieval_records", "run_id", "other"),
        ("chunks", "chunk_id", "other"),
        ("chunks", "source_id", "other"),
        ("chunks", "text", "overwritten"),
        ("chunks", "locator", "overwritten"),
        ("sources", "source_id", "other"),
        ("evidence", "provenance", []),
    ],
)
def test_export_rechecks_final_provenance(tmp_path, target, field, value):
    bundle, records = retrieval_fixture(tmp_path)
    material = research_material(
        root=tmp_path, bundle=bundle, records=records, run_id="test-run"
    )
    next(iter(material[target].values()))[field] = value
    with pytest.raises(ValueError, match="RESEARCH_PROVENANCE_INVALID"):
        build_research_context(run_id="test-run", material=material, reviews={})
