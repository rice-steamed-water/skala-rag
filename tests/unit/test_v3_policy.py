"""Offline operational-policy fixtures; not investment or live approval."""

import importlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

PATH = Path(__file__).parents[2] / "configs/scoring.v3.json"


def test_explicit_fixture_loader():
    module = importlib.import_module("skala_rag.scoring.v3_policy")
    policy = module.load_v3_policy(PATH, execution_mode="fixture")
    assert policy.policy_version == "v3-operational-1.0.0"
    assert policy.approval.rubric_status == "not_approved"


def validate(payload):
    from skala_rag.scoring.v3_policy import V3Policy

    return V3Policy.model_validate(payload)


def payload():
    return json.loads(PATH.read_text())


@pytest.mark.parametrize("mode", ["live", "Fixture", "", None, True])
def test_nonfixture_mode_rejected(mode):
    from skala_rag.scoring.v3_policy import load_v3_policy

    with pytest.raises(ValueError):
        load_v3_policy(PATH, execution_mode=mode)


def test_mode_has_no_default():
    from skala_rag.scoring.v3_policy import load_v3_policy

    with pytest.raises(TypeError):
        load_v3_policy(PATH)


@pytest.mark.parametrize(
    "section",
    [
        None,
        "approval",
        "numeric",
        "applicability",
        "selection",
        "research",
        "report",
        "criteria",
        "dimension_weights",
    ],
)
def test_unknown_and_every_missing_field_rejected(section):
    p = payload()
    target = p if section is None else p[section]
    if isinstance(target, list):
        target = target[0]
    for key in tuple(target):
        value = target.pop(key)
        with pytest.raises(ValidationError):
            validate(p)
        target[key] = value
    target["unknown"] = 1
    with pytest.raises(ValidationError):
        validate(p)


@pytest.mark.parametrize(
    "section,key,value",
    [
        (None, "policy_version", "v3"),
        (None, "execution_mode", "live"),
        ("approval", "status", "draft"),
        ("approval", "source", "#35"),
        ("approval", "approved_on", "2026-09-29"),
        ("approval", "rubric_status", "approved"),
        ("approval", "live_readiness", "approved"),
        ("approval", "live_budget", "approved"),
        ("numeric", "priority_score", "79.9999999999999999999999999999"),
        ("numeric", "low_dimension_scope", ["founder", "market"]),
        ("numeric", "guard_order", ["score", "missing", "low_dimension"]),
        ("numeric", "labels_descending", ["RECOMMEND", "WATCHLIST", "PASS"]),
        ("numeric", "low_dimension_ratio_pct", "41"),
        ("numeric", "weighted_missing_pct", "29"),
        ("numeric", "recommend_score", "71"),
        ("numeric", "watchlist_score", "59"),
        ("research", "additional_requests_per_candidate", True),
        ("research", "additional_requests_per_candidate", 3),
        ("research", "additional_requests_per_candidate", "2"),
        ("report", "shared_revisions", 3),
        ("report", "exhausted_cli_exit", 0),
        ("report", "revision_scope", ["structural"]),
        ("applicability", "na_requires", ["approved_rule"]),
        ("selection", "ordering", ["original_candidate_id_asc"]),
    ],
)
def test_conflicting_policy_rejected(section, key, value):
    p = payload()
    target = p if section is None else p[section]
    target[key] = value
    with pytest.raises(ValidationError):
        validate(p)


@pytest.mark.parametrize("value", [True, False, 80.0, "NaN", "Infinity", "-Infinity"])
def test_nonexact_nonfinite_numeric_rejected(value):
    p = payload()
    p["numeric"]["priority_score"] = value
    with pytest.raises(ValidationError):
        validate(p)


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "space",
        "duplicate",
        "weight",
        "bool",
        "dimension",
        "dimension_bool",
        "dimension_duplicate",
    ],
)
def test_catalog_mismatch_rejected(mutation):
    p = payload()
    first = p["criteria"][0]
    if mutation == "unknown":
        first["criterion_id"] = "founder.other"
    elif mutation == "space":
        first["criterion_id"] += " "
    elif mutation == "duplicate":
        p["criteria"][1] = first.copy()
    elif mutation == "weight":
        first["weight"] = 1
        p["criteria"][1]["weight"] = 3
    elif mutation == "bool":
        first["weight"] = True
    elif mutation == "dimension":
        first["dimension"] = "market"
    elif mutation == "dimension_bool":
        p["dimension_weights"][0]["weight"] = True
    else:
        p["dimension_weights"][1] = p["dimension_weights"][0].copy()
    with pytest.raises(ValidationError):
        validate(p)


def test_baseline_catalog_and_roundtrip_and_frozen():
    from skala_rag.scoring.catalog import load_policy
    from skala_rag.scoring.v3_policy import V3Policy, load_v3_policy

    p = load_v3_policy(PATH, execution_mode="fixture")
    baseline = load_policy(
        PATH.with_name("scoring.draft.json"), execution_mode="fixture"
    )
    assert tuple(c.model_dump() for c in p.criteria) == tuple(
        c.model_dump() for c in baseline.criteria
    )
    assert {
        w.dimension: w.weight for w in p.dimension_weights
    } == baseline.dimension_weights
    assert V3Policy.model_validate_json(p.model_dump_json()) == p
    assert isinstance(p.numeric.priority_score, Decimal)
    for obj, field, value in [
        (p, "execution_mode", "live"),
        (p.report, "shared_revisions", 3),
        (p.criteria[0], "weight", 4),
        (p.dimension_weights[0], "weight", 10),
    ]:
        with pytest.raises(ValidationError):
            setattr(obj, field, value)
    assert isinstance(p.criteria, tuple)
    assert isinstance(p.dimension_weights, tuple)


@pytest.mark.parametrize(
    "text",
    [
        '{"policy_version":"v3-operational-1.0.0","policy_version":"v3"}',
        '{"numeric":{"priority_score":NaN}}',
        '{"numeric":{"priority_score":Infinity}}',
    ],
)
def test_bad_json_rejected(tmp_path, text):
    from skala_rag.scoring.v3_policy import load_v3_policy

    path = tmp_path / "policy.json"
    path.write_text(text)
    with pytest.raises(ValueError):
        load_v3_policy(path, execution_mode="fixture")


def test_json_decimal_tokens_are_exact(tmp_path):
    from skala_rag.scoring.v3_policy import load_v3_policy

    path = tmp_path / "policy.json"
    path.write_text(
        PATH.read_text().replace('"priority_score": "80"', '"priority_score": 80.0')
    )
    assert load_v3_policy(
        path, execution_mode="fixture"
    ).numeric.priority_score == Decimal(80)
    path.write_text(path.read_text().replace("80.0", "79.9999999999999999999999999999"))
    with pytest.raises(ValidationError):
        load_v3_policy(path, execution_mode="fixture")
