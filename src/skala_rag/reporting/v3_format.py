"""Lossless v3 presentation input for offline fixtures, not a D09 renderer.

Decimals use the existing v3 DTO JSON representation (exact strings); unavailable
numbers remain null. No rounding, fixed-100 score, translated labels, representative
reason, report headings, Markdown/PDF layout, or live wiring is selected here.
"""

from typing import Any, Literal, TypedDict

from skala_rag.contracts.v3 import InvestmentDecision, ScoreSummary


class FixtureScoreDecisionPresentation(TypedDict):
    execution_mode: Literal["fixture"]
    score_basis: Literal["normalized_score"]
    score_summary: dict[str, Any]
    decision: dict[str, Any]


def format_fixture_score_decision(
    summary: ScoreSummary, decision: InvestmentDecision
) -> FixtureScoreDecisionPresentation:
    """Project supplied observations without calculating a score or decision.

    This is fixture presentation data, not a validated/final report. Full DTO
    field names and identity/provenance metadata remain available to a future
    renderer; raw observed_score must not be substituted for normalized_score.
    Input DTOs are revalidated (including mutable nested scores), then their
    score_summary_id, candidate_id and run_id must match. This does not validate
    arithmetic, catalog completeness, evidence, or live readiness.
    """
    summary = ScoreSummary.model_validate(summary)
    decision = InvestmentDecision.model_validate(decision)
    for field in ("score_summary_id", "candidate_id", "run_id"):
        if getattr(summary, field) != getattr(decision, field):
            raise ValueError(f"decision/score summary mismatch: {field}")
    return {
        "execution_mode": "fixture",
        "score_basis": "normalized_score",
        "score_summary": summary.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
    }
