"""Cross-DTO regression matrix over the implemented synthetic contracts."""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

import skala_rag.contracts as contracts

NAMES = tuple(
    name
    for name in contracts.__all__
    if isinstance(getattr(contracts, name), type)
    and issubclass(getattr(contracts, name), BaseModel)
)


@pytest.mark.parametrize("section", ["field_table", "identity_core"])
def test_canonical_evidence_sections_include_value_as_of(section):
    text = (Path(__file__).parents[2] / "docs/implementation/contracts.md").read_text()
    evidence = text.split("### Evidence\n", 1)[1].split("### 구조 예시", 1)[0]
    if section == "field_table":
        table = evidence.split("| 필드 | 타입 / 의미 |\n", 1)[1].split("\n\n", 1)[0]
        fields = ", ".join(row.split("|")[1] for row in table.splitlines())
    else:
        fields = evidence.split("| 식별 core |", 1)[1].split("|", 1)[0]
    assert "value_as_of" in fields, f"{section} omits the monetary basis date"


def test_documented_synthetic_evidence_validates_with_fixture_context():
    text = (Path(__file__).parents[2] / "docs/implementation/contracts.md").read_text()
    example = text.split("```json\n", 1)[1].split("```", 1)[0]
    model = contracts.Evidence.model_validate_json(
        example, context={"execution_mode": "fixture"}
    )
    assert model.schema_version == "draft-2"
    assert (
        contracts.Evidence.model_validate_json(
            model.model_dump_json(), context={"execution_mode": "fixture"}
        )
        == model
    )


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_json_nonfinite_number_rejected(token, payloads):
    data = json.dumps({**payloads["MonetaryObservation"], "value": "TOKEN"})
    with pytest.raises(ValidationError):
        contracts.MonetaryObservation.model_validate_json(
            data.replace('"TOKEN"', token)
        )


def test_nested_provenance_requires_its_own_version(payloads):
    del payloads["Evidence"]["provenance"][0]["schema_version"]
    with pytest.raises(ValidationError):
        contracts.Evidence.model_validate(payloads["Evidence"])


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("version", [None, "", " ", 42, ["v"], {"v": "x"}])
def test_all_dtos_reject_invalid_versions(name, version, payloads):
    with pytest.raises(ValidationError):
        getattr(contracts, name).model_validate(
            {**payloads[name], "schema_version": version}
        )


@pytest.mark.parametrize("name", NAMES)
def test_all_dtos_require_version_and_reject_extra_fields(name, payloads):
    cls = getattr(contracts, name)
    data = payloads[name]
    del data["schema_version"]
    with pytest.raises(ValidationError):
        cls.model_validate(data)
    with pytest.raises(ValidationError):
        cls.model_validate(
            {**data, "schema_version": "synthetic-1", "unknown_field": 1}
        )


@pytest.mark.parametrize("name", NAMES)
def test_every_fixture_json_roundtrip(name, payloads):
    cls = getattr(contracts, name)
    model = cls.model_validate(payloads[name])
    assert cls.model_validate_json(model.model_dump_json()) == model


@pytest.mark.parametrize("field", ["domain_match", "is_listed", "exit_completed"])
@pytest.mark.parametrize("value", [True, False, None])
def test_observed_boolean_values_are_lossless(field, value, payloads):
    model = contracts.CompanyProfile.model_validate(
        {**payloads["CompanyProfile"], field: value}
    )
    restored = contracts.CompanyProfile.model_validate_json(model.model_dump_json())
    assert getattr(restored, field) is value


@pytest.mark.parametrize(
    "name,field,value",
    [
        ("RunInput", "investment_theme", 42),
        ("Candidate", "aliases", [" "]),
        ("Candidate", "legal_identifiers", {"registry": ""}),
        ("CompanyProfile", "field_evidence_ids", {"field": [""]}),
        ("CompanyProfile", "field_evidence_ids", {"field": "ev-synthetic"}),
        ("EligibilityResult", "checks", ["check"]),
        ("Chunk", "page_end", -1),
        ("Chunk", "page_start", 1.5),
        ("Evidence", "criterion_ids", [" "]),
        ("ResearchGap", "eligibility_field", " "),
        ("CoverageResult", "unresolved_conflicts", [""]),
        ("RetrievalRecord", "arguments_without_secrets", {"nested": [{"x": object()}]}),
    ],
)
def test_collection_shapes_and_text_rejections(name, field, value, payloads):
    with pytest.raises(ValidationError):
        getattr(contracts, name).model_validate({**payloads[name], field: value})


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), float("-inf"), True, "0"]
)
@pytest.mark.parametrize(
    "name,field",
    [
        ("MonetaryObservation", "value"),
        ("Evidence", "value"),
        ("ResearchGap", "priority_weight"),
        ("CoverageResult", "missing_weight"),
        ("CoverageResult", "coverage_pct"),
    ],
)
def test_numeric_observations_do_not_coerce_or_allow_nonfinite(
    name, field, value, payloads
):
    with pytest.raises(ValidationError):
        getattr(contracts, name).model_validate({**payloads[name], field: value})


@pytest.mark.parametrize("value", [0, -2.5])
def test_nonmonetary_observation_preserves_actual_values(value, payloads):
    model = contracts.Evidence.model_validate(
        {**payloads["Evidence"], "value": value, "unit": "synthetic-count"}
    )
    assert model.value == value
    assert model.currency is None


def test_stage_money_nested_json_roundtrip(payloads):
    data = {
        **payloads["StageInfo"],
        "cumulative_funding_krw": payloads["MonetaryObservation"],
    }
    model = contracts.StageInfo.model_validate(data)
    restored = contracts.StageInfo.model_validate_json(model.model_dump_json())
    assert restored == model and restored.cumulative_funding_krw.value == 0


@pytest.mark.parametrize(
    "name,field,values",
    [
        ("RunInput", "execution_mode", ["fixture", "live"]),
        (
            "StageInfo",
            "normalized_round",
            ["seed", "series_a", "series_b", "series_c", "out_of_scope", "unknown"],
        ),
        ("StageInfo", "bucket", ["early", "late", "unknown"]),
        ("StageInfo", "method", ["explicit", "estimated", "unknown"]),
        ("StageInfo", "confidence", ["high", "medium", "low", "unknown"]),
        ("EligibilityResult", "status", ["eligible", "ineligible", "unknown"]),
        ("Source", "source_kind", ["web", "report", "paper", "filing", "patent"]),
        ("Chunk", "scope", ["company", "industry"]),
        ("Evidence", "scope", ["company", "industry"]),
        ("Evidence", "evidence_kind", ["reported", "derived", "estimated"]),
        ("ResearchGap", "status", ["open", "resolved", "exhausted"]),
    ],
)
def test_documented_enums_are_observations_not_policy_couplings(
    name, field, values, payloads
):
    for value in values:
        model = getattr(contracts, name).model_validate(
            {**payloads[name], field: value}
        )
        assert getattr(model, field) == value
