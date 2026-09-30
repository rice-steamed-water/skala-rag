"""Synthetic source/chunk shape; no fetch, indexing, or hash computation."""

import pytest
from pydantic import ValidationError

import skala_rag.contracts as contracts


@pytest.mark.parametrize("name", ["Source", "Chunk"])
def test_source_chunk_roundtrip(name, payloads):
    assert hasattr(contracts, name), f"{name} missing"
    cls = getattr(contracts, name)
    model = cls.model_validate(payloads[name])
    assert cls.model_validate_json(model.model_dump_json()) == model


@pytest.mark.parametrize("published", [None, "2026-09-01", "2026-09-01T12:00:00+09:00"])
def test_publication_precision_preserved(published, payloads):
    assert hasattr(contracts, "Source"), "Source missing"
    data = {**payloads["Source"], "published_at": published}
    model = contracts.Source.model_validate(data)
    assert model.model_dump(mode="json")["published_at"] == published
    assert contracts.Source.model_validate_json(model.model_dump_json()) == model


@pytest.mark.parametrize(
    "url,path",
    [
        (None, "synthetic/report.txt"),
        ("https://fixture.invalid/report", "synthetic/report.txt"),
    ],
)
def test_source_locator_alternatives(url, path, payloads):
    assert hasattr(contracts, "Source"), "Source missing"
    contracts.Source.model_validate(
        {**payloads["Source"], "url": url, "local_path": path}
    )


@pytest.mark.parametrize(
    "name,field,value",
    [
        ("Source", "url", None),
        ("Source", "url", " "),
        ("Source", "source_kind", "blog"),
        ("Source", "retrieved_at", "2026-09-01T12:00:00"),
        ("Source", "published_at", "2026-09-01T12:00:00"),
        ("Source", "bibliographic_metadata", {"client": object()}),
        ("Source", "bibliographic_metadata", {"number": float("nan")}),
        ("Chunk", "scope", "other"),
        ("Chunk", "page_start", -1),
        ("Chunk", "source_id", ""),
        ("Chunk", "embedding_revision", " "),
    ],
)
def test_source_chunk_rejections(name, field, value, payloads):
    assert hasattr(contracts, name), f"{name} missing"
    with pytest.raises(ValidationError):
        getattr(contracts, name).model_validate({**payloads[name], field: value})


@pytest.mark.parametrize(
    "name,field", [("Source", "url"), ("Source", "local_path"), ("Chunk", "locator")]
)
def test_fixture_locator_needs_explicit_context(name, field, payloads):
    assert hasattr(contracts, name), f"{name} missing"
    data = {**payloads[name], field: "fixture://synthetic#page=1"}
    cls = getattr(contracts, name)
    with pytest.raises(ValidationError):
        cls.model_validate(data)
    with pytest.raises(ValidationError):
        cls.model_validate(data, context={"execution_mode": "live"})
    model = cls.model_validate(data, context={"execution_mode": "fixture"})
    assert (
        cls.model_validate_json(
            model.model_dump_json(), context={"execution_mode": "fixture"}
        )
        == model
    )
