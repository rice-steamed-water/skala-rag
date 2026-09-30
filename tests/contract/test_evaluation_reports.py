"""Synthetic DTO boundary tests; not policy or controller behavior tests."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from skala_rag import contracts


@pytest.fixture
def evaluation_payloads(payloads):
    examples = json.loads(
        (Path(__file__).parents[1] / "fixtures/evaluation_contracts.json").read_text()
    )
    snapshot = examples["EvaluationSnapshot"]
    snapshot["evidence_ids"] = ["ev-synthetic"]
    for field, name, identity in (
        ("evidence", "Evidence", "evidence_id"),
        ("sources", "Source", "source_id"),
        ("chunks", "Chunk", "chunk_id"),
        ("retrieval_records", "RetrievalRecord", "retrieval_id"),
    ):
        snapshot[field] = {payloads[name][identity]: copy.deepcopy(payloads[name])}
    examples["Evaluation"]["criteria"] = [
        copy.deepcopy(examples["CriterionAssessment"])
    ]
    examples["EvaluationResult"] = {
        **{
            key: examples["Evaluation"][key]
            for key in (
                "schema_version",
                "run_id",
                "candidate_id",
                "dimension",
                "evaluation_round",
                "snapshot_id",
                "evidence_revision",
                "policy_version",
            )
        },
        "status": "success",
        "evaluation": copy.deepcopy(examples["Evaluation"]),
        "errors": [],
    }
    examples["ReportInput"]["candidate_outcomes"] = [
        copy.deepcopy(examples["CandidateOutcome"])
    ]
    examples["ReportInput"]["permitted_evidence_ids"] = ["ev-synthetic"]
    examples["ReportContext"] = {
        "schema_version": "synthetic-1",
        "context_id": "context-synthetic",
        "input": copy.deepcopy(examples["ReportInput"]),
        "snapshots": {"snapshot-synthetic": copy.deepcopy(snapshot)},
        "eligibility_results": {
            "elig-synthetic": copy.deepcopy(payloads["EligibilityResult"])
        },
        "evaluations": {
            "co-synthetic:1:technology": copy.deepcopy(examples["Evaluation"])
        },
        "score_summaries": {"score-synthetic": copy.deepcopy(examples["ScoreSummary"])},
        "decisions": {
            "decision-synthetic": copy.deepcopy(examples["InvestmentDecision"])
        },
        **{
            field: copy.deepcopy(snapshot[field])
            for field in (
                "evidence",
                "sources",
                "chunks",
                "retrieval_records",
            )
        },
        "errors": {"error-synthetic": copy.deepcopy(examples["WorkflowError"])},
    }
    examples["RunManifest"] = {
        "schema_version": "synthetic-1",
        "run_id": "run-synthetic",
        "run_input": copy.deepcopy(payloads["RunInput"]),
        "code_revision": None,
        "uncommitted": True,
        "policy_version": "unapproved-fixture",
        "corpus_version": "synthetic-corpus",
        "prompt_versions": {},
        "model_versions": {},
        "corpus_hash": "synthetic-hash",
        "tool_status": {"synthetic": "unavailable"},
        "budgets": {},
        "usage": {},
        "artifacts": {"markdown": copy.deepcopy(examples["ArtifactMetadata"])},
        "validation_results": {
            "structure": copy.deepcopy(examples["ValidationResult"]),
            "semantic": copy.deepcopy(examples["ReportJudgement"]),
        },
        "workflow_status": "completed",
        "run_outcome": "no_recommendation",
    }
    return examples


MODELS = [
    "CriterionAssessment",
    "EvaluationSnapshot",
    "Evaluation",
    "EvaluationResult",
    "ScoreSummary",
    "InvestmentDecision",
    "CandidateOutcome",
    "ReportInput",
    "ReportContext",
    "ReportDraft",
    "ValidationResult",
    "ReportJudgement",
    "WorkflowError",
    "RunManifest",
    "ArtifactMetadata",
    "ValidationErrorDetail",
    "ReportFinding",
]


@pytest.mark.parametrize("name", MODELS)
def test_public_dto_roundtrip(name, evaluation_payloads):
    model = getattr(contracts, name)
    instance = model.model_validate(evaluation_payloads[name])
    assert model.model_validate_json(instance.model_dump_json()) == instance
    assert json.loads(instance.model_dump_json())["schema_version"] == "synthetic-1"
    assert "schema_version" in model.model_json_schema()["required"]


@pytest.mark.parametrize("name", MODELS)
@pytest.mark.parametrize("mutation", ["no_schema", "blank_schema", "extra"])
def test_schema_and_unknown_fields(name, mutation, evaluation_payloads):
    data = evaluation_payloads[name]
    if mutation == "no_schema":
        del data["schema_version"]
    elif mutation == "blank_schema":
        data["schema_version"] = " "
    else:
        data["unexpected"] = True
    with pytest.raises(ValidationError):
        getattr(contracts, name).model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "another-run"),
        ("candidate_id", "another-company"),
        ("dimension", "market"),
        ("evaluation_round", 2),
        ("snapshot_id", "another-snapshot"),
        ("evidence_revision", 1),
        ("policy_version", "another-policy"),
    ],
)
def test_envelope_mismatch(field, value, evaluation_payloads):
    data = evaluation_payloads["EvaluationResult"]
    data["evaluation"][field] = value
    with pytest.raises(ValidationError, match="envelope mismatch"):
        contracts.EvaluationResult.model_validate(data)


@pytest.mark.parametrize(
    "status,has_evaluation,has_errors,valid",
    [
        ("success", True, False, True),
        ("success", False, False, False),
        ("success", True, True, False),
        ("success", False, True, False),
        ("failure", False, True, True),
        ("failure", False, False, False),
        ("failure", True, True, False),
        ("failure", True, False, False),
    ],
)
def test_envelope_success_failure(
    status,
    has_evaluation,
    has_errors,
    valid,
    evaluation_payloads,
):
    data = evaluation_payloads["EvaluationResult"]
    data["status"] = status
    data["evaluation"] = data["evaluation"] if has_evaluation else None
    data["errors"] = [evaluation_payloads["WorkflowError"]] if has_errors else []
    if valid:
        assert contracts.EvaluationResult.model_validate(data).status == status
    else:
        with pytest.raises(ValidationError):
            contracts.EvaluationResult.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "another-run"),
        ("candidate_id", "another-company"),
    ],
)
def test_failure_error_identity(field, value, evaluation_payloads):
    data = evaluation_payloads["EvaluationResult"]
    error = evaluation_payloads["WorkflowError"]
    error[field] = value
    data.update(status="failure", evaluation=None, errors=[error])
    with pytest.raises(ValidationError, match="error must belong"):
        contracts.EvaluationResult.model_validate(data)


@pytest.mark.parametrize(
    "name,field",
    [
        ("EvaluationSnapshot", "evaluation_round"),
        ("Evaluation", "evidence_revision"),
        ("EvaluationResult", "evaluation_round"),
        ("ScoreSummary", "evidence_revision"),
        ("ReportDraft", "revision"),
        ("WorkflowError", "attempt"),
    ],
)
@pytest.mark.parametrize("value", [True, -1, "1", 1.2])
def test_strict_counters(name, field, value, evaluation_payloads):
    data = evaluation_payloads[name]
    data[field] = value
    with pytest.raises(ValidationError):
        getattr(contracts, name).model_validate(data)


@pytest.mark.parametrize(
    "name,field,identity",
    [
        ("EvaluationSnapshot", "sources", "source_id"),
        ("EvaluationSnapshot", "chunks", "chunk_id"),
        ("EvaluationSnapshot", "retrieval_records", "retrieval_id"),
        ("ReportContext", "snapshots", "snapshot_id"),
        ("ReportContext", "eligibility_results", "eligibility_result_id"),
        ("ReportContext", "score_summaries", "score_summary_id"),
        ("ReportContext", "decisions", "decision_id"),
        ("ReportContext", "evidence", "evidence_id"),
        ("ReportContext", "sources", "source_id"),
        ("ReportContext", "chunks", "chunk_id"),
        ("ReportContext", "retrieval_records", "retrieval_id"),
        ("ReportContext", "errors", "error_id"),
    ],
)
def test_payload_map_identity(name, field, identity, evaluation_payloads):
    data = evaluation_payloads[name]
    next(iter(data[field].values()))[identity] = "wrong-id"
    with pytest.raises(ValidationError, match="map key"):
        getattr(contracts, name).model_validate(data)


@pytest.mark.parametrize(
    "mutation", ["missing_id", "extra_id", "duplicate_id", "wrong_core"]
)
def test_snapshot_evidence_keys(mutation, evaluation_payloads):
    data = evaluation_payloads["EvaluationSnapshot"]
    if mutation == "missing_id":
        data["evidence_ids"] = []
    elif mutation == "extra_id":
        data["evidence_ids"].append("ev-unknown")
    elif mutation == "duplicate_id":
        data["evidence_ids"].append("ev-synthetic")
    else:
        data["evidence"]["ev-synthetic"]["evidence_id"] = "another-id"
    with pytest.raises(ValidationError):
        contracts.EvaluationSnapshot.model_validate(data)


def test_context_keys_and_permissions(evaluation_payloads):
    data = evaluation_payloads["ReportContext"]
    data["evaluations"]["wrong-key"] = data["evaluations"].pop(
        "co-synthetic:1:technology"
    )
    with pytest.raises(ValidationError, match="evaluations map key"):
        contracts.ReportContext.model_validate(data)
    data["evaluations"] = {}
    data["input"]["permitted_evidence_ids"] = []
    with pytest.raises(ValidationError, match="permitted_evidence_ids"):
        contracts.ReportContext.model_validate(data)


@pytest.mark.parametrize("field", ["cited_evidence_ids", "reference_source_ids"])
def test_draft_citation_metadata_is_unique(field, evaluation_payloads):
    data = evaluation_payloads["ReportDraft"]
    data[field] = ["synthetic-id", "synthetic-id"]
    with pytest.raises(ValidationError, match="must be unique"):
        contracts.ReportDraft.model_validate(data)


def test_snapshot_detached_payloads(evaluation_payloads):
    data = evaluation_payloads["EvaluationSnapshot"]
    instance = contracts.EvaluationSnapshot.model_validate(data)
    data["evidence"]["ev-synthetic"]["limitations"].append("new state limitation")
    assert "new state limitation" not in instance.evidence["ev-synthetic"].limitations
    assert instance.evidence["ev-synthetic"].provenance[0].method == "rag"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "3", -1])
def test_score_numbers(value, evaluation_payloads):
    data = evaluation_payloads["ScoreSummary"]
    data["criterion_points"]["technology.integration"] = value
    with pytest.raises(ValidationError):
        contracts.ScoreSummary.model_validate(data)


def test_missing_scores_keep_null(evaluation_payloads):
    instance = contracts.ScoreSummary.model_validate(
        evaluation_payloads["ScoreSummary"]
    )
    assert instance.criterion_points["technology.integration"] is None
    assert instance.dimension_ratings["technology"] is None
    assert instance.observed_score == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("dimension_ratings", {"technology": 0}),
        ("dimension_ratings", {"technology": 6}),
        ("dimension_ratings", {"technology": True}),
        ("dimension_ratings", {"finance": 3}),
        ("coverage_pct", 101),
        ("coverage_pct", -1),
        ("observed_score", float("nan")),
        ("missing_weight", -1),
    ],
)
def test_score_observation_bounds(field, value, evaluation_payloads):
    data = evaluation_payloads["ScoreSummary"]
    data[field] = value
    with pytest.raises(ValidationError):
        contracts.ScoreSummary.model_validate(data)


def test_dimension_fraction_and_text_preserved(evaluation_payloads):
    data = evaluation_payloads["ScoreSummary"]
    data["dimension_ratings"]["technology"] = 2.01
    assert (
        contracts.ScoreSummary.model_validate(data).dimension_ratings["technology"]
        == 2.01
    )
    data = evaluation_payloads["CriterionAssessment"]
    data["rationale"] = "  original whitespace  "
    assert (
        contracts.CriterionAssessment.model_validate(data).rationale
        == data["rationale"]
    )


@pytest.mark.parametrize(
    "mode,selected,valid",
    [
        ("single_candidate", "co-synthetic", True),
        ("single_candidate", None, False),
        ("single_candidate", "co-unknown", False),
        ("no_recommendation", None, True),
        ("no_recommendation", "co-synthetic", False),
    ],
)
def test_report_selection(mode, selected, valid, evaluation_payloads):
    data = evaluation_payloads["ReportInput"]
    data.update(mode=mode, selected_candidate_id=selected)
    if valid:
        contracts.ReportInput.model_validate(data)
    else:
        with pytest.raises(ValidationError):
            contracts.ReportInput.model_validate(data)


@pytest.mark.parametrize(
    "valid,has_errors,accepted",
    [
        (True, False, True),
        (True, True, False),
        (False, True, True),
        (False, False, False),
    ],
)
def test_validation_envelope(valid, has_errors, accepted, evaluation_payloads):
    data = evaluation_payloads["ValidationResult"]
    data["valid"] = valid
    data["errors"] = (
        [evaluation_payloads["ValidationErrorDetail"]] if has_errors else []
    )
    if accepted:
        contracts.ValidationResult.model_validate(data)
    else:
        with pytest.raises(ValidationError):
            contracts.ValidationResult.model_validate(data)


@pytest.mark.parametrize(
    "name,field,value",
    [
        ("WorkflowError", "timestamp", "2026-09-01T12:00:00"),
        ("WorkflowError", "timestamp", 123456),
        ("WorkflowError", "retryable", 1),
        ("EvaluationSnapshot", "as_of", "2026-09-01T12:00:00Z"),
        ("ReportInput", "as_of", 0),
        ("ValidationResult", "valid", "true"),
        ("ReportJudgement", "verdict", "approved"),
        ("CandidateOutcome", "status", "evaluating"),
        ("InvestmentDecision", "label", "eligible"),
        ("Evaluation", "dimension", "finance"),
    ],
)
def test_dates_enums_booleans(name, field, value, evaluation_payloads):
    data = evaluation_payloads[name]
    data[field] = value
    with pytest.raises(ValidationError):
        getattr(contracts, name).model_validate(data)


@pytest.mark.parametrize(
    "updates",
    [
        {"code_revision": None, "uncommitted": False},
        {"uncommitted": "true"},
        {"policy_version": "different"},
        {"corpus_version": "different"},
        {"run_outcome": None},
        {"workflow_status": "running"},
        {"workflow_status": "unknown"},
        {"tool_status": {"client": object()}},
        {"usage": {"nested": {"cost": float("inf")}}},
    ],
)
def test_manifest_metadata(updates, evaluation_payloads):
    data = evaluation_payloads["RunManifest"]
    data.update(updates)
    with pytest.raises(ValidationError):
        contracts.RunManifest.model_validate(data)


def test_manifest_running_and_code_revision(evaluation_payloads):
    data = evaluation_payloads["RunManifest"]
    data.update(
        workflow_status="running",
        run_outcome=None,
        code_revision="synthetic-revision",
        uncommitted=False,
    )
    contracts.RunManifest.model_validate(data)


def test_nested_instance_revalidation_and_fixture_context(evaluation_payloads):
    data = evaluation_payloads["EvaluationSnapshot"]
    data["evidence"]["ev-synthetic"]["locator"] = "fixture://synthetic"
    instance = contracts.EvaluationSnapshot.model_validate(
        data,
        context={"execution_mode": "fixture"},
    )
    with pytest.raises(ValidationError):
        contracts.EvaluationSnapshot.model_validate(instance)
    contracts.EvaluationSnapshot.model_validate_json(
        instance.model_dump_json(),
        context={"execution_mode": "fixture"},
    )
    result = contracts.EvaluationResult.model_validate(
        evaluation_payloads["EvaluationResult"]
    )
    result.evaluation.criteria[0].rating = 6
    with pytest.raises(ValidationError):
        contracts.EvaluationResult.model_validate(result)
