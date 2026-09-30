import hashlib
import json

import pytest
from pydantic import ValidationError

from skala_rag.contracts.assessment import CriterionAssessment
from skala_rag.contracts.ids import (
    decision_id,
    eligibility_result_id,
    evaluation_key,
    score_summary_id,
    snapshot_id,
)


def assessment(**updates):
    return CriterionAssessment.model_validate(
        {
            "schema_version": "fixture-1",
            "criterion_id": "technology.integration",
            "status": "observed",
            "rating": 3,
            "evidence_ids": ["ev-fixture-1"],
            "rationale": "가상 근거에 기반한 평가",
            **updates,
        }
    )


@pytest.mark.parametrize("rating", [0, 6, 2.5, float("nan"), True, "3", None])
def test_invalid_rating(rating):
    with pytest.raises(ValidationError):
        assessment(rating=rating)


@pytest.mark.parametrize(
    "updates",
    [
        {"rationale": " "},
        {"evidence_ids": []},
        {"evidence_ids": ["ev-fixture-1", "ev-fixture-1"]},
        {"missing_reason": "unknown"},
        {"status": "missing", "rating": None},
        {"status": "missing", "missing_reason": "자료 부족"},
    ],
)
def test_invalid_assessment(updates):
    with pytest.raises(ValidationError):
        assessment(**updates)


def test_assessment_roundtrip():
    for model in [
        assessment(),
        assessment(
            status="missing",
            rating=None,
            missing_reason="자료 부족",
            evidence_ids=[],
        ),
    ]:
        assert CriterionAssessment.model_validate_json(model.model_dump_json()) == model


def test_id_encoding_and_generation():
    payload = json.dumps(
        ["skala-rag-id-v1", "snapshot", "run", "회사", 1, 0, "p1"],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    expected = "snapshot-v1-" + hashlib.sha256(payload).hexdigest()
    assert snapshot_id("run", "회사", 1, 0, "p1") == expected
    assert snapshot_id("run", "회사", 2, 0, "p1") != expected
    assert snapshot_id("run", "회사", 1, 1, "p1") != expected
    assert snapshot_id("a:b", "c", 1, 0, "p") != snapshot_id("a", "b:c", 1, 0, "p")
    score = score_summary_id("run", "co", 1, "p")
    assert score == score_summary_id("run", "co", 1, "p")
    assert score != score_summary_id("run", "co", 2, "p")
    assert decision_id(score) == decision_id(score)
    assert decision_id(score) != decision_id(score_summary_id("run", "co", 2, "p"))
    assert eligibility_result_id("run", "co", 0, "p") != eligibility_result_id(
        "run", "co", 1, "p"
    )
    assert evaluation_key("co", 1, "technology") == "co:1:technology"


@pytest.mark.parametrize("counter", [-1, True, "1", 1.5])
def test_invalid_counter(counter):
    with pytest.raises(ValueError):
        snapshot_id("run", "co", counter, 0, "p")


def test_invalid_key_and_id_text():
    with pytest.raises(ValueError):
        evaluation_key("co:1", 1, "technology")
    with pytest.raises(ValueError):
        decision_id(" ")
