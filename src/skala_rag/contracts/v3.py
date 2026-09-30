"""Explicit v3 structural observations; no scoring or controller policy."""

from typing import Literal, Self

from pydantic import StrictBool, model_validator

from ._validation import validate_unique
from .assessment import Rating
from .common import Contract, Count, Text
from .coverage import ResearchGap
from .decisions import ScorePercentage, ScorePoints
from .errors import WorkflowError
from .evaluation import Dimension, EvaluationSnapshot
from .evidence import Evidence
from .sources import Chunk, Source

__all__ = [
    "CriterionAssessment",
    "ApplicabilityAssessment",
    "Evaluation",
    "EvaluationBranchResult",
    "EvaluationSnapshot",
    "DimensionScore",
    "ScoreSummary",
    "InvestmentDecision",
    "CoverageResult",
    "Source",
    "Chunk",
    "Evidence",
    "WorkflowError",
]

BranchId = Literal["founder", "market", "technology", "moat", "business_deal"]
BRANCH_DIMENSIONS = {
    "founder": frozenset({"founder"}),
    "market": frozenset({"market"}),
    "technology": frozenset({"technology"}),
    "moat": frozenset({"moat"}),
    "business_deal": frozenset({"traction", "deal_terms"}),
}


class ApplicabilityAssessment(Contract):
    applicability_reason: Text
    applicability_rule_id: Text
    evidence_ids: list[Text]

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        validate_unique(self.evidence_ids, "applicability evidence_ids")
        if not self.evidence_ids:
            raise ValueError("applicability requires evidence IDs")
        return self


class CriterionAssessment(Contract):
    criterion_id: Text
    status: Literal["observed", "missing", "not_applicable"]
    rating: Rating | None
    evidence_ids: list[Text]
    rationale: Text
    missing_reason: Text | None = None
    applicability_reason: Text | None = None
    applicability_rule_id: Text | None = None
    applicability_evidence_ids: list[Text] | None = None
    applicability_note: Text | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        validate_unique(self.evidence_ids, "evidence_ids")
        applicability = (
            self.applicability_reason,
            self.applicability_rule_id,
            self.applicability_evidence_ids,
        )
        if self.status == "not_applicable":
            if self.rating is not None or self.missing_reason is not None:
                raise ValueError("N/A requires null rating and no missing reason")
            if any(value is None for value in applicability):
                raise ValueError(
                    "N/A requires reason, rule ID and applicability evidence"
                )
            validate_unique(
                self.applicability_evidence_ids, "applicability_evidence_ids"
            )
            if not self.applicability_evidence_ids:
                raise ValueError("N/A requires applicability evidence")
        else:
            if any(value is not None for value in applicability):
                raise ValueError("applicability fields require not_applicable")
            if self.status == "observed":
                if self.rating is None or not self.evidence_ids:
                    raise ValueError("observed requires rating and evidence")
                if self.missing_reason is not None:
                    raise ValueError("observed forbids missing_reason")
            elif self.rating is not None or self.missing_reason is None:
                raise ValueError("missing requires null rating and missing_reason")
        return self


class EvaluationIdentity(Contract):
    run_id: Text
    candidate_id: Text
    evaluation_round: Count
    snapshot_id: Text
    evidence_revision: Count
    policy_version: Text


class Evaluation(EvaluationIdentity):
    dimension: Dimension
    rubric_version: Text
    criteria: list[CriterionAssessment]
    research_gaps: list[ResearchGap]
    caveats: list[Text]


class EvaluationBranchResult(EvaluationIdentity):
    branch_id: BranchId
    status: Literal["success", "failure"]
    evaluations: dict[Dimension, Evaluation] | None = None
    errors: list[WorkflowError]

    @model_validator(mode="after")
    def validate_terminal(self) -> Self:
        if self.status == "success":
            if self.evaluations is None or self.errors:
                raise ValueError("success requires evaluations and no errors")
            if set(self.evaluations) != BRANCH_DIMENSIONS[self.branch_id]:
                raise ValueError("branch must contain exactly its dimensions")
            for dimension, evaluation in self.evaluations.items():
                if dimension != evaluation.dimension:
                    raise ValueError("dimension map key mismatch")
                for field in EvaluationIdentity.model_fields:
                    if field != "schema_version" and getattr(self, field) != getattr(
                        evaluation, field
                    ):
                        raise ValueError(f"evaluation identity mismatch: {field}")
        elif self.evaluations is not None or not self.errors:
            raise ValueError("failure requires null evaluations and errors")
        for error in self.errors:
            if error.run_id != self.run_id or error.candidate_id not in (
                None,
                self.candidate_id,
            ):
                raise ValueError("error identity mismatch")
        return self


class DimensionScore(Contract):
    observed_score: ScorePoints | None = None
    applicable_weight: ScorePoints | None = None
    not_applicable_weight: ScorePoints | None = None
    missing_weight: ScorePoints | None = None
    dimension_score_pct: ScorePercentage | None = None


class ScoreSummary(EvaluationIdentity):
    score_summary_id: Text
    criterion_points: dict[Text, ScorePoints | None]
    dimension_scores: dict[Dimension, DimensionScore]
    observed_score: ScorePoints | None = None
    applicable_weight: ScorePoints | None = None
    not_applicable_weight: ScorePoints | None = None
    missing_weight: ScorePoints | None = None
    normalized_score: ScorePercentage | None = None
    weighted_missing_pct: ScorePercentage | None = None
    coverage_pct: ScorePercentage | None = None
    low_score_dimensions: list[Literal["market", "technology"]]
    hold_reasons: list[Text]


class InvestmentDecision(Contract):
    """Versioned label observation; does not calculate a decision."""

    decision_id: Text
    run_id: Text
    candidate_id: Text
    label: Literal["RECOMMEND_PRIORITY", "RECOMMEND", "WATCHLIST", "PASS"]
    report_grade: Text
    score_summary_id: Text
    reason_codes: list[Text]
    evidence_ids: list[Text]
    rationale: Text
    risks: list[Text]
    limitations: list[Text]


class CoverageResult(Contract):
    candidate_id: Text
    evidence_revision: Count
    policy_version: Text
    covered_criterion_ids: list[Text]
    missing_criterion_ids: list[Text]
    not_applicable_criterion_ids: list[Text]
    applicability_assessments: dict[Text, ApplicabilityAssessment]
    applicable_weight: ScorePoints | None = None
    not_applicable_weight: ScorePoints | None = None
    missing_weight: ScorePoints | None = None
    weighted_missing_pct: ScorePercentage | None = None
    coverage_pct: ScorePercentage | None = None
    research_ready: StrictBool | None = None
    unresolved_conflicts: list[Text]

    @model_validator(mode="after")
    def validate_partition(self) -> Self:
        all_ids = (
            self.covered_criterion_ids
            + self.missing_criterion_ids
            + self.not_applicable_criterion_ids
        )
        validate_unique(all_ids, "coverage criterion partition")
        if set(self.applicability_assessments) != set(
            self.not_applicable_criterion_ids
        ):
            raise ValueError("applicability map must exactly resolve N/A criterion IDs")
        return self
