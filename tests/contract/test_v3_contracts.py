"""Offline structural evidence, never policy or live execution evidence."""

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from skala_rag import contracts as baseline
from skala_rag.contracts import v3

DATA = json.loads(
    (Path(__file__).parents[1] / "fixtures/v3_contracts.json").read_text()
)
CONTEXT = {"execution_mode": "fixture"}


@pytest.mark.parametrize(
    "model,key",
    [
        (v3.CriterionAssessment, "criteria"),
        (v3.EvaluationBranchResult, "branches"),
        (v3.InvestmentDecision, "decisions"),
        (v3.EvaluationBranchResult, "failure"),
        (v3.ScoreSummary, "score"),
        (v3.CoverageResult, "coverage"),
    ],
)
def test_fixture_roundtrip(model, key):
    payloads = DATA[key] if isinstance(DATA[key], list) else [DATA[key]]
    for payload in payloads:
        value = model.model_validate(payload, context=CONTEXT)
        assert (
            model.model_validate_json(value.model_dump_json(), context=CONTEXT) == value
        )


@pytest.mark.parametrize(
    "index,field,value",
    [
        (0, "rating", 0),
        (0, "rating", 6),
        (0, "rating", True),
        (0, "rating", 1.5),
        (0, "evidence_ids", []),
        (0, "evidence_ids", ["e", "e"]),
        (0, "missing_reason", "no"),
        (1, "rating", 1),
        (1, "missing_reason", None),
        (1, "applicability_rule_id", "rule"),
        (2, "rating", 1),
        (2, "applicability_reason", None),
        (2, "applicability_rule_id", " "),
        (2, "applicability_evidence_ids", []),
        (2, "applicability_evidence_ids", ["e", "e"]),
        (2, "missing_reason", "no"),
        (0, "schema_version", None),
        (0, "unknown", "extra"),
    ],
)
def test_reject_invalid_criterion(index, field, value):
    payload = deepcopy(DATA["criteria"][index])
    payload[field] = value
    with pytest.raises(ValidationError):
        v3.CriterionAssessment.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "other"),
        ("candidate_id", "other"),
        ("evaluation_round", 2),
        ("snapshot_id", "other"),
        ("evidence_revision", 2),
        ("policy_version", "other"),
        ("dimension", "traction"),
        ("schema_version", None),
    ],
)
def test_reject_nested_identity(field, value):
    payload = deepcopy(DATA["branches"][-1])
    payload["evaluations"]["deal_terms"][field] = value
    with pytest.raises(ValidationError):
        v3.EvaluationBranchResult.model_validate(payload)


@pytest.mark.parametrize(
    "change",
    ["partial", "extra", "errors", "failure_payload", "empty_errors", "bad_branch"],
)
def test_atomic_terminal(change):
    payload = deepcopy(DATA["branches"][-1])
    if change == "partial":
        del payload["evaluations"]["traction"]
    elif change == "extra":
        payload["evaluations"]["founder"] = DATA["branches"][0]["evaluations"][
            "founder"
        ]
    elif change == "errors":
        payload["errors"] = DATA["failure"]["errors"]
    elif change == "failure_payload":
        payload["status"] = "failure"
        payload["errors"] = DATA["failure"]["errors"]
    elif change == "empty_errors":
        payload.update(status="failure", evaluations=None)
    else:
        payload["branch_id"] = "deal_terms"
    with pytest.raises(ValidationError):
        v3.EvaluationBranchResult.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "other"),
        ("candidate_id", "other"),
        ("timestamp", "2026-09-30T10:00:00"),
        ("schema_version", None),
    ],
)
def test_failure_error_contract(field, value):
    payload = deepcopy(DATA["failure"])
    payload["errors"][0][field] = value
    with pytest.raises(ValidationError):
        v3.EvaluationBranchResult.model_validate(payload)


@pytest.mark.parametrize("number", [True, "NaN", "Infinity", " 1", "1_0", -1])
def test_exact_score_rejects_invalid_numbers(number):
    payload = deepcopy(DATA["score"])
    payload["observed_score"] = number
    with pytest.raises(ValidationError):
        v3.ScoreSummary.model_validate(payload)


