"""Exact demo instructions; schemas and scoring remain in the consumer."""

from skala_rag.prompt._resources import read_prompt

REVIEWER_SYSTEM = read_prompt("demo_reviewer.json")
COMMON_SUFFIX = read_prompt("demo_common.json")
GENERATOR_SUFFIX = read_prompt("demo_generator.json")
JUDGE_SUFFIX = read_prompt("demo_judge.json")


def common_suffix(schema_version: str) -> str:
    return COMMON_SUFFIX.replace("{schema_version}", schema_version)
