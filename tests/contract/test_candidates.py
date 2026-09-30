"""Candidate observations are structural, not eligibility algorithms."""

import pytest
from pydantic import ValidationError

import skala_rag.contracts as contracts


@pytest.mark.parametrize(
    "name",
    [
        "Candidate",
        "StageInfo",
        "CompanyProfile",
        "EligibilityResult",
        "MonetaryObservation",
    ],
)
def test_candidate_contract_roundtrip(name, payloads):
    assert hasattr(contracts, name), f"{name} contract not implemented"
    cls = getattr(contracts, name)
    model = cls.model_validate(payloads[name])
    assert cls.model_validate_json(model.model_dump_json()) == model


def test_unknown_observations_and_zero_money(payloads):
    assert hasattr(contracts, "CompanyProfile"), "profile missing"
    model = contracts.CompanyProfile.model_validate(payloads["CompanyProfile"])
    assert model.domain_match is model.is_listed is model.exit_completed is None
    assert model.stage.raw_label is model.stage.last_round_date is None
    assert model.stage.cumulative_funding_krw is None
    money = contracts.MonetaryObservation.model_validate(
        payloads["MonetaryObservation"]
    )
    assert money.value == 0
    candidate = contracts.Candidate.model_validate(payloads["Candidate"])
    assert candidate.aliases == [" Synthetic Alias "]
    assert candidate.legal_identifiers == {"synthetic-registry": " 001 "}


@pytest.mark.parametrize("field", ["domain_match", "is_listed", "exit_completed"])
@pytest.mark.parametrize("value", ["false", 0, 1])
def test_boolean_observations_reject_coercion(field, value, payloads):
    assert hasattr(contracts, "CompanyProfile"), "profile missing"
    with pytest.raises(ValidationError):
        contracts.CompanyProfile.model_validate(
            {**payloads["CompanyProfile"], field: value}
        )


@pytest.mark.parametrize(
    "field", ["value", "currency", "unit", "as_of", "schema_version"]
)
def test_money_requires_complete_context(field, payloads):
    assert hasattr(contracts, "MonetaryObservation"), "money contract missing"
    data = payloads["MonetaryObservation"]
    del data[field]
    with pytest.raises(ValidationError):
        contracts.MonetaryObservation.model_validate(data)


@pytest.mark.parametrize(
    "name,field,value",
    [
        ("Candidate", "candidate_id", " "),
        ("Candidate", "legal_identifiers", {"": "x"}),
        ("StageInfo", "normalized_round", "tips"),
        ("StageInfo", "bucket", "seed"),
        ("StageInfo", "method", "inferred"),
        ("StageInfo", "confidence", "certain"),
        ("StageInfo", "last_round_date", "2026-09-01T00:00:00Z"),
        ("StageInfo", "cumulative_funding_krw", 0),
        ("EligibilityResult", "status", "pass"),
        ("EligibilityResult", "evidence_revision", -1),
        ("EligibilityResult", "checks", {"check": object()}),
        ("EligibilityResult", "checks", {"check": float("inf")}),
        ("MonetaryObservation", "value", float("nan")),
        ("MonetaryObservation", "value", float("inf")),
        ("MonetaryObservation", "currency", " "),
    ],
)
def test_candidate_structural_rejections(name, field, value, payloads):
    assert hasattr(contracts, name), f"{name} contract missing"
    with pytest.raises(ValidationError):
        getattr(contracts, name).model_validate({**payloads[name], field: value})
