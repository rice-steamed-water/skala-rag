"""Company configuration is a frozen request, never collection authority."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from skala_rag.contracts.company_report import (
    CompanyReportConfigError,
    CompanyReportReceipt,
    CompanyReportRequest,
    load_company_report_config,
)
from skala_rag.settings import load_runtime_document

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def config_data():
    data = json.loads((ROOT / "configs/company-report.example.json").read_bytes())
    data["runtime_path"] = str(ROOT / "configs/runtime.json")
    return data


def write_config(tmp_path, data):
    path = tmp_path / "company-report.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_explicit_false_overrides_file_true(tmp_path, config_data):
    # Given
    config_data["research_enabled"] = True
    path = write_config(tmp_path, config_data)
    # When
    effective = load_company_report_config(path, research=False)
    # Then
    assert effective.research_enabled is False
    assert effective.research_limits is None
    assert json.loads(path.read_bytes())["research_enabled"] is True


@pytest.mark.parametrize(
    "file_value,override,expected",
    [
        (None, None, False),
        (False, None, False),
        (True, None, True),
        (False, True, True),
        (True, False, False),
    ],
)
def test_research_precedence(tmp_path, config_data, file_value, override, expected):
    # Given
    config_data.pop("research_enabled")
    if file_value is not None:
        config_data["research_enabled"] = file_value
    config_data["research_limits"] = {
        "max_calls": 2,
        "max_cost_usd": "0.10",
        "deadline_seconds": 30,
    }
    path = write_config(tmp_path, config_data)
    # When
    effective = load_company_report_config(path, research=override)
    # Then
    assert effective.research_enabled is expected


@pytest.mark.parametrize("bad", ["false", 0, 1, [], {}])
def test_python_boolean_rejected_before_file_access(tmp_path, bad):
    # Given / When / Then
    with pytest.raises(ValidationError):
        load_company_report_config(tmp_path / "absent.json", research=bad)


@pytest.mark.parametrize(
    "field,value",
    [
        ("research_enabled", "true"),
        ("research_enabled", 1),
        ("research_enabled", None),
        ("schema_version", "company-report-2"),
        ("runtime_path", ""),
        ("store_dir", " "),
        ("model_path", True),
        ("model_receipt_path", None),
        ("policy_path", "\x00"),
        ("catalog_path", []),
        ("api_key", "synthetic-secret"),
        ("authority", {"approved": True}),
        ("actual_admission", {"approved": True}),
        ("unknown", None),
    ],
)
def test_file_input_is_strict(tmp_path, config_data, field, value):
    # Given
    config_data[field] = value
    path = write_config(tmp_path, config_data)
    # When / Then
    with pytest.raises(ValidationError) as error:
        load_company_report_config(path)
    assert "synthetic-secret" not in str(error.value)


@pytest.mark.parametrize(
    "text",
    [
        "{",
        "null",
        "[]",
        '{"research_enabled":false,"research_enabled":true}',
        '{"research_limits":{"max_calls":1,"max_calls":2}}',
        '{"research_limits":{"max_cost_usd":NaN}}',
        '{"research_limits":{"max_cost_usd":Infinity}}',
    ],
)
def test_malformed_json_is_rejected(tmp_path, text):
    # Given
    path = tmp_path / "company-report.json"
    path.write_text(text, encoding="utf-8")
    # When / Then
    with pytest.raises(ValueError):
        load_company_report_config(path)


def test_all_paths_resolve_against_config_directory(tmp_path, config_data, monkeypatch):
    # Given
    fields = (
        "runtime_path",
        "store_dir",
        "model_path",
        "model_receipt_path",
        "policy_path",
        "catalog_path",
    )
    for field in fields:
        config_data[field] = f"../assets/{field}"
    config_data["model_path"] = str(tmp_path / "absolute-model")
    path = write_config(tmp_path, config_data)
    runtime = load_runtime_document()
    monkeypatch.chdir(tmp_path.parent)
    # When
    effective = load_company_report_config(path, runtime_document=runtime)
    # Then
    for field in fields:
        expected = (path.parent / config_data[field]).resolve()
        assert getattr(effective, field) == expected
    assert not effective.store_dir.exists()


def test_runtime_override_wins_without_reading_missing_file(tmp_path, config_data):
    # Given
    config_data["runtime_path"] = "missing-runtime.json"
    path = write_config(tmp_path, config_data)
    runtime = load_runtime_document()
    changed = runtime.model_dump()
    changed["profiles"]["actual_v3"]["max_calls"] = 7
    runtime = type(runtime).model_validate(changed)
    # When
    effective = load_company_report_config(path, runtime_document=runtime)
    # Then
    assert effective.runtime_document == runtime
    assert effective.runtime_document.profiles.actual_v3.max_calls == 7
    assert not effective.runtime_path.exists()


def test_missing_research_caps_rejected_before_collection(tmp_path, config_data):
    # Given
    config_data["research_enabled"] = True
    config_data["runtime_path"] = "must-not-be-read.json"
    path = write_config(tmp_path, config_data)
    before = tuple(tmp_path.iterdir())
    # When / Then
    with pytest.raises(CompanyReportConfigError) as error:
        load_company_report_config(path)
    assert error.value.code == "RESEARCH_CAPS_REQUIRED"
    assert tuple(tmp_path.iterdir()) == before


def test_nonnull_limits_merge_without_mutating_inputs(tmp_path, config_data):
    # Given
    caps = {"max_calls": 3, "max_cost_usd": "0.10", "deadline_seconds": 30}
    config_data["research_limits"] = caps.copy()
    path = write_config(tmp_path, config_data)
    overrides = {
        "max_calls": 0,
        "max_cost_usd": Decimal("0"),
        "deadline_seconds": None,
    }
    runtime = load_runtime_document()
    original_runtime = runtime.model_dump_json()
    # When
    effective = load_company_report_config(
        path, research_limits=overrides, runtime_document=runtime
    )
    # Then
    assert effective.research_limits is not None
    assert effective.research_limits.max_calls == 0
    assert effective.research_limits.max_cost_usd == Decimal("0")
    assert effective.research_limits.deadline_seconds == 30
    assert overrides["deadline_seconds"] is None
    assert config_data["research_limits"] == caps
    assert runtime.model_dump_json() == original_runtime
    before = effective.model_dump_json()
    overrides["max_calls"] = 2
    config_data["research_limits"]["max_calls"] = 2
    write_config(tmp_path, config_data)
    assert effective.model_dump_json() == before
    with pytest.raises(ValidationError):
        setattr(effective.research_limits, "max_calls", 2)
    with pytest.raises(ValidationError):
        setattr(effective.runtime_document.profiles.actual_v3, "max_calls", 2)
    with pytest.raises(ValidationError):
        setattr(effective, "research_enabled", True)


@pytest.mark.parametrize(
    "override",
    [
        {"unknown": None},
        {"max_calls": True},
        {"max_calls": 1.5},
        {"max_calls": "1"},
        {"max_calls": -1},
        {"max_cost_usd": True},
        {"max_cost_usd": "-0.1"},
        {"max_cost_usd": "NaN"},
        {"deadline_seconds": False},
        {"deadline_seconds": "30"},
        {"deadline_seconds": 0},
        {"deadline_seconds": float("inf")},
        [],
        "max_calls",
    ],
)
def test_invalid_limit_overrides_rejected(tmp_path, config_data, override):
    # Given
    path = write_config(tmp_path, config_data)
    # When / Then
    with pytest.raises(ValidationError):
        load_company_report_config(path, research_limits=override)


@pytest.mark.parametrize(
    "limits",
    [
        True,
        [],
        {},
        {"max_calls": 1},
        {
            "max_calls": 1,
            "max_cost_usd": "0",
            "deadline_seconds": 10,
            "approved": True,
        },
    ],
)
def test_malformed_file_limits_rejected(tmp_path, config_data, limits):
    # Given
    config_data["research_limits"] = limits
    path = write_config(tmp_path, config_data)
    # When / Then
    with pytest.raises(ValidationError):
        load_company_report_config(path)


@pytest.mark.parametrize(
    "override",
    [{"max_calls": 41}, {"max_cost_usd": "1.01"}, {"deadline_seconds": 3601}],
)
def test_merged_limits_cannot_raise_runtime_ceilings(tmp_path, config_data, override):
    # Given
    config_data["research_limits"] = {
        "max_calls": 2,
        "max_cost_usd": "0.10",
        "deadline_seconds": 30,
    }
    path = write_config(tmp_path, config_data)
    # When / Then
    with pytest.raises(ValidationError) as error:
        load_company_report_config(path, research_limits=override)
    code = error.value.errors()[0]["ctx"]["error"].code
    assert code == "RESEARCH_CAPS_EXCEED_RUNTIME"


def test_complete_python_caps_work_without_file_caps(tmp_path, config_data):
    # Given
    path = write_config(tmp_path, config_data)
    # When
    effective = load_company_report_config(
        path,
        research=True,
        research_limits={"max_calls": 1, "max_cost_usd": "0", "deadline_seconds": 10},
    )
    # Then
    assert effective.research_enabled is True
    assert effective.research_limits is not None
    assert effective.research_limits.max_calls == 1
    assert effective.research_limits.max_cost_usd == Decimal("0")


def test_effective_hash_binds_values_not_file_format(tmp_path, config_data):
    # Given
    path = write_config(tmp_path, config_data)
    first = load_company_report_config(path)
    path.write_text(json.dumps(config_data, indent=4), encoding="utf-8")
    # When
    second = load_company_report_config(path)
    # Then
    assert first.config_hash == second.config_hash
    assert len(second.config_hash) == 64
    exported = second.model_dump(mode="json")
    exported["runtime_document"]["llm"]["model"] = "changed"
    assert first.config_hash == second.config_hash
    config_data["store_dir"] = "another-store"
    changed = load_company_report_config(write_config(tmp_path, config_data))
    assert changed.config_hash != first.config_hash


def test_request_copies_and_freezes_identity_hints():
    # Given
    hints = {"registration": "synthetic-company"}
    # When
    request = CompanyReportRequest(
        schema_version="company-report-request-1",
        company_name="Robotics",
        legal_identifiers=hints,
        as_of=date(2026, 10, 10),
    )
    # Then
    hints["registration"] = "changed"
    assert request.legal_identifiers["registration"] == "synthetic-company"
    with pytest.raises(AttributeError):
        getattr(request.legal_identifiers, "__setitem__")
    restored = CompanyReportRequest.model_validate_json(request.model_dump_json())
    assert restored == request


@pytest.mark.parametrize(
    "override",
    [
        {"company_name": " "},
        {"company_name": True},
        {"homepage_url": 1},
        {"legal_identifiers": {"registration": 1}},
        {"as_of": "2026-10-10T00:00:00Z"},
        {"authority": {}},
        {"actual_admission": {}},
    ],
)
def test_request_rejects_invalid_identity_or_authority(override):
    # Given
    values = {
        "schema_version": "company-report-request-1",
        "company_name": "Robotics",
    }
    # When / Then
    with pytest.raises(ValidationError):
        CompanyReportRequest.model_validate(values | override)


@pytest.fixture
def receipt_data():
    return {
        "schema_version": "company-report-result-1",
        "run_id": "synthetic-run",
        "company_name": "Robotics",
        "candidate_id": "synthetic-company",
        "matching_candidate_ids": ("synthetic-company",),
        "as_of": date(2026, 10, 10),
        "effective_config_hash": "a" * 64,
        "research_requested": False,
        "collection_records": (),
        "collection_cost_usd": None,
        "selected_index_versions": ("previous-index",),
        "eligibility_status": "eligible",
        "outcome": "warning",
        "report_path": Path("report.md"),
        "report_validation": "passed",
        "validation_receipt_hashes": ("b" * 64,),
        "publication_allowed": False,
        "ingestion_status": "failed",
        "reason_codes": ("INGESTION_FAILED",),
    }


def test_receipt_separates_valid_report_from_failed_ingestion(receipt_data):
    # Given / When
    receipt = CompanyReportReceipt.model_validate(receipt_data)
    # Then
    assert receipt.outcome == "warning"
    assert receipt.report_validation == "passed"
    assert receipt.ingestion_status == "failed"
    assert receipt.selected_index_versions == ("previous-index",)
    assert receipt.collection_cost_usd is None
    assert receipt.publication_allowed is False
    restored = CompanyReportReceipt.model_validate_json(receipt.model_dump_json())
    assert restored == receipt


@pytest.mark.parametrize(
    "override",
    [
        {"outcome": "completed"},
        {"outcome": "identity_unknown"},
        {"eligibility_status": "unknown"},
        {"report_path": None},
        {"validation_receipt_hashes": ()},
        {"report_validation": "failed", "publication_allowed": True},
        {"reason_codes": ()},
        {"research_requested": 1},
        {"authority": {"approved": True}},
        {"actual_admission": {"approved": True}},
    ],
)
def test_receipt_rejects_misleading_success_or_authority(receipt_data, override):
    # Given / When / Then
    with pytest.raises(ValidationError):
        CompanyReportReceipt.model_validate(receipt_data | override)
