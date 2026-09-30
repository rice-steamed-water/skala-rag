"""Coverage records caller values and does not choose thresholds or weights."""

import pytest
from pydantic import ValidationError

import skala_rag.contracts as contracts


@pytest.mark.parametrize("name", ["ResearchGap", "CoverageResult"])
def test_coverage_roundtrip(name, payloads):
    assert hasattr(contracts, name), f"{name} missing"
    cls = getattr(contracts, name)
    model = cls.model_validate(payloads[name])
    assert cls.model_validate_json(model.model_dump_json()) == model


def test_eligibility_gap_target(payloads):
    assert hasattr(contracts, "ResearchGap"), "gap missing"
    data = {
        **payloads["ResearchGap"],
        "criterion_id": None,
        "eligibility_field": "is_listed",
    }
    model = contracts.ResearchGap.model_validate(data)
    assert model.criterion_id is None
    assert model.eligibility_field == "is_listed"
    assert contracts.ResearchGap.model_validate_json(model.model_dump_json()) == model


@pytest.mark.parametrize(
    "criterion,eligibility", [(None, None), ("criterion", "field")]
)
def test_gap_requires_exactly_one_target(criterion, eligibility, payloads):
    assert hasattr(contracts, "ResearchGap"), "gap missing"
    with pytest.raises(ValidationError):
        contracts.ResearchGap.model_validate(
            {
                **payloads["ResearchGap"],
                "criterion_id": criterion,
                "eligibility_field": eligibility,
            }
        )


def test_coverage_does_not_infer_readiness_or_missing_values(payloads):
    assert hasattr(contracts, "CoverageResult"), "coverage missing"
    model = contracts.CoverageResult.model_validate(payloads["CoverageResult"])
    assert model.coverage_pct == 0 and model.research_ready is True
    data = {
        **payloads["CoverageResult"],
        "coverage_pct": None,
        "missing_weight": None,
        "research_ready": None,
    }
    model = contracts.CoverageResult.model_validate(data)
    assert model.coverage_pct is model.missing_weight is model.research_ready is None


@pytest.mark.parametrize(
    "name,field,value",
    [
        ("ResearchGap", "priority_weight", -1),
        ("ResearchGap", "priority_weight", float("inf")),
        ("ResearchGap", "status", "retry"),
        ("ResearchGap", "gap_id", " "),
        ("CoverageResult", "evidence_revision", -1),
        ("CoverageResult", "coverage_pct", -1),
        ("CoverageResult", "coverage_pct", 101),
        ("CoverageResult", "coverage_pct", float("nan")),
        ("CoverageResult", "missing_weight", -1),
        ("CoverageResult", "research_ready", "true"),
    ],
)
def test_coverage_rejections(name, field, value, payloads):
    assert hasattr(contracts, name), f"{name} missing"
    with pytest.raises(ValidationError):
        getattr(contracts, name).model_validate({**payloads[name], field: value})
