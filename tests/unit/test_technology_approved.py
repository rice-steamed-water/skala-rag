"""Synthetic, caller-owned review registry; no actual semantic/search authority."""

import asyncio
import importlib
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from tests.fixtures.loader import load_common_fixtures
from tests.unit.test_approved_policy import approval_payload
from tests.unit.test_moat import _closed_snapshot

from skala_rag.agents.evaluation import output_from_evaluation
from skala_rag.agents.moat_verification import (
    CoreArtifactApproval,
    core_artifact_digest,
    frozen_snapshot_digest,
)
from skala_rag.fakes import FakeClock, FakeLLM
from skala_rag.scoring.approved_policy import PolicyApprovals
from skala_rag.scoring.catalog import load_policy

ROOT = Path(__file__).resolve().parents[2]


def case():
    policy = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
    fixtures = load_common_fixtures(policy)
    snapshot = _closed_snapshot(next(iter(fixtures.snapshots.values())))
    snapshot = snapshot.model_copy(update={"policy_version": "v3-operational-1.0.0"})
    output = output_from_evaluation(
        fixtures.evaluations[
            f"{snapshot.candidate_id}:{snapshot.evaluation_round}:technology"
        ]
    )
    rubric = yaml.safe_load((ROOT / "configs/rubrics/core.yaml").read_text())
    # Synthetic copy only; this does not propagate approval into production Core.
    rubric = deepcopy(rubric)
    rubric["status"] = "approved"
    approvals = PolicyApprovals.model_validate(approval_payload())
    artifact = CoreArtifactApproval(
        approvals.core.reference, "core-0.1.0", core_artifact_digest(rubric)
    )
    llm = FakeLLM([output])
    receipts = {}

    def review(criterion, evidence):
        cls = importlib.import_module(
            "skala_rag.agents.technology_verification"
        ).ReviewedTechnologyAnchor
        receipt = cls(
            review_reference="synthetic-review:" + criterion.criterion_id,
            artifact_sha256=core_artifact_digest(rubric),
            snapshot_sha256=frozen_snapshot_digest(snapshot),
            criterion_id=criterion.criterion_id,
            rating=criterion.rating,
            evidence_ids=tuple(criterion.evidence_ids),
            anchor_facts_reviewed=True,
            minimum_evidence_reviewed=True,
            direct_negative_facts_reviewed=True,
            independent_corroboration_reviewed=True,
        )
        receipts[receipt.review_reference] = receipt
        assert set(evidence) == set(criterion.evidence_ids)
        return receipt

    kwargs = dict(
        policy_path=ROOT / "configs/scoring.v3.json",
        approvals=approvals,
        approval_verifier=lambda a, p: a.model_dump() == approval_payload()[a.scope],
        rubric=rubric,
        llm=llm,
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=UTC)),
        schema_version=snapshot.schema_version,
        verify_observation=review,
        review_verifier=lambda r: receipts.get(r.review_reference) == r,
        artifact_approval=artifact,
        artifact_verifier=lambda a: a == artifact,
    )
    return snapshot, output, kwargs


def run(snapshot, kwargs):
    module = importlib.import_module("skala_rag.agents.technology")
    assert hasattr(module, "evaluate_technology_approved_fixture"), (
        "missing approved entry"
    )
    return module.evaluate_technology_approved_fixture(snapshot, **kwargs)


def test_loader_backed_consumer_preserves_container_and_original_snapshot_review():
    snapshot, output, kwargs = case()
    result = run(snapshot, kwargs)
    assert result.result.status == "success"
    assert result.result.snapshot_id == snapshot.snapshot_id
    assert result.result.evaluation.criteria[0].rating == output.criteria[0].rating
    assert len(result.trace) == sum(len(c.evidence_ids) for c in output.criteria)
    assert len(kwargs["llm"].calls) == 1
    assert result.prompt_version
    assert result.allowed_evidence_ids


@pytest.mark.parametrize("change", ["record_source", "record_chunk", "record_evidence"])
def test_full_snapshot_record_closure_rejects_before_llm(change):
    snapshot, _, kwargs = case()
    rid = next(iter(snapshot.retrieval_records))
    record = snapshot.retrieval_records[rid]
    field = {
        "record_source": "source_ids",
        "record_chunk": "chunk_ids",
        "record_evidence": "evidence_ids",
    }[change]
    snapshot.retrieval_records[rid] = record.model_copy(
        update={field: [*getattr(record, field), "ghost"]}
    )
    with pytest.raises(ValueError):
        run(snapshot, kwargs)
    assert kwargs["llm"].calls == []


