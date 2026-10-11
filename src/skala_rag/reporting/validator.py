"""Shared citation tokens and exact report-artifact hashing for persistence/PDF."""

import hashlib
import json
import re

from skala_rag.contracts import ReportDraft

TOKEN = re.compile(r"\[@evidence:([^\]\s]+)\]")


def artifact_hash(draft: ReportDraft) -> str:
    """검증 대상 draft의 hash. 문장·metadata가 바뀌면 달라진다."""
    payload = json.dumps(
        {
            "context_id": draft.context_id,
            "revision": draft.revision,
            "markdown": draft.markdown,
            "cited_evidence_ids": draft.cited_evidence_ids,
            "reference_source_ids": draft.reference_source_ids,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
