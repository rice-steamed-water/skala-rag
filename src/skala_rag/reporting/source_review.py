"""Detached, unscored source review input; no research/scoring admission."""

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime

from skala_rag.contracts.common import JSONMap
from skala_rag.contracts.evidence import Evidence
from skala_rag.contracts.sources import Source
from skala_rag.reporting.company_context import canonical


def validate_source_review(
    capsule: dict,
    *,
    expected_input_id: str,
    expected_sources: Mapping[str, dict],
) -> JSONMap:
    """Caller pins the whole capsule and exact Source payloads outside model output.

    source_texts are offline extractions, not fetched here. Raw capture integrity
    and semantic attribution remain caller responsibilities, not hash authority.
    """
    encoded = canonical(capsule)
    if "sha256:" + hashlib.sha256(encoded.encode()).hexdigest() != expected_input_id:
        raise ValueError("source review input mismatch")

    data = json.loads(encoded)
    if set(data) != {
        "schema_version",
        "run_id",
        "as_of",
        "execution_mode",
        "research_subject",
        "sources",
        "source_texts",
        "evidence",
    }:
        raise ValueError("source review input shape mismatch")
    if data["research_subject"] != "Dexory" or data["execution_mode"] not in (
        "live",
        "fixture",
    ):
        raise ValueError("explicit Dexory source review required")
    cutoff = date.fromisoformat(data["as_of"])
    if cutoff.isoformat() != data["as_of"] or any(
        not isinstance(data[k], str) or not data[k].strip()
        for k in ("schema_version", "run_id")
    ):
        raise ValueError("source review identity invalid")
    sources = {
        sid: Source.model_validate(
            s, context={"execution_mode": data["execution_mode"]}
        )
        for sid, s in data["sources"].items()
    }
    if (
        not sources
        or canonical(data["sources"]) != canonical(dict(expected_sources))
        or set(sources) != set(data["source_texts"])
    ):
        raise ValueError("source review source mismatch")
    for sid, source in sources.items():
        published = source.published_at
        published_day = (
            published.date() if isinstance(published, datetime) else published
        )
        if (
            sid != source.source_id
            or source.schema_version != data["schema_version"]
            or not source.url
            or source.retrieved_at.date() > cutoff
            or (published_day is not None and published_day > cutoff)
            or not isinstance(data["source_texts"][sid], str)
            or not data["source_texts"][sid].strip()
        ):
            raise ValueError("source review source identity/date invalid")
    evidence = {
        eid: Evidence.model_validate(
            e, context={"execution_mode": data["execution_mode"]}
        )
        for eid, e in data["evidence"].items()
    }
    if not evidence:
        raise ValueError("source review needs quoted evidence")
    for eid, item in evidence.items():
        if (
            eid != item.evidence_id
            or item.schema_version != data["schema_version"]
            or item.source_id not in sources
            or item.candidate_id != "dexory"
            or item.scope != "company"
            or item.evidence_kind != "reported"
            or item.claim != item.excerpt
            or item.excerpt not in data["source_texts"][item.source_id]
            or item.locator != sources[item.source_id].url
            or item.criterion_ids
            or item.provenance
            or item.supporting_evidence_ids
            or item.conflicts_with
            or item.supersedes is not None
            or item.derivation is not None
            or item.value is not None
            or item.currency is not None
            or item.unit is not None
            or item.period is not None
            or item.geography is not None
            or item.confidence != "unknown"
            or not item.limitations
            or any(
                d is not None and d > cutoff
                for d in (item.event_date, item.value_as_of)
            )
        ):
            raise ValueError("source review quote closure invalid")
    return data
