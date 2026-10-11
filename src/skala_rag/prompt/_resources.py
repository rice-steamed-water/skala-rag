"""Read fixed package resources without changing model-visible text."""

import json
from importlib.resources import files
from typing import Final

_PROMPT_NAMES: Final = frozenset(
    {
        "eligibility_facts.json",
        "company_report_freshness.json",
        "company_analysis.v1.json",
        "narrative_composition.v1.json",
        "narrative_judge.v1.json",
        "evidence_extraction.json",
        "technology_evaluation.json",
        "moat_evaluation.json",
        "market_evaluation.json",
        "business_deal_evaluation.json",
        "evaluation_system.json",
        "evaluation_repair.json",
        "report_generator.json",
        "report_judge.json",
        "report_source_review_generator.json",
        "report_source_review_judge.json",
        "demo_reviewer.json",
        "demo_common.json",
        "demo_generator.json",
        "demo_judge.json",
    }
)

__all__ = ["read_prompt", "read_questions"]


def read_questions() -> str:
    """Return the fixed versioned narrative question resource for DTO parsing."""
    return (
        files("skala_rag.prompt")
        .joinpath("text", "company_analysis_questions.v1.json")
        .read_bytes()
        .decode("utf-8")
    )


def read_prompt(name: str) -> str:
    """Join literal fragments from a known UTF-8 JSON resource; fail closed."""
    if name not in _PROMPT_NAMES:
        raise ValueError(f"Unknown prompt resource: {name}")
    resource = files("skala_rag.prompt").joinpath("text", name)
    fragments = json.loads(resource.read_bytes().decode("utf-8"))
    if not isinstance(fragments, list) or not fragments:
        raise ValueError(f"Prompt resource must be a nonempty JSON array: {name}")
    for fragment in fragments:
        if not isinstance(fragment, str):
            raise ValueError(f"Prompt resource fragments must be strings: {name}")
    return "".join(fragments)