def test_exact_decimal_and_unknowns_not_filled():
    score = v3.ScoreSummary.model_validate(DATA["score"])
    assert score.observed_score == Decimal("4.00000000000000000001")
    assert (
        json.loads(score.model_dump_json())["observed_score"]
        == "4.00000000000000000001"
    )
    assert score.normalized_score is None
    assert score.dimension_scores["market"].dimension_score_pct is None
    assert score.criterion_points["missing"] is None
    assert v3.CoverageResult.model_validate(DATA["coverage"]).research_ready is None


@pytest.mark.parametrize(
    "change",
    [
        "overlap",
        "missing_applicability",
        "extra_applicability",
        "nested_version",
        "empty_evidence",
        "percentage",
    ],
)
def test_coverage_partition(change):
    payload = deepcopy(DATA["coverage"])
    if change == "overlap":
        payload["missing_criterion_ids"].append("observed")
    elif change == "missing_applicability":
        payload["applicability_assessments"] = {}
    elif change == "extra_applicability":
        payload["applicability_assessments"]["other"] = payload[
            "applicability_assessments"
        ]["na"]
    elif change == "nested_version":
        del payload["applicability_assessments"]["na"]["schema_version"]
    elif change == "empty_evidence":
        payload["applicability_assessments"]["na"]["evidence_ids"] = []
    else:
        payload["weighted_missing_pct"] = "101"
    with pytest.raises(ValidationError):
        v3.CoverageResult.model_validate(payload)


def test_nested_v3_and_baseline_unchanged():
    branch = v3.EvaluationBranchResult.model_validate(DATA["branches"][-1])
    assert type(branch.evaluations["traction"].criteria[0]) is v3.CriterionAssessment
    assert v3.EvaluationSnapshot is baseline.EvaluationSnapshot
    assert v3.Source is baseline.Source
    assert v3.Chunk is baseline.Chunk
    assert v3.Evidence is baseline.Evidence
    assert v3.WorkflowError is baseline.WorkflowError
    assert baseline.CriterionAssessment is not v3.CriterionAssessment
    with pytest.raises(ValidationError):
        baseline.CriterionAssessment.model_validate(DATA["criteria"][2])
    with pytest.raises(ValidationError):
        baseline.InvestmentDecision.model_validate(DATA["decisions"][0])


def test_snapshot_fixture_context_revalidated():
    original = json.loads(
        (Path(__file__).parents[1] / "fixtures/evaluation_contracts.json").read_text()
    )
    snapshot = v3.EvaluationSnapshot.model_validate(
        original["EvaluationSnapshot"], context=CONTEXT
    )
    assert (
        v3.EvaluationSnapshot.model_validate_json(
            snapshot.model_dump_json(), context=CONTEXT
        )
        == snapshot
    )
    assert v3.EvaluationSnapshot.model_validate(snapshot) == snapshot
    for name in (
        "CriterionAssessment",
        "Evaluation",
        "EvaluationResult",
        "ScoreSummary",
        "InvestmentDecision",
    ):
        model = getattr(baseline, name)
        value = model.model_validate(original[name], context=CONTEXT)
        assert (
            model.model_validate_json(value.model_dump_json(), context=CONTEXT) == value
        )
    source = dict(
        schema_version="v3-fixture",
        source_id="source-synthetic",
        title="Synthetic",
        source_kind="web",
        url="fixture://source",
        retrieved_at="2026-09-30T10:00:00+09:00",
        content_hash="synthetic-not-computed",
        language="ko",
        access_notes="Offline only",
        bibliographic_metadata={},
    )
    validated = v3.Source.model_validate(source, context=CONTEXT)
    with pytest.raises(ValidationError):
        v3.Source.model_validate(validated)
    assert (
        v3.Source.model_validate_json(validated.model_dump_json(), context=CONTEXT)
        == validated
    )
