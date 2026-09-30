"""Demo-only rubric ratings and deterministic role scores, never a decision.

Uses existing artifact weights/anchors without promoting the fixture policy to
live approval. The caller owns demo approval, provenance and request admission.
"""

import hashlib
import json
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from skala_rag.contracts.assessment import Rating
from skala_rag.contracts.common import Text


class CriterionSupport(BaseModel):
    """An exact source span mapped to one rubric minimum-evidence requirement."""

    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    criterion_id: Text
    evidence_id: Text
    quote: Text
    requirement: Text


class CriterionRating(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    criterion_id: Text
    status: Literal["observed", "missing"]
    rating: Rating | None
    rationale: Text
    missing_reason: Text | None
    supports: list[CriterionSupport]

    @model_validator(mode="after")
    def consistent_observation(self):
        if self.status == "observed":
            if (
                self.rating is None
                or not self.supports
                or self.missing_reason is not None
            ):
                raise ValueError("REVIEW_RATING_INVALID")
        elif self.rating is not None or self.missing_reason is None or self.supports:
            raise ValueError("REVIEW_RATING_INVALID")
        return self


METHOD = (
    "demo-rubric-1: 100 * sum(observed weight * rating / 5) / full role weight; "
    "missing retained; no observations => null"
)
ROLE_DIMENSIONS = {
    "founder": ("founder",),
    "market": ("market",),
    "technology": ("technology",),
    "moat": ("moat",),
    "business_deal": ("traction", "deal_terms"),
}


def load_demo_rubric(root: Path) -> dict:
    """Read existing artifacts; preserve versions, status and content hashes."""
    policy_path = root / "configs/scoring.v3.json"
    policy = json.loads(policy_path.read_text())
    criteria, artifacts, common_rules = {}, {}, {}
    for name in ("core", "finance"):
        path = root / f"configs/rubrics/{name}.yaml"
        data = yaml.safe_load(path.read_text())
        artifacts[name] = {
            "version": data["rubric_version"],
            "status": data["status"],
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        common_rules[name] = data.get("common_rules", {})
        for dimension, spec in data["dimensions"].items():
            for cid, rule in spec["criteria"].items():
                criteria[cid] = {**rule, "dimension": dimension, "rubric": name}
    catalog = {c["criterion_id"]: c for c in policy["criteria"]}
    if set(criteria) != set(catalog) or any(
        (r["dimension"], r["weight"])
        != (catalog[cid]["dimension"], catalog[cid]["weight"])
        for cid, r in criteria.items()
    ):
        raise ValueError("DEMO_RUBRIC_INVALID")
    return {
        "criteria": criteria,
        "roles": {
            role: [cid for cid, c in criteria.items() if c["dimension"] in dimensions]
            for role, dimensions in ROLE_DIMENSIONS.items()
        },
        "artifacts": artifacts,
        "common_rules": common_rules,
        "catalog_version": policy["policy_version"],
        "catalog_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        "method": METHOD,
    }


def _validated_assessments(role, review, evidence, rubric):
    expected = rubric["roles"][role]
    items = [CriterionRating.model_validate(a) for a in review.get("criteria", [])]
    if sorted(a.criterion_id for a in items) != sorted(expected):
        raise ValueError("REVIEW_CRITERIA_INVALID")
    for a in items:
        rule = rubric["criteria"][a.criterion_id]
        if a.status == "missing":
            continue
        # This demo admits author-reported excerpts, not independently verified
        # corroboration. The existing core self-claim cap therefore applies.
        if (
            rule["rubric"] == "core"
            and a.rating > rubric["common_rules"]["core"]["self_claim_max_rating"]
        ):
            raise ValueError("REVIEW_RATING_INVALID")
        requirements = rule["minimum_evidence"]
        supplied = {s.requirement for s in a.supports}
        sufficient = supplied == set(requirements)
        if a.criterion_id == "traction.runway":
            sufficient = bool(supplied) and supplied <= set(requirements)
        elif a.criterion_id == "traction.burn":
            sufficient = supplied <= set(requirements) and (
                requirements[0] in supplied
                or a.rating == 5
                and requirements[1] in supplied
            )
        if not sufficient:
            raise ValueError("REVIEW_EVIDENCE_INVALID")
        for support in a.supports:
            item = evidence.get(support.evidence_id)
            if (
                item is None
                or item.get("evidence_id") != support.evidence_id
                or support.criterion_id != a.criterion_id
                or support.quote not in item.get("excerpt", "")
                or item.get("conflicts_with")
                or (
                    item.get("criterion_ids")
                    and a.criterion_id not in item["criterion_ids"]
                )
                or not (
                    item.get("scope") == "company"
                    and item.get("candidate_id") == "co-physical-intelligence"
                    or item.get("scope") == "industry"
                    and role == "market"
                )
            ):
                raise ValueError("REVIEW_EVIDENCE_INVALID")
    return {a.criterion_id: a.model_dump(mode="json") for a in items}


def score_reviews(reviews: dict, *, evidence: dict, rubric: dict) -> dict:
    """Normalize earned criterion points, retaining missing in each denominator.

    Absent roles remain blank during the sequential review. A returned role must
    cover its entire catalog. Invalid ratings fail, never become missing/zero.
    Exact spans and explicit mappings establish traceability, not semantic truth;
    the independent report Judge must check anchor/relevance against those spans.
    """
    if set(reviews) - set(rubric["roles"]):
        raise ValueError("REVIEW_CRITERIA_INVALID")
    scores = {}
    for role, ids in rubric["roles"].items():
        weight = sum(rubric["criteria"][cid]["weight"] for cid in ids)
        assessments = (
            _validated_assessments(role, reviews[role], evidence, rubric)
            if role in reviews
            else {}
        )
        trace, used, earned, missing_weight, count = {}, set(), Decimal(0), 0, 0
        for cid in ids:
            rule = rubric["criteria"][cid]
            assessment = assessments.get(cid)
            points = None
            if assessment and assessment["status"] == "observed":
                points = Decimal(rule["weight"] * assessment["rating"]) / 5
                earned += points
                count += 1
                used.update(s["evidence_id"] for s in assessment["supports"])
            else:
                missing_weight += rule["weight"]
            trace[cid] = {
                "weight": rule["weight"],
                "points": str(points) if points is not None else None,
                "assessment": assessment,
            }
        with localcontext() as ctx:
            ctx.prec = 60
            score = format((earned * 100 / weight).normalize(), "f") if count else None
        scores[role] = {
            "score": score,
            "evidence_ids": sorted(used),
            "scored_count": count,
            "missing_count": len(ids) - count,
            "applicable_weight": str(weight),
            "missing_weight": str(missing_weight),
            "earned_points": str(earned) if count else None,
            "method": METHOD,
            "criteria": trace,
        }
    return scores
