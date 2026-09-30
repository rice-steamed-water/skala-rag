"""Load explicitly synthetic DTO payloads, never a live corpus."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def payloads():
    return json.loads(
        (Path(__file__).parents[1] / "fixtures/contracts.json").read_text()
    )
