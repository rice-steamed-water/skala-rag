"""Permutation-invariant v3 Best Selector, separate from baseline first-match."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from skala_rag.scoring.v3_policy import NumericPolicy, V3Policy


@dataclass(frozen=True)
class SelectionResultV3:
    schema_version: str
    run_id: str
    policy_version: str
    considered_candidate_ids: tuple[str, ...]
    compared_score_summary_ids: tuple[str, ...]
    selected_candidate_id: str | None
    reason: str


def _exact(value: object) -> Decimal:
    if type(value) not in (str, int, Decimal):
        raise ValueError("selector requires exact decimal value")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid selector decimal") from exc
    if not number.is_finite() or not 0 <= number <= 100:
        raise ValueError("selector percentage outside 0..100")
    return number


def select_best_v3(
    candidates: Sequence[Mapping[str, object]],
    policy: V3Policy,
    *,
    run_id: str,
    schema_version: str,
) -> SelectionResultV3:
    """Only eligible terminal normal evaluations compete, never failed/unknown."""
    if not isinstance(policy, V3Policy) or policy.execution_mode != "fixture":
        raise ValueError("fixture V3Policy required")
    return _select_best_v3(
        candidates,
        numeric=policy.numeric,
        policy_version=policy.policy_version,
        run_id=run_id,
        schema_version=schema_version,
    )


def _select_best_v3(
    candidates: Sequence[Mapping[str, object]],
    *,
    numeric: NumericPolicy,
    policy_version: str,
    run_id: str,
    schema_version: str,
) -> SelectionResultV3:
    """Shared pure ordering core; public entry points own policy admission."""
    if not run_id.strip() or not schema_version.strip():
        raise ValueError("explicit run/schema version required")
    seen = set()
    normal = []
    for item in candidates:
        cid = item["candidate_id"]
        if not isinstance(cid, str) or not cid.strip() or cid in seen:
            raise ValueError("duplicate or invalid raw candidate ID")
        seen.add(cid)
        if item["eligibility_status"] != "eligible" or item["status"] != "evaluated":
            continue
        label = item["label"]
        if label not in numeric.labels_descending:
            raise ValueError("invalid v3 label")
        score = _exact(item["normalized_score"])
        missing = _exact(item["weighted_missing_pct"])
        denominator = _exact(item["applicable_weight"])
        if denominator <= 0:
            raise ValueError("undefined score denominator")
        sid = item["score_summary_id"]
        if not isinstance(sid, str) or not sid.strip():
            raise ValueError("missing score summary ID")
        normal.append((cid, label, score, missing, denominator, sid))
    ordered = sorted(
        normal,
        key=lambda row: (
            numeric.labels_descending.index(row[1]),
            -Fraction(row[2]),
            Fraction(row[3]),
            row[0],
        ),
    )
    winner = next(
        (row[0] for row in ordered if row[1] in ("RECOMMEND_PRIORITY", "RECOMMEND")),
        None,
    )
    return SelectionResultV3(
        schema_version=schema_version,
        run_id=run_id,
        policy_version=policy_version,
        considered_candidate_ids=tuple(row[0] for row in ordered),
        compared_score_summary_ids=tuple(row[5] for row in ordered),
        selected_candidate_id=winner,
        reason="SELECTED"
        if winner
        else "NO_RECOMMENDATION"
        if ordered
        else "NO_ELIGIBLE_RESULTS",
    )
