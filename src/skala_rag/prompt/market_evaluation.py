"""Versioned prompt for bounded Market evaluation (#59)."""

from skala_rag.prompt._resources import read_prompt
from skala_rag.prompt.versions import MARKET_EVALUATION_VERSION

PROMPT_VERSION = MARKET_EVALUATION_VERSION

SYSTEM_PROMPT = read_prompt("market_evaluation.json")


__all__ = [
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
]
