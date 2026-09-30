"""Additional boundary regressions, with genuinely synthetic observations."""

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

import skala_rag.contracts as contracts


@pytest.mark.parametrize(
    "name,field",
    [
        ("Source", "retrieved_at"),
        ("Source", "published_at"),
        ("RetrievalRecord", "started_at"),
        ("RetrievalRecord", "finished_at"),
    ],
)
@pytest.mark.parametrize("value", [0, 1.0, "0"])
def test_clock_cannot_be_inferred_from_epoch_number(name, field, value, payloads):
    with pytest.raises(ValidationError):
        getattr(contracts, name).model_validate({**payloads[name], field: value})


def test_stage_krw_field_rejects_other_currency(payloads):
    data = {
        **payloads["StageInfo"],
        "cumulative_funding_krw": {
            **payloads["MonetaryObservation"],
            "currency": "USD",
        },
    }
    with pytest.raises(ValidationError):
        contracts.StageInfo.model_validate(data)


def test_nested_model_instances_are_revalidated(payloads):
    source = contracts.Source.model_validate(payloads["Source"])
    source.title = " "
    with pytest.raises(ValidationError):
        contracts.RetrievalBundle.model_validate(
            dict(
                schema_version="synthetic-1",
                chunks=[],
                sources={source.source_id: source},
            )
        )


def test_nested_fixture_instance_cannot_bypass_context(payloads):
    source = contracts.Source.model_validate(
        {**payloads["Source"], "url": "fixture://synthetic"},
        context={"execution_mode": "fixture"},
    )
    with pytest.raises(ValidationError):
        contracts.RetrievalBundle.model_validate(
            dict(
                schema_version="synthetic-1",
                chunks=[],
                sources={source.source_id: source},
            )
        )


def test_homepage_fixture_requires_context(payloads):
    data = {**payloads["Candidate"], "homepage_url": "fixture://synthetic"}
    with pytest.raises(ValidationError):
        contracts.Candidate.model_validate(data)
    assert (
        contracts.Candidate.model_validate(
            data, context={"execution_mode": "fixture"}
        ).homepage_url
        == "fixture://synthetic"
    )


@pytest.mark.parametrize(
    "value",
    [
        {"tuple": (1, 2)},
        {"set": {1, 2}},
        {"date": date(2026, 9, 1)},
        {1: "nonstring-key"},
        {"nested": [{"x": float("-inf")}]},
    ],
)
def test_metadata_is_json_data_not_python_objects(value, payloads):
    with pytest.raises(ValidationError):
        contracts.Source.model_validate(
            {**payloads["Source"], "bibliographic_metadata": value}
        )


def test_typed_date_and_aware_datetime_are_preserved(payloads):
    dt = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    model = contracts.Source.model_validate(
        {**payloads["Source"], "published_at": date(2026, 8, 1), "retrieved_at": dt}
    )
    assert type(model.published_at) is date
    assert model.retrieved_at == dt
    assert contracts.Source.model_validate_json(model.model_dump_json()) == model


def test_date_only_rejects_week_notation(payloads):
    with pytest.raises(ValidationError):
        contracts.RunInput.model_validate(
            {**payloads["RunInput"], "as_of": "2026-W01-1"}
        )
