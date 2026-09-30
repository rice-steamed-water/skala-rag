"""Offline Moat fixtures; not investment or live evidence."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from tests.fixtures.loader import load_common_fixtures

from skala_rag.agents.evaluation import output_from_evaluation
from skala_rag.fakes import FakeClock, FakeLLM
from skala_rag.scoring.catalog import load_policy

ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
RUBRIC = yaml.safe_load((ROOT / "configs/rubrics/core.yaml").read_text())


def _closed_snapshot(snapshot):
    # Common legacy fixture chunk lacks excerpts; enrich only this synthetic test.
    chunks = {
        cid: chunk.model_copy(
            update={
                "text": "\n".join(e.excerpt for e in snapshot.evidence.values()),
                "candidate_ids": [snapshot.candidate_id],
                "corpus_version": snapshot.corpus_version,
            }
        )
        for cid, chunk in snapshot.chunks.items()
    }
    return snapshot.model_copy(update={"chunks": chunks}, deep=True)


@pytest.mark.parametrize(
    "patent_state",
    ["absent", "other_owner", "unknown_status", "valid", "no_comparison"],
)
def test_snapshot_only_moat_bridge(patent_state):
    from skala_rag.agents.moat import evaluate_moat

    fixtures = load_common_fixtures(POLICY)
    snapshot = _closed_snapshot(next(iter(fixtures.snapshots.values())))
    output = output_from_evaluation(
        fixtures.evaluations[
            f"{snapshot.candidate_id}:{snapshot.evaluation_round}:moat"
        ]
    )
    from skala_rag.agents.moat import IndependentComparison, VerifiedPatent

    cited = {c.criterion_id: tuple(c.evidence_ids) for c in output.criteria}
    patents = (
        {}
        if patent_state == "absent"
        else {
            "moat.ip": VerifiedPatent(
                "other" if patent_state == "other_owner" else snapshot.candidate_id,
                "unknown" if patent_state == "unknown_status" else "active",
                "verified fixture claim scope",
                cited["moat.ip"],
            )
        }
    )
    comparisons = (
        {}
        if patent_state == "no_comparison"
        else {
            "moat.differentiation": IndependentComparison(
                "competitor", cited["moat.differentiation"]
            )
        }
    )
    result = evaluate_moat(
        snapshot,
        rubric=RUBRIC,
        policy=POLICY,
        llm=FakeLLM([output]),
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=UTC)),
        schema_version="fixture-1",
        verify_observation=lambda criterion, evidence: True,
        verified_patents=patents,
        independent_comparisons=comparisons,
    )
    assert result.branch_id == "moat"
    assert result.status == ("success" if patent_state == "valid" else "failure")
    assert result.snapshot_id == snapshot.snapshot_id


@pytest.mark.parametrize("kind", ["missing", "unknown", "foreign", "timeout"])
def test_offline_boundaries(kind):
    from skala_rag.agents.moat import evaluate_moat
    from skala_rag.contracts.error_codes import ErrorCode
    from skala_rag.contracts.interfaces import LLMError

    fixtures = load_common_fixtures(POLICY)
    snapshot = _closed_snapshot(next(iter(fixtures.snapshots.values())))
    output = output_from_evaluation(
        fixtures.evaluations[
            f"{snapshot.candidate_id}:{snapshot.evaluation_round}:moat"
        ]
    ).model_dump()
    for criterion in output["criteria"]:
        criterion.update(
            status="missing",
            rating=None,
            evidence_ids=[],
            missing_reason="not_disclosed",
        )
    if kind in {"unknown", "foreign"}:
        criterion = output["criteria"][0]
        eid = "unknown"
        if kind == "foreign":
            base = next(iter(snapshot.evidence.values()))
            foreign = base.model_copy(
                update={"evidence_id": "foreign", "candidate_id": "other"}
            )
            snapshot = snapshot.model_copy(
                update={
                    "evidence_ids": [*snapshot.evidence_ids, "foreign"],
                    "evidence": {**snapshot.evidence, "foreign": foreign},
                }
            )
            eid = "foreign"
        criterion.update(
            status="observed", rating=3, evidence_ids=[eid], missing_reason=None
        )
    llm = FakeLLM(
        [LLMError(ErrorCode.TOOL_TIMEOUT, "timeout")] if kind == "timeout" else [output]
    )
    result = evaluate_moat(
        snapshot,
        rubric=RUBRIC,
        policy=POLICY,
        llm=llm,
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=UTC)),
        schema_version="fixture-1",
        verify_observation=lambda criterion, evidence: True,
        verified_patents={},
        independent_comparisons={},
    )
    assert result.status == ("success" if kind == "missing" else "failure")
    if kind == "missing":
        assert set(result.evaluations) == {"moat"}
        assert all(c.status == "missing" for c in result.evaluations["moat"].criteria)
    else:
        assert result.evaluations is None
    assert len(llm.calls) == 1


def _approved_case(
    *, rubric_version="core-0.1.0", mutate=None, verifier=None, policy=POLICY
):
    from copy import deepcopy

    from skala_rag.agents.moat import evaluate_moat

    fixtures = load_common_fixtures(POLICY)
    snapshot = _closed_snapshot(next(iter(fixtures.snapshots.values())))
    output = output_from_evaluation(
        fixtures.evaluations[
            f"{snapshot.candidate_id}:{snapshot.evaluation_round}:moat"
        ]
    ).model_dump()
    if mutate:
        mutate(output)
    rubric = deepcopy(RUBRIC)
    rubric.update(status="approved", rubric_version=rubric_version)
    llm = FakeLLM([output])
    calls = []

    def verify(criterion, evidence):
        calls.append((criterion.criterion_id, criterion.rating, set(evidence)))
        return True if verifier is None else verifier(criterion, evidence)

    result = evaluate_moat(
        snapshot,
        rubric=rubric,
        policy=policy,
        llm=llm,
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=UTC)),
        schema_version="fixture-1",
        verify_observation=verify,
        verified_patents={},
        independent_comparisons={},
    )
    return result, calls, llm


@pytest.mark.parametrize("rating", [1, 2, 3, 4])
def test_approved_core_anchors_do_not_require_active_rights_or_independent_comparison(
    rating,
):
    def mutate(output):
        for criterion in output["criteria"]:
            criterion["rating"] = rating

    result, calls, llm = _approved_case(mutate=mutate)
    assert result.status == "success"
    assert result.evaluations is not None
    assert result.evaluations["moat"].rubric_version == "core-0.1.0"
    assert len(calls) == 4
    assert all(cited for _, _, cited in calls)
    assert len(llm.calls) == 1


@pytest.mark.parametrize("kind", ["not_applicable", "unknown", "wrong_criterion"])
def test_approved_core_rejects_invalid_status_or_source(kind):
    def mutate(output):
        criterion = output["criteria"][0]
        if kind == "not_applicable":
            criterion.update(status=kind, rating=None)
        elif kind == "unknown":
            criterion["evidence_ids"] = ["unknown"]
        else:
            criterion["evidence_ids"] = output["criteria"][1]["evidence_ids"]

    result, _, llm = _approved_case(mutate=mutate)
    assert result.status == "failure"
    assert result.evaluations is None
    assert len(llm.calls) == 1


@pytest.mark.parametrize("rating", [1, 2, 5])
def test_approved_core_requires_semantic_verifier_for_negative_and_independent_evidence(
    rating,
):
    # Evidence/Source DTOs have no verified negative-fact or independence field.
    # Upstream verifier rejects unconfirmed weakness or self-claim-only rating 5.
    def mutate(output):
        output["criteria"][0]["rating"] = rating

    result, calls, _ = _approved_case(
        mutate=mutate,
        verifier=lambda criterion, evidence: criterion.rating not in {1, 2, 5},
    )
    assert result.status == "failure"
    assert result.evaluations is None
    assert calls[0][1] == rating
    assert "MOAT_RUBRIC_UNVERIFIED" in result.errors[0].message_redacted


def test_approved_core_unknown_version_is_not_implicitly_approved():
    with pytest.raises(ValueError, match="offline fixture only"):
        _approved_case(rubric_version="core-future")


def test_approved_core_does_not_approve_scoring_policy():
    with pytest.raises(ValueError, match="offline fixture only"):
        _approved_case(policy=POLICY.model_copy(update={"status": "approved"}))


@pytest.mark.parametrize("receipt", [False, None, 1])
def test_approved_core_requires_exact_true_verifier_receipt(receipt):
    result, _, _ = _approved_case(verifier=lambda criterion, evidence: receipt)
    assert result.status == "failure"
    assert result.evaluations is None


def test_approved_core_missing_is_not_replaced_with_rating():
    def mutate(output):
        for criterion in output["criteria"]:
            criterion.update(
                status="missing",
                rating=None,
                evidence_ids=[],
                missing_reason="not_disclosed",
            )

    result, calls, _ = _approved_case(mutate=mutate)
    assert result.status == "success"
    assert result.evaluations is not None
    assert all(c.rating is None for c in result.evaluations["moat"].criteria)
    assert calls == []


def test_approved_core_rating_five_requires_positive_verifier_receipt():
    def mutate(output):
        for criterion in output["criteria"]:
            criterion["rating"] = 5

    result, calls, _ = _approved_case(mutate=mutate)
    assert result.status == "success"
    assert len(calls) == 4


@pytest.mark.parametrize(
    "admission", ["accepted", "rejected", "actual", "plain", "mismatch"]
)
def test_loaded_approved_fixture_consumer(admission):
    from copy import deepcopy

    from tests.unit.test_approved_policy import approval_payload

    from skala_rag.agents.moat import evaluate_moat_approved_fixture
    from skala_rag.scoring.approved_policy import PolicyApprovals

    fixtures = load_common_fixtures(POLICY)
    snapshot = _closed_snapshot(next(iter(fixtures.snapshots.values())))
    snapshot = snapshot.model_copy(update={"policy_version": "v3-operational-1.0.0"})
    output = output_from_evaluation(
        fixtures.evaluations[
            f"{snapshot.candidate_id}:{snapshot.evaluation_round}:moat"
        ]
    )
    rubric = deepcopy(RUBRIC)
    rubric.update(status="approved", rubric_version="core-0.1.0")
    llm = FakeLLM([output])
    seen = []

    def verify(evidence, operational):
        seen.append(evidence.scope)
        return admission != "rejected"

    kwargs = dict(
        policy_path=ROOT / "configs/scoring.v3.json",
        approvals=PolicyApprovals.model_validate(approval_payload()),
        approval_verifier=verify,
        rubric=rubric,
        llm=object() if admission == "plain" else llm,
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=UTC)),
        schema_version="fixture-1",
        verify_observation=lambda c, e: True,
        actual_runtime=admission == "actual",
    )
    if admission == "mismatch":
        snapshot = snapshot.model_copy(update={"policy_version": "wrong"})
    if admission != "accepted":
        with pytest.raises(ValueError):
            evaluate_moat_approved_fixture(snapshot, **kwargs)
        assert llm.calls == []
        return
    result = evaluate_moat_approved_fixture(snapshot, **kwargs)
    assert result.status == "success"
    assert result.policy_version == "v3-operational-1.0.0"
    assert seen == ["operational", "core", "finance"]
    assert len(llm.calls) == 1


def test_constructed_approved_contract_is_not_loading_proof():
    from skala_rag.scoring.approved_policy import ApprovedScoringPolicy

    constructed = ApprovedScoringPolicy.model_construct(execution_mode="fixture")
    with pytest.raises(ValueError, match="no trusted-loading receipt"):
        _approved_case(policy=constructed)