@pytest.mark.parametrize("change", ["actual", "zero", "plain", "subclass"])
def test_runtime_denied_before_paths_and_callbacks(change):
    snapshot, _, kwargs = case()
    touched = []

    def forbidden(*args):
        touched.append(True)
        raise AssertionError("not reached")

    kwargs.update(
        policy_path="/does/not/exist",
        artifact_verifier=forbidden,
        approval_verifier=forbidden,
        verify_observation=forbidden,
    )
    if change in {"actual", "zero"}:
        kwargs["actual_runtime"] = True if change == "actual" else 0
    elif change == "plain":
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
        "policy",
        "schema",
        "excerpt",
        "chunk",
        "source",
        "record",
        "extra",
        "nested_extra",
        "field_type",
    ],
)
def test_preflight_rejects_tampered_content_before_model(change):
    snapshot, _, kwargs = case()
    eid = next(iter(snapshot.evidence))
    evidence = snapshot.evidence[eid]
    if change == "proposed":
        kwargs["rubric"]["status"] = "proposed"
    elif change == "anchor":
        kwargs["rubric"]["dimensions"]["technology"]["criteria"]["technology.maturity"][
            "anchors"
        ][5] = "tampered"
    elif change == "version":
        kwargs["rubric"]["rubric_version"] = "wrong"
    elif change == "policy":
        snapshot.policy_version = "wrong"
    elif change == "schema":
        kwargs["schema_version"] = "wrong"
    elif change == "excerpt":
        evidence.excerpt = "absent from frozen text"
    elif change == "chunk":
        next(iter(snapshot.chunks.values())).source_id = "ghost"
    elif change == "source":
        evidence.source_id = "ghost"
    elif change == "record":
        next(iter(snapshot.retrieval_records.values())).run_id = "wrong"
    elif change == "extra":
        snapshot = snapshot.model_copy(update={"unknown": True})
    elif change == "nested_extra":
        snapshot.evidence[eid] = evidence.model_copy(update={"unknown": True})
    else:
        snapshot = snapshot.model_copy(update={"evaluation_round": True})
    with pytest.raises(ValueError):
        run(snapshot, kwargs)
    assert kwargs["llm"].calls == []


@pytest.mark.parametrize(
    "change",
    [
        "boolean",
        "reference",
        "rating",
        "bool_rating",
        "criterion",
        "evidence",
        "duplicate",
        "snapshot",
        "artifact",
        "list",
        "anchor_facts_reviewed",
        "minimum_evidence_reviewed",
        "direct_negative_facts_reviewed",
        "independent_corroboration_reviewed",
        "resolver_false",
        "resolver_integer",
        "resolver_exception",
        "review_exception",
        "subclass",
        "duck",
    ],
)
def test_unreviewed_observation_is_technical_error_without_repair(change):
    from skala_rag.agents.technology_verification import TechnologyReviewError

    snapshot, _, kwargs = case()
    original = kwargs["verify_observation"]

    def review(c, e):
        receipt = original(c, e)
        if change == "boolean":
            return True
        if change == "review_exception":
            raise RuntimeError("SECRET")
        if change == "subclass":

            class Derived(type(receipt)):
                pass

            return Derived(**receipt.__dict__)
        if change == "duck":
            from types import SimpleNamespace

            return SimpleNamespace(**receipt.__dict__)
        replacements = {
            "reference": {"review_reference": "unregistered"},
            "rating": {"rating": 1 if c.rating != 1 else 2},
            "bool_rating": {"rating": True},
            "criterion": {"criterion_id": "moat.ip"},
            "evidence": {"evidence_ids": ("unknown",)},
            "duplicate": {"evidence_ids": receipt.evidence_ids * 2},
            "snapshot": {"snapshot_sha256": "0" * 64},
            "artifact": {"artifact_sha256": "0" * 64},
            "list": {"evidence_ids": list(receipt.evidence_ids)},
        }
        if change.endswith("reviewed"):
            return replace(receipt, **{change: 1})
        return replace(receipt, **replacements.get(change, {}))

    kwargs["verify_observation"] = review
    if change.startswith("resolver"):

        def resolver(receipt):
            if change == "resolver_exception":
                raise RuntimeError("SECRET")
            return 1 if change == "resolver_integer" else False

        kwargs["review_verifier"] = resolver
    with pytest.raises(
        TechnologyReviewError, match="TECHNOLOGY_REVIEW_REJECTED"
    ) as err:
        run(snapshot, kwargs)
    assert "SECRET" not in str(err.value)
    assert len(kwargs["llm"].calls) == 1


