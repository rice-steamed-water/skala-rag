"""Synthetic Founder facts and review registry, never person/semantic authority."""

import asyncio
import importlib
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from tests.fixtures.loader import load_common_fixtures
from tests.unit.test_moat import _closed_snapshot

from skala_rag.agents.evaluation import output_from_evaluation
from skala_rag.agents.moat_verification import (
    core_artifact_digest,
    frozen_snapshot_digest,
)
from skala_rag.contracts.evaluation import EvaluationResult
from skala_rag.fakes import FakeClock, FakeLLM
from skala_rag.scoring.approval_registry import pinned_approval_registry
from skala_rag.scoring.approved_policy import load_approved_policy
from skala_rag.scoring.catalog import load_policy

ROOT = Path(__file__).resolve().parents[2]


def case():
    registry = pinned_approval_registry(ROOT)
    policy = load_approved_policy(
        ROOT / "configs/scoring.v3.json",
        approvals=registry.policy_approvals(),
        approval_verifier=registry.verify_policy,
        execution_mode="fixture",
    )
    draft = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
    fixtures = load_common_fixtures(draft)
    # Prepare operational generation before evaluation; never stamp a returned DTO.
    snapshot = _closed_snapshot(next(iter(fixtures.snapshots.values()))).model_copy(
        update={"policy_version": policy.policy_version}, deep=True
    )
    output = output_from_evaluation(
        fixtures.evaluations[
            f"{snapshot.candidate_id}:{snapshot.evaluation_round}:founder"
        ]
    )
    rubric = yaml.safe_load((ROOT / "configs/rubrics/core.yaml").read_text())
    rubric = deepcopy(rubric)
    rubric["status"] = "approved"  # Explicit synthetic copy; main stays proposed.
    people = ("synthetic-founder-1",)
    links = {
        eid: people[0]
        for eid, e in snapshot.evidence.items()
        if e.scope == "company"
        and e.candidate_id == snapshot.candidate_id
        and any(cid.startswith("founder.") for cid in e.criterion_ids)
    }
    receipts = {}
    reviewed = []

    def review(criterion, evidence):
        cls = importlib.import_module(
            "skala_rag.agents.founder_verification"
        ).ReviewedFounderAnchor
        receipt = cls(
            review_reference="synthetic-review:" + criterion.criterion_id,
            artifact_sha256=core_artifact_digest(rubric),
            snapshot_sha256=frozen_snapshot_digest(snapshot),
            criterion_id=criterion.criterion_id,
            rating=criterion.rating,
            evidence_ids=tuple(criterion.evidence_ids),
            founder_person_ids=people,
            person_by_evidence_id=tuple(
                (eid, links[eid]) for eid in criterion.evidence_ids
            ),
            anchor_facts_reviewed=True,
            minimum_evidence_reviewed=True,
            person_identity_reviewed=True,
            employment_identity_reviewed=True,
            independent_corroboration_reviewed=True,
        )
        assert set(evidence) == set(criterion.evidence_ids)
        receipts[receipt.review_reference] = receipt
        reviewed.append(receipt)
        return receipt

    kwargs = dict(
        founder_person_ids=people,
        verified_person_by_evidence_id=links,
        policy_path=ROOT / "configs/scoring.v3.json",
        approvals=registry.policy_approvals(),
        approval_verifier=registry.verify_policy,
        rubric=rubric,
        llm=FakeLLM([output]),
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=UTC)),
        schema_version=snapshot.schema_version,
        verify_observation=review,
        review_verifier=lambda r: receipts.get(r.review_reference) == r,
        artifact_approval=registry.core_approval(),
        artifact_verifier=registry.verify_core,
    )
    return snapshot, output, kwargs, reviewed


def run(snapshot, kwargs):
    module = importlib.import_module("skala_rag.agents.founder")
    assert hasattr(module, "evaluate_founder_approved_fixture"), (
        "missing approved entry"
    )
    return module.evaluate_founder_approved_fixture(snapshot, **kwargs)


