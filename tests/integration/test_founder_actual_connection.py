"""Controlled MockTransport, SYNTHETIC semantics and test-only runtime budgets.

Local captured bytes establish integrity, not founder truth or production approval.
"""

import json
import socket
from dataclasses import replace

import httpx
import pytest
from tests.unit.test_actual_admission_v3 import configured as configured
from tests.unit.test_openai_attempt import body

from skala_rag.agents.founder import evaluate_founder_approved
from skala_rag.agents.founder_verification import (
    FOUNDER_CRITERIA,
    FounderReviewError,
    ReviewedFounderAnchor,
)
from skala_rag.agents.moat_verification import (
    core_artifact_digest,
    frozen_snapshot_digest,
)
from skala_rag.agents.source_fact_verification import (
    SourceBoundReviewResolver,
    SourceFactError,
    review_receipt_digest,
)
from skala_rag.contracts.evaluation import EvaluationResult
from skala_rag.fakes import FakeLLM
from skala_rag.tools.source_fetch import content_hash

REQUEST = b'{"purpose":"SYNTHETIC founder/person/employment/anchor review"}'
PEOPLE = ("synthetic-founder-1",)
SUBJECT = "Synthetic founder subject"


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch):
    def denied(*_args, **_kwargs):
        pytest.fail("External sockets forbidden in controlled founder connection")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.fixture
def founder_case(configured):
    admission, snapshot, llm, _, _, review = configured
    rubric = admission.registry.rubric("core-0.1.0")
    snapshot.evidence["ev-capture"].criterion_ids = sorted(FOUNDER_CRITERIA)
    resolver = admission.review_resolvers[(snapshot.snapshot_id, "core-0.1.0")]
    anchors = {
        cid: ReviewedFounderAnchor(
            review_reference=f"synthetic-founder-review:{cid}",
            artifact_sha256=core_artifact_digest(rubric),
            snapshot_sha256=frozen_snapshot_digest(snapshot),
            criterion_id=cid,
            rating=3,
            evidence_ids=("ev-capture",),
            founder_person_ids=PEOPLE,
            person_by_evidence_id=(("ev-capture", PEOPLE[0]),),
            anchor_facts_reviewed=True,
            minimum_evidence_reviewed=True,
            person_identity_reviewed=True,
            employment_identity_reviewed=True,
            independent_corroboration_reviewed=True,
        )
        for cid in sorted(FOUNDER_CRITERIA)
    }
    reviews = tuple(
        review.model_copy(
            update={
                "review_reference": anchor.review_reference,
                "request_sha256": content_hash(REQUEST).removeprefix("sha256:"),
                "receipt_sha256": review_receipt_digest(anchor),
                "subject": SUBJECT,
                "snapshot_sha256": frozen_snapshot_digest(snapshot),
            }
        )
        for anchor in anchors.values()
    )
    sources = resolver._sources
    admission = replace(
        admission,
        review_resolvers={
            (snapshot.snapshot_id, "core-0.1.0"): SourceBoundReviewResolver(
                snapshot, rubric, sources=sources, reviews=reviews
            )
        },
    )
    llm.call = llm.call.model_copy(update={"node": "founder_evaluation"})
    output = {
        "criteria": [
            {
                "criterion_id": cid,
                "status": "observed",
                "rating": 3,
                "evidence_ids": ["ev-capture"],
                "rationale": "SYNTHETIC review",
                "missing_reason": None,
                "applicability_note": None,
            }
            for cid in sorted(FOUNDER_CRITERIA)
        ],
        "research_gaps": [],
        "caveats": ["SYNTHETIC semantic decision"],
    }
    seen = []

    def response(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=body(
                json.dumps(output), usage={"input_tokens": 10, "output_tokens": 10}
            ),
        )

    llm.transport._http_transport = httpx.MockTransport(response)
    kwargs = dict(
        actual_admission=admission,
        founder_person_ids=PEOPLE,
        verified_person_by_evidence_id={"ev-capture": PEOPLE[0]},
        llm=llm,
        review_request=REQUEST,
        review_subject=SUBJECT,
        reviewed_anchors=anchors,
    )
    return snapshot, kwargs, output, seen, reviews, sources


def test_observed_when_independent_receipts_match_real_runtime_wire(founder_case):
    # Given: independently supplied synthetic founder/person/employment decisions.
    snapshot, kwargs, _, seen, _, _ = founder_case
    original = frozen_snapshot_digest(snapshot)
    # When: the new public entrypoint uses the real runtime and provider attempt.
    result = evaluate_founder_approved(snapshot, **kwargs)
    # Then: the original envelope survives, with one physically accounted response.
    assert type(result) is EvaluationResult and result.status == "success"
    assert result.snapshot_id == snapshot.snapshot_id
    assert result.policy_version == snapshot.policy_version
    assert result.evaluation is not None
    assert all(c.rating == 3 for c in result.evaluation.criteria)
    assert frozen_snapshot_digest(snapshot) == original
    assert len(seen) == 1
    ledger = kwargs["llm"].runtime.ledger.snapshot()
    assert ledger["calls"] == 1
    assert ledger["input_tokens_accounted"] == ledger["output_tokens_accounted"] == 10
    assert ledger["cost_usd_accounted"] == "0.10"


