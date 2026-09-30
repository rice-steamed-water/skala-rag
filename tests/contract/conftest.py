"""Load explicitly synthetic DTO payloads, never a live corpus."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def payloads():
    fixtures = Path(__file__).parents[1] / "fixtures"
    base = json.loads((fixtures / "contracts.json").read_text())
    evaluation = json.loads((fixtures / "evaluation_contracts.json").read_text())
    return {
        **base,
        **{key: value for key, value in evaluation.items() if key != "label"},
    }
