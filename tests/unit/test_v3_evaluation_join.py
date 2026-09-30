"""Pure join/reducer adversarial tests; synthetic evidence only."""

from copy import deepcopy

import pytest
from tests.integration.test_v3_parallel_evaluation import case as fixture_factory

from skala_rag.contracts.v3 import BRANCH_DIMENSIONS, EvaluationBranchResult
from skala_rag.graph.evaluation_v3 import (
    archive_advance_failure_v3,
    join_evaluation_v3,
)
from skala_rag.graph.reducers import MergeConflict
from skala_rag.graph.reducers_v3 import branch_key, merge_branch_results_v3


@pytest.fixture(name="case")
def v3_case():
    return fixture_factory.__wrapped__()


def payloads(case):
    snapshot, policy, _, result = case
    values = [result(b, snapshot) for b in reversed(BRANCH_DIMENSIONS)]
    return (
        snapshot,
        {branch_key(r): r.model_dump(mode="json") for r in values},
        dict(
            criteria=policy.criteria,
            industry_evidence_dimensions=set(),
            applicability_validator=None,
        ),
    )


def test_shuffled_join_and_duplicate_idempotency(case):
    snapshot, results, options = payloads(case)
    merged = merge_branch_results_v3(results, deepcopy(results))
    assert merged == results
    assert len(join_evaluation_v3(snapshot, merged, **options)["evaluations_v3"]) == 6


def test_conflicting_duplicate_is_detected(case):
    _, results, _ = payloads(case)
    incoming = deepcopy(results)
    next(iter(incoming.values()))["evaluations"]["traction"]["caveats"] = ["Conflict"]
    with pytest.raises(MergeConflict):
        merge_branch_results_v3(results, incoming)


@pytest.mark.parametrize(
    "field", ["run_id", "snapshot_id", "policy_version", "evidence_revision"]
)
def test_same_round_identity_corruption_rejected(case, field):
    snapshot, results, options = payloads(case)
    raw = next(iter(results.values()))
    value = 99 if field == "evidence_revision" else "other"
    raw[field] = value
    for evaluation in raw["evaluations"].values():
        evaluation[field] = value
    result = EvaluationBranchResult.model_validate(raw)
    del results[next(iter(results))]
    results[branch_key(result)] = result.model_dump(mode="json")
    with pytest.raises(ValueError):
        join_evaluation_v3(snapshot, results, **options)


@pytest.mark.parametrize("field", ["candidate_id", "evaluation_round"])
def test_other_candidate_or_stale_round_ignored(case, field):
    snapshot, results, options = payloads(case)
    raw = deepcopy(next(iter(results.values())))
    value = 0 if field == "evaluation_round" else "other-candidate"
    raw[field] = value
    for evaluation in raw["evaluations"].values():
        evaluation[field] = value
    result = EvaluationBranchResult.model_validate(raw)
    results[branch_key(result)] = result.model_dump(mode="json")
    assert len(join_evaluation_v3(snapshot, results, **options)["evaluations_v3"]) == 6


@pytest.mark.parametrize("mode", ["none", "reject", "approve"])
def test_na_requires_supplied_rule_reason_evidence_validator(case, mode):
    snapshot, results, options = payloads(case)
    branch = next(iter(results.values()))
    criterion = branch["evaluations"]["traction"]["criteria"][0]
    criterion.update(
        status="not_applicable",
        rating=None,
        applicability_reason="Synthetic approved fixture rule reason",
        applicability_rule_id="explicit-fixture-rule",
        applicability_evidence_ids=criterion["evidence_ids"],
    )
    calls = []

    def validator(c, s):
        calls.append(
            (
                c.applicability_rule_id,
                c.applicability_reason,
                c.applicability_evidence_ids,
            )
        )
        return mode == "approve" and c.applicability_rule_id == "explicit-fixture-rule"

    options["applicability_validator"] = None if mode == "none" else validator
    if mode != "approve":
        with pytest.raises(ValueError):
            join_evaluation_v3(snapshot, results, **options)
    else:
        out = join_evaluation_v3(snapshot, results, **options)
        assert len(out["evaluations_v3"]) == 6
        assert calls
        assert any(
            c["status"] == "not_applicable"
            for e in out["evaluations_v3"].values()
            for c in e["criteria"]
        )


def test_archive_advance_exact_repeat_noop(case):
    _, _, state, _ = case
    state.update(
        evaluation_status_v3="failure",
        evaluation_failure_ids_v3=["synthetic-error"],
        errors=[
            {
                "schema_version": "synthetic-common-1",
                "error_id": "synthetic-error",
                "candidate_id": state["current_candidate_id"],
            }
        ],
    )
    update = archive_advance_failure_v3(state)
    assert update["candidate_index"] == 1
    assert archive_advance_failure_v3({**state, **update}) == {}


def test_missing_branch_rejected(case):
    snapshot, results, options = payloads(case)
    results.pop(next(iter(results)))
    with pytest.raises(ValueError, match="Missing terminal"):
        join_evaluation_v3(snapshot, results, **options)


@pytest.mark.parametrize("kind", ["criterion", "candidate", "industry", "na_outside"])
def test_actual_evidence_attribution_not_id_membership_only(case, kind):
    snapshot, results, options = payloads(case)
    evidence = next(iter(snapshot.evidence.values()))
    if kind == "criterion":
        evidence.criterion_ids.clear()
    elif kind == "candidate":
        evidence.candidate_id = "other-company"
    elif kind == "industry":
        evidence.scope = "industry"
        evidence.candidate_id = None
    else:
        criterion = next(iter(results.values()))["evaluations"]["traction"]["criteria"][
            0
        ]
        criterion.update(
            status="not_applicable",
            rating=None,
            applicability_reason="Synthetic reason",
            applicability_rule_id="fixture-rule",
            applicability_evidence_ids=["outside"],
        )
        options["applicability_validator"] = lambda c, s: True
    with pytest.raises(ValueError):
        join_evaluation_v3(snapshot, results, **options)


def test_missing_preserved_as_missing_not_zero(case):
    snapshot, results, options = payloads(case)
    criterion = next(iter(results.values()))["evaluations"]["traction"]["criteria"][0]
    criterion.update(
        status="missing",
        rating=None,
        evidence_ids=[],
        missing_reason="Synthetic missing information",
    )
    out = join_evaluation_v3(snapshot, results, **options)
    criteria = [c for e in out["evaluations_v3"].values() for c in e["criteria"]]
    assert (
        next(c for c in criteria if c["criterion_id"] == criterion["criterion_id"])[
            "rating"
        ]
        is None
    )
