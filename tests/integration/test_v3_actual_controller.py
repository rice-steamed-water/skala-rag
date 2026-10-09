"""Original actual-admission plumbing; SYNTHETIC callbacks, index and mock wire.

This is not real domain evaluation, provider execution or publication evidence."""

from dataclasses import replace
from datetime import timedelta
from itertools import count
from typing import Literal, assert_never
from uuid import UUID

import pytest
from tests.integration.test_v3_evidence_snapshot_consumer import actual_research_case
from tests.unit import test_actual_admission_v3 as shared
from tests.unit.test_scoring_v3 import evaluations
from tests.unit.test_v3_report_pipeline import Stub

import skala_rag.graph.snapshot as snapshot_module
import skala_rag.rag.adapter as rag_adapter
import skala_rag.tools.runtime as runtime_module
from skala_rag.agents.source_fact_verification import SourceBoundReviewResolver
from skala_rag.contracts.v3 import BRANCH_DIMENSIONS, EvaluationBranchResult
from skala_rag.graph.candidate_workflow_v3 import (
    build_candidate_workflow_v3,
    run_candidate_report_v3,
    run_candidate_workflow_v3,
)
from skala_rag.graph.candidates_v3 import CandidateStagesV3
from skala_rag.graph.research_artifacts_v3 import (
    consume_outcome_v3,
    initialize_artifacts_v3,
)
from skala_rag.reporting.v3_pipeline import ReportGeneratorV3, SemanticJudgeV3
from skala_rag.scoring.catalog import load_policy

offline_fixture = shared.offline
actual_admission_fixture = shared.configured
research_case = actual_research_case


@pytest.fixture
def controller(research_case, monkeypatch):
    binding, candidate, eligibility, seed, requests = research_case
    admission = binding.actual_admission
    policy = admission.load_policy().operational
    # Every semantic observation is explicitly synthetic, not a source review.
    seed = replace(
        seed,
        evidence={
            key: item.model_copy(
                update={"criterion_ids": [c.criterion_id for c in policy.criteria]}
            )
            for key, item in seed.evidence.items()
        },
    )
    identifiers = count(1)
    monkeypatch.setattr(runtime_module, "uuid4", lambda: UUID(int=next(identifiers)))
    monkeypatch.setattr(rag_adapter, "uuid4", lambda: UUID(int=next(identifiers)))
    pinned = binding.pin(
        run_id=binding.run_id,
        schema_version=binding.schema_version,
        policy_version=policy.policy_version,
    )
    owned = initialize_artifacts_v3(seed, eligibility, candidate.model_dump(), pinned)
    outcome = pinned.research.run(candidate, (), pinned.budget)
    consume_outcome_v3(owned, outcome, pinned, candidate.candidate_id, initial=True)
    frozen = snapshot_module.freeze_snapshot(
        candidate.candidate_id,
        owned["state"],
        binding.run_input,
        run_id=binding.run_id,
        index_version=binding.index_version,
        schema_version=binding.schema_version,
        allowed_source_ids=binding.allowed_source_ids,
        industry_evidence_ids=(),
        clock=admission.runtime_binding.runtime.clock,
    )
    trusted = next(iter(admission.review_resolvers.values()))._sources
    admission = replace(
        admission,
        review_resolvers={
            (frozen.snapshot_id, version): SourceBoundReviewResolver(
                frozen, admission.registry.rubric(version), sources=trusted, reviews=()
            )
            for version in ("core-0.1.0", "finance-0.1.0")
        },
    )
    binding = replace(binding, actual_admission=admission)
    binding.research._retrieve._cache.clear()
    identifiers = count(1)
    requests.clear()
    calls = []

    def forbidden(*_args):
        pytest.fail("actual controller used legacy collection or freeze")

    stages = CandidateStagesV3(
        discover=lambda: [candidate],
        normalize=lambda items: items,
        research=lambda _candidate: seed,
        eligibility=lambda _candidate, _seed: eligibility,
        collect=forbidden,
        freeze=forbidden,
        evidence_research=binding,
    )

    def branch(branch_id):
        def evaluate(snapshot):
            calls.append((branch_id, snapshot.model_dump(mode="json")))
            evidence_items = snapshot.evidence.items()
            identity = {
                field: getattr(snapshot, field)
                for field in (
                    "schema_version",
                    "run_id",
                    "candidate_id",
                    "evaluation_round",
                    "snapshot_id",
                    "evidence_revision",
                    "policy_version",
                )
            }
            return EvaluationBranchResult.model_validate(
                dict(
                    **identity,
                    branch_id=branch_id,
                    status="success",
                    errors=[],
                    evaluations={
                        item.dimension: item.model_copy(
                            update={
                                **identity,
                                "rubric_version": "finance-0.1.0"
                                if branch_id == "business_deal"
                                else "core-0.1.0",
                                "criteria": [
                                    c.model_copy(
                                        update={
                                            "schema_version": snapshot.schema_version,
                                            "evidence_ids": [
                                                next(
                                                    eid
                                                    for eid, e in evidence_items
                                                    if c.criterion_id in e.criterion_ids
                                                )
                                            ],
                                        }
                                    )
                                    for c in item.criteria
                                ],
                            }
                        )
                        for item in evaluations(policy, rating=4)
                        if item.dimension in BRANCH_DIMENSIONS[branch_id]
                    },
                )
            )

        return evaluate

    catalog = load_policy("configs/scoring.draft.json", execution_mode="fixture")
    options = dict(
        policy=policy,
        catalog=catalog,
        catalog_policy_version=catalog.policy_version,
        run_id=binding.run_id,
        schema_version=binding.schema_version,
        support_check=lambda _criterion, evidence: bool(evidence),
        applicability_assessments=lambda _cid: {},
        applicability_check=lambda *_args: False,
        applicability_verifier=None,
        industry_evidence_dimensions=(),
        clock=admission.runtime_binding.runtime.clock.now,
        approved_policy_source=admission.source,
        actual_admission=admission,
    )
    callbacks = {key: branch(key) for key in BRANCH_DIMENSIONS}
    return stages, callbacks, options, calls, requests, frozen


