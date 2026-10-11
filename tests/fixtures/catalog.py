"""Historical fixture identities, not a production scoring policy or evaluator."""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FixtureCriterion:
    criterion_id: str
    dimension: str


@dataclass(frozen=True, slots=True)
class FixtureCatalog:
    policy_version: str
    criteria: tuple[FixtureCriterion, ...]
    dimension_weights: dict[str, int]


def load_policy(path: str | Path, *, execution_mode: str) -> FixtureCatalog:
    """Read only the historical test catalog's identity and partition."""
    if execution_mode != "fixture":
        raise ValueError("Historical test catalog requires fixture mode")
    payload = json.loads(Path(path).read_bytes())
    return FixtureCatalog(
        payload["policy_version"],
        tuple(
            FixtureCriterion(item["criterion_id"], item["dimension"])
            for item in payload["criteria"]
        ),
        payload["dimension_weights"],
    )
