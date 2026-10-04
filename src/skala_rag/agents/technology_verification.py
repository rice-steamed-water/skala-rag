"""Technology review claims, not production semantic authority or retrieval."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

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