@pytest.mark.parametrize("people", [(), ("unknown-person",)])
def test_missing_when_founder_identity_is_unknown(founder_case, people):
    # Given: no attributable founder facts and no supplied positive founder receipts.
    snapshot, kwargs, output, seen, _, _ = founder_case
    kwargs.update(founder_person_ids=people, reviewed_anchors={})
    for criterion in output["criteria"]:
        criterion.update(
            status="missing",
            rating=None,
            evidence_ids=[],
            missing_reason="founder_identity_unverified",
        )
    # When
    result = evaluate_founder_approved(snapshot, **kwargs)
    # Then: no people or ratings are invented.
    assert result.status == "success"
    assert result.evaluation is not None
    assert all(
        c.rating is None and c.status == "missing" for c in result.evaluation.criteria
    )
    user = json.loads(seen[0]["input"][1]["content"])
    assert user["evidence"] == []


@pytest.mark.parametrize(
    "change",
    [
        "request",
        "subject",
        "receipt",
        "people",
        "employment",
        "anchor",
        "unreviewed",
        "rejected",
        "unresolved",
    ],
)
def test_denied_when_receipt_or_independent_acceptance_is_invalid(founder_case, change):
    # Given: a plausible observed answer cannot authenticate its own claims.
    snapshot, kwargs, _, seen, reviews, sources = founder_case
    admission = kwargs["actual_admission"]
    if change in {"rejected", "unresolved"}:
        kwargs["actual_admission"] = replace(
            admission,
            review_resolvers={
                (snapshot.snapshot_id, "core-0.1.0"): SourceBoundReviewResolver(
                    snapshot,
                    admission.registry.rubric("core-0.1.0"),
                    sources=sources,
                    reviews=tuple(
                        r.model_copy(update={"decision": change}) for r in reviews
                    ),
                )
            },
        )
    elif change == "request":
        kwargs["review_request"] += b" "
    elif change == "subject":
        kwargs["review_subject"] = "different subject"
    elif change == "unreviewed":
        kwargs["reviewed_anchors"] = {}
    else:
        cid = sorted(FOUNDER_CRITERIA)[0]
        updates = {
            "receipt": {"rating": 4},
            "people": {"person_by_evidence_id": (("ev-capture", "same-name-other"),)},
            "employment": {"employment_identity_reviewed": False},
            "anchor": {"anchor_facts_reviewed": False},
        }
        kwargs["reviewed_anchors"][cid] = replace(
            kwargs["reviewed_anchors"][cid], **updates[change]
        )
    # When / Then: technical denial, no missing fallback and no repair request.
    with pytest.raises(FounderReviewError, match="FOUNDER_REVIEW_REJECTED"):
        evaluate_founder_approved(snapshot, **kwargs)
    assert len(seen) == 1
    assert kwargs["llm"].runtime.ledger.snapshot()["calls"] == 1


@pytest.mark.parametrize(
    "change", ["source_bytes", "source_metadata", "snapshot", "fake", "node"]
)
def test_preflight_denied_when_source_or_runtime_is_tampered(founder_case, change):
    # Given
    snapshot, kwargs, _, seen, _, sources = founder_case
    if change == "source_bytes":
        next(iter(sources.values())).path.write_bytes(b"tampered original")
    elif change == "source_metadata":
        next(iter(snapshot.sources.values())).title = "tampered metadata"
    elif change == "snapshot":
        snapshot.evidence["ev-capture"].claim = "tampered fact"
    elif change == "fake":
        kwargs["llm"] = FakeLLM([])
    else:
        kwargs["llm"].call = kwargs["llm"].call.model_copy(
            update={"node": "market_evaluation"}
        )
    # When / Then
    with pytest.raises((ValueError, SourceFactError)):
        evaluate_founder_approved(snapshot, **kwargs)
    assert seen == []
    ledger = kwargs["actual_admission"].runtime_binding.runtime.ledger.snapshot()
    assert ledger["calls"] == 0


def test_terminal_failure_when_output_cites_unattributable_person(founder_case):
    # Given: model claims an observed founder despite a foreign person mapping.
    snapshot, kwargs, _, seen, _, _ = founder_case
    kwargs["verified_person_by_evidence_id"] = {"ev-capture": "same-name-other"}
    # When
    result = evaluate_founder_approved(snapshot, **kwargs)
    # Then: filtering applies to the prompt and output without repairs.
    assert result.status == "failure" and result.evaluation is None
    assert "EVIDENCE_NOT_IN_SNAPSHOT" in result.errors[0].message_redacted
    assert json.loads(seen[0]["input"][1]["content"])["evidence"] == []
    assert len(seen) == 1


def test_review_denied_when_original_changes_during_wire_call(founder_case):
    # Given: a source passes preflight, but changes before post-output review.
    snapshot, kwargs, _, seen, _, sources = founder_case
    transport = kwargs["llm"].transport._http_transport

    def tampered(request: httpx.Request) -> httpx.Response:
        response = transport.handle_request(request)
        next(iter(sources.values())).path.write_bytes(b"changed during evaluation")
        return response

    kwargs["llm"].transport._http_transport = httpx.MockTransport(tampered)
    # When / Then: the consumed response remains accounted, not falsely accepted.
    with pytest.raises(FounderReviewError, match="FOUNDER_REVIEW_REJECTED"):
        evaluate_founder_approved(snapshot, **kwargs)
    assert len(seen) == 1
    assert kwargs["llm"].runtime.ledger.snapshot()["calls"] == 1