@pytest.mark.parametrize("rating", [1, 2, 3, 4, 5])
def test_reviewed_anchor_has_no_invented_rating_cap(rating):
    snapshot, output, kwargs = case()
    for c in output.criteria:
        c.rating = rating
    result = run(snapshot, kwargs)
    assert all(c.rating == rating for c in result.result.evaluation.criteria)


@pytest.mark.parametrize("kind", ["missing", "na", "incomplete", "timeout"])
def test_baseline_terminal_behavior_and_no_review_for_missing_or_failure(kind):
    from skala_rag.contracts.error_codes import ErrorCode
    from skala_rag.contracts.interfaces import LLMError

    snapshot, output, kwargs = case()
    data = output.model_dump()
    if kind == "missing":
        for c in data["criteria"]:
            c.update(
                status="missing",
                rating=None,
                evidence_ids=[],
                missing_reason="not_disclosed",
            )
    elif kind == "na":
        data["criteria"][0].update(status="not_applicable", rating=None)
    elif kind == "incomplete":
        data["criteria"].pop()
    kwargs["llm"] = FakeLLM(
        [LLMError(ErrorCode.TOOL_TIMEOUT, "timeout") if kind == "timeout" else data]
    )

    def forbidden(*args):
        raise AssertionError("review must not run")

    kwargs["verify_observation"] = forbidden
    result = run(snapshot, kwargs)
    assert result.result.status == ("success" if kind == "missing" else "failure")
    assert len(kwargs["llm"].calls) == 1
    if kind == "missing":
        assert all(c.rating is None for c in result.result.evaluation.criteria)
        assert result.trace == ()
    else:
        assert result.result.evaluation is None


@pytest.mark.parametrize(
    "exception", [KeyboardInterrupt, SystemExit, asyncio.CancelledError]
)
def test_process_controls_propagate(exception):
    snapshot, _, kwargs = case()

    def interrupt(*args):
        raise exception()

    kwargs["verify_observation"] = interrupt
    with pytest.raises(exception):
        run(snapshot, kwargs)


@pytest.mark.parametrize(
    "field",
    [
        "snapshot_id",
        "run_id",
        "evaluation_round",
        "evidence_revision",
        "index_version",
        "as_of",
        "source_title",
        "chunk_text",
        "excerpt",
        "nontechnology_claim",
    ],
)
def test_replay_against_changed_whole_snapshot_rejected(field):
    from datetime import date

    from skala_rag.agents.technology_verification import TechnologyReviewError

    snapshot, output, kwargs = case()
    receipts = {
        c.criterion_id: kwargs["verify_observation"](
            c, {eid: snapshot.evidence[eid] for eid in c.evidence_ids}
        )
        for c in output.criteria
    }
    kwargs["verify_observation"] = lambda c, e: receipts[c.criterion_id]
    changed = snapshot.model_copy(deep=True)
    if field == "source_title":
        next(iter(changed.sources.values())).title = "changed"
    elif field == "chunk_text":
        next(iter(changed.chunks.values())).text += " changed"
    elif field == "excerpt":
        evidence = changed.evidence[output.criteria[0].evidence_ids[0]]
        evidence.excerpt = evidence.excerpt[:10]
    elif field == "nontechnology_claim":
        next(
            e
            for e in changed.evidence.values()
            if not any(cid.startswith("technology.") for cid in e.criterion_ids)
        ).claim = "changed unrelated whole-snapshot fact"
    elif field == "as_of":
        changed.as_of = date(2026, 10, 1)
    else:
        setattr(
            changed,
            field,
            2 if field in {"evaluation_round", "evidence_revision"} else "different",
        )
    with pytest.raises((TechnologyReviewError, ValueError)):
        run(changed, kwargs)


