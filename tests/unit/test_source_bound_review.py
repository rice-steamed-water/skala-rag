"""Real local byte/quote checks with SYNTHETIC independent reviewer records.

The generated PDF proves integrity, not factual truth or anchor correspondence.
Only these tests supply semantic decisions; the resolver must never invent them.
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Literal, assert_never

import pytest
from pydantic import ValidationError
from tests.unit.test_source_fact_verification import LINES, SYSTEM, span, world

from skala_rag.agents.finance_verification import ReviewedFinancialFact
from skala_rag.agents.founder_verification import ReviewedFounderAnchor
from skala_rag.agents.moat_verification import (
    ReviewedMoatAnchor,
    _digest,
    core_artifact_digest,
    frozen_snapshot_digest,
)
from skala_rag.agents.source_fact_verification import (
    CompanyObservationReviewRequest,
    SourceBoundReview,
    SourceBoundReviewResolver,
    SourceFactError,
    SourceTextSpan,
    TrustedCapture,
    review_receipt_digest,
)
from skala_rag.agents.technology_verification import ReviewedTechnologyAnchor
from skala_rag.contracts.assessment import CriterionAssessment
from skala_rag.rag.text_review import text_hash
from skala_rag.tools.company_research import FieldObservation, StageObservation
from skala_rag.tools.source_fetch import content_hash

REQUEST = b'{"criterion":"technology.integration","subject":"Synthetic System S"}'


def setup_review(tmp_path: Path):
    """Supply a synthetic independent decision outside the resolver."""
    snapshot, rubric, sources = world(tmp_path)
    receipt = ReviewedTechnologyAnchor(
        "independent-test-review",
        core_artifact_digest(rubric),
        frozen_snapshot_digest(snapshot),
        "technology.integration",
        3,
        ("ev-synthetic-a-3",),
        True,
        True,
        True,
        True,
    )
    review = SourceBoundReview(
        review_reference=receipt.review_reference,
        request_sha256=content_hash(REQUEST).removeprefix("sha256:"),
        receipt_sha256=review_receipt_digest(receipt),
        candidate_id=snapshot.candidate_id,
        subject=SYSTEM,
        as_of=snapshot.as_of,
        snapshot_sha256=frozen_snapshot_digest(snapshot),
        rubric_sha256=_digest(rubric),
        decision="accepted",
        spans=(span(snapshot, sources, LINES[3]),),
        rationale="Synthetic independent decision, not a factual assessment.",
    )
    return snapshot, rubric, sources, receipt, review


@pytest.mark.parametrize("decision", ["accepted", "rejected", "unresolved"])
def test_review_matches_when_source_integrity_holds(tmp_path: Path, decision: str):
    # Given: a separately supplied synthetic semantic decision.
    snapshot, rubric, sources, receipt, review = setup_review(tmp_path)
    review = review.model_copy(update={"decision": decision})
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When: real local bytes and extracted offsets are rechecked.
    result = resolver.resolve_review(REQUEST, receipt, subject=SYSTEM)
    # Then: the decision and exact audit commitments survive.
    assert result == review
    assert result is not None
    assert result is not review
    assert result.review_sha256 == _digest(review.model_dump(mode="json"))


@pytest.mark.parametrize("missing", ["review", "source", "span"])
def test_unsupported_when_review_or_source_is_unknown(tmp_path: Path, missing: str):
    # Given
    snapshot, rubric, sources, receipt, review = setup_review(tmp_path)
    if missing == "span":
        review = review.model_copy(update={"spans": ()})
    resolver = SourceBoundReviewResolver(
        snapshot,
        rubric,
        sources={} if missing == "source" else sources,
        reviews=() if missing == "review" else (review,),
    )
    # When
    result = resolver.resolve_review(REQUEST, receipt, subject=SYSTEM)
    # Then: captured public text is not semantic review.
    assert result is None


@pytest.mark.parametrize("decision", ["accepted", "rejected", "unresolved"])
def test_source_rejected_when_original_bytes_change(tmp_path: Path, decision: str):
    # Given
    snapshot, rubric, sources, receipt, review = setup_review(tmp_path)
    review = review.model_copy(update={"decision": decision})
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    next(iter(sources.values())).path.write_bytes(b"changed original")
    # When / Then
    with pytest.raises(SourceFactError, match="SOURCE_PROOF_REJECTED"):
        resolver.resolve_review(REQUEST, receipt, subject=SYSTEM)


@pytest.mark.parametrize(
    "changes",
    [
        {"quote": "A is owned."},
        {"start": 1},
        {"end": 2},
        {"page": 2},
        {"page_text_sha256": "0" * 64},
        {"chunk_id": "other-chunk"},
        {"source_id": "other-source"},
    ],
)
def test_source_integrity_not_established_by_accepted_record(tmp_path: Path, changes):
    # Given: even a trusted semantic record cannot override byte/quote integrity.
    snapshot, rubric, sources, receipt, review = setup_review(tmp_path)
    review = review.model_copy(update={"spans": (replace(review.spans[0], **changes),)})
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    if "source_id" in changes:
        assert resolver.resolve_review(REQUEST, receipt, subject=SYSTEM) is None
    else:
        with pytest.raises(SourceFactError, match="SOURCE_SPAN_REJECTED"):
            resolver.resolve_review(REQUEST, receipt, subject=SYSTEM)


@pytest.mark.parametrize(
    "changes",
    [
        {"candidate_id": "other"},
        {"as_of": "2020-01-01"},
        {"snapshot_sha256": "0" * 64},
        {"rubric_sha256": "0" * 64},
        {"decision": "approved"},
        {"decision": True},
    ],
)
def test_forged_review_context_is_rejected(tmp_path: Path, changes):
    # Given
    snapshot, rubric, sources, _, review = setup_review(tmp_path)
    review = review.model_copy(update=changes)
    # When / Then
    with pytest.raises((SourceFactError, ValidationError)):
        SourceBoundReviewResolver(snapshot, rubric, sources=sources, reviews=(review,))


@pytest.mark.parametrize(
    "changes",
    [
        {"rating": 4},
        {"rating": True},
        {"review_reference": "other"},
        {"evidence_ids": ("ev-synthetic-a-4",)},
        {"snapshot_sha256": "0" * 64},
        {"artifact_sha256": "0" * 64},
    ],
)
def test_review_does_not_transfer_when_receipt_changes(tmp_path: Path, changes):
    # Given
    snapshot, rubric, sources, receipt, review = setup_review(tmp_path)
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When
    result = resolver.resolve_review(
        REQUEST, replace(receipt, **changes), subject=SYSTEM
    )
    # Then
    assert result is None


@pytest.mark.parametrize("changes", [{"anchor_facts_reviewed": "approved"}])
def test_truthy_receipt_values_are_rejected(tmp_path: Path, changes):
    # Given
    snapshot, rubric, sources, receipt, review = setup_review(tmp_path)
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    with pytest.raises(SourceFactError, match="REVIEW_RECEIPT_REJECTED"):
        resolver.resolve_review(REQUEST, replace(receipt, **changes), subject=SYSTEM)


@pytest.mark.parametrize("change", ["request", "subject"])
def test_review_binds_exact_request_identity(tmp_path: Path, change: str):
    # Given
    snapshot, rubric, sources, receipt, review = setup_review(tmp_path)
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    if change == "request":
        assert resolver.resolve_review(REQUEST + b" ", receipt, subject=SYSTEM) is None
    else:
        with pytest.raises(SourceFactError, match="REVIEW_BINDING_REJECTED"):
            resolver.resolve_review(REQUEST, receipt, subject="Other system")


@pytest.mark.parametrize("change", ["snapshot", "rubric"])
def test_graph_admission_rejected_when_frozen_context_changes(
    tmp_path: Path, change: str
):
    # Given
    snapshot, rubric, sources, _, review = setup_review(tmp_path)
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    if change == "snapshot":
        snapshot.evidence["ev-fixture-eligible-moat-ip"].claim = "Changed non-target"
    else:
        rubric["status"] = "changed"
    # When / Then
    with pytest.raises(SourceFactError, match="REVIEW_BINDING_REJECTED"):
        resolver.verify_snapshot(snapshot, rubric)


def test_captured_configuration_survives_caller_mutation(tmp_path: Path):
    # Given
    snapshot, rubric, sources, receipt, review = setup_review(tmp_path)
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    sources.clear()
    snapshot.evidence["ev-synthetic-a-3"].excerpt = "Changed alias"
    object.__setattr__(review.spans[0], "quote", "Changed review alias")
    # When
    result = resolver.resolve_review(REQUEST, receipt, subject=SYSTEM)
    # Then
    assert result is not None and result.spans[0].quote == LINES[3]


def test_existing_validator_can_resolve_exact_independent_receipt(tmp_path: Path):
    from skala_rag.agents.technology_verification import validate_technology_anchor

    # Given: synthetic independent review, not an always-True callback.
    snapshot, rubric, sources, receipt, review = setup_review(tmp_path)
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    criterion = CriterionAssessment(
        schema_version=snapshot.schema_version,
        criterion_id=receipt.criterion_id,
        status="observed",
        rating=receipt.rating,
        evidence_ids=list(receipt.evidence_ids),
        rationale="Synthetic reviewed anchor",
    )

    def verifier(candidate: ReviewedTechnologyAnchor) -> bool:
        resolved = resolver.resolve_review(REQUEST, candidate, subject=SYSTEM)
        return resolved is not None and resolved.decision == "accepted"

    # When
    result = validate_technology_anchor(
        receipt,
        rubric=rubric,
        snapshot=snapshot,
        criterion=criterion,
        verifier=verifier,
    )
    # Then
    assert result is True


@pytest.mark.parametrize(
    "value", [None, StageObservation(None, "unknown", "estimated")]
)
@pytest.mark.parametrize("decision", ["accepted", "unresolved"])
def test_unknown_observation_is_not_promoted_by_source_capture(
    tmp_path: Path, value, decision: str
):
    # Given
    snapshot, rubric, sources, _, review = setup_review(tmp_path)
    observation = FieldObservation(
        field="stage" if value is not None else "identity",
        value=value,
        source_id=review.spans[0].source_id,
        locator="page=1",
        claim="Unresolved",
        excerpt=LINES[3],
        identity_basis="official_domain",
    )
    unresolved = review.model_copy(
        update={
            "receipt_sha256": review_receipt_digest(observation),
            "decision": decision,
        }
    )
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(unresolved,)
    )
    # When
    result = resolver.resolve_review(REQUEST, observation, subject=SYSTEM)
    # Then
    if decision == "accepted":
        assert result is None
    else:
        assert result is not None and result.decision == decision


def capture_review(tmp_path: Path, *, format="html", chunked=False, industry=False):
    """Real local originals; all semantic decisions are SYNTHETIC controls."""
    snapshot, rubric, _, _, old_review = setup_review(tmp_path)
    quote = "Warehouse robots & software."
    text = f"Captured company\n{quote}\n"
    raw = (
        b"<h1>Captured company</h1><script>not retained</script>"
        b"<p>Warehouse   robots &amp; software.</p>"
        if format == "html"
        else text.encode()
    )
    local = tmp_path / "capture"
    (local / "raw").mkdir(parents=True)
    (local / "text").mkdir()
    path = local / "raw/company.html"
    extracted = local / "text/company.txt"
    path.write_bytes(raw)
    extracted.write_text(text, encoding="utf-8")
    source = next(iter(snapshot.sources.values())).model_copy(
        update={
            "source_id": "src-local-capture",
            "url": "https://example.com/company",
            "local_path": None,
            "content_hash": content_hash(raw),
            "bibliographic_metadata": {
                "original_receipt": {
                    "source_id": "src-local-capture",
                    "raw_path": "raw/company.html",
                    "extracted_path": "text/company.txt",
                    "raw_sha256": content_hash(raw).removeprefix("sha256:"),
                    "extracted_sha256": content_hash(text.encode()).removeprefix(
                        "sha256:"
                    ),
                    "charset": "utf-8",
                }
            },
        }
    )
    chunk = next(iter(snapshot.chunks.values())).model_copy(
        update={
            "chunk_id": "chunk-capture",
            "source_id": source.source_id,
            "text": text,
            "page_start": None,
            "page_end": None,
            "candidate_ids": [snapshot.candidate_id],
        }
    )
    record = next(iter(snapshot.retrieval_records.values()))
    evidence = snapshot.evidence["ev-synthetic-a-3"].model_copy(
        update={
            "evidence_id": "ev-capture",
            "source_id": source.source_id,
            "locator": "https://example.com/company#retained-text",
            "excerpt": quote,
            "criterion_ids": ["market.size"],
            "scope": "industry" if industry else "company",
            "candidate_id": None if industry else snapshot.candidate_id,
            "provenance": [
                p.model_copy(
                    update={
                        "method": "web",
                        "chunk_id": chunk.chunk_id if chunked else None,
                    }
                )
                for p in snapshot.evidence["ev-synthetic-a-3"].provenance
            ],
        }
    )
    record = record.model_copy(
        update={
            "source_ids": [*record.source_ids, source.source_id],
            "chunk_ids": [*record.chunk_ids, *([chunk.chunk_id] if chunked else [])],
            "evidence_ids": [*record.evidence_ids, evidence.evidence_id],
        }
    )
    snapshot = snapshot.model_copy(
        update={
            "sources": {**snapshot.sources, source.source_id: source},
            "chunks": {
                **snapshot.chunks,
                **({chunk.chunk_id: chunk} if chunked else {}),
            },
            "evidence": {**snapshot.evidence, evidence.evidence_id: evidence},
            "evidence_ids": [*snapshot.evidence_ids, evidence.evidence_id],
            "retrieval_records": {record.retrieval_id: record},
        }
    )
    trusted = TrustedCapture(
        path=path,
        allowed_root=local,
        extracted_path=extracted,
        format=format,
        charset="utf-8",
        source=source,
        corpus_version=snapshot.corpus_version,
        extracted_text=text,
        extracted_sha256=content_hash(text.encode()),
        approved_chunks=(chunk,) if chunked else (),
    )
    receipt = CriterionAssessment(
        schema_version=snapshot.schema_version,
        criterion_id="market.size",
        status="observed",
        rating=3,
        evidence_ids=[evidence.evidence_id],
        rationale="SYNTHETIC independent Market decision, not market truth.",
    )
    request = (
        b'{"target":{"segment_id":"warehouse","geographies":["global"]},'
        b'"links":{"ev-capture":{"segment_id":"warehouse","metric":"tam"}},'
        b'"criterion":"market.size","excerpt":"Warehouse robots & software."}'
    )
    start = text.index(quote)
    review = old_review.model_copy(
        update={
            "request_sha256": content_hash(request).removeprefix("sha256:"),
            "receipt_sha256": review_receipt_digest(receipt),
            "snapshot_sha256": frozen_snapshot_digest(snapshot),
            "spans": (
                SourceTextSpan(
                    source.source_id,
                    start,
                    start + len(quote),
                    quote,
                    text_hash(text),
                    chunk.chunk_id if chunked else None,
                ),
            ),
        }
    )
    return snapshot, rubric, {source.source_id: trusted}, receipt, review, request


@pytest.mark.parametrize("format", ["html", "text"])
@pytest.mark.parametrize("decision", ["accepted", "rejected", "unresolved"])
@pytest.mark.parametrize("chunked", [False, True])
def test_local_capture_preserves_synthetic_decision(
    tmp_path, format, decision, chunked
):
    # Given: original files, exact extraction, and a synthetic Market receipt.
    snapshot, rubric, sources, receipt, review, request = capture_review(
        tmp_path, format=format, chunked=chunked
    )
    review = review.model_copy(update={"decision": decision})
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When
    result = resolver.resolve_review(request, receipt, subject=SYSTEM)
    # Then
    assert result == review


@pytest.mark.parametrize("reviews", [False, True])
@pytest.mark.parametrize("format", ["html", "text", "pdf"])
def test_snapshot_admission_rechecks_original_without_semantic_review(
    tmp_path, reviews, format
):
    # Given
    if format == "pdf":
        snapshot, rubric, sources, _, review = setup_review(tmp_path)
    else:
        snapshot, rubric, sources, _, review, _ = capture_review(
            tmp_path, format=format
        )
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,) if reviews else ()
    )
    next(iter(sources.values())).path.write_bytes(b"tampered")
    # When / Then
    with pytest.raises(SourceFactError, match="SOURCE_PROOF_REJECTED"):
        resolver.verify_snapshot(snapshot, rubric)


@pytest.mark.parametrize("change", ["excerpt", "hash", "start", "end", "chunk"])
def test_capture_span_tamper_is_rejected(tmp_path, change):
    # Given
    snapshot, rubric, sources, receipt, review, request = capture_review(tmp_path)
    changes = {
        "excerpt": {"quote": "not captured"},
        "hash": {"text_sha256": "0" * 64},
        "start": {"start": 0},
        "end": {"end": 1},
        "chunk": {"chunk_id": "unknown"},
    }
    review = review.model_copy(
        update={"spans": (replace(review.spans[0], **changes[change]),)}
    )
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    with pytest.raises(SourceFactError, match="SOURCE_SPAN_REJECTED"):
        resolver.resolve_review(request, receipt, subject=SYSTEM)


@pytest.mark.parametrize("change", ["retained", "text", "hash", "root", "receipt_path"])
def test_capture_extraction_and_path_tamper_is_rejected(tmp_path, change):
    # Given
    snapshot, rubric, sources, receipt, review, request = capture_review(tmp_path)
    sid, trusted = next(iter(sources.items()))
    if change == "retained":
        trusted.extracted_path.write_text("changed", encoding="utf-8")
    elif change == "receipt_path":
        source = trusted.source.model_copy(deep=True)
        source.bibliographic_metadata["original_receipt"]["raw_path"] = "raw/other.html"
        sources[sid] = replace(trusted, source=source)
    else:
        changes = {
            "text": {"extracted_text": "changed"},
            "hash": {"extracted_sha256": "sha256:" + "0" * 64},
            "root": {"allowed_root": tmp_path / "other"},
        }
        sources[sid] = replace(trusted, **changes[change])
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    with pytest.raises(SourceFactError, match="SOURCE_PROOF_REJECTED"):
        resolver.resolve_review(request, receipt, subject=SYSTEM)


@pytest.mark.parametrize(
    "field", [b"warehouse", b"global", b"tam", b"market.size", b"Warehouse"]
)
def test_market_request_binds_target_links_criterion_and_excerpt(tmp_path, field):
    # Given
    snapshot, rubric, sources, receipt, review, request = capture_review(tmp_path)
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When
    result = resolver.resolve_review(
        request.replace(field, b"changed"), receipt, subject=SYSTEM
    )
    # Then
    assert result is None


@pytest.mark.parametrize(
    "changes",
    [
        {"rating": 4},
        {"criterion_id": "market.growth"},
        {"status": "missing", "rating": None, "missing_reason": "unknown"},
    ],
)
def test_market_receipt_cannot_reuse_old_review(tmp_path, changes):
    # Given
    snapshot, rubric, sources, receipt, review, request = capture_review(tmp_path)
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When
    result = resolver.resolve_review(
        request, receipt.model_copy(update=changes), subject=SYSTEM
    )
    # Then
    assert result is None


@pytest.mark.parametrize("criterion", ["market.size", "market.growth", "market.demand"])
def test_market_industry_evidence_has_exact_source_binding(tmp_path, criterion):
    # Given: SYNTHETIC Market attribution can use industry evidence without a candidate.
    snapshot, rubric, sources, receipt, review, request = capture_review(
        tmp_path, industry=True
    )
    snapshot.evidence["ev-capture"].criterion_ids = [criterion]
    receipt = receipt.model_copy(update={"criterion_id": criterion})
    request = request.replace(b"market.size", criterion.encode())
    review = review.model_copy(
        update={
            "snapshot_sha256": frozen_snapshot_digest(snapshot),
            "receipt_sha256": review_receipt_digest(receipt),
            "request_sha256": content_hash(request).removeprefix("sha256:"),
        }
    )
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When
    result = resolver.resolve_review(request, receipt, subject=SYSTEM)
    # Then
    assert result == review


def test_capture_unreviewed_sources_do_not_require_positive_semantics(tmp_path):
    # Given
    snapshot, rubric, sources, receipt, _, request = capture_review(tmp_path)
    resolver = SourceBoundReviewResolver(snapshot, rubric, sources=sources, reviews=())
    resolver.verify_snapshot(snapshot, rubric)
    # When
    result = resolver.resolve_review(request, receipt, subject=SYSTEM)
    # Then
    assert result is None


def test_capture_forged_retained_text_fails_even_with_matching_hash(tmp_path):
    # Given: retained bytes and supplied hash agree, but HTML does not derive them.
    snapshot, rubric, sources, receipt, review, request = capture_review(tmp_path)
    sid, trusted = next(iter(sources.items()))
    forged = "Invented market evidence.\n"
    trusted.extracted_path.write_text(forged, encoding="utf-8")
    sources[sid] = replace(
        trusted,
        extracted_text=forged,
        extracted_sha256=content_hash(forged.encode()),
    )
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    with pytest.raises(SourceFactError, match="SOURCE_PROOF_REJECTED"):
        resolver.resolve_review(request, receipt, subject=SYSTEM)


@pytest.mark.parametrize("change", ["source", "chunk", "excerpt", "criterion"])
def test_capture_binds_actual_frozen_dtos(tmp_path, change):
    # Given: a separately supplied SYNTHETIC decision over altered snapshot DTOs.
    snapshot, rubric, sources, receipt, review, request = capture_review(
        tmp_path, chunked=True
    )
    snapshot = snapshot.model_copy(deep=True)
    if change == "source":
        snapshot.sources["src-local-capture"].title = "Changed source title"
    elif change == "chunk":
        snapshot.chunks["chunk-capture"].locator = "Changed locator"
    elif change == "excerpt":
        snapshot.evidence["ev-capture"].excerpt = "Captured company"
    else:
        snapshot.evidence["ev-capture"].criterion_ids = ["market.growth"]
    review = review.model_copy(
        update={"snapshot_sha256": frozen_snapshot_digest(snapshot)}
    )
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    error = (
        "SNAPSHOT_SOURCE_REJECTED"
        if change in ("source", "chunk")
        else "EVIDENCE_BINDING_REJECTED"
    )
    with pytest.raises(SourceFactError, match=error):
        resolver.resolve_review(request, receipt, subject=SYSTEM)


def test_capture_review_does_not_rebind_new_snapshot(tmp_path):
    # Given: a review supplied for one stage/candidate snapshot.
    snapshot, rubric, sources, _, review, _ = capture_review(tmp_path)
    snapshot = snapshot.model_copy(
        update={"evidence_revision": snapshot.evidence_revision + 1}
    )
    # When / Then
    with pytest.raises(SourceFactError, match="REVIEW_BINDING_REJECTED"):
        SourceBoundReviewResolver(snapshot, rubric, sources=sources, reviews=(review,))


def test_market_receipt_type_is_part_of_digest(tmp_path):
    # Given: identical field payloads do not confer the registered receipt type.
    snapshot, rubric, sources, receipt, review, request = capture_review(tmp_path)

    class OtherAssessment(CriterionAssessment):
        pass

    other = OtherAssessment.model_validate(receipt.model_dump())
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    with pytest.raises(SourceFactError, match="REVIEW_RECEIPT_REJECTED"):
        resolver.resolve_review(request, other, subject=SYSTEM)


@pytest.mark.parametrize(
    "rules",
    [
        None,
        {},
        {"industry_evidence_dimensions": []},
        {"industry_evidence_dimensions": ["founder"]},
        {"industry_evidence_dimensions": "market"},
        {"industry_evidence_dimensions": ["market", True]},
    ],
)
def test_market_industry_permission_is_never_defaulted(tmp_path, rules):
    # Given: SYNTHETIC review explicitly pinned to a rubric without valid permission.
    snapshot, rubric, sources, receipt, review, request = capture_review(
        tmp_path, industry=True
    )
    if rules is None:
        rubric.pop("common_rules")
    else:
        rubric["common_rules"] = rules
    review = review.model_copy(update={"rubric_sha256": _digest(rubric)})
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    with pytest.raises(SourceFactError, match="EVIDENCE_BINDING_REJECTED"):
        resolver.resolve_review(request, receipt, subject=SYSTEM)


def test_market_industry_requires_approved_rubric(tmp_path):
    # Given: permission exists, but the exactly pinned rubric is only proposed.
    snapshot, rubric, sources, receipt, review, request = capture_review(
        tmp_path, industry=True
    )
    rubric["status"] = "proposed"
    review = review.model_copy(update={"rubric_sha256": _digest(rubric)})
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    with pytest.raises(SourceFactError, match="EVIDENCE_BINDING_REJECTED"):
        resolver.resolve_review(request, receipt, subject=SYSTEM)


@pytest.mark.parametrize(
    "changes",
    [
        {"target": None},
        {"target": {}},
        {"links": None},
        {"links": {}},
        {"links": {"ev-capture": {"segment_id": "different"}}},
        {"criterion": "market.growth"},
    ],
)
def test_market_industry_requires_reviewed_target_and_links(tmp_path, changes):
    # Given: even a new SYNTHETIC independent record must bind target and links.
    snapshot, rubric, sources, receipt, review, request = capture_review(
        tmp_path, industry=True
    )
    context = json.loads(request)
    context.update(changes)
    request = json.dumps(context).encode()
    review = review.model_copy(
        update={"request_sha256": content_hash(request).removeprefix("sha256:")}
    )
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    with pytest.raises(SourceFactError, match="EVIDENCE_BINDING_REJECTED"):
        resolver.resolve_review(request, receipt, subject=SYSTEM)


@pytest.mark.parametrize("scope", ["company", "industry"])
def test_market_rejects_other_company_attribution(tmp_path, scope):
    # Given: permission cannot authorize a foreign candidate, even on industry data.
    snapshot, rubric, sources, receipt, review, request = capture_review(tmp_path)
    snapshot.evidence["ev-capture"].scope = scope
    snapshot.evidence["ev-capture"].candidate_id = "other-company"
    review = review.model_copy(
        update={"snapshot_sha256": frozen_snapshot_digest(snapshot)}
    )
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    with pytest.raises(SourceFactError, match="EVIDENCE_BINDING_REJECTED"):
        resolver.resolve_review(request, receipt, subject=SYSTEM)


@pytest.mark.parametrize("domain", ["founder", "moat", "finance", "technology"])
def test_market_permission_never_authorizes_other_domain_industry(
    tmp_path, domain: Literal["founder", "moat", "finance", "technology"]
):
    # Given: SYNTHETIC non-Market receipts over exactly captured industry evidence.
    snapshot, rubric, sources, _, review, request = capture_review(
        tmp_path, industry=True
    )
    common = {
        "review_reference": "synthetic-domain-review",
        "artifact_sha256": core_artifact_digest(rubric),
        "snapshot_sha256": frozen_snapshot_digest(snapshot),
        "criterion_id": f"{domain}.synthetic",
        "rating": 3,
        "evidence_ids": ("ev-capture",),
    }
    match domain:
        case "founder":
            receipt = ReviewedFounderAnchor(
                **common,
                founder_person_ids=("synthetic-person",),
                person_by_evidence_id=(("ev-capture", "synthetic-person"),),
                anchor_facts_reviewed=True,
                minimum_evidence_reviewed=True,
                person_identity_reviewed=True,
                employment_identity_reviewed=True,
                independent_corroboration_reviewed=True,
            )
        case "moat":
            receipt = ReviewedMoatAnchor(**common)
        case "technology":
            receipt = ReviewedTechnologyAnchor(
                **common,
                anchor_facts_reviewed=True,
                minimum_evidence_reviewed=True,
                direct_negative_facts_reviewed=True,
                independent_corroboration_reviewed=True,
            )
        case "finance":
            receipt = ReviewedFinancialFact(
                run_id=snapshot.run_id,
                snapshot_id=snapshot.snapshot_id,
                candidate_id=snapshot.candidate_id,
                evaluation_round=snapshot.evaluation_round,
                evidence_revision=snapshot.evidence_revision,
                policy_version=snapshot.policy_version,
                reviewer_reference="synthetic-domain-review",
                accounting_entity=snapshot.candidate_id,
                metric_role="revenue",
                funding_round=None,
                valuation_basis=None,
                period=None,
                evidence=snapshot.evidence["ev-capture"],
            )
        case unreachable:
            assert_never(unreachable)
    review = review.model_copy(
        update={"receipt_sha256": review_receipt_digest(receipt)}
    )
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    with pytest.raises(SourceFactError, match="EVIDENCE_BINDING_REJECTED"):
        resolver.resolve_review(request, receipt, subject=SYSTEM)


def observation_review(tmp_path, *, field="domain_match", value=True):
    """SYNTHETIC reviewer conclusions over real local HTML, not source inference."""
    snapshot, rubric, sources, _, review, _ = capture_review(tmp_path)
    source = sources["src-local-capture"].source
    observation = FieldObservation(
        field=field,
        value=value,
        source_id=source.source_id,
        locator=source.url,
        claim="Synthetic independent observation conclusion.",
        excerpt=review.spans[0].quote,
        identity_basis="official_domain",
        event_date=snapshot.as_of,
    )
    context = CompanyObservationReviewRequest(
        kind="company-research-observation",
        field=field,
        candidate_id=snapshot.candidate_id,
        as_of=snapshot.as_of,
        subject=SYSTEM,
        observation=observation,
        source=source,
        span=review.spans[0],
    )
    request = context.model_dump_json().encode()
    review = review.model_copy(
        update={
            "request_sha256": content_hash(request).removeprefix("sha256:"),
            "receipt_sha256": review_receipt_digest(observation),
        }
    )
    return snapshot, rubric, sources, observation, review, request


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("domain_match", True),
        ("domain_match", False),
        ("is_listed", True),
        ("is_listed", False),
        ("exit_completed", True),
        ("exit_completed", False),
        ("stage", StageObservation("Completed Series B", "series_b", "explicit")),
        ("identity", None),
        ("business", None),
    ],
)
def test_structured_observation_preserves_reviewed_value(tmp_path, field, value):
    # Given: values are reviewer conclusions, not keywords in the generic quote.
    snapshot, rubric, sources, observation, review, request = observation_review(
        tmp_path, field=field, value=value
    )
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When
    result = resolver.resolve_review(request, observation, subject=SYSTEM)
    # Then
    assert result == review
    assert observation.value == value


@pytest.mark.parametrize("decision", ["rejected", "unresolved"])
def test_negative_review_never_admits_observation(tmp_path, decision):
    # Given
    snapshot, rubric, sources, observation, review, request = observation_review(
        tmp_path, value=False
    )
    review = review.model_copy(update={"decision": decision})
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When
    result = resolver.resolve_review(request, observation, subject=SYSTEM)
    # Then: the false observation itself has not been approved.
    assert result is not None and result.decision == decision
    assert result.decision != "accepted"


@pytest.mark.parametrize(
    "change",
    [
        "field",
        "candidate",
        "asof",
        "subject",
        "kind",
        "extra",
        "missing",
        "value",
        "claim",
        "source",
        "quote",
        "offset",
    ],
)
def test_new_review_cannot_authorize_malformed_observation_context(tmp_path, change):
    # Given: an independently supplied record cannot override technical binding.
    snapshot, rubric, sources, observation, review, request = observation_review(
        tmp_path
    )
    context = json.loads(request)
    if change == "field":
        context["field"] = "is_listed"
    elif change == "candidate":
        context["candidate_id"] = "another-company"
    elif change == "asof":
        context["as_of"] = "2020-01-01"
    elif change == "subject":
        context["subject"] = "another-subject"
    elif change == "kind":
        context["kind"] = "generic-extraction"
    elif change == "extra":
        context["approved"] = True
    elif change == "missing":
        del context["observation"]
    elif change == "value":
        context["observation"]["value"] = False
    elif change == "claim":
        context["observation"]["claim"] = "another conclusion"
    elif change == "source":
        context["source"]["title"] = "another original"
    elif change == "quote":
        context["span"]["quote"] = "another quote"
    else:
        context["span"]["start"] = 0
    request = json.dumps(context).encode()
    review = review.model_copy(
        update={"request_sha256": content_hash(request).removeprefix("sha256:")}
    )
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    if change in ("kind", "extra", "missing"):
        assert resolver.resolve_review(request, observation, subject=SYSTEM) is None
    else:
        with pytest.raises(SourceFactError, match="EVIDENCE_BINDING_REJECTED"):
            resolver.resolve_review(request, observation, subject=SYSTEM)


@pytest.mark.parametrize(
    "change", ["opaque", "unbound", "unknown-stage", "unknown-field"]
)
def test_unbound_or_unknown_observation_is_never_positive(tmp_path, change):
    # Given
    snapshot, rubric, sources, observation, review, request = observation_review(
        tmp_path
    )
    if change == "opaque":
        request = REQUEST
    elif change == "unbound":
        request += b" "
    elif change == "unknown-stage":
        observation = replace(
            observation,
            field="stage",
            value=StageObservation(None, "unknown", "estimated"),
        )
    else:
        object.__setattr__(observation, "field", "unsupported")
    review = review.model_copy(
        update={
            "request_sha256": content_hash(
                request.rstrip() if change == "unbound" else request
            ).removeprefix("sha256:"),
            "receipt_sha256": review_receipt_digest(observation),
        }
    )
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    if change == "unknown-field":
        with pytest.raises(SourceFactError, match="EVIDENCE_BINDING_REJECTED"):
            resolver.resolve_review(request, observation, subject=SYSTEM)
    else:
        assert resolver.resolve_review(request, observation, subject=SYSTEM) is None


@pytest.mark.parametrize("change", ["original", "conflict", "future-event"])
def test_reviewed_observation_rechecks_original_and_conflicts(tmp_path, change):
    # Given
    snapshot, rubric, sources, observation, review, request = observation_review(
        tmp_path
    )
    if change == "original":
        sources["src-local-capture"].path.write_bytes(b"tampered")
    elif change == "conflict":
        snapshot.evidence["ev-capture"].conflicts_with = ["ev-synthetic-a-3"]
        review = review.model_copy(
            update={"snapshot_sha256": frozen_snapshot_digest(snapshot)}
        )
    else:
        from datetime import timedelta

        observation = replace(
            observation, event_date=snapshot.as_of + timedelta(days=1)
        )
        context = json.loads(request)
        context["observation"]["event_date"] = observation.event_date.isoformat()
        request = json.dumps(context).encode()
        review = review.model_copy(
            update={
                "request_sha256": content_hash(request).removeprefix("sha256:"),
                "receipt_sha256": review_receipt_digest(observation),
            }
        )
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    error = (
        "SOURCE_PROOF_REJECTED" if change == "original" else "EVIDENCE_BINDING_REJECTED"
    )
    with pytest.raises(SourceFactError, match=error):
        resolver.resolve_review(request, observation, subject=SYSTEM)


@pytest.mark.parametrize("value", [True, False])
def test_live_research_consumes_independently_verified_boolean(tmp_path, value):
    from skala_rag.contracts import Candidate
    from skala_rag.contracts.tools import ToolBudget
    from skala_rag.tools.company_research import (
        LiveResearchCompany,
        ProviderCall,
        ProviderOutcome,
    )

    # Given: no network; a local provider gates observations before the assembler.
    snapshot, rubric, sources, observation, review, request = observation_review(
        tmp_path, value=value
    )
    source = sources["src-local-capture"].source
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    candidate = Candidate(
        schema_version=snapshot.schema_version,
        candidate_id=snapshot.candidate_id,
        canonical_name="Synthetic company",
        aliases=[],
        country="US",
        homepage_url=source.url,
        legal_identifiers={},
        discovery_source_ids=[source.source_id],
    )

    class LocalClock:
        def now(self):
            return source.retrieved_at

    class ReviewedLocalProvider:
        name = "synthetic-independent-review"
        required = True

        def __call__(self, candidate, calls):
            assert candidate.candidate_id == snapshot.candidate_id
            assert calls.take()
            decision = resolver.resolve_review(request, observation, subject=SYSTEM)
            return ProviderOutcome(
                status="ok",
                sources=(source,),
                observations=(
                    (observation,)
                    if decision is not None and decision.decision == "accepted"
                    else ()
                ),
                calls=(
                    ProviderCall(
                        "ok",
                        "web",
                        None,
                        {},
                        source.retrieved_at,
                        source.retrieved_at,
                        (source.source_id,),
                    ),
                ),
            )

    research = LiveResearchCompany(
        [ReviewedLocalProvider()],
        run_id=snapshot.run_id,
        schema_version=snapshot.schema_version,
        as_of=snapshot.as_of,
        clock=LocalClock(),
    )
    # When: use the unchanged existing LiveResearchCompany entrypoint.
    result = research(
        candidate,
        ToolBudget(
            schema_version=snapshot.schema_version,
            max_calls=1,
            max_retries=0,
            timeout_seconds=30,
        ),
    )
    # Then: the typed value is consumed faithfully, and unknown peers stay unknown.
    assert result.data is not None
    assert result.data.profile.domain_match is value
    assert result.data.profile.is_listed is None
    assert result.data.profile.exit_completed is None
    assert result.data.profile.stage.normalized_round == "unknown"


@pytest.mark.parametrize("change", ["opaque", "excerpt", "target", "links"])
def test_market_company_receipt_requires_bound_context(tmp_path, change):
    # Given: a fresh SYNTHETIC review still must contain relevant request context.
    snapshot, rubric, sources, receipt, review, request = capture_review(tmp_path)
    context = json.loads(request)
    if change == "opaque":
        request = REQUEST
    else:
        context[change] = "unrelated"
        request = json.dumps(context).encode()
    review = review.model_copy(
        update={"request_sha256": content_hash(request).removeprefix("sha256:")}
    )
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    with pytest.raises(SourceFactError, match="EVIDENCE_BINDING_REJECTED"):
        resolver.resolve_review(request, receipt, subject=SYSTEM)


@pytest.mark.parametrize("value", [None, 0, "false"])
def test_malformed_observation_value_is_not_coerced(tmp_path, value):
    # Given: a SYNTHETIC record cannot authorize a malformed typed observation.
    snapshot, rubric, sources, observation, review, request = observation_review(
        tmp_path, value=False
    )
    context = json.loads(request)
    context["observation"]["value"] = value
    request = json.dumps(context).encode()
    review = review.model_copy(
        update={"request_sha256": content_hash(request).removeprefix("sha256:")}
    )
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When / Then
    assert resolver.resolve_review(request, observation, subject=SYSTEM) is None


def test_market_request_supports_exact_multiple_excerpt_map(tmp_path):
    # Given: source-excerpt maps avoid ambiguously reusing one quote for many claims.
    snapshot, rubric, sources, receipt, review, request = capture_review(tmp_path)
    context = json.loads(request)
    context["excerpts"] = {"ev-capture": context.pop("excerpt")}
    request = json.dumps(context).encode()
    review = review.model_copy(
        update={"request_sha256": content_hash(request).removeprefix("sha256:")}
    )
    resolver = SourceBoundReviewResolver(
        snapshot, rubric, sources=sources, reviews=(review,)
    )
    # When
    result = resolver.resolve_review(request, receipt, subject=SYSTEM)
    # Then
    assert result == review
