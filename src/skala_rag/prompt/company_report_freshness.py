"""Versioned, structured freshness proposals; never collection authority."""

import json
from collections.abc import Mapping
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from skala_rag.contracts import Evidence, Source
from skala_rag.contracts.common import Text
from skala_rag.prompt._resources import read_prompt
from skala_rag.prompt.versions import COMPANY_REPORT_FRESHNESS_VERSION

PROMPT_VERSION = COMPANY_REPORT_FRESHNESS_VERSION
SYSTEM_PROMPT = read_prompt("company_report_freshness.json")


class GapQuery(BaseModel):
    """A subject and topic, not arbitrary web-search text or a model-selected URL."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    candidate_id: Text | None
    scope: Literal["company", "industry"]
    field: Literal[
        "identity",
        "business",
        "domain_match",
        "is_listed",
        "exit_completed",
        "stage",
        "founder",
        "technology",
        "market",
        "moat",
        "traction",
        "deal_terms",
    ]


class FreshnessJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    evidence_id: Text
    status: Literal["current", "needs_update", "unknown"]
    reason: Text
    gap_queries: tuple[GapQuery, ...]


class FreshnessOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    judgments: tuple[FreshnessJudgment, ...]


def build_user_prompt(
    evidence: Mapping[str, Evidence],
    sources: Mapping[str, Source],
    *,
    as_of: date,
) -> str:
    return json.dumps(
        {
            "prompt_version": PROMPT_VERSION,
            "as_of": as_of.isoformat(),
            "untrusted_evidence": {
                key: value.model_dump(mode="json")
                for key, value in sorted(evidence.items())
            },
            "untrusted_sources": {
                key: value.model_dump(mode="json")
                for key, value in sorted(sources.items())
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