def test_loader_backed_founder_returns_terminal_dto_and_whole_snapshot_review():
    snapshot, output, kwargs, reviewed = case()
    result = run(snapshot, kwargs)
    assert type(result) is EvaluationResult
    assert result.status == "success"
    assert result.policy_version == "v3-operational-1.0.0"
    assert result.snapshot_id == snapshot.snapshot_id
    assert [c.rating for c in result.evaluation.criteria] == [
        c.rating for c in output.criteria
    ]
    assert len(kwargs["llm"].calls) == 1
    assert len(reviewed) == len(output.criteria)
    assert all(r.snapshot_sha256 == frozen_snapshot_digest(snapshot) for r in reviewed)
    assert set(snapshot.evidence) - set(kwargs["verified_person_by_evidence_id"])
    import json

    prompt_evidence = json.loads(kwargs["llm"].calls[0].user)["evidence"]
    assert {e["evidence_id"] for e in prompt_evidence} == set(
        kwargs["verified_person_by_evidence_id"]
    )
    assert all(e["scope"] == "company" for e in prompt_evidence)


@pytest.mark.parametrize(
    "people", ["person", (), ("p", "p"), (1,), (" ",), iter(["p"])]
)
def test_explicit_person_collection_rejected_before_model(people):
    snapshot, _, kwargs, _ = case()
    kwargs["founder_person_ids"] = people
    with pytest.raises(ValueError, match="Founder approved fixture preflight rejected"):
        run(snapshot, kwargs)
    assert kwargs["llm"].calls == []


@pytest.mark.parametrize("links", [{"ghost": "p"}, {1: "p"}, {"eid": 1}])
def test_invalid_person_mapping_rejected_before_model(links):
    snapshot, _, kwargs, _ = case()
    kwargs["verified_person_by_evidence_id"] = links
    with pytest.raises(ValueError, match="Founder approved fixture preflight rejected"):
        run(snapshot, kwargs)
    assert kwargs["llm"].calls == []


@pytest.mark.parametrize(
    "change",
    [
        "bare_true",
        "reference",
        "truthy",
        "rating_bool",
        "evidence_string",
        "duplicate_evidence",
        "people_string",
        "duplicate_people",
        "wrong_person",
        "extra_person",
        "duplicate_mapping",
        "missing_mapping",
        "identity_flag",
        "employment_flag",
        "anchor_flag",
        "minimum_flag",
        "independent_flag",
        "blank_ref",
    ],
)
def test_forged_receipt_rejected_even_with_accept_all_resolver(change):
    snapshot, _, kwargs, _ = case()
    review = kwargs["verify_observation"]

    def forged(c, e):
        r = review(c, e)
        if change == "bare_true":
            return True
        if change == "reference":
            return r.review_reference
        changes = {
            "rating_bool": {"rating": True},
            "evidence_string": {"evidence_ids": r.evidence_ids[0]},
            "duplicate_evidence": {"evidence_ids": r.evidence_ids * 2},
            "people_string": {"founder_person_ids": r.founder_person_ids[0]},
            "duplicate_people": {"founder_person_ids": r.founder_person_ids * 2},
            "wrong_person": {
                "person_by_evidence_id": tuple(
                    (eid, "other-person") for eid in r.evidence_ids
                )
            },
            "extra_person": {
                "person_by_evidence_id": (*r.person_by_evidence_id, ("ghost", "other"))
            },
            "duplicate_mapping": {"person_by_evidence_id": r.person_by_evidence_id * 2},
            "missing_mapping": {"person_by_evidence_id": ()},
            "identity_flag": {"person_identity_reviewed": False},
            "employment_flag": {"employment_identity_reviewed": 1},
            "anchor_flag": {"anchor_facts_reviewed": False},
            "minimum_flag": {"minimum_evidence_reviewed": False},
            "independent_flag": {"independent_corroboration_reviewed": False},
            "blank_ref": {"review_reference": " "},
        }
        return replace(r, **changes.get(change, {}))

    kwargs.update(
        verify_observation=forged,
        review_verifier=lambda r: "yes" if change == "truthy" else True,
    )
    from skala_rag.agents.founder_verification import FounderReviewError

    with pytest.raises(FounderReviewError, match="^FOUNDER_REVIEW_REJECTED$"):
        run(snapshot, kwargs)
    assert len(kwargs["llm"].calls) == 1


