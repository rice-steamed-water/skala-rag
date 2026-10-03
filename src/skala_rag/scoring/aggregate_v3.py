"""Fixture-only v3 arithmetic; baseline fixed-denominator scorer stays untouched."""

from collections.abc import Callable, Sequence
from decimal import Decimal, localcontext

from skala_rag.contracts.ids import score_summary_id
from skala_rag.contracts.v3 import (
    CriterionAssessment,
    DimensionScore,
    Evaluation,
    ScoreSummary,
)
from skala_rag.scoring.v3_policy import NumericPolicy, V3Criterion, V3Policy

DIMENSIONS = ("founder", "market", "technology", "moat", "traction", "deal_terms")
ApplicabilityVerifier = Callable[[CriterionAssessment, object | None], bool]


class ZeroDenominatorV3(ValueError):
    """Candidate-scoped error: never emit a score/label for 0/0."""

    def __init__(self, candidate_id: str, dimension: str | None):
        self.candidate_id = candidate_id
        self.dimension = dimension
        super().__init__(
            f"zero applicable denominator: {candidate_id} / {dimension or 'total'}"
        )


def aggregate_scores_v3(
    evaluations: Sequence[Evaluation],
    policy: V3Policy,
    *,
    applicability_verifier: ApplicabilityVerifier | None,
    snapshot: object | None = None,
) -> ScoreSummary:
    """Aggregate exactly six same-generation evaluations after external N/A approval.

    Evidence attribution and five-branch terminal success are checked by #24's
    join. The caller MUST only pass that join's promoted evaluations. This
    independent gate rechecks the complete catalog and N/A approval.
    """
    if not isinstance(policy, V3Policy) or policy.execution_mode != "fixture":
        raise ValueError("approved fixture V3Policy required")
    return _aggregate_scores_v3(
        evaluations,
        criteria=policy.criteria,
        numeric=policy.numeric,
        policy_version=policy.policy_version,
        applicability_verifier=applicability_verifier,
        snapshot=snapshot,
    )


def _aggregate_scores_v3(
    evaluations: Sequence[Evaluation],
    *,
    criteria: tuple[V3Criterion, ...],
    numeric: NumericPolicy,
    policy_version: str,
    applicability_verifier: ApplicabilityVerifier | None,
    snapshot: object | None,
) -> ScoreSummary:
    """Shared pure core; entry points own admission, join owns attribution."""
    items = [Evaluation.model_validate(e.model_dump()) for e in evaluations]
    if len(items) != 6 or {e.dimension for e in items} != set(DIMENSIONS):
        raise ValueError("exactly six distinct dimensions required")
    identity_fields = (
        "run_id",
        "candidate_id",
        "evaluation_round",
        "snapshot_id",
        "evidence_revision",
        "policy_version",
        "schema_version",
    )
    identity = tuple(getattr(items[0], field) for field in identity_fields)
    if any(
        tuple(getattr(e, field) for field in identity_fields) != identity for e in items
    ):
        raise ValueError("mixed evaluation generation")
    if items[0].policy_version != policy_version:
        raise ValueError("evaluation policy generation mismatch")
    if snapshot is not None and any(
        getattr(snapshot, field) != getattr(items[0], field)
        for field in identity_fields
    ):
        raise ValueError("snapshot generation mismatch")
    catalog = {c.criterion_id: c for c in criteria}
    by_dimension = {e.dimension: e for e in items}
    points: dict[str, Decimal | None] = {}
    dimensions = {}
    total_score = Decimal(0)
    total_missing = Decimal(0)
    total_na = Decimal(0)
    total_applicable = Decimal(0)
    with localcontext() as ctx:
        ctx.prec = 60  # Display only; decision guards use exact cross products.
        for dimension in DIMENSIONS:
            evaluation = by_dimension[dimension]
            assessments = evaluation.criteria
            expected = {c.criterion_id for c in criteria if c.dimension == dimension}
            actual = [a.criterion_id for a in assessments]
            if len(actual) != len(set(actual)) or set(actual) != expected:
                raise ValueError(
                    f"incomplete or duplicate criterion dimension: {dimension}"
                )
            earned = Decimal(0)
            applicable = Decimal(0)
            missing = Decimal(0)
            na = Decimal(0)
            for assessment in assessments:
                weight = Decimal(catalog[assessment.criterion_id].weight)
                if assessment.status == "not_applicable":
                    if (
                        applicability_verifier is None
                        or applicability_verifier(assessment, snapshot) is not True
                    ):
                        raise ValueError(
                            "N/A requires external approved applicability verifier"
                        )
                    na += weight
                    points[assessment.criterion_id] = None
                elif assessment.status == "missing":
                    applicable += weight
                    missing += weight
                    points[assessment.criterion_id] = None
                elif assessment.status == "observed":
                    applicable += weight
                    value = weight * Decimal(assessment.rating) / Decimal(5)
                    earned += value
                    points[assessment.criterion_id] = value
                else:
                    raise ValueError("invalid criterion status")
            if applicable == 0:
                raise ZeroDenominatorV3(items[0].candidate_id, dimension)
            dimensions[dimension] = DimensionScore(
                schema_version=items[0].schema_version,
                observed_score=earned,
                applicable_weight=applicable,
                not_applicable_weight=na,
                missing_weight=missing,
                dimension_score_pct=earned * 100 / applicable,
            )
            total_score += earned
            total_applicable += applicable
            total_missing += missing
            total_na += na
        if total_applicable == 0:
            raise ZeroDenominatorV3(items[0].candidate_id, None)
        low = [
            d
            for d in numeric.low_dimension_scope
            if dimensions[d].observed_score * 100
            <= numeric.low_dimension_ratio_pct * dimensions[d].applicable_weight
        ]
        hold = (
            ["WEIGHTED_MISSING"]
            if total_missing * 100 >= numeric.weighted_missing_pct * total_applicable
            else []
        )
        hold += [f"LOW_{d.upper()}" for d in low]
        return ScoreSummary(
            schema_version=items[0].schema_version,
            score_summary_id=score_summary_id(
                items[0].run_id,
                items[0].candidate_id,
                items[0].evaluation_round,
                policy_version,
            ),
            run_id=items[0].run_id,
            candidate_id=items[0].candidate_id,
            evaluation_round=items[0].evaluation_round,
            snapshot_id=items[0].snapshot_id,
            evidence_revision=items[0].evidence_revision,
            policy_version=policy_version,
            criterion_points=points,
            dimension_scores=dimensions,
            observed_score=total_score,
            applicable_weight=total_applicable,
            not_applicable_weight=total_na,
            missing_weight=total_missing,
            normalized_score=total_score * 100 / total_applicable,
            weighted_missing_pct=total_missing * 100 / total_applicable,
            coverage_pct=Decimal(100) - total_missing * 100 / total_applicable,
            low_score_dimensions=low,
            hold_reasons=hold,
        )
