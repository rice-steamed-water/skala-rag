"""Offline provenance plumbing with synthetic reviews and HTTP-level responses.

Scope labels test the trusted admission contract, not real provider execution.
No evaluator terminal stubs, factual ratings or publication approval are supplied.
"""

import json
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256

import httpx
import pytest
from tests.integration.test_v3_actual_research_binding import pin
from tests.integration.test_v3_evidence_snapshot_consumer import actual_research_case
from tests.unit import test_actual_admission_v3 as shared
from tests.unit.test_openai_attempt import body
from tests.unit.test_scoring_v3 import evaluations

from skala_rag.agents.source_fact_verification import SourceBoundReviewResolver
from skala_rag.contracts.ids import evaluation_key
from skala_rag.contracts.reports import CandidateOutcome
from skala_rag.graph.candidates_v3 import CandidateRunV3
from skala_rag.graph.research_artifacts_v3 import initialize_artifacts_v3
from skala_rag.graph.snapshot import freeze_snapshot
from skala_rag.reporting.v3_context import build_report_context_from_run_v3
from skala_rag.reporting.v3_pipeline import ReportGeneratorV3
from skala_rag.reporting.validator import artifact_hash
from skala_rag.scoring.approved_consumers import (
    aggregate_scores_approved,
    decide_approved,
    select_best_approved,
)
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM

offline_fixture = shared.offline
actual_admission_fixture = shared.configured
research_case = actual_research_case


@pytest.fixture
def scored_context_case(research_case):
    binding, candidate, eligibility, seed, _ = research_case
    admission = binding.actual_admission
    assert admission is not None
    llm = binding.research._llm
    assert type(llm) is RuntimeStructuredLLM
    owned = initialize_artifacts_v3(
        seed, eligibility, candidate.model_dump(), pin(binding)
    )
    state = owned["state"]
    snapshot = freeze_snapshot(
        candidate.candidate_id,
        state,
        binding.run_input,
        run_id=binding.run_id,
        index_version=binding.index_version,
        schema_version=binding.schema_version,
        allowed_source_ids=binding.allowed_source_ids,
        industry_evidence_ids=(),
        clock=admission.runtime_binding.runtime.clock,
    )
    # Exact original Source readers bind this freeze, not the reference seed.
    trusted = next(iter(admission.review_resolvers.values()))._sources
    admission = replace(
        admission,
        execution_scope="actual",
        review_resolvers={
            (snapshot.snapshot_id, version): SourceBoundReviewResolver(
                snapshot,
                admission.registry.rubric(version),
                sources=trusted,
                reviews=(),
            )
            for version in ("core-0.1.0", "finance-0.1.0")
        },
    )
    policy = admission.load_policy()
    # Missing observations remain missing; real deterministic consumers score them.
    items = evaluations(policy, missing={c.criterion_id for c in policy.criteria})
    for item in items:
        for field in (
            "schema_version",
            "run_id",
            "candidate_id",
            "snapshot_id",
            "evaluation_round",
            "evidence_revision",
        ):
            setattr(item, field, getattr(snapshot, field))
        item.rubric_version = (
            "finance-0.1.0"
            if item.dimension in ("traction", "deal_terms")
            else "core-0.1.0"
        )
        for criterion in item.criteria:
            criterion.schema_version = snapshot.schema_version
    score = aggregate_scores_approved(
        items,
        admission.source,
        snapshot=snapshot,
        applicability_verifier=None,
        actual_admission=admission,
    )
    decision = decide_approved(score, admission.source, actual_admission=admission)
    cid = candidate.candidate_id
    selection = select_best_approved(
        [
            dict(
                candidate_id=cid,
                eligibility_status="eligible",
                status="evaluated",
                label=decision.label,
                normalized_score=score.normalized_score,
                weighted_missing_pct=score.weighted_missing_pct,
                applicable_weight=score.applicable_weight,
                score_summary_id=score.score_summary_id,
            )
        ],
        admission.source,
        run_id=binding.run_id,
        schema_version=binding.schema_version,
        actual_admission=admission,
    )
    state.update(
        evaluations_v3={
            evaluation_key(cid, item.evaluation_round, item.dimension): item.model_dump(
                mode="json"
            )
            for item in items
        },
        score_summaries={cid: score.model_dump(mode="json")},
        investment_decisions={cid: decision.model_dump(mode="json")},
    )
    result = CandidateRunV3(
        schema_version=binding.schema_version,
        run_id=binding.run_id,
        policy_version=binding.run_input.policy_version,
        status="ready_for_v3_reporting",
        candidate_index=1,
        outcomes={
            cid: CandidateOutcome(
                schema_version=binding.schema_version,
                candidate_id=cid,
                status="watchlist",
                decision_id=decision.decision_id,
                failure_ids=[],
                summary_reason="Missing observations retained",
            )
        },
        scores={cid: score},
        decisions={cid: decision},
        selection=selection,
        errors=(),
        execution_mode="live",
        research_artifacts={cid: owned},
    )
    return result, admission, llm


