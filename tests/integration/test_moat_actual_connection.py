"""Mock-wire Moat admission; SYNTHETIC semantics and test-only token/cost limits.

No external socket, factual review, provider billing or production approval.
"""

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from tests.unit.test_actual_admission_v3 import configured as configured
from tests.unit.test_actual_admission_v3 import offline as offline
from tests.unit.test_openai_attempt import body
from tests.unit.test_source_bound_review import capture_review

from skala_rag.agents.moat import MOAT_CRITERIA, evaluate_moat_approved
from skala_rag.agents.moat_verification import (
    ReviewedMoatAnchor,
    _digest,
    core_artifact_digest,
    frozen_snapshot_digest,
)
from skala_rag.agents.source_fact_verification import (
    SourceBoundReviewResolver,
    SourceFactError,
    review_receipt_digest,
)
from skala_rag.fakes import FakeLLM
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt
from skala_rag.tools.source_fetch import content_hash


@pytest.fixture
def connected(configured, tmp_path: Path):
    """Separate synthetic reviewer records from the evaluating wire response."""
    admission, _, llm, _, _, _ = configured
    local = tmp_path / "moat"
    local.mkdir()
    snapshot, _, sources, _, review, _ = capture_review(local)
    evidence = snapshot.evidence["ev-capture"].model_copy(
        update={"criterion_ids": sorted(MOAT_CRITERIA)}
    )
    record = next(iter(snapshot.retrieval_records.values())).model_copy(
        update={
            "source_ids": list(sources),
            "chunk_ids": [],
            "evidence_ids": [evidence.evidence_id],
        }
    )
    snapshot = snapshot.model_copy(
        update={
            "sources": {sid: source.source for sid, source in sources.items()},
            "chunks": {},
            "evidence_ids": [evidence.evidence_id],
            "evidence": {evidence.evidence_id: evidence},
            "retrieval_records": {record.retrieval_id: record},
        }
    )
    rubric = admission.registry.rubric("core-0.1.0")
    request = b"SYNTHETIC Moat minimum facts, rights and independent comparison"
    anchors = {
        cid: ReviewedMoatAnchor(
            f"synthetic-independent:{cid}",
            core_artifact_digest(rubric),
            frozen_snapshot_digest(snapshot),
            cid,
            5,
            (evidence.evidence_id,),
        )
        for cid in sorted(MOAT_CRITERIA)
    }
    reviews = {
        cid: review.model_copy(
            update={
                "review_reference": anchor.review_reference,
                "request_sha256": content_hash(request).removeprefix("sha256:"),
                "receipt_sha256": review_receipt_digest(anchor),
                "snapshot_sha256": frozen_snapshot_digest(snapshot),
                "rubric_sha256": _digest(rubric),
            }
        )
        for cid, anchor in anchors.items()
    }
    output = {
        "criteria": [
            {
                "criterion_id": cid,
                "status": "observed",
                "rating": 5,
                "evidence_ids": [evidence.evidence_id],
                "rationale": "SYNTHETIC reviewed anchor; not factual evidence.",
                "missing_reason": None,
                "applicability_note": None,
            }
            for cid in sorted(MOAT_CRITERIA)
        ],
        "research_gaps": [],
        "caveats": ["SYNTHETIC independent semantics and test-only budgets."],
    }
    requests = []

    def respond(wire_request: httpx.Request) -> httpx.Response:
        requests.append(wire_request)
        return httpx.Response(
            200,
            json=body(
                text=json.dumps(output),
                usage={"input_tokens": 10, "output_tokens": 10},
            ),
        )

    llm.call = llm.call.model_copy(update={"node": "moat_evaluation"})
    llm.transport = OpenAIResponsesAttempt(
        api_key="SYNTHETIC-NOT-A-CREDENTIAL",
        prompt_version="synthetic-moat-connection",
        schema_version=snapshot.schema_version,
        clock=llm.runtime.clock,
        http_transport=httpx.MockTransport(respond),
    )

    def invoke():
        resolver = SourceBoundReviewResolver(
            snapshot, rubric, sources=sources, reviews=tuple(reviews.values())
        )
        current = replace(
            admission,
            review_resolvers={(snapshot.snapshot_id, "core-0.1.0"): resolver},
        )
        return evaluate_moat_approved(
            snapshot,
            actual_admission=current,
            llm=llm,
            review_request=request,
            review_subject=review.subject,
            reviewed_anchors=anchors,
        )

    return invoke, llm, output, anchors, reviews, sources, requests, rubric


@pytest.mark.parametrize("rating", [1, 2, 3, 4, 5])
def test_reviewed_anchor_when_mock_wire_is_admitted(connected, rating):
    # Given: separately supplied synthetic minimum-fact/rights/comparison reviews.
    invoke, llm, output, anchors, reviews, _, requests, rubric = connected
    for criterion in output["criteria"]:
        cid = criterion["criterion_id"]
        criterion["rating"] = rating
        anchors[cid] = replace(anchors[cid], rating=rating)
        reviews[cid] = reviews[cid].model_copy(
            update={"receipt_sha256": review_receipt_digest(anchors[cid])}
        )
    # When: the real runtime and OpenAI attempt consume a controlled wire response.
    result = invoke()
    # Then: the original atomic envelope survives; usage is physically accounted.
    assert result.status == "success" and result.branch_id == "moat"
    assert result.evaluations is not None
    assert {c.rating for c in result.evaluations["moat"].criteria} == {rating}
    assert rubric["status"] == "proposed"
    assert len(requests) == 1
    ledger = llm.runtime.ledger.snapshot()
    assert ledger["calls"] == 1
    assert ledger["input_tokens_accounted"] == ledger["output_tokens_accounted"] == 10
    assert ledger["cost_usd_accounted"] == "0.10"
    assert ledger["usage_invalid"] is False


