"""Deterministic v3 decision; no baseline label or LLM authority."""

from skala_rag.contracts.ids import decision_id
from skala_rag.contracts.v3 import InvestmentDecision, ScoreSummary
from skala_rag.scoring.v3_policy import V3Policy


def decide_v3(
    summary: ScoreSummary,
    policy: V3Policy,
    *,
    evidence_ids: tuple[str, ...] = (),
    rationale: str = "Deterministic v3 policy decision",
    risks: tuple[str, ...] = (),
    limitations: tuple[str, ...] = (),
) -> InvestmentDecision:
    """Apply all hold reasons before comparing the unrounded normalized score."""
    summary = ScoreSummary.model_validate(summary.model_dump())
    if not isinstance(policy, V3Policy) or policy.execution_mode != "fixture":
        raise ValueError("fixture V3Policy required")
    if (
        summary.policy_version != policy.policy_version
        or summary.normalized_score is None
    ):
        raise ValueError("v3 score/policy mismatch or undefined score")
    if summary.applicable_weight is None or summary.applicable_weight <= 0:
        raise ValueError("undefined applicable denominator")
    numeric = policy.numeric
    # Cross products retain exact boundaries even for repeating ratios.
    reasons = []
    if summary.missing_weight is None:
        raise ValueError("undefined missing weight")
    if (
        summary.missing_weight * 100
        >= numeric.weighted_missing_pct * summary.applicable_weight
    ):
        reasons.append("WEIGHTED_MISSING")
    for dimension in numeric.low_dimension_scope:
        value = summary.dimension_scores[dimension]
        if (
            value.observed_score is None
            or value.applicable_weight is None
            or value.applicable_weight <= 0
        ):
            raise ValueError("undefined dimension denominator")
        if (
            value.observed_score * 100
            <= numeric.low_dimension_ratio_pct * value.applicable_weight
        ):
            reasons.append(f"LOW_{dimension.upper()}")
    if reasons:
        label, grade = "WATCHLIST", "보류"
    elif summary.normalized_score >= numeric.priority_score:
        label, grade = "RECOMMEND_PRIORITY", "투자 우선 검토"
    elif summary.normalized_score >= numeric.recommend_score:
        label, grade = "RECOMMEND", "투자 검토"
    elif summary.normalized_score >= numeric.watchlist_score:
        label, grade = "WATCHLIST", "보류"
    else:
        label, grade = "PASS", "투자비추천"
    return InvestmentDecision(
        schema_version=summary.schema_version,
        decision_id=decision_id(summary.score_summary_id),
        run_id=summary.run_id,
        candidate_id=summary.candidate_id,
        label=label,
        report_grade=grade,
        score_summary_id=summary.score_summary_id,
        reason_codes=reasons or [f"SCORE_{label}"],
        evidence_ids=list(evidence_ids),
        rationale=rationale,
        risks=list(risks),
        limitations=list(limitations),
    )
