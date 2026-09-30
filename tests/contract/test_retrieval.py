"""Caller retrieval observations; no tool execution or selection."""

import pytest
from pydantic import ValidationError

import skala_rag.contracts as contracts


@pytest.mark.parametrize("name", ["RetrievalRequest", "RetrievalRecord"])
def test_retrieval_roundtrip(name, payloads):
    assert hasattr(contracts, name), f"{name} missing"
    cls = getattr(contracts, name)
    model = cls.model_validate(payloads[name])
    assert cls.model_validate_json(model.model_dump_json()) == model


@pytest.mark.parametrize("status", ["ok", "empty", "unavailable", "failed"])
def test_retrieval_status_is_not_inferred(status, payloads):
    assert hasattr(contracts, "RetrievalRecord"), "record missing"
    model = contracts.RetrievalRecord.model_validate(
        {**payloads["RetrievalRecord"], "status": status}
    )
    assert model.status == status
    assert model.cost is model.error_id is model.candidate_id is None


def test_retrieval_cost_preserves_zero_and_context(payloads):
    assert hasattr(contracts, "RetrievalRecord"), "record missing"
    data = {**payloads["RetrievalRecord"], "cost": payloads["MonetaryObservation"]}
    model = contracts.RetrievalRecord.model_validate(data)
    assert model.cost.value == 0
    assert (
        contracts.RetrievalRecord.model_validate_json(model.model_dump_json()) == model
    )


@pytest.mark.parametrize(
    "name,field,value",
    [
        ("RetrievalRequest", "top_k", -1),
        ("RetrievalRequest", "top_k", True),
        ("RetrievalRequest", "query", " "),
        ("RetrievalRequest", "allowed_source_ids", [""]),
        ("RetrievalRecord", "status", "success"),
        ("RetrievalRecord", "started_at", "2026-09-01T12:00:00"),
        ("RetrievalRecord", "finished_at", "2026-09-01T12:00:00"),
        ("RetrievalRecord", "arguments_without_secrets", {"client": object()}),
        ("RetrievalRecord", "arguments_without_secrets", {"number": float("inf")}),
        ("RetrievalRecord", "cache_hit", "false"),
        ("RetrievalRecord", "cost", 0),
        ("RetrievalRecord", "cost", {"schema_version": "synthetic-1", "value": 0}),
    ],
)
def test_retrieval_rejections(name, field, value, payloads):
    assert hasattr(contracts, name), f"{name} missing"
    with pytest.raises(ValidationError):
        getattr(contracts, name).model_validate({**payloads[name], field: value})