@pytest.mark.parametrize(
    "change", ["policy_extra", "policy_nested_extra", "support_ghost"]
)
def test_full_original_and_approval_payload_not_silently_normalized(change):
    snapshot, _, kwargs, _ = case()
    if change == "policy_extra":
        kwargs["approvals"] = kwargs["approvals"].model_copy(
            update={"secret": "SECRET"}
        )
    elif change == "policy_nested_extra":
        kwargs["approvals"] = kwargs["approvals"].model_copy(
            update={
                "core": kwargs["approvals"].core.model_copy(update={"secret": "SECRET"})
            }
        )
    else:
        eid = next(iter(snapshot.evidence))
        snapshot.evidence[eid] = snapshot.evidence[eid].model_copy(
            update={"supporting_evidence_ids": ["ghost"]}
        )
    with pytest.raises(
        ValueError, match="^Founder approved fixture preflight rejected$"
    ):
        run(snapshot, kwargs)
    assert kwargs["llm"].calls == []


@pytest.mark.parametrize("also_original", [False, True])
def test_resolver_cannot_change_reviewed_membership_after_binding(also_original):
    snapshot, _, kwargs, reviewed = case()

    def mutate(r):
        object.__setattr__(r, "person_identity_reviewed", False)
        if also_original:
            object.__setattr__(reviewed[-1], "person_identity_reviewed", False)
        return True

    kwargs["review_verifier"] = mutate
    from skala_rag.agents.founder_verification import FounderReviewError

    with pytest.raises(FounderReviewError):
        run(snapshot, kwargs)


@pytest.mark.parametrize("mode", ["runtime", "zero", "plain", "subclass"])
def test_denial_precedes_paths_loads_and_all_callbacks(mode):
    snapshot, _, kwargs, _ = case()
    touched = []

    def forbidden(*args):
        touched.append(True)
        raise AssertionError("SECRET")

    kwargs.update(
        policy_path="/never/read",
        approval_verifier=forbidden,
        artifact_verifier=forbidden,
        verify_observation=forbidden,
        review_verifier=forbidden,
    )
    if mode in {"runtime", "zero"}:
        kwargs["actual_runtime"] = True if mode == "runtime" else 0
    elif mode == "plain":
        kwargs["llm"] = object()
    else:

        class Derived(FakeLLM):
            pass

        kwargs["llm"] = Derived([])
    with pytest.raises(ValueError):
        run(snapshot, kwargs)
    assert touched == []


@pytest.mark.parametrize(
    "change",
    [
        "proposed",
        "anchor",
        "version",
        "reference",
        "policy",
        "schema",
        "nested_schema",
        "snapshot_extra",
        "nested_extra",
        "run",
        "ghost_record",
        "source",
        "chunk",
        "excerpt",
    ],
)
def test_preflight_validates_original_content_and_generation(change):
    snapshot, _, kwargs, _ = case()
    eid = next(iter(snapshot.evidence))
    evidence = snapshot.evidence[eid]
    rid = next(iter(snapshot.retrieval_records))
    if change == "proposed":
        kwargs["rubric"]["status"] = "proposed"
    elif change == "anchor":
        kwargs["rubric"]["dimensions"]["founder"]["criteria"]["founder.expertise"][
            "anchors"
        ][5] = "SECRET"
    elif change == "version":
        kwargs["rubric"]["rubric_version"] = "wrong"
    elif change == "reference":
        kwargs["artifact_approval"] = replace(
            kwargs["artifact_approval"], reference="wrong"
        )
        kwargs["artifact_verifier"] = lambda a: True
    elif change == "policy":
        snapshot.policy_version = "draft"
    elif change == "schema":
        kwargs["schema_version"] = "wrong"
    elif change == "nested_schema":
        snapshot.evidence[eid].schema_version = "wrong"
    elif change == "snapshot_extra":
        snapshot = snapshot.model_copy(update={"SECRET": True})
    elif change == "nested_extra":
        snapshot.evidence[eid] = evidence.model_copy(update={"SECRET": True})
    elif change in {"run", "ghost_record"}:
        record = snapshot.retrieval_records[rid]
        snapshot.retrieval_records[rid] = record.model_copy(
            update={"run_id": "wrong"}
            if change == "run"
            else {"evidence_ids": [*record.evidence_ids, "ghost"]}
        )
    elif change == "source":
        snapshot.sources.pop(evidence.source_id)
    elif change == "chunk":
        snapshot.chunks.pop(next(iter(snapshot.chunks)))
    elif change == "excerpt":
        snapshot.evidence[eid].excerpt = "SECRET unavailable excerpt"
    with pytest.raises(
        ValueError, match="^Founder approved fixture preflight rejected$"
    ):
        run(snapshot, kwargs)
    assert kwargs["llm"].calls == []


