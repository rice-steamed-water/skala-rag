"""#16 산술 결과를 #6 공식 DTO로 감싼다 — ScoreSummary·InvestmentDecision.

label·report_grade·reason_codes는 decide() 계산값만 쓴다. 호출 측이 넘기는
설명(rationale·risks·limitations)은 판단을 바꿀 수 없다(scoring §5).
"""

from collections.abc import Sequence

from skala_rag.contracts import (
    EvaluationResult,
    InvestmentDecision,
    ScoreSummary,
    decision_id,
    score_summary_id,
)
from skala_rag.scoring.aggregate import ScoringError, aggregate_scores
from skala_rag.scoring.catalog import ScoringPolicy
from skala_rag.scoring.decide import decide, decide_breakdown

HOLD_REASON_PREFIXES = ("INSUFFICIENT_EVIDENCE", "LOW_DIMENSION:")


def build_score_summary(
    results: Sequence[EvaluationResult],
    policy: ScoringPolicy,
    *,
    schema_version: str,
) -> ScoreSummary:
    """여섯 EvaluationResult가 모두 success일 때만 ScoreSummary를 만든다(D04).

    실패 결과가 하나라도 있으면 집계하지 않는다 — 평가 실패를 missing이나
    0점으로 바꾸지 않는다. run_id가 섞여도 거절한다.
    """
    failed = [r.dimension for r in results if r.status != "success"]
    if failed:
        raise ScoringError("EVALUATION_FAILED", f"실패한 영역: {failed}")
    run_ids = {r.run_id for r in results}
    if len(run_ids) != 1:
        raise ScoringError("MIXED_GENERATION", "run_id가 서로 다르다")
    evaluations = [r.evaluation for r in results]
    breakdown = aggregate_scores(evaluations, policy)  # 세대·완전성 검증 포함
    decision = decide_breakdown(breakdown, policy.thresholds)
    first = evaluations[0]
    return ScoreSummary(
        schema_version=schema_version,
        score_summary_id=score_summary_id(
            first.run_id,
            first.candidate_id,
            first.evaluation_round,
            first.policy_version,
        ),
        run_id=first.run_id,
        candidate_id=first.candidate_id,
        evaluation_round=first.evaluation_round,
        snapshot_id=first.snapshot_id,
        evidence_revision=first.evidence_revision,
        policy_version=first.policy_version,
        criterion_points=breakdown.criterion_points,
        dimension_ratings=breakdown.dimension_ratings,
        observed_score=breakdown.observed_score,
        missing_weight=breakdown.missing_weight,
        coverage_pct=breakdown.coverage_pct,
        low_score_dimensions=list(decision.low_score_dimensions),
        hold_reasons=[
            code
            for code in decision.reason_codes
            if code.startswith(HOLD_REASON_PREFIXES)
        ],
    )


def build_investment_decision(
    summary: ScoreSummary,
    policy: ScoringPolicy,
    *,
    schema_version: str,
    evidence_ids: Sequence[str],
    rationale: str,
    risks: Sequence[str] = (),
    limitations: Sequence[str] = (),
) -> InvestmentDecision:
    """ScoreSummary에서 판단을 다시 계산해 InvestmentDecision을 만든다.

    요약 값만으로 재계산하므로 요약 이후 설명 단계가 label을 바꿀 수 없다.
    """
    if summary.policy_version != policy.policy_version:
        raise ScoringError("POLICY_MISMATCH", "요약과 정책의 policy_version이 다르다")
    decision = decide(
        summary.observed_score,
        summary.missing_weight,
        summary.dimension_ratings,
        policy.thresholds,
    )
    return InvestmentDecision(
        schema_version=schema_version,
        decision_id=decision_id(summary.score_summary_id),
        run_id=summary.run_id,
        candidate_id=summary.candidate_id,
        label=decision.label,
        report_grade=decision.report_grade,
        score_summary_id=summary.score_summary_id,
        reason_codes=list(decision.reason_codes),
        evidence_ids=list(evidence_ids),
        rationale=rationale,
        risks=list(risks),
        limitations=list(limitations),
    )