def context(result, admission):
    return build_report_context_from_run_v3(
        result,
        run_input=admission.run_input,
        run_id=result.run_id,
        actual_admission=admission,
    )


def test_verified_replay_preserves_original_context_request_and_draft(
    scored_context_case,
):
    # Given: original scored DTOs and a trusted replay verifier for their capture.
    result, original, llm = scored_context_case
    replay = replace(
        original, execution_scope="actual_replay", replay_verifier=lambda: True
    )
    original_context = context(result, original)
    replay_context = context(result, replay)
    requests = []
    content = dict(
        schema_version=result.schema_version,
        summary="Missing observations",
        company_team="Unknown",
        technology="Unknown",
        market="Unknown",
        assessment_risks="Missing observations",
        limitations=["Synthetic plumbing test"],
    )

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.content)
        return httpx.Response(
            200,
            json=body(
                json.dumps(content), usage={"input_tokens": 10, "output_tokens": 10}
            ),
        )

    llm.transport._http_transport = httpx.MockTransport(respond)
    generator = ReportGeneratorV3(llm)
    before = deepcopy(result.research_artifacts)
    # When: the real generator serializes both fixed inputs through the mock wire.
    original_draft = generator(original_context, ())
    replay_draft = generator(replay_context, ())
    # Then: replay changes no generation claim, request bytes or draft hash.
    assert replay_context == original_context
    payload = replay_context.snapshot()
    assert (
        payload["execution_scope"]
        == payload["provenance"]["provider_execution"]
        == "actual"
    )
    assert payload["provenance"]["synthetic"] is False
    assert (
        not payload["publication_allowed"] and not payload["final_publication_allowed"]
    )
    assert len(requests) == 2 and requests[0] == requests[1]
    assert sha256(requests[0]).digest() == sha256(requests[1]).digest()
    assert artifact_hash(original_draft) == artifact_hash(replay_draft)
    assert result.research_artifacts == before


def test_controlled_context_cannot_claim_original_actual_provenance(
    scored_context_case,
):
    # Given
    result, original, _ = scored_context_case
    controlled = replace(original, execution_scope="controlled_response")
    # When
    payload = context(result, controlled).snapshot()
    # Then
    assert payload["execution_scope"] == "controlled_response"
    assert payload["provenance"]["provider_execution"] == "controlled_response"
    assert payload["provenance"]["synthetic"] is True
    assert (
        context(result, controlled).context_id != context(result, original).context_id
    )
    with pytest.raises(ValueError, match="requires actual_replay scope"):
        replace(controlled, replay_verifier=lambda: True)


def test_revoked_replay_verifier_rejects_context_before_request(scored_context_case):
    # Given
    result, original, llm = scored_context_case
    valid = True
    replay = replace(
        original, execution_scope="actual_replay", replay_verifier=lambda: valid
    )
    before = llm.runtime.ledger.snapshot()
    valid = False
    # When / Then
    with pytest.raises(ValueError, match="verified actual-origin replay"):
        ReportGeneratorV3(llm)(context(result, replay), ())
    assert llm.runtime.ledger.snapshot() == before


def test_seed_resolvers_cannot_authorize_replay_scored_snapshot(
    scored_context_case, actual_admission_fixture
):
    # Given: the reference seed resolver has the same Sources but another freeze.
    result, original, llm = scored_context_case
    reference, *_ = actual_admission_fixture
    replay = replace(
        original,
        execution_scope="actual_replay",
        replay_verifier=lambda: True,
        review_resolvers=reference.review_resolvers,
    )
    before = llm.runtime.ledger.snapshot()
    # When / Then
    with pytest.raises(ValueError, match="configured source-bound"):
        context(result, replay)
    assert llm.runtime.ledger.snapshot() == before