def test_complete_admission_reaches_original_score_context_and_report(controller):
    # Given: immutable review bindings for the exact original freeze generation.
    stages, callbacks, options, calls, requests, frozen = controller
    events, generator, judge = [], Stub(), Stub()
    # When: drive the existing public outer/report composite.
    result, context, report = run_candidate_report_v3(
        stages,
        callbacks,
        run_input=stages.evidence_research.run_input,
        generate=ReportGeneratorV3(generator),
        judge=SemanticJudgeV3(judge),
        graph_events=events,
        **options,
    )
    # Then: original five branches promote six dimensions into one scored State.
    cid = frozen.candidate_id
    assert result.execution_mode == "live"
    assert {key for key, _ in calls} == set(BRANCH_DIMENSIONS) and len(calls) == 5
    assert all(snapshot == frozen.model_dump(mode="json") for _, snapshot in calls)
    assert len(requests) == 1 and result.candidate_index == 1
    assert result.scores[cid].normalized_score == 80
    payload = context.snapshot()
    state = result.research_artifacts[cid]["state"]
    assert payload["snapshots"][cid] == state["snapshots"][frozen.snapshot_id]
    assert payload["evaluations"] == state["evaluations_v3"]
    assert len(payload["evaluations"]) == 6
    assert payload["execution_scope"] == "controlled_response"
    assert payload["provenance"]["synthetic"]
    assert not payload["publication_allowed"] and not report.final_allowed
    assert generator.calls[0][1]["context"] == judge.calls[0][1]["context"] == payload
    assert report.validation is not None
    assert report.status == "completed" and report.validation.valid
    assert result.research_retry_count[cid] == 0


@pytest.mark.parametrize(
    "entrypoint",
    [build_candidate_workflow_v3, run_candidate_workflow_v3, run_candidate_report_v3],
)
@pytest.mark.parametrize("denial", ["default", "zero", "foreign", "stale", "research"])
def test_incomplete_admission_rejects_before_any_callback(
    controller,
    entrypoint,
    denial: Literal["default", "zero", "foreign", "stale", "research"],
):
    # Given: live source inputs cannot grant authority without complete admission.
    stages, callbacks, options, calls, requests, _ = controller
    admission = options["actual_admission"]
    match denial:
        case "default":
            options.pop("actual_admission")
        case "zero":
            ledger = admission.runtime_binding.runtime.ledger
            ledger._calls = ledger.limits.max_calls
        case "foreign":
            options["approved_policy_source"] = replace(admission.source)
        case "stale":
            admission.run_input.corpus_version = "stale"
        case "research":
            stages = replace(
                stages,
                evidence_research=replace(
                    stages.evidence_research, actual_admission=None
                ),
            )
        case unreachable:
            assert_never(unreachable)
    stages = replace(stages, discover=lambda: pytest.fail("discovery executed"))
    if entrypoint is run_candidate_report_v3:
        options.update(
            run_input=stages.evidence_research.run_input,
            generate=lambda *_args: pytest.fail("generator executed"),
            judge=lambda *_args: pytest.fail("judge executed"),
        )
    # When / Then: reject before discovery, evaluators or mock transport.
    with pytest.raises(ValueError):
        entrypoint(stages, callbacks, **options)
    assert not calls and not requests


def test_partial_business_deal_failure_cannot_promote_or_score(controller):
    # Given: one of the two business dimensions is omitted.
    stages, callbacks, options, calls, _, frozen = controller
    original = callbacks["business_deal"]

    def partial(snapshot):
        raw = original(snapshot).model_dump(mode="json")
        raw["evaluations"].pop("deal_terms")
        return raw

    callbacks["business_deal"] = partial
    # When: the original atomic join consumes five terminal branches.
    result = run_candidate_workflow_v3(stages, callbacks, **options)
    # Then: the candidate fails once and neither dimension reaches scoring.
    assert len(calls) == 5 and result.candidate_index == 1
    assert result.outcomes[frozen.candidate_id].status == "failed"
    assert not result.scores and not result.decisions
    state = result.research_artifacts[frozen.candidate_id]["state"]
    assert not state.get("evaluations_v3")


@pytest.mark.parametrize(
    "field", ["index_version", "corpus_version", "as_of", "evidence_revision"]
)
def test_wrong_original_snapshot_rejects_before_evaluators(
    controller, monkeypatch, field
):
    # Given: the original freeze is corrupted after research, before evaluation.
    stages, callbacks, options, calls, _, frozen = controller
    changed = {
        "index_version": "foreign",
        "corpus_version": "foreign",
        "as_of": frozen.as_of + timedelta(days=1),
        "evidence_revision": frozen.evidence_revision + 1,
    }[field]
    original = snapshot_module.freeze_snapshot

    def corrupt(*args, **kwargs):
        return original(*args, **kwargs).model_copy(update={field: changed})

    monkeypatch.setattr(snapshot_module, "freeze_snapshot", corrupt)
    # When: the admitted public controller consumes the original generation.
    result = run_candidate_workflow_v3(stages, callbacks, **options)
    # Then: wrong cutoff/index/corpus/generation is archived, never evaluated.
    assert not calls and not result.scores
    assert result.outcomes[frozen.candidate_id].status == "failed"
    assert any(error.error_code == "SNAPSHOT_INVALID" for error in result.errors)