@pytest.mark.parametrize("change", ["extra", "nested_extra", "bool_rating"])
def test_output_model_copy_bypasses_are_terminal_without_repair(change):
    snapshot, output, kwargs, reviewed = case()
    if change == "extra":
        output = output.model_copy(update={"SECRET": True})
    else:
        output.criteria[0] = output.criteria[0].model_copy(
            update={"SECRET": True} if change == "nested_extra" else {"rating": True}
        )
    kwargs["llm"] = FakeLLM([output])
    result = run(snapshot, kwargs)
    assert result.status == "failure"
    assert result.evaluation is None
    assert result.errors[0].error_code == "LLM_OUTPUT_INVALID"
    assert "SECRET" not in result.errors[0].message_redacted
    assert len(kwargs["llm"].calls) == 1
    assert reviewed == []


@pytest.mark.parametrize("kind", ["missing", "na", "incomplete", "timeout"])
def test_missing_na_and_terminal_errors_preserve_common_contract(kind):
    from skala_rag.contracts.error_codes import ErrorCode
    from skala_rag.contracts.interfaces import LLMError

    snapshot, output, kwargs, reviewed = case()
    data = output.model_dump()
    if kind == "missing":
        kwargs["verified_person_by_evidence_id"] = {}
        for c in data["criteria"]:
            c.update(
                status="missing",
                rating=None,
                evidence_ids=[],
                missing_reason="attribution_unverified",
            )
    elif kind == "na":
        data["criteria"][0].update(status="not_applicable", rating=None)
    elif kind == "incomplete":
        data["criteria"].pop()
    kwargs["llm"] = FakeLLM(
        [
            LLMError(ErrorCode.TOOL_TIMEOUT, "upstream redacted timeout")
            if kind == "timeout"
            else data
        ]
    )
    result = run(snapshot, kwargs)
    assert result.status == ("success" if kind == "missing" else "failure")
    assert reviewed == []
    assert len(kwargs["llm"].calls) == 1
    if kind == "missing":
        assert all(
            c.rating is None and c.status == "missing"
            for c in result.evaluation.criteria
        )
    else:
        assert result.evaluation is None
    if kind == "timeout":
        assert result.errors[0].error_code == "TOOL_TIMEOUT"
        assert result.errors[0].message_redacted == "upstream redacted timeout"


@pytest.mark.parametrize("change", ["source", "uncited", "generation"])
def test_receipt_binds_whole_original_not_filtered_snapshot(change):
    snapshot, _, kwargs, _ = case()
    # Callback still returns receipts for the old original generation.
    original = snapshot.model_copy(deep=True)
    if change == "source":
        next(iter(snapshot.sources.values())).title = "Changed source"
    elif change == "uncited":
        eid = next(
            eid
            for eid, e in snapshot.evidence.items()
            if not any(cid.startswith("founder.") for cid in e.criterion_ids)
        )
        snapshot.evidence[eid].claim = "Changed uncited original evidence"
    else:
        snapshot.evidence_revision += 1
    review = kwargs["verify_observation"]

    def stale(c, e):
        return replace(review(c, e), snapshot_sha256=frozen_snapshot_digest(original))

    kwargs.update(verify_observation=stale, review_verifier=lambda r: True)
    from skala_rag.agents.founder_verification import FounderReviewError

    with pytest.raises(FounderReviewError):
        run(snapshot, kwargs)


def test_other_person_excluded_from_prompt_and_citations():
    snapshot, output, kwargs, reviewed = case()
    excluded = output.criteria[0].evidence_ids[0]
    kwargs["verified_person_by_evidence_id"][excluded] = "same-name-other-person"
    result = run(snapshot, kwargs)
    assert result.status == "failure"
    assert excluded not in kwargs["llm"].calls[0].user
    assert "EVIDENCE_NOT_IN_SNAPSHOT" in result.errors[0].message_redacted
    assert reviewed == []


def test_review_callback_copies_cannot_mutate_result_or_original():
    snapshot, output, kwargs, _ = case()
    before = frozen_snapshot_digest(snapshot)
    review = kwargs["verify_observation"]
    # Capture receipt facts before deliberately changing the caller-owned inputs.
    saved = []
    for c in output.criteria:
        saved.append(review(c, {eid: snapshot.evidence[eid] for eid in c.evidence_ids}))

    def detached_review(c, e):
        r = next(r for r in saved if r.criterion_id == c.criterion_id)
        c.rating = 1
        next(iter(e.values())).claim = "Changed callback copy"
        kwargs["verified_person_by_evidence_id"].clear()
        kwargs["rubric"]["status"] = "proposed"
        return r

    kwargs["verify_observation"] = detached_review
    result = run(snapshot, kwargs)
    assert result.status == "success"
    assert [c.rating for c in result.evaluation.criteria] == [
        c.rating for c in output.criteria
    ]
    assert frozen_snapshot_digest(snapshot) == before


