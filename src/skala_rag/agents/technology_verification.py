"""Technology review claims, not production semantic authority or retrieval."""

import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace

from pydantic import BaseModel

from skala_rag.agents.evaluation import DimensionAssessmentOutput
from skala_rag.agents.moat_verification import (
    core_artifact_digest,
    frozen_snapshot_digest,
)
from skala_rag.contracts.assessment import CriterionAssessment
from skala_rag.contracts.evaluation import EvaluationSnapshot

CRITERIA = frozenset(
    {
        "technology.maturity",
        "technology.reliability",
        "technology.integration",
        "technology.commercialization",
    }
)


class TechnologyReviewError(ValueError):
    """Controlled technical review failure; never Missing or an investment score."""


@dataclass(frozen=True)
class ReviewedTechnologyAnchor:
    """Untrusted assertion until authenticated by an external controller.

    All four reviewed flags mean the reviewer checked the applicable approved
    requirements, not that all observations contain negative facts or independent
    evidence. Anchor correspondence includes TRL events, environment/duration/
    results, owned component integration and commercial contract/PoC facts as
    applicable. No keyword or publisher-name inference supplies these facts.
    """

    review_reference: str
    artifact_sha256: str
    snapshot_sha256: str
    criterion_id: str
    rating: int
    evidence_ids: tuple[str, ...]
    anchor_facts_reviewed: bool
    minimum_evidence_reviewed: bool
    direct_negative_facts_reviewed: bool
    independent_corroboration_reviewed: bool


def _exact_tree(original: object, validated: object, schema_version: str) -> None:
    """Reject duck models, subclasses and model_copy unknown-field bypasses."""
    if type(original) is not type(validated):
        raise ValueError("Invalid frozen field type")
    if isinstance(original, BaseModel):
        fields = type(original).model_fields
        if (
            "schema_version" in fields
            and getattr(original, "schema_version") != schema_version
        ):
            raise ValueError("Mixed frozen schema")
        if set(original.__dict__) - set(fields) or original.model_extra:
            raise ValueError("Unknown frozen fields")
        for name in fields:
            _exact_tree(
                getattr(original, name), getattr(validated, name), schema_version
            )
    elif isinstance(original, dict):
        assert isinstance(validated, dict)
        for key in original:
            _exact_tree(original[key], validated[key], schema_version)
    elif isinstance(original, (list, tuple)):
        assert isinstance(validated, (list, tuple))
        for a, b in zip(original, validated, strict=True):
            _exact_tree(a, b, schema_version)


def checked_snapshot(snapshot: EvaluationSnapshot) -> EvaluationSnapshot:
    """Detach and check full Source–Chunk–Record attribution, not byte truth."""
    if type(snapshot) is not EvaluationSnapshot:
        raise ValueError("Exact frozen snapshot required")
    validated = EvaluationSnapshot.model_validate(
        snapshot.model_dump(), context={"execution_mode": "fixture"}
    )
    _exact_tree(snapshot, validated, validated.schema_version)
    for record in validated.retrieval_records.values():
        if (
            record.run_id != validated.run_id
            or record.candidate_id not in (None, validated.candidate_id)
            or not set(record.source_ids) <= set(validated.sources)
            or not set(record.chunk_ids) <= set(validated.chunks)
            or not set(record.evidence_ids) <= set(validated.evidence)
            or any(
                validated.chunks[cid].source_id not in record.source_ids
                for cid in record.chunk_ids
            )
            or any(
                validated.evidence[eid].source_id not in record.source_ids
                for eid in record.evidence_ids
            )
        ):
            raise ValueError("Invalid full frozen retrieval closure")
    for chunk in validated.chunks.values():
        if chunk.source_id not in validated.sources or (
            chunk.corpus_version != validated.corpus_version
        ):
            raise ValueError("Invalid frozen chunk source")
    for evidence in validated.evidence.values():
        if evidence.source_id not in validated.sources or not evidence.provenance:
            raise ValueError("Invalid frozen evidence source")
        for path in evidence.provenance:
            record = validated.retrieval_records.get(path.retrieval_id)
            if (
                record is None
                or record.status != "ok"
                or record.run_id != validated.run_id
                or record.candidate_id not in (None, validated.candidate_id)
                or evidence.evidence_id not in record.evidence_ids
                or evidence.source_id not in record.source_ids
            ):
                raise ValueError("Invalid frozen retrieval attribution")
            for field, expected in (
                ("corpus_version", validated.corpus_version),
                ("index_version", validated.index_version),
                ("as_of", validated.as_of.isoformat()),
            ):
                if (
                    field in record.arguments_without_secrets
                    and record.arguments_without_secrets[field] != expected
                ):
                    raise ValueError("Stale frozen retrieval identity")
            if path.method == "rag" and path.chunk_id is None:
                raise ValueError("RAG chunk required")
            if path.chunk_id is not None:
                chunk = validated.chunks.get(path.chunk_id)
                if (
                    chunk is None
                    or path.chunk_id not in record.chunk_ids
                    or chunk.source_id != evidence.source_id
                    or validated.candidate_id not in chunk.candidate_ids
                    or evidence.excerpt not in chunk.text
                ):
                    raise ValueError("Invalid frozen chunk attribution")
    return validated


