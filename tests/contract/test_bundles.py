"""Synthetic bundle closure, not snapshot closure or live collection."""

import pytest
from pydantic import ValidationError

import skala_rag.contracts as contracts


@pytest.mark.parametrize(
    "name,items,item_name",
    [
        ("DiscoveryBundle", "candidates", "Candidate"),
        ("RetrievalBundle", "chunks", "Chunk"),
    ],
)
def test_bundle_payload_closure_roundtrip(name, items, item_name, payloads):
    assert hasattr(contracts, name), f"{name} missing"
    cls = getattr(contracts, name)
    data = dict(
        schema_version="synthetic-1", sources={"src-synthetic": payloads["Source"]}
    )
    data[items] = [payloads[item_name]]
    model = cls.model_validate(data)
    assert cls.model_validate_json(model.model_dump_json()) == model
    empty = cls.model_validate(
        dict(schema_version="synthetic-1", sources={}, **{items: []})
    )
    assert empty.sources == {}


@pytest.mark.parametrize(
    "name,items,item_name",
    [
        ("DiscoveryBundle", "candidates", "Candidate"),
        ("RetrievalBundle", "chunks", "Chunk"),
    ],
)
@pytest.mark.parametrize(
    "fault", ["missing", "key_mismatch", "invalid_payload", "id_only"]
)
def test_bundle_rejects_unresolved_source(name, items, item_name, fault, payloads):
    assert hasattr(contracts, name), f"{name} missing"
    source = payloads["Source"]
    sources = {"src-synthetic": source}
    if fault == "missing":
        sources = {}
    elif fault == "key_mismatch":
        source["source_id"] = "different-source"
    elif fault == "invalid_payload":
        del source["title"]
    else:
        sources["src-synthetic"] = "src-synthetic"
    with pytest.raises(ValidationError):
        getattr(contracts, name).model_validate(
            dict(
                schema_version="synthetic-1",
                sources=sources,
                **{items: [payloads[item_name]]},
            )
        )


@pytest.mark.parametrize(
    "name,items", [("DiscoveryBundle", "candidates"), ("RetrievalBundle", "chunks")]
)
def test_bundle_context_propagates_to_sources(name, items, payloads):
    assert hasattr(contracts, name), f"{name} missing"
    payloads["Source"]["url"] = "fixture://synthetic"
    data = dict(
        schema_version="synthetic-1",
        sources={"src-synthetic": payloads["Source"]},
        **{items: []},
    )
    cls = getattr(contracts, name)
    with pytest.raises(ValidationError):
        cls.model_validate(data)
    model = cls.model_validate(data, context={"execution_mode": "fixture"})
    assert (
        cls.model_validate_json(
            model.model_dump_json(), context={"execution_mode": "fixture"}
        )
        == model
    )