def test_binder_uses_original_generation_and_explicit_result_unwrap():
    from skala_rag.agents.evaluation_v3_adapter import bind_baseline_evaluator_v3
    from skala_rag.contracts.v3 import Evaluation
    from skala_rag.scoring.v3_policy import load_v3_policy

    snapshot, _, kwargs = case()
    saved = []

    def evaluator(frozen):
        receipt = run(frozen, kwargs)
        saved.append(receipt)
        return receipt.result

    policy = load_v3_policy(kwargs["policy_path"], execution_mode="fixture")
    call = bind_baseline_evaluator_v3(
        "technology",
        evaluator,
        criteria=policy.criteria,
        industry_evidence_dimensions={"market", "moat"},
    )
    result = call(snapshot)
    assert result.status == "success"
    assert result.evaluations["technology"].model_dump() == (
        Evaluation.model_validate(saved[0].result.evaluation.model_dump()).model_dump()
    )
    assert saved[0].trace and saved[0].prompt_version
    kwargs["llm"] = FakeLLM([case()[1]])
    bad = bind_baseline_evaluator_v3(
        "technology",
        lambda s: run(s, kwargs),
        criteria=policy.criteria,
        industry_evidence_dimensions={"market", "moat"},
    )
    with pytest.raises(ValueError):
        bad(snapshot)


@pytest.mark.parametrize("change", ["source_hash", "page_text"])
def test_real_approved_pdf_preflight_negatives_not_retrieval(change):
    from skala_rag.rag.extraction import PageChunkSettings, extract_local_document
    from skala_rag.rag.index_validation import prepare_corpus
    from skala_rag.rag.reviewed_extraction import verify_text_review

    root = Path("/Users/luk/workspace/skala-rag-issue184")
    if not (root / "data/local").is_dir():
        pytest.skip("Approved local PDF assets not available")
    manifest, sources, results, chunks = prepare_corpus(
        root, model_id="not-embedded", model_revision="not-embedded"
    )
    assert len(manifest.documents) == 2 and len(chunks) == 36
    document = manifest.documents[0]
    if change == "source_hash":
        source = sources[document.source_id].model_copy(
            update={"content_hash": "sha256:" + "0" * 64}
        )
        with pytest.raises(ValueError):
            extract_local_document(
                manifest,
                document.document_id,
                source,
                root=root,
                schema_version=manifest.schema_version,
                settings=PageChunkSettings(
                    **document.text_index_review.extraction_settings
                ),
                sections_by_page={},
                embedding_model="not-embedded",
                embedding_revision="not-embedded",
            )
    else:
        result = deepcopy(results[document.document_id])
        # Existing reviewed text hash must reject tampered extraction, no fake search.
        result.chunks[0].text += " tampered"
        with pytest.raises(ValueError):
            verify_text_review(result, document.text_index_review)


def test_mixed_nested_schema_rejected_before_model():
    snapshot, _, kwargs = case()
    next(iter(snapshot.evidence.values())).schema_version = "stale"
    with pytest.raises(ValueError):
        run(snapshot, kwargs)
    assert kwargs["llm"].calls == []


def test_callback_mutation_does_not_change_returned_container():
    snapshot, _, kwargs = case()
    review = kwargs["verify_observation"]

    def mutate(c, e):
        receipt = review(c, e)
        c.rating = 1
        next(iter(e.values())).excerpt = "changed callback input"
        return receipt

    kwargs["verify_observation"] = mutate
    result = run(snapshot, kwargs)
    assert result.result.evaluation.criteria[0].rating == 3


@pytest.mark.parametrize("target", ["output", "criterion"])
def test_forged_model_copy_output_extras_cannot_be_silently_dropped(target):
    snapshot, output, kwargs = case()
    if target == "output":
        output = output.model_copy(update={"secret_unknown": "SECRET"})
    else:
        output.criteria[0] = output.criteria[0].model_copy(
            update={"secret_unknown": "SECRET"}
        )
    kwargs["llm"] = FakeLLM([output])
    result = run(snapshot, kwargs)
    assert result.result.status == "failure"
    assert "SECRET" not in result.result.errors[0].message_redacted
    assert len(kwargs["llm"].calls) == 1