def checked_fixture_output(
    output: DimensionAssessmentOutput,
) -> DimensionAssessmentOutput:
    """Revalidate instance payloads that FakeLLM may otherwise pass through."""
    if type(output) is not DimensionAssessmentOutput:
        raise ValueError("Invalid Technology fixture output type")
    validated = DimensionAssessmentOutput.model_validate(output.model_dump())
    _exact_tree(output, validated, "")
    return validated


def validate_technology_anchor(
    receipt: object,
    *,
    rubric: Mapping[str, object],
    snapshot: EvaluationSnapshot,
    criterion: CriterionAssessment,
    verifier: Callable[[ReviewedTechnologyAnchor], bool],
) -> bool:
    """Bind exact typed claims to original whole snapshot, then resolve authority."""
    if type(receipt) is not ReviewedTechnologyAnchor:
        return False
    if type(criterion) is not CriterionAssessment:
        return False
    if (
        type(receipt.review_reference) is not str
        or not receipt.review_reference.strip()
        or type(receipt.criterion_id) is not str
        or receipt.criterion_id not in CRITERIA
        or receipt.criterion_id != criterion.criterion_id
        or type(receipt.rating) is not int
        or receipt.rating not in range(1, 6)
        or receipt.rating != criterion.rating
        or criterion.status != "observed"
        or type(receipt.artifact_sha256) is not str
        or receipt.artifact_sha256 != core_artifact_digest(rubric)
        or type(receipt.snapshot_sha256) is not str
        or receipt.snapshot_sha256 != frozen_snapshot_digest(snapshot)
        or type(receipt.evidence_ids) is not tuple
        or not receipt.evidence_ids
        or any(type(e) is not str or not e.strip() for e in receipt.evidence_ids)
        or len(set(receipt.evidence_ids)) != len(receipt.evidence_ids)
        or len(set(criterion.evidence_ids)) != len(criterion.evidence_ids)
        or set(receipt.evidence_ids) != set(criterion.evidence_ids)
        or any(
            getattr(receipt, flag) is not True
            for flag in (
                "anchor_facts_reviewed",
                "minimum_evidence_reviewed",
                "direct_negative_facts_reviewed",
                "independent_corroboration_reviewed",
            )
        )
    ):
        return False
    for eid in receipt.evidence_ids:
        evidence = snapshot.evidence.get(eid)
        if (
            evidence is None
            or evidence.scope != "company"
            or evidence.candidate_id != snapshot.candidate_id
            or criterion.criterion_id not in evidence.criterion_ids
        ):
            return False
    return callable(verifier) and verifier(receipt) is True


def _checked_adjudication(adjudication: object, rubric: Mapping[str, object]):
    """Accept only a supported, rated #201 integration adjudication result."""
    from skala_rag.agents.source_fact_verification import IntegrationAdjudication

    def distinct_texts(values: object) -> bool:
        return (
            type(values) is tuple
            and bool(values)
            and all(type(v) is str and bool(v.strip()) for v in values)
            and len(set(values)) == len(values)
        )

    if (
        type(adjudication) is not IntegrationAdjudication
        or type(adjudication.criterion_id) is not str
        or adjudication.criterion_id != "technology.integration"
        or type(adjudication.status) is not str
        or adjudication.status != "supported"
        or type(adjudication.rating) is not int
        or adjudication.rating not in (3, 4, 5)
        or adjudication.minimum_evidence_supported is not True
        or type(adjudication.anchor_text) is not str
        or not adjudication.anchor_text.strip()
        or not distinct_texts(adjudication.evidence_ids)
        or not distinct_texts(adjudication.admitted_fact_ids)
        or type(adjudication.denied) is not tuple
        or any(
            type(d) is not tuple
            or len(d) != 2
            or any(type(v) is not str or not v.strip() for v in d)
            for d in adjudication.denied
        )
        or len({d[0] for d in adjudication.denied}) != len(adjudication.denied)
        or set(adjudication.admitted_fact_ids) & {d[0] for d in adjudication.denied}
        or any(
            type(v) is not str or re.fullmatch(r"[0-9a-f]{64}", v) is None
            for v in (adjudication.snapshot_sha256, adjudication.rubric_sha256)
        )
    ):
        raise TechnologyReviewError("TECHNOLOGY_REVIEW_REJECTED")
    try:
        spec = rubric["dimensions"]["technology"]["criteria"][adjudication.criterion_id]
        anchors = {int(k): v for k, v in spec["anchors"].items()}
        if (
            adjudication.rubric_sha256 != core_artifact_digest(rubric)
            or adjudication.anchor_text != anchors[adjudication.rating]
        ):
            raise ValueError("anchor/rubric mismatch")
    except Exception:
        raise TechnologyReviewError("TECHNOLOGY_REVIEW_REJECTED") from None
    return adjudication


