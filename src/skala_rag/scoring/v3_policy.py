"""Issue #82 operational configuration only; no scoring/controller execution."""

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Literal, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from skala_rag.contracts.common import Count, Text
from skala_rag.scoring.catalog import Criterion, Dimension, ScoringPolicy

# Exact baseline IDs and weights, not a new applicability-rule catalog.
CATALOG = (
    ("founder.expertise", 2),
    ("founder.industry", 2),
    ("founder.execution", 1),
    ("market.size", 10),
    ("market.growth", 10),
    ("market.demand", 10),
    ("technology.maturity", 10),
    ("technology.reliability", 5),
    ("technology.integration", 5),
    ("technology.commercialization", 5),
    ("moat.differentiation", 5),
    ("moat.ip", 5),
    ("moat.data", 5),
    ("moat.lock_in", 5),
    ("traction.revenue_growth", 3),
    ("traction.gross_margin", 2),
    ("traction.burn", 1),
    ("traction.runway", 2),
    ("traction.concentration", 1),
    ("traction.rule_of_40", 1),
    ("deal_terms.stage", 2),
    ("deal_terms.valuation", 5),
    ("deal_terms.ownership", 3),
)


def _exact(value: object) -> Decimal:
    if type(value) not in (int, str, Decimal):
        raise ValueError("exact number required; bool/float are not accepted")
    try:
        result = Decimal(cast(int | str | Decimal, value))
    except InvalidOperation as exc:
        raise ValueError("invalid Decimal") from exc
    if not result.is_finite():
        raise ValueError("finite Decimal required")
    return result


Exact = Annotated[Decimal, BeforeValidator(_exact)]


class FrozenPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Approval(FrozenPolicy):
    status: Literal["operational_approved"]
    source: Literal["rice-steamed-water/skala-rag#82"]
    approved_on: Literal["2026-09-30"]
    rubric_status: Literal["not_approved"]
    live_readiness: Literal["not_approved"]
    live_budget: Literal["not_approved"]


class V3Criterion(Criterion):
    # Reuse baseline validation; strict text prevents coercion/normalization.
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    criterion_id: Text
    display_name: Text


class DimensionWeight(FrozenPolicy):
    dimension: Dimension
    weight: Annotated[int, Field(strict=True, gt=0)]


class NumericPolicy(FrozenPolicy):
    priority_score: Exact
    recommend_score: Exact
    watchlist_score: Exact
    weighted_missing_pct: Exact
    low_dimension_ratio_pct: Exact
    low_dimension_scope: tuple[Literal["market", "technology"], ...]
    guard_order: tuple[Literal["missing", "low_dimension", "score"], ...]
    missing_comparison: Literal[">="]
    low_dimension_comparison: Literal["<="]
    score_comparison: Literal[">="]
    score_basis: Literal["normalized_score"]
    comparison_precision: Literal["exact_unrounded"]
    labels_descending: tuple[
        Literal["RECOMMEND_PRIORITY", "RECOMMEND", "WATCHLIST", "PASS"], ...
    ]

    @model_validator(mode="after")
    def approved_values(self) -> "NumericPolicy":
        if (
            (
                self.priority_score,
                self.recommend_score,
                self.watchlist_score,
                self.weighted_missing_pct,
                self.low_dimension_ratio_pct,
            )
            != tuple(map(Decimal, (80, 70, 60, 30, 40)))
            or self.low_dimension_scope != ("market", "technology")
            or self.guard_order != ("missing", "low_dimension", "score")
            or self.labels_descending
            != ("RECOMMEND_PRIORITY", "RECOMMEND", "WATCHLIST", "PASS")
        ):
            raise ValueError("numeric/grade policy differs from issue #82 approval")
        return self


class ApplicabilityPolicy(FrozenPolicy):
    lack_of_evidence: Literal["missing"]
    na_requires: tuple[
        Literal["applicability_reason", "approved_rule", "applicability_evidence"], ...
    ]
    rule_authorization: Literal["required_external_controller_input"]
    denominator: Literal["include_missing_exclude_not_applicable"]
    zero_denominator: Literal["no_score_candidate_error_archive_then_advance"]

    @model_validator(mode="after")
    def required_evidence(self) -> "ApplicabilityPolicy":
        if self.na_requires != (
            "applicability_reason",
            "approved_rule",
            "applicability_evidence",
        ):
            raise ValueError("all N/A prerequisites must be explicit")
        return self


