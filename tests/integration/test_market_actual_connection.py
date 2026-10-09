"""Controlled wire; ALL semantic decisions/readiness/budgets are SYNTHETIC.

No external sockets, provider billing, real eligibility or market truth claimed.
"""

import json
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

import httpx
import pytest
from tests.unit import test_actual_admission_v3 as shared
from tests.unit.test_openai_attempt import body

from skala_rag.agents.evaluation import DimensionAssessmentOutput
from skala_rag.agents.market import (
    MarketLink,
    MarketObservationReview,
    MarketObservationReviewRequest,
    MarketTarget,
    evaluate_market_approved,
)
from skala_rag.agents.moat_verification import _digest, frozen_snapshot_digest
from skala_rag.agents.source_fact_verification import (
    SourceBoundReviewResolver,
    review_receipt_digest,
)
from skala_rag.contracts import EvaluationResult, EvaluationSnapshot
from skala_rag.scoring.approved_consumers import ActualAdmissionV3
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM
from skala_rag.tools.source_fetch import content_hash

admission_fixture = shared.configured
offline_fixture = shared.offline


@dataclass
class MarketCase:
    """Mutable synthetic wire response/controller inputs for adversarial tests."""

    admission: ActualAdmissionV3
    snapshot: EvaluationSnapshot
    llm: RuntimeStructuredLLM
    target: MarketTarget | None
    links: dict[str, MarketLink]
    reviews: dict[str, MarketObservationReview]
    output: DimensionAssessmentOutput
    seen: list[httpx.Request]

    def run(self) -> EvaluationResult:
        return evaluate_market_approved(
            self.snapshot,
            target_market=self.target,
            market_links=self.links,
            rubric=self.admission.registry.rubric("core-0.1.0"),
            llm=self.llm,
            actual_admission=self.admission,
            reviewed_observations=self.reviews,
        )


@pytest.fixture
def market_case(admission_fixture, request):
    admission, snapshot, llm, _, receipt, review = admission_fixture
    metric, scope, decision = getattr(
        request, "param", ("demand", "company", "accepted")
    )
    cid: Literal["market.size", "market.growth", "market.demand"] = (
        "market.demand"
        if metric == "demand"
        else ("market.growth" if metric == "cagr" else "market.size")
    )
    evidence = snapshot.evidence["ev-capture"].model_copy(
        update={
            "criterion_ids": [cid],
            "scope": scope,
            "candidate_id": None if scope == "industry" else snapshot.candidate_id,
        }
    )
    target = MarketTarget(segment_id="warehouse", geographies=("global",))
    links = {"ev-capture": MarketLink(segment_id=target.segment_id)}
    if metric != "demand":
        growth = metric == "cagr"
        evidence = evidence.model_copy(
            update={
                "value": 18.2 if growth else (5e10 if metric == "tam" else 8e8),
                "unit": "%" if growth else "USD",
                "currency": None if growth else "USD",
                "value_as_of": None if growth else date(2025, 1, 1),
                "geography": "global",
            }
        )
        links["ev-capture"] = MarketLink(
            segment_id=target.segment_id,
            metric=metric,
            basis="forecast" if growth else "actual",
            reference_year=2025,
            end_year=2030 if growth else None,
        )
    snapshot = snapshot.model_copy(update={"evidence": {"ev-capture": evidence}})
    receipt = receipt.model_copy(
        update={"criterion_id": cid, "rating": 4 if metric == "cagr" else 3}
    )
    request = (
        MarketObservationReviewRequest(
            target=target,
            links=links,
            criterion=cid,
            excerpts={"ev-capture": evidence.excerpt},
        )
        .model_dump_json()
        .encode()
    )
    core = admission.registry.rubric("core-0.1.0")
    review = review.model_copy(
        update={
            "request_sha256": content_hash(request).removeprefix("sha256:"),
            "receipt_sha256": review_receipt_digest(receipt),
            "snapshot_sha256": frozen_snapshot_digest(snapshot),
            "rubric_sha256": _digest(core),
            "decision": decision,
        }
    )
    resolver = admission.review_resolvers[(snapshot.snapshot_id, "core-0.1.0")]
    admission = replace(
        admission,
        review_resolvers={
            (snapshot.snapshot_id, "core-0.1.0"): SourceBoundReviewResolver(
                snapshot, core, sources=resolver._sources, reviews=(review,)
            )
        },
    )
    criteria = [
        {
            "criterion_id": cid,
            "status": "missing",
            "rating": None,
            "evidence_ids": [],
            "rationale": "SYNTHETIC missing control",
            "missing_reason": "not_disclosed",
            "applicability_note": None,
        }
        for cid in ("market.size", "market.growth", "market.demand")
    ]
    criteria = [
        receipt.model_dump(exclude={"schema_version"})
        if c["criterion_id"] == cid
        else c
        for c in criteria
    ]
    output = DimensionAssessmentOutput.model_validate({"criteria": criteria})
    case = MarketCase(
        admission,
        snapshot,
        llm,
        target,
        links,
        {cid: MarketObservationReview(request, review.subject, receipt)},
        output,
        [],
    )

    def wire(request):
        case.seen.append(request)
        return httpx.Response(
            200,
            json=body(
                case.output.model_dump_json(),
                usage={"input_tokens": 10, "output_tokens": 10},
            ),
        )

    llm.transport._http_transport = httpx.MockTransport(wire)
    return case


