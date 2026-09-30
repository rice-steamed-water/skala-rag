"""Local payload-map identity checks; not controller reference resolution."""

from collections.abc import Mapping

from .common import Contract


def validate_map_ids(values: Mapping[str, Contract], field: str) -> None:
    if any(key != getattr(value, field) for key, value in values.items()):
        raise ValueError(f"map key must match payload {field}")


def validate_unique(values: list[str], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} must be unique")