def anchor_from_integration_adjudication(
    adjudication: object, review_reference: str, *, rubric: Mapping[str, object]
) -> ReviewedTechnologyAnchor:
    """Format a coherent result as an untrusted anchor assertion.

    Public result construction is not authority. Only the separate input-bound
    resolver re-establishes the minimum, anchor and independent-source checks.
    """
    checked = _checked_adjudication(adjudication, rubric)
    if type(review_reference) is not str or not review_reference.strip():
        raise TechnologyReviewError("TECHNOLOGY_REVIEW_REJECTED")
    return ReviewedTechnologyAnchor(
        review_reference=review_reference,
        artifact_sha256=checked.rubric_sha256,
        snapshot_sha256=checked.snapshot_sha256,
        criterion_id=checked.criterion_id,
        rating=checked.rating,
        evidence_ids=tuple(checked.evidence_ids),
        anchor_facts_reviewed=checked.anchor_text is not None,
        minimum_evidence_reviewed=checked.minimum_evidence_supported,
        direct_negative_facts_reviewed=checked.rating >= 3,
        # Rating 5 is only produced after an admitted independent-source fact.
        independent_corroboration_reviewed=checked.anchor_text is not None,
    )


def integration_adjudication_resolver(
    snapshot: EvaluationSnapshot,
    rubric: Mapping[str, object],
    artifact: object,
    *,
    sources: Mapping[str, object],
    registry: object,
) -> Callable[[ReviewedTechnologyAnchor], bool]:
    """Re-adjudicate captured original controller inputs, not a public result DTO.

    Source paths/provenance and review authority are controller-owned. Capture
    detached, validated copies; each verification re-reads the approved local
    source bytes and applies the narrow correspondence/structure checks again.
    No token or freely constructed result authenticates a semantic claim.
    """
    from skala_rag.agents.source_fact_verification import (
        TrustedReviewRegistry,
        _check_artifact,
        adjudicate_technology_integration,
    )

    try:
        _check_artifact(artifact, registry)
        frozen = checked_snapshot(snapshot)
        pinned_rubric = deepcopy(dict(rubric))
        pinned_artifact = replace(
            artifact,
            anchor_texts=dict(artifact.anchor_texts),
            facts=deepcopy(artifact.facts),
            reviews=deepcopy(artifact.reviews),
        )
        pinned_registry = TrustedReviewRegistry(
            {k: frozenset(v) for k, v in registry.accepted.items()}
        )
        pinned_sources = {
            key: replace(
                value,
                source=value.source.model_copy(deep=True),
                document=value.document.model_copy(deep=True),
                approved_chunks=tuple(
                    c.model_copy(deep=True) for c in value.approved_chunks
                ),
            )
            for key, value in sources.items()
        }
    except Exception:
        raise TechnologyReviewError("TECHNOLOGY_REVIEW_REJECTED") from None

    def recheck() -> ReviewedTechnologyAnchor:
        try:
            result = adjudicate_technology_integration(
                frozen,
                pinned_rubric,
                pinned_artifact,
                sources=pinned_sources,
                registry=pinned_registry,
            )
            return anchor_from_integration_adjudication(
                result, "resolver", rubric=pinned_rubric
            )
        except Exception:
            raise TechnologyReviewError("TECHNOLOGY_REVIEW_REJECTED") from None

    recheck()  # Deny unsupported or invalid controller inputs at construction.

    def resolve(receipt: ReviewedTechnologyAnchor) -> bool:
        if (
            type(receipt) is not ReviewedTechnologyAnchor
            or type(receipt.review_reference) is not str
            or not receipt.review_reference.strip()
            or type(receipt.rating) is not int
            or type(receipt.evidence_ids) is not tuple
            or any(type(e) is not str or not e.strip() for e in receipt.evidence_ids)
            or len(set(receipt.evidence_ids)) != len(receipt.evidence_ids)
        ):
            return False
        expected = recheck()
        return (
            receipt.criterion_id == expected.criterion_id
            and receipt.rating == expected.rating
            and receipt.artifact_sha256 == expected.artifact_sha256
            and receipt.snapshot_sha256 == expected.snapshot_sha256
            and set(receipt.evidence_ids) == set(expected.evidence_ids)
            and all(
                getattr(receipt, name) is True and getattr(expected, name) is True
                for name in (
                    "anchor_facts_reviewed",
                    "minimum_evidence_reviewed",
                    "direct_negative_facts_reviewed",
                    "independent_corroboration_reviewed",
                )
            )
        )

    return resolve
