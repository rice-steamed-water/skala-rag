"""Synthetic inputs; no live readiness or investment-policy approval."""

import pytest
from pydantic import ValidationError

import skala_rag.contracts as contracts


def input_payload():
    return dict(
        schema_version="synthetic-1",
        investment_theme=" Robotics ",
        countries=["KR"],
        languages=["ko"],
        as_of="2026-09-01",
        policy_version="unapproved-fixture",
        corpus_version="synthetic-corpus",
        execution_mode="fixture",
    )


def test_input_structural_roundtrip():
    assert hasattr(contracts, "RunInput"), "RunInput contract not implemented"
    model = contracts.RunInput.model_validate(input_payload())
    assert model.investment_theme == " Robotics "
    assert model.as_of.isoformat() == "2026-09-01"
    assert contracts.RunInput.model_validate_json(model.model_dump_json()) == model


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", " "),
        ("investment_theme", "\t"),
        ("execution_mode", "unknown"),
        ("as_of", "2026-09-01T00:00:00Z"),
        ("countries", [""]),
        ("policy_version", ""),
    ],
)
def test_input_rejects_invalid_structure(field, value):
    assert hasattr(contracts, "RunInput"), "RunInput contract not implemented"
    payload = input_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        contracts.RunInput.model_validate(payload)


def test_input_requires_explicit_version():
    assert hasattr(contracts, "RunInput"), "RunInput contract not implemented"
    payload = input_payload()
    del payload["schema_version"]
    with pytest.raises(ValidationError):
        contracts.RunInput.model_validate(payload)
