"""Explicit fixture-only v3 coverage, independent of unmerged operational policy."""

from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import Literal

from skala_rag.contracts import Evidence, ResearchGap
from skala_rag.contracts.v3 import ApplicabilityAssessment, CoverageResult
from skala_rag.scoring.catalog import Criterion, ScoringPolicy
from skala_rag.scoring.coverage import SupportCheck

ApplicabilityCheck = Callable[
    [str, Criterion, ApplicabilityAssessment, tuple[Evidence, ...]], bool
]


class NoApplicableCriteria(ValueError):
    """Candidate-scoped terminal coverage error; no percentage or recommendation."""

    def __init__(self, candidate_id: str, policy_version: str):
        self.candidate_id = candidate_id
        self.policy_version = policy_version
        super().__init__(
            f"candidate {candidate_id}: zero applicable weight ({policy_version})"
        )


def check_coverage_v3(
    candidate_id: str,
    evidence: Sequence[Evidence],
    catalog: ScoringPolicy,
    *,
    evidence_revision: int,
    schema_version: str,
    execution_mode: Literal["fixture", "live"],
    policy_version: str,
    support_check: SupportCheck,
    applicability_assessments: Mapping[str, ApplicabilityAssessment],
    applicability_check: ApplicabilityCheck,
    unresolved_conflict_ids: Sequence[str],
) -> CoverageResult:
    """Require caller-owned policy approval and evidence sufficiency checks."""
    if execution_mode != "fixture":
        raise ValueError("v3 Coverage requires fixture execution")
    if not isinstance(policy_version, str) or not policy_version.strip():
        raise ValueError("explicit policy_version is required")
    catalog = ScoringPolicy.model_validate(catalog.model_dump())
    known = {c.criterion_id for c in catalog.criteria}
    if len(known) != 23:
        raise ValueError("catalog requires 23 unique criteria")
    by_id = {e.evidence_id: e for e in evidence}
    if len(by_id) != len(evidence):
        raise ValueError("duplicate Evidence ID")
    conflicts = set(unresolved_conflict_ids)
    if len(conflicts) != len(unresolved_conflict_ids) or not conflicts <= by_id.keys():
        raise ValueError("duplicate or unknown conflict ID")
    if not set(applicability_assessments) <= known:
        raise ValueError("unknown applicability criterion in catalog")
    relevant = []
    for item in evidence:
        if item.schema_version != schema_version:
            raise ValueError("Evidence schema_version mismatch")
        if len(set(item.criterion_ids)) != len(item.criterion_ids):
            raise ValueError("duplicate Evidence criterion attribution")
        if not set(item.criterion_ids) <= known:
            raise ValueError("unknown Evidence criterion in catalog")
        if item.scope == "industry" and item.candidate_id is not None:
            raise ValueError("industry evidence requires null candidate_id")
        if item.scope == "company" and item.candidate_id is None:
            raise ValueError("company evidence requires candidate_id")
        if item.scope == "company" and item.candidate_id != candidate_id:
            continue
        relevant.append(item)
    blocked = {
        item.evidence_id
        for item in relevant
        if item.evidence_id in conflicts or set(item.conflicts_with) & conflicts
    }
    covered, missing, na = [], [], []
    validated = {}
    missing_weight = Decimal(0)
    na_weight = Decimal(0)
    for criterion in catalog.criteria:
        cid = criterion.criterion_id
        matches = tuple(item for item in relevant if cid in item.criterion_ids)
        usable = matches if not any(e.evidence_id in blocked for e in matches) else ()
        if cid in applicability_assessments:
            assessment = ApplicabilityAssessment.model_validate(
                applicability_assessments[cid].model_dump()
            )
            if not set(assessment.evidence_ids) <= by_id.keys():
                raise ValueError("unknown applicability Evidence ID")
            actual = tuple(by_id[eid] for eid in assessment.evidence_ids)
            if assessment.schema_version != schema_version:
                raise ValueError("applicability schema_version mismatch")
            if any(not item.provenance for item in actual):
                raise ValueError("applicability Evidence requires provenance")
            if any(item not in usable for item in actual):
                raise ValueError(
                    "applicability Evidence candidate/criterion/conflict mismatch"
                )
            approved = applicability_check(candidate_id, criterion, assessment, actual)
            if type(approved) is not bool:
                raise TypeError("applicability_check must return bool")
            if not approved:
                raise ValueError("applicability rule/reason/evidence not approved")
            na.append(cid)
            validated[cid] = assessment.model_copy(deep=True)
            na_weight += Decimal(criterion.weight)
            continue
        supported = support_check(criterion, usable) if usable else False
        if type(supported) is not bool:
            raise TypeError("support_check must return bool")
        if supported:
            covered.append(cid)
        else:
            missing.append(cid)
            missing_weight += Decimal(criterion.weight)
    applicable_weight = (
        sum((Decimal(c.weight) for c in catalog.criteria), Decimal(0)) - na_weight
    )
    if applicable_weight == 0:
        raise NoApplicableCriteria(candidate_id, policy_version)
    weighted_missing_pct = missing_weight / applicable_weight * Decimal(100)
    return CoverageResult(
        schema_version=schema_version,
        candidate_id=candidate_id,
        evidence_revision=evidence_revision,
        policy_version=policy_version,
        covered_criterion_ids=covered,
        missing_criterion_ids=missing,
        not_applicable_criterion_ids=na,
        applicability_assessments=validated,
        applicable_weight=applicable_weight,
        not_applicable_weight=na_weight,
        missing_weight=missing_weight,
        weighted_missing_pct=weighted_missing_pct,
        coverage_pct=Decimal(100) - weighted_missing_pct,
        research_ready=missing_weight * Decimal(100) < Decimal(30) * applicable_weight,
        unresolved_conflicts=[
            e.evidence_id for e in relevant if e.evidence_id in blocked
        ],
    )


def build_research_gaps_v3(
    coverage: CoverageResult,
    catalog: ScoringPolicy,
    templates: Sequence[ResearchGap],
    *,
    policy_version: str,
) -> list[ResearchGap]:
    """Validate complete partition and preserve caller-owned missing gap payloads."""
    catalog = ScoringPolicy.model_validate(catalog.model_dump())
    coverage = CoverageResult.model_validate(coverage.model_dump())
    if coverage.policy_version != policy_version:
        raise ValueError("coverage policy_version mismatch")
    partition = (
        coverage.covered_criterion_ids
        + coverage.missing_criterion_ids
        + coverage.not_applicable_criterion_ids
    )
    if set(partition) != {c.criterion_id for c in catalog.criteria}:
        raise ValueError("coverage must contain the complete catalog")
    targets = [t.criterion_id for t in templates]
    if len(set(targets)) != len(targets) or set(targets) != set(
        coverage.missing_criterion_ids
    ):
        raise ValueError("each missing criterion requires exactly one gap")
    if len({t.gap_id for t in templates}) != len(templates):
        raise ValueError("duplicate gap ID")
    weights = {c.criterion_id: c.weight for c in catalog.criteria}
    order = {c.criterion_id: i for i, c in enumerate(catalog.criteria)}
    gaps = []
    for template in templates:
        if (
            template.candidate_id != coverage.candidate_id
            or template.schema_version != coverage.schema_version
            or template.status != "open"
            or not template.suggested_queries
            or not template.missing_fields
        ):
            raise ValueError("gap candidate/schema/status/query/fields mismatch")
        payload = template.model_dump()
        payload["priority_weight"] = weights[template.criterion_id]
        gaps.append(ResearchGap.model_validate(payload))
    return sorted(gaps, key=lambda g: (-g.priority_weight, order[g.criterion_id]))
