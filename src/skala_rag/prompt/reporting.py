"""Report prompt text and request payload builders."""

from collections.abc import Iterable, Mapping
from typing import Any

from skala_rag.prompt._resources import read_prompt
from skala_rag.prompt.versions import REPORT_PROMPT_VERSION

PROMPT_VERSION = REPORT_PROMPT_VERSION
GENERATOR_SYSTEM = read_prompt("report_generator.json")
JUDGE_SYSTEM = read_prompt("report_judge.json")
SOURCE_REVIEW_GENERATOR = read_prompt("report_source_review_generator.json")
SOURCE_REVIEW_JUDGE = read_prompt("report_source_review_judge.json")

__all__ = [
    "GENERATOR_SYSTEM",
    "JUDGE_SYSTEM",
    "PROMPT_VERSION",
    "build_generator_payload",
    "build_judge_payload",
]


def build_generator_payload(
    *,
    context_id: str,
    context: Mapping[str, Any],
    feedback: Iterable[str],
) -> dict[str, Any]:
    """Build the exact generator request fields from already computed inputs."""
    payload = {
        "prompt_version": PROMPT_VERSION,
        "context_id": context_id,
        "context": context,
        "feedback": list(feedback),
    }
    if context.get("mode") == "source_review":
        payload.update(
            {
                "required_output_schema_version": context["schema_version"],
                "required_citation_tokens": [
                    f"[@evidence:{evidence_id}]"
                    for evidence_id in sorted(context["evidence"])
                ],
                "source_review": SOURCE_REVIEW_GENERATOR,
            }
        )
    return payload


def build_judge_payload(
    *,
    context_id: str,
    artifact_hash: str,
    context: Mapping[str, Any],
    draft: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the exact judge request fields from the pinned context and draft."""
    payload = {
        "prompt_version": PROMPT_VERSION,
        "context_id": context_id,
        "artifact_hash": artifact_hash,
        "context": context,
        "draft": draft,
    }
    if context.get("mode") == "source_review":
        payload.update(
            {
                "required_output_schema_version": context["schema_version"],
                "source_review": SOURCE_REVIEW_JUDGE,
            }
        )
    return payload
