"""Versioned snapshot-only atomic Business & Deal prompt."""

import json

from skala_rag.prompt._resources import read_prompt
from skala_rag.prompt.versions import BUSINESS_DEAL_EVALUATION_VERSION

PROMPT_VERSION = BUSINESS_DEAL_EVALUATION_VERSION
SYSTEM_PROMPT = read_prompt("business_deal_evaluation.json")


def build_user_prompt(snapshot, rubric, policy):
    return json.dumps(
        {
            "prompt_version": PROMPT_VERSION,
            "snapshot_id": snapshot.snapshot_id,
            "as_of": snapshot.as_of,
            "rubric": rubric,
            "criteria": {
                d: sorted(c.criterion_id for c in policy.criteria if c.dimension == d)
                for d in ("traction", "deal_terms")
            },
            "evidence": [
                {
                    "evidence_id": e.evidence_id,
                    "criterion_ids": e.criterion_ids,
                    "value": e.value,
                    "unit": e.unit,
                    "currency": e.currency,
                    "period": e.period,
                    "value_as_of": e.value_as_of,
                    "source_id": e.source_id,
                    "supporting_evidence_ids": e.supporting_evidence_ids,
                    "untrusted_source_text": {
                        "claim": e.claim,
                        "excerpt": e.excerpt,
                        "limitations": e.limitations,
                        "derivation": e.derivation,
                    },
                }
                for e in sorted(snapshot.evidence.values(), key=lambda e: e.evidence_id)
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


__all__ = [
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "build_user_prompt",
]
