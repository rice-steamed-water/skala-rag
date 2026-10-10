"""Existing logical prompt versions and execution composition labels."""

from typing import Final

ELIGIBILITY_FACTS_VERSION: Final = "eligibility-facts-v1"
COMPANY_REPORT_FRESHNESS_VERSION: Final = "company-report-freshness-v1"
EVIDENCE_EXTRACTION_VERSION: Final = "evidence-extraction-v1"
TECHNOLOGY_EVALUATION_VERSION: Final = "technology-evaluation-v1"
MOAT_EVALUATION_VERSION: Final = "moat-evaluation-v2"
MARKET_EVALUATION_VERSION: Final = "market-evaluation-v2"
BUSINESS_DEAL_EVALUATION_VERSION: Final = "business-deal-evaluation-v2"
REPORT_PROMPT_VERSION: Final = "report-v3-3"

# These label composed execution requests, not each authored instruction layer.
LOCAL_DEMO_COMPOSITION_VERSION: Final = "local-demo-1"
ACTUAL_COMPOSITION_VERSION: Final = "actual-v3-1"

__all__ = [
    "ELIGIBILITY_FACTS_VERSION",
    "COMPANY_REPORT_FRESHNESS_VERSION",
    "EVIDENCE_EXTRACTION_VERSION",
    "TECHNOLOGY_EVALUATION_VERSION",
    "MOAT_EVALUATION_VERSION",
    "MARKET_EVALUATION_VERSION",
    "BUSINESS_DEAL_EVALUATION_VERSION",
    "REPORT_PROMPT_VERSION",
    "LOCAL_DEMO_COMPOSITION_VERSION",
    "ACTUAL_COMPOSITION_VERSION",
]
