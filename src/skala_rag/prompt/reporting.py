"""Report prompt text and request payload builders."""

import json

from skala_rag.contracts.company_report import (
    AnalysisReply,
    CompositionReply,
    WriterContext,
)
from skala_rag.prompt._resources import read_prompt

NARRATIVE_COMPOSITION_SYSTEM = read_prompt("narrative_composition.v1.json")
NARRATIVE_JUDGE_SYSTEM = read_prompt("narrative_judge.v1.json")


def build_narrative_payload(
    writer: WriterContext,
    analysis: AnalysisReply,
    *,
    headings: tuple[str, ...],
    feedback: tuple[str, ...] = (),
    composition: CompositionReply | None = None,
    artifact_hash: str | None = None,
) -> str:
    """Serialize only admitted writer facts and contextual narrative replies."""
    payload = {
        "prompt_version": (
            "narrative-composition-1" if composition is None else "narrative-judge-1"
        ),
        "writer_context": writer.model_dump(mode="json"),
        "analysis": analysis.model_dump(mode="json"),
        "headings": list(headings),
        "feedback_codes": list(feedback),
    }
    if composition is not None:
        payload["composition"] = composition.model_dump(mode="json")
        payload["artifact_hash"] = artifact_hash
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
