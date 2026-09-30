"""Strict text rejects raw client values with normal Pydantic errors."""

import pytest
from pydantic import ValidationError

import skala_rag.contracts as contracts


@pytest.mark.parametrize(
    "name,field",
    [
        ("Candidate", "schema_version"),
        ("Candidate", "candidate_id"),
        ("Candidate", "canonical_name"),
    ],
)
@pytest.mark.parametrize("value", [42, ["text"], {"text": "value"}])
def test_required_text_rejects_non_string(name, field, value, payloads):
    with pytest.raises(ValidationError):
        getattr(contracts, name).model_validate({**payloads[name], field: value})
