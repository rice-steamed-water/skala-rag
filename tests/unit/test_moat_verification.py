"""Synthetic review registries only; never live approval evidence."""

from copy import deepcopy
from dataclasses import replace

import pytest
from tests.fixtures.loader import load_common_fixtures
from tests.unit.test_moat import POLICY, RUBRIC, _artifact, _closed_snapshot, _review

from skala_rag.agents.evaluation import output_from_evaluation
from skala_rag.agents.moat_verification import (
    core_artifact_digest,
    validate_core_artifact,
    validate_reviewed_anchor,
)


@pytest.mark.parametrize(
    "mutation", ["anchor", "band", "rule", "weight", "minimum", "extra"]
)
def test_artifact_content_changes_rejected(mutation):
    rubric = deepcopy(RUBRIC)
    if mutation == "anchor":
        rubric["dimensions"]["moat"]["criteria"]["moat.ip"]["anchors"][5] = "changed"
    elif mutation == "band":
        rubric["dimensions"]["market"]["criteria"]["market.size"]["bands"][0]["max"] = 1
    elif mutation == "rule":
        rubric["common_rules"]["self_claim_max_rating"] = 5
    elif mutation == "weight":
        rubric["dimensions"]["moat"]["weight"] = 1
    elif mutation == "minimum":
        rubric["dimensions"]["moat"]["criteria"]["moat.data"]["minimum_evidence"] = []
    else:
        rubric["unknown_override"] = True
    with pytest.raises(ValueError, match="content differs"):
        validate_core_artifact(rubric, _artifact(), lambda a: True)


@pytest.mark.parametrize("kind", ["missing", "tampered", "rejected"])
def test_public_approved_entry_rejects_before_model_call(kind):
    from datetime import UTC, datetime

    from skala_rag.agents.moat import evaluate_moat
    from skala_rag.fakes import FakeClock, FakeLLM

    fixtures = load_common_fixtures(POLICY)
    snapshot = _closed_snapshot(next(iter(fixtures.snapshots.values())))
    rubric = deepcopy(RUBRIC)
    rubric["status"] = "approved"
    if kind == "tampered":
        rubric["common_rules"]["self_claim_max_rating"] = 5
    llm = FakeLLM([])
    with pytest.raises(ValueError):
        evaluate_moat(
            snapshot,
            rubric=rubric,
            policy=POLICY,
            llm=llm,
            clock=FakeClock(datetime(2026, 9, 30, tzinfo=UTC)),
            schema_version="fixture-1",
            verify_observation=lambda c, e: True,
            verified_patents={},
            independent_comparisons={},
            artifact_approval=None if kind == "missing" else _artifact(),
            artifact_verifier=lambda a: kind != "rejected",
        )
    assert llm.calls == []


def test_status_and_key_order_only_do_not_change_artifact():
    rubric = dict(reversed(list(RUBRIC.items())))
    rubric["status"] = "approved"
    assert core_artifact_digest(rubric) == core_artifact_digest(RUBRIC)
    validate_core_artifact(rubric, _artifact(), lambda a: True)


@pytest.mark.parametrize("reply", [False, None, 1])
def test_hash_match_does_not_approve_artifact(reply):
    with pytest.raises(ValueError, match="rejected"):
        validate_core_artifact(RUBRIC, _artifact(), lambda a: reply)


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "rating",
        "criterion",
        "evidence",
        "snapshot",
        "source",
        "artifact",
        "boolean",
        "duplicate",
        "rejected",
    ],
)
def test_review_receipt_exact_binding(change):
    fixtures = load_common_fixtures(POLICY)
    snapshot = _closed_snapshot(next(iter(fixtures.snapshots.values())))
    output = output_from_evaluation(
        fixtures.evaluations[
            f"{snapshot.candidate_id}:{snapshot.evaluation_round}:moat"
        ]
    )
    criterion = output.criteria[0]
    receipt = _review(snapshot, criterion)
    rubric = deepcopy(RUBRIC)
    if change == "rating":
        receipt = replace(receipt, rating=1 if criterion.rating != 1 else 2)
    elif change == "criterion":
        receipt = replace(receipt, criterion_id="moat.ip")
    elif change == "evidence":
        receipt = replace(receipt, evidence_ids=("unknown",))
    elif change == "snapshot":
        snapshot = snapshot.model_copy(update={"snapshot_id": "different"})
    elif change == "source":
        sid = snapshot.evidence[criterion.evidence_ids[0]].source_id
        sources = dict(snapshot.sources)
        sources[sid] = sources[sid].model_copy(update={"title": "changed content"})
        snapshot = snapshot.model_copy(update={"sources": sources})
    elif change == "artifact":
        rubric["common_rules"]["self_claim_max_rating"] = 5
    elif change == "boolean":
        receipt = True
    elif change == "duplicate":
        receipt = replace(receipt, evidence_ids=receipt.evidence_ids * 2)
    assert validate_reviewed_anchor(
        receipt,
        rubric=rubric,
        snapshot=snapshot,
        criterion=criterion,
        verifier=lambda r: change != "rejected",
    ) is (change == "none")
