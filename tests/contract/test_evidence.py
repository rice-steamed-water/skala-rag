"""Evidence syntax, without factual verification, merging, or derivation."""

import pytest
from pydantic import ValidationError

import skala_rag.contracts as contracts


@pytest.mark.parametrize("name", ["Evidence", "EvidenceProvenance"])
def test_evidence_roundtrip(name, payloads):
    assert hasattr(contracts, name), f"{name} missing"
    cls = getattr(contracts, name)
    model = cls.model_validate(payloads[name])
    assert cls.model_validate_json(model.model_dump_json()) == model


def test_evidence_missing_context_stays_unknown(payloads):
    assert hasattr(contracts, "Evidence"), "Evidence missing"
    model = contracts.Evidence.model_validate(payloads["Evidence"])
    assert model.value is model.unit is model.currency is model.value_as_of is None
    assert model.period is model.geography is model.event_date is None
    assert model.derivation is model.supersedes is None


@pytest.mark.parametrize("method", ["web", "api", "manual"])
@pytest.mark.parametrize("chunk", [None, " chunk-exact "])
def test_nonrag_chunk_is_preserved(method, chunk, payloads):
    assert hasattr(contracts, "EvidenceProvenance"), "provenance missing"
    model = contracts.EvidenceProvenance.model_validate(
        {**payloads["EvidenceProvenance"], "method": method, "chunk_id": chunk}
    )
    assert model.chunk_id == chunk


@pytest.mark.parametrize(
    "field,value",
    [
        ("chunk_id", None),
        ("chunk_id", " "),
        ("method", "search"),
    ],
)
def test_rag_provenance_rejections(field, value, payloads):
    assert hasattr(contracts, "EvidenceProvenance"), "provenance missing"
    with pytest.raises(ValidationError):
        contracts.EvidenceProvenance.model_validate(
            {**payloads["EvidenceProvenance"], field: value}
        )


def test_evidence_money_has_its_own_basis_date(payloads):
    assert hasattr(contracts, "Evidence"), "Evidence missing"
    data = {
        **payloads["Evidence"],
        "value": 0,
        "unit": "million",
        "currency": "USD",
        "value_as_of": "2026-08-01",
        "event_date": "2026-07-01",
    }
    model = contracts.Evidence.model_validate(data)
    assert model.value == 0
    assert model.value_as_of.isoformat() == "2026-08-01"
    assert contracts.Evidence.model_validate_json(model.model_dump_json()) == model
    for field in ("value", "unit", "currency", "value_as_of"):
        invalid = {**data, field: None}
        with pytest.raises(ValidationError):
            contracts.Evidence.model_validate(invalid)


def test_distinct_explicit_money_basis_dates_survive_roundtrip(payloads):
    restored_payloads = []
    for basis_date in ("2026-08-01", "2026-09-01"):
        data = {
            **payloads["Evidence"],
            "value": 0,
            "unit": "million",
            "currency": "USD",
            "value_as_of": basis_date,
        }
        model = contracts.Evidence.model_validate(data)
        restored = contracts.Evidence.model_validate_json(model.model_dump_json())
        assert restored == model
        assert restored.value_as_of is not None
        assert restored.value_as_of.isoformat() == basis_date
        assert restored.evidence_id == data["evidence_id"]
        restored_payloads.append(restored.model_dump(mode="json"))
    assert restored_payloads[0] != restored_payloads[1]
    for data in restored_payloads:
        del data["value_as_of"]
    assert restored_payloads[0] == restored_payloads[1]


@pytest.mark.parametrize(
    "field,value",
    [
        ("value", float("nan")),
        ("value", float("inf")),
        ("evidence_kind", "confirmed"),
        ("scope", "global"),
        ("confidence", "certain"),
        ("source_id", " "),
        ("event_date", "2026-09-01T00:00:00Z"),
    ],
)
def test_evidence_rejections(field, value, payloads):
    assert hasattr(contracts, "Evidence"), "Evidence missing"
    with pytest.raises(ValidationError):
        contracts.Evidence.model_validate({**payloads["Evidence"], field: value})


def test_nested_fixture_locator_context(payloads):
    assert hasattr(contracts, "Evidence"), "Evidence missing"
    data = {**payloads["Evidence"], "locator": "fixture://synthetic#page=1"}
    with pytest.raises(ValidationError):
        contracts.Evidence.model_validate(data)
    contracts.Evidence.model_validate(data, context={"execution_mode": "fixture"})
