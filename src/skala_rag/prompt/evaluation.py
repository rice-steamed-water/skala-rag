"""Generic evaluator prompts and deterministic JSON payload construction."""

import json
from collections.abc import Mapping

from skala_rag.contracts.evaluation import Dimension, EvaluationSnapshot
from skala_rag.prompt._resources import read_prompt
from skala_rag.scoring.approved_policy import ApprovedScoringPolicy
from skala_rag.scoring.catalog import ScoringPolicy

SYSTEM_PROMPT = read_prompt("evaluation_system.json")
REPAIR_PROMPT = read_prompt("evaluation_repair.json")


def build_user_prompt(
    dimension: Dimension,
    snapshot: EvaluationSnapshot,
    rubric: Mapping[str, object],
    policy: ScoringPolicy | ApprovedScoringPolicy,
    context: Mapping[str, object] | None = None,
) -> str:
    """Serialize the selected rubric, evidence, and optional context as JSON."""
    dimensions = rubric.get("dimensions")
    rubric_dimension = (
        dimensions.get(dimension, {}) if isinstance(dimensions, Mapping) else {}
    )
    payload = {
        "dimension": dimension,
        "criteria": [
            criterion.criterion_id
            for criterion in policy.criteria
            if criterion.dimension == dimension
        ],
        "rubric_version": rubric.get("rubric_version"),
        "rubric": rubric_dimension,
        "evidence": [
            {
                "evidence_id": evidence.evidence_id,
                "scope": evidence.scope,
                "criterion_ids": evidence.criterion_ids,
                "claim": evidence.claim,
                "excerpt": evidence.excerpt,
                "value": evidence.value,
                "unit": evidence.unit,
                "currency": evidence.currency,
                "period": evidence.period,
                "evidence_kind": evidence.evidence_kind,
                "limitations": evidence.limitations,
            }
            for evidence in snapshot.evidence.values()
        ],
    }
    if context is not None:
        payload["context"] = dict(context)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


__all__ = ["SYSTEM_PROMPT", "REPAIR_PROMPT", "build_user_prompt"]
