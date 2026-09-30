"""Caller-reviewed artifact and frozen-evidence boundaries; no live admission.

Registry resolution and semantic review are trusted controller responsibilities,
not assertions obtained from an LLM, publisher name, or retrieval zero hits.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from skala_rag.contracts.evaluation import EvaluationSnapshot


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def core_artifact_digest(rubric: Mapping[str, object]) -> str:
    """Bind all supplied content, ignoring only the top-level approval label.

    YAML comments/key ordering are not content. Anchors, bands, common rules,
    other dimensions and unknown fields all remain inside the commitment.
    """
    return _digest({key: value for key, value in rubric.items() if key != "status"})


@dataclass(frozen=True)
class CoreArtifactApproval:
    """Untrusted registry claim until the caller's resolver accepts it."""

    reference: str
    rubric_version: str
    content_sha256: str


def validate_core_artifact(
    rubric: Mapping[str, object],
    approval: CoreArtifactApproval,
    verifier: Callable[[CoreArtifactApproval], bool],
) -> None:
    """Resolve explicit approval, then compare the exact supplied artifact."""
    if not isinstance(approval, CoreArtifactApproval):
        raise ValueError("Explicit authoritative Core artifact approval required")
    if (
        not isinstance(approval.reference, str)
        or not approval.reference.strip()
        or approval.rubric_version != "core-0.1.0"
        or rubric.get("rubric_version") != approval.rubric_version
        or not isinstance(approval.content_sha256, str)
        or len(approval.content_sha256) != 64
        or any(c not in "0123456789abcdef" for c in approval.content_sha256)
    ):
        raise ValueError("Invalid Core artifact approval identity")
    if not callable(verifier) or verifier(approval) is not True:
        raise ValueError("Authoritative Core artifact approval rejected")
    if core_artifact_digest(rubric) != approval.content_sha256:
        raise ValueError(
            "Supplied Core artifact content differs from approved artifact"
        )


def frozen_snapshot_digest(snapshot: EvaluationSnapshot) -> str:
    """Include generation, evidence, sources, chunks and retrieval identity."""
    validated = EvaluationSnapshot.model_validate(
        snapshot.model_dump(), context={"execution_mode": "fixture"}
    )
    return _digest(validated.model_dump(mode="json"))


@dataclass(frozen=True)
class ReviewedMoatAnchor:
    """Review of one exact anchor against one exact frozen snapshot.

    This is deliberately an anchor receipt, not a new fact taxonomy. The trusted
    reviewer must resolve minimum evidence, negative facts, independence, patent
    counts/rights and comparison conditions from the approved anchor. Merely
    constructing this value is not a review or an approval.
    """

    review_reference: str
    artifact_sha256: str
    snapshot_sha256: str
    criterion_id: str
    rating: int
    evidence_ids: tuple[str, ...]


def validate_reviewed_anchor(
    receipt: object,
    *,
    rubric: Mapping[str, object],
    snapshot: EvaluationSnapshot,
    criterion: object,
    verifier: Callable[[ReviewedMoatAnchor], bool],
) -> bool:
    """Fail closed on stale/replayed or semantically unreviewed receipts."""
    if not isinstance(receipt, ReviewedMoatAnchor):
        return False
    cited = getattr(criterion, "evidence_ids", ())
    cid = getattr(criterion, "criterion_id", None)
    if (
        not isinstance(receipt.review_reference, str)
        or not receipt.review_reference.strip()
        or type(receipt.rating) is not int
        or receipt.rating not in range(1, 6)
        or receipt.rating != getattr(criterion, "rating", None)
        or receipt.criterion_id != cid
        or receipt.artifact_sha256 != core_artifact_digest(rubric)
        or receipt.snapshot_sha256 != frozen_snapshot_digest(snapshot)
        or type(receipt.evidence_ids) is not tuple
        or not receipt.evidence_ids
        or any(
            not isinstance(eid, str) or not eid.strip() for eid in receipt.evidence_ids
        )
        or len(set(receipt.evidence_ids)) != len(receipt.evidence_ids)
        or set(receipt.evidence_ids) != set(cited)
    ):
        return False
    for eid in receipt.evidence_ids:
        evidence = snapshot.evidence.get(eid)
        if (
            eid not in snapshot.evidence_ids
            or evidence is None
            or evidence.evidence_id != eid
            or evidence.candidate_id != snapshot.candidate_id
            or evidence.scope != "company"
            or cid not in evidence.criterion_ids
            or evidence.source_id not in snapshot.sources
            or snapshot.sources[evidence.source_id].source_id != evidence.source_id
        ):
            return False
    return callable(verifier) and verifier(receipt) is True
