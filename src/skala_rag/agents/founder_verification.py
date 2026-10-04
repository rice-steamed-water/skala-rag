"""Snapshot-bound Founder review claims; no person or semantic authority."""

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass

from skala_rag.agents.moat_verification import (
    core_artifact_digest,
    frozen_snapshot_digest,
)
from skala_rag.contracts.assessment import CriterionAssessment
from skala_rag.contracts.evaluation import EvaluationSnapshot

FOUNDER_CRITERIA = frozenset(
    {"founder.expertise", "founder.industry", "founder.execution"}
)


class FounderReviewError(ValueError):
    """Technical review rejection, never Missing or an investment decision."""


@dataclass(frozen=True)
class ReviewedFounderAnchor:
    """Caller assertion requiring separate external authority resolution.

    Reviewed flags attest review of applicable approved requirements, not a new
    requirement that every rating have independent sources. Person/employment
    review includes same-person, company, role, dates and prior commercialization
    identity as applicable; names/keywords and LLM rationale do not prove it.
    """

    review_reference: str
    artifact_sha256: str
    snapshot_sha256: str
    criterion_id: str
    rating: int
    evidence_ids: tuple[str, ...]
    founder_person_ids: tuple[str, ...]
    person_by_evidence_id: tuple[tuple[str, str], ...]
    anchor_facts_reviewed: bool
    minimum_evidence_reviewed: bool
    person_identity_reviewed: bool
    employment_identity_reviewed: bool
    independent_corroboration_reviewed: bool


def validate_founder_anchor(
    receipt: object,
    *,
    rubric: Mapping[str, object],
    snapshot: EvaluationSnapshot,
    criterion: CriterionAssessment,
    people: tuple[str, ...],
    attribution: Mapping[str, str],
    verifier: Callable[[ReviewedFounderAnchor], bool],
) -> bool:
    """Bind review to original whole snapshot and explicit cited person identity."""
    if type(receipt) is not ReviewedFounderAnchor:
        return False
    if type(criterion) is not CriterionAssessment or criterion.status != "observed":
        return False
    if (
        type(receipt.review_reference) is not str
        or not receipt.review_reference.strip()
        or type(receipt.criterion_id) is not str
        or receipt.criterion_id not in FOUNDER_CRITERIA
        or receipt.criterion_id != criterion.criterion_id
        or type(receipt.rating) is not int
        or receipt.rating not in range(1, 6)
        or receipt.rating != criterion.rating
        or type(receipt.artifact_sha256) is not str
        or receipt.artifact_sha256 != core_artifact_digest(rubric)
        or type(receipt.snapshot_sha256) is not str
        or receipt.snapshot_sha256 != frozen_snapshot_digest(snapshot)
        or type(receipt.evidence_ids) is not tuple
        or not receipt.evidence_ids
        or any(type(eid) is not str or not eid.strip() for eid in receipt.evidence_ids)
        or len(set(receipt.evidence_ids)) != len(receipt.evidence_ids)
        or set(receipt.evidence_ids) != set(criterion.evidence_ids)
        or type(receipt.founder_person_ids) is not tuple
        or any(
            type(pid) is not str or not pid.strip()
            for pid in receipt.founder_person_ids
        )
        or len(set(receipt.founder_person_ids)) != len(receipt.founder_person_ids)
        or set(receipt.founder_person_ids) != set(people)
        or type(receipt.person_by_evidence_id) is not tuple
        or any(
            type(pair) is not tuple
            or len(pair) != 2
            or any(type(item) is not str or not item.strip() for item in pair)
            for pair in receipt.person_by_evidence_id
        )
        or len({pair[0] for pair in receipt.person_by_evidence_id})
        != len(receipt.person_by_evidence_id)
        or dict(receipt.person_by_evidence_id)
        != {eid: attribution.get(eid) for eid in criterion.evidence_ids}
        or any(
            getattr(receipt, flag) is not True
            for flag in (
                "anchor_facts_reviewed",
                "minimum_evidence_reviewed",
                "person_identity_reviewed",
                "employment_identity_reviewed",
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
            or attribution.get(eid) not in people
        ):
            return False
    if not callable(verifier):
        return False
    expected = deepcopy(receipt)
    detached = deepcopy(receipt)
    return verifier(detached) is True and detached == expected and receipt == expected