def test_reviewed_demand_crosses_real_runtime_wire(market_case):
    # Given: independently supplied SYNTHETIC review and one test-only allowance.
    case = market_case
    original = case.snapshot.model_dump(mode="json")
    call = case.llm.call.model_dump()
    # When
    result = case.run()
    # Then: response use is physically accounted; the caller's context survives.
    assert result.status == "success"
    assert result.evaluation.criteria[2] == case.reviews["market.demand"].receipt
    assert case.snapshot.model_dump(mode="json") == original
    assert case.llm.call.model_dump() == call
    ledger = case.llm.runtime.ledger.snapshot()
    assert len(case.seen) == ledger["calls"] == 1
    assert ledger["input_tokens_accounted"] == ledger["output_tokens_accounted"] == 10
    assert ledger["cost_usd_accounted"] == "0.10"
    assert len(case.llm.retrieval_records) == 1


@pytest.mark.parametrize(
    "market_case",
    [
        ("tam", "industry", "accepted"),
        ("cagr", "industry", "accepted"),
        ("demand", "industry", "accepted"),
    ],
    indirect=True,
)
def test_pinned_proposed_core_admits_reviewed_industry_without_metadata_flip(
    market_case,
):
    # Synthetic reviews and budget; the actual historical Core bytes stay intact.
    case = market_case
    rubric = case.admission.registry.rubric("core-0.1.0")
    assert rubric["status"] == "proposed"
    original = next(iter(case.admission.review_resolvers.values()))
    resolver = SourceBoundReviewResolver(
        case.snapshot,
        rubric,
        sources=original._sources,
        reviews=original._reviews,
        approval_registry=case.admission.registry,
    )
    case.admission = replace(
        case.admission,
        review_resolvers={(case.snapshot.snapshot_id, "core-0.1.0"): resolver},
    )
    result = case.run()
    assert result.status == "success"
    assert result.evaluation is not None
    observed = [c for c in result.evaluation.criteria if c.status == "observed"]
    assert len(observed) == 1
    assert observed[0] == next(iter(case.reviews.values())).receipt
    assert case.admission.registry.rubric("core-0.1.0") == rubric
    assert len(case.seen) == case.llm.runtime.ledger.snapshot()["calls"] == 1


def test_industry_pin_does_not_admit_altered_core(market_case):
    case = market_case
    rubric = case.admission.registry.rubric("core-0.1.0")
    rubric["common_rules"]["industry_evidence_dimensions"] = ["market", "technology"]
    original = next(iter(case.admission.review_resolvers.values()))
    with pytest.raises(ValueError, match="ARTIFACT_BINDING_REJECTED"):
        SourceBoundReviewResolver(
            case.snapshot,
            rubric,
            sources=original._sources,
            reviews=original._reviews,
            approval_registry=case.admission.registry,
        )
    assert case.seen == []
    assert case.llm.runtime.ledger.snapshot()["calls"] == 0


@pytest.mark.parametrize(
    "market_case",
    [
        ("tam", "industry", "accepted"),
        ("tam", "company", "accepted"),
        ("sam", "company", "accepted"),
        ("cagr", "industry", "accepted"),
        ("cagr", "company", "accepted"),
        ("demand", "industry", "accepted"),
        ("demand", "company", "rejected"),
        ("demand", "company", "unresolved"),
    ],
    indirect=True,
)
def test_numeric_industry_and_review_decisions(market_case):
    # Given: explicit SYNTHETIC numerical/source reviews or non-accepted records.
    case = market_case
    review = next(iter(case.admission.review_resolvers.values()))._reviews[0]
    # When
    result = case.run()
    # Then
    evidence = case.snapshot.evidence["ev-capture"]
    # Current pinned Core retains proposed status; the shared resolver denies
    # industry despite the historical pin and explicit market common rule.
    admitted = review.decision == "accepted" and evidence.scope == "company"
    assert result.status == ("success" if admitted else "failure")
    assert len(case.seen) == case.llm.runtime.ledger.snapshot()["calls"] == 1


@pytest.mark.parametrize("unknown", ["target", "links", "both"])
def test_all_missing_without_fabricated_context(market_case, unknown):
    # Given: unknown controller context, not guessed segment/country/link values.
    case = market_case
    if unknown in ("target", "both"):
        case.target = None
    if unknown in ("links", "both"):
        case.links = {}
    case.reviews = {}
    case.output.criteria[2] = case.output.criteria[2].model_copy(
        update={
            "status": "missing",
            "rating": None,
            "evidence_ids": [],
            "missing_reason": "not_disclosed",
        }
    )
    # When
    result = case.run()
    # Then
    assert result.status == "success"
    assert all(c.status == "missing" for c in result.evaluation.criteria)
    payload = json.loads(case.seen[0].content)
    user = json.loads(payload["input"][1]["content"])
    assert user["evidence"] == []
    assert user["context"]["target_market"] == (
        None if case.target is None else case.target.model_dump(mode="json")
    )
    assert user["context"]["market_figures"] == {}
    assert len(case.seen) == 1


def test_original_bytes_changed_during_wire_are_not_cached_pass(market_case):
    # Given: admission passed before a SYNTHETIC wire changes retained source bytes.
    case = market_case
    resolver = next(iter(case.admission.review_resolvers.values()))
    trusted = next(iter(resolver._sources.values()))
    transport = case.llm.transport._http_transport

    def wire(request):
        response = transport.handle_request(request)
        trusted.path.write_bytes(b"<p>Tampered SYNTHETIC source</p>")
        return response

    case.llm.transport._http_transport = httpx.MockTransport(wire)
    # When / Then: post-wire source closure rejects instead of emitting Missing.
    result = case.run()
    assert result.status == "failure"
    assert "MARKET_SOURCE_REVIEW_INVALID" in result.errors[0].message_redacted
    assert len(case.seen) == case.llm.runtime.ledger.snapshot()["calls"] == 1