@pytest.mark.parametrize("stage", ["preflight", "review", "upstream"])
def test_ordinary_exception_boundaries(stage, monkeypatch):
    from skala_rag.agents.founder_verification import FounderReviewError

    snapshot, _, kwargs, _ = case()
    error = RuntimeError("SECRET")

    def fail(*args, **kw):
        raise error

    if stage == "preflight":
        kwargs["artifact_verifier"] = fail
    elif stage == "review":
        kwargs["verify_observation"] = fail
    else:
        monkeypatch.setattr(kwargs["llm"], "generate", fail)
    expected = (
        RuntimeError
        if stage == "upstream"
        else (FounderReviewError if stage == "review" else ValueError)
    )
    with pytest.raises(expected) as caught:
        run(snapshot, kwargs)
    if stage == "upstream":
        assert caught.value is error
    else:
        assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("stage", ["preflight", "review", "upstream"])
def test_cancellation_propagates(stage, monkeypatch):
    snapshot, _, kwargs, _ = case()
    error = asyncio.CancelledError()

    def fail(*args, **kw):
        raise error

    if stage == "preflight":
        kwargs["artifact_verifier"] = fail
    elif stage == "review":
        kwargs["verify_observation"] = fail
    else:
        monkeypatch.setattr(kwargs["llm"], "generate", fail)
    with pytest.raises(asyncio.CancelledError) as caught:
        run(snapshot, kwargs)
    assert caught.value is error


@pytest.mark.parametrize("success", [True, False])
def test_real_founder_callback_binds_lossless_original_generation(success):
    from skala_rag.agents.evaluation_v3_adapter import bind_baseline_evaluator_v3
    from skala_rag.scoring.v3_policy import load_v3_policy

    snapshot, _, kwargs, _ = case()
    if not success:
        from skala_rag.contracts.error_codes import ErrorCode
        from skala_rag.contracts.interfaces import LLMError

        kwargs["llm"] = FakeLLM([LLMError(ErrorCode.TOOL_TIMEOUT, "timeout")])
    saved = []

    def evaluator(frozen):
        assert frozen.model_dump() == snapshot.model_dump()
        saved.append(run(frozen, kwargs))
        return saved[0]

    policy = load_v3_policy(kwargs["policy_path"], execution_mode="fixture")
    assert len(policy.criteria) == 23
    bound = bind_baseline_evaluator_v3(
        "founder",
        evaluator,
        criteria=policy.criteria,
        industry_evidence_dimensions={"market", "moat"},
    )
    converted = bound(snapshot)
    assert len(saved) == len(kwargs["llm"].calls) == 1
    payload = saved[0].model_dump()
    evaluation = payload.pop("evaluation")
    payload.pop("dimension")
    payload.update(
        branch_id="founder", evaluations={"founder": evaluation} if success else None
    )
    from skala_rag.contracts.v3 import EvaluationBranchResult

    expected = EvaluationBranchResult.model_validate(payload)
    assert converted.model_dump() == expected.model_dump()
    # V3 adds only absent applicability fields; every baseline field survives.
    if success:
        actual = converted.evaluations["founder"].model_dump()
        for field, value in evaluation.items():
            if field != "criteria":
                assert actual[field] == value
        for baseline, v3 in zip(
            evaluation["criteria"], actual["criteria"], strict=True
        ):
            assert all(v3[field] == value for field, value in baseline.items())


def test_accept_all_fixture_callback_is_not_semantic_proof_or_new_rating_cap():
    snapshot, output, kwargs, _ = case()
    for c in output.criteria:
        c.rating = 5
    kwargs["review_verifier"] = lambda r: True
    result = run(snapshot, kwargs)
    assert result.status == "success"
    assert all(c.rating == 5 for c in result.evaluation.criteria)
    # The typed binding is structurally valid. This deliberately dishonest resolver
    # cannot prove person identity or satisfy production semantic/runtime gates.
    assert snapshot.policy_version == "v3-operational-1.0.0"