class SelectionPolicy(FrozenPolicy):
    candidates: Literal["eligible_normal_evaluated"]
    ordering: tuple[
        Literal[
            "recommend_label_priority",
            "normalized_score_desc",
            "weighted_missing_pct_asc",
            "original_candidate_id_asc",
        ],
        ...,
    ]
    no_recommendation: Literal["no_selection_comparison_report"]

    @model_validator(mode="after")
    def ordering_is_approved(self) -> "SelectionPolicy":
        if self.ordering != (
            "recommend_label_priority",
            "normalized_score_desc",
            "weighted_missing_pct_asc",
            "original_candidate_id_asc",
        ):
            raise ValueError("selector ordering differs from approval")
        return self


class ResearchPolicy(FrozenPolicy):
    additional_requests_per_candidate: Count
    initial_collection: Literal["excluded"]
    decrement: Literal["before_request"]
    empty_or_failure: Literal["consumes_attempt"]
    post_evaluation: Literal["forbidden"]

    @model_validator(mode="after")
    def budget_is_approved(self) -> "ResearchPolicy":
        if self.additional_requests_per_candidate != 2:
            raise ValueError("additional evidence budget must be 2")
        return self


class ReportPolicy(FrozenPolicy):
    shared_revisions: Count
    revision_scope: tuple[Literal["structural", "semantic"], ...]
    initial_generation: Literal["excluded"]
    exhausted_status: Literal["completed"]
    exhausted_notice: Literal["Warning"]
    exhausted_output: Literal["current_draft_and_findings"]
    validated_final: Literal["forbidden_when_exhausted"]
    exhausted_cli_exit: Count
    corrupt_context_or_upstream: Literal["failed"]

    @model_validator(mode="after")
    def budget_is_approved(self) -> "ReportPolicy":
        if (
            self.shared_revisions != 2
            or self.exhausted_cli_exit != 2
            or self.revision_scope != ("structural", "semantic")
        ):
            raise ValueError("report budget/exit/scope differs from approval")
        return self


class V3Policy(FrozenPolicy):
    policy_version: Literal["v3-operational-1.0.0"]
    execution_mode: Literal["fixture"]
    approval: Approval
    criteria: tuple[V3Criterion, ...] = Field(min_length=23, max_length=23)
    dimension_weights: tuple[DimensionWeight, ...] = Field(min_length=6, max_length=6)
    numeric: NumericPolicy
    applicability: ApplicabilityPolicy
    selection: SelectionPolicy
    research: ResearchPolicy
    report: ReportPolicy

    @model_validator(mode="after")
    def catalog_matches_baseline(self) -> "V3Policy":
        actual = {c.criterion_id: c.weight for c in self.criteria}
        if len(actual) != 23 or actual != dict(CATALOG):
            raise ValueError("exact baseline criterion IDs/weights required")
        weights = {w.dimension: w.weight for w in self.dimension_weights}
        if len(weights) != 6:
            raise ValueError("duplicate dimension")
        # Reuse baseline catalog invariants only, not its draft approval/rubric.
        catalog = ScoringPolicy.model_construct(
            criteria=self.criteria, dimension_weights=weights
        )
        catalog.validate_catalog()
        return self


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def load_v3_policy(path: str | Path, *, execution_mode: Literal["fixture"]) -> V3Policy:
    """Explicit fixture-only path; no live approval or algorithm execution."""
    if execution_mode != "fixture":
        raise ValueError("v3 operational policy requires explicit fixture mode")
    payload = json.loads(
        Path(path).read_text(encoding="utf-8"),
        parse_float=Decimal,
        parse_constant=lambda value: _exact(value),
        object_pairs_hook=_unique_object,
    )
    return V3Policy.model_validate(payload)
