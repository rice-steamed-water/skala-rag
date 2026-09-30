"""Load explicitly synthetic DTO payloads, never a live corpus."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def payloads():
    fixtures = Path(__file__).parents[1] / "fixtures"
    base = json.loads((fixtures / "contracts.json").read_text())
    extra = {}
    for name in ("evaluation_contracts.json", "tool_contracts.json"):
        extra.update(json.loads((fixtures / name).read_text()))
    extra.pop("label")
    return {**base, **extra}