@pytest.mark.parametrize("cid", ["moat.ip", "moat.differentiation"])
def test_missing_when_rights_or_comparison_are_unknown(connected, cid):
    # Given: unresolved review cannot establish rights or independent comparison.
    invoke, _, output, _, reviews, _, _, _ = connected
    reviews[cid] = reviews[cid].model_copy(update={"decision": "unresolved"})
    criterion = next(c for c in output["criteria"] if c["criterion_id"] == cid)
    criterion.update(
        status="missing", rating=None, evidence_ids=[], missing_reason="unreviewed"
    )
    # When
    result = invoke()
    # Then: uncertainty is not an observed low rating, false fact or N/A.
    assert result.status == "success" and result.evaluations is not None
    missing = next(
        c for c in result.evaluations["moat"].criteria if c.criterion_id == cid
    )
    assert missing.status == "missing" and missing.rating is None


@pytest.mark.parametrize("decision", ["unresolved", "rejected", "unreviewed"])
@pytest.mark.parametrize("rating", [1, 2, 5])
def test_atomic_denial_when_observed_anchor_lacks_review(connected, decision, rating):
    # Given: unknown rights/comparison cannot justify positive or negative facts.
    invoke, _, output, anchors, reviews, _, _, _ = connected
    for criterion in output["criteria"]:
        cid = criterion["criterion_id"]
        criterion["rating"] = rating
        anchors[cid] = replace(anchors[cid], rating=rating)
        reviews[cid] = reviews[cid].model_copy(
            update={
                "receipt_sha256": review_receipt_digest(anchors[cid]),
                "decision": "unresolved" if decision == "unreviewed" else decision,
            }
        )
    if decision == "unreviewed":
        reviews.clear()
    # When
    result = invoke()
    # Then: no partial success or manufactured low rating.
    assert result.status == "failure" and result.evaluations is None
    assert "MOAT_RUBRIC_UNVERIFIED" in result.errors[0].message_redacted


@pytest.mark.parametrize(
    "change",
    [
        {"rating": 1},
        {"artifact_sha256": "0" * 64},
        {"snapshot_sha256": "0" * 64},
        {"evidence_ids": ("unknown",)},
        {"criterion_id": "moat.ip"},
        {"review_reference": "forged"},
    ],
)
def test_receipt_denial_when_anchor_is_tampered(connected, change):
    # Given: accepted semantics cannot override the existing domain validator.
    invoke, _, _, anchors, reviews, _, _, _ = connected
    cid = "moat.data"
    anchors[cid] = replace(anchors[cid], **change)
    if not ("evidence_ids" in change or "review_reference" in change):
        reviews[cid] = reviews[cid].model_copy(
            update={"receipt_sha256": review_receipt_digest(anchors[cid])}
        )
    # When
    result = invoke()
    # Then
    assert result.status == "failure" and result.evaluations is None


def test_source_denial_when_original_bytes_change(connected):
    # Given: a known source changes after independent review.
    invoke, llm, _, _, _, sources, requests, _ = connected
    next(iter(sources.values())).path.write_bytes(b"tampered original")
    # When / Then: reject before consuming the wire request or budget.
    with pytest.raises(SourceFactError, match="SOURCE_PROOF_REJECTED"):
        invoke()
    assert requests == [] and llm.runtime.ledger.snapshot()["calls"] == 0


@pytest.mark.parametrize("change", ["snapshot", "rubric", "subject", "span"])
def test_review_denial_when_source_binding_is_tampered(connected, change):
    # Given: even an accepted review cannot override the original binding.
    invoke, _, _, _, reviews, _, _, _ = connected
    for cid, review in reviews.items():
        changes = {
            "snapshot": {"snapshot_sha256": "0" * 64},
            "rubric": {"rubric_sha256": "0" * 64},
            "subject": {"subject": "other controller subject"},
            "span": {"spans": (replace(review.spans[0], quote="forged quote"),)},
        }
        reviews[cid] = review.model_copy(update=changes[change])
    # When / Then
    with pytest.raises(SourceFactError):
        invoke()


def test_na_denial_when_model_proposes_unsupported_applicability(connected):
    # Given: the existing atomic body supports observed/missing only.
    invoke, _, output, _, _, _, _, _ = connected
    output["criteria"][0].update(status="not_applicable", rating=None)
    # When
    result = invoke()
    # Then
    assert result.status == "failure" and result.evaluations is None


def test_runtime_denial_when_node_is_foreign(connected):
    # Given: the same runtime has a foreign branch call.
    invoke, llm, _, _, _, _, requests, _ = connected
    llm.call = llm.call.model_copy(update={"node": "market_evaluation"})
    # When / Then
    with pytest.raises(ValueError, match="branch mismatch"):
        invoke()
    assert requests == [] and llm.runtime.ledger.snapshot()["calls"] == 0


def test_runtime_denial_when_fake_llm_is_supplied(configured):
    # Given: fake/fixture output is never an actual runtime capability.
    admission, snapshot, _, _, _, _ = configured
    llm = FakeLLM([])
    # When / Then
    with pytest.raises(ValueError, match="branch mismatch"):
        evaluate_moat_approved(
            snapshot,
            actual_admission=admission,
            llm=llm,
            review_request=b"SYNTHETIC",
            review_subject="SYNTHETIC",
            reviewed_anchors={},
        )
    assert llm.calls == []
