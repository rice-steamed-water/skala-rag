"""Caller-reviewed financial facts; no prose classification or approval token.

Receipts bind human-reviewed metric roles to exact frozen Evidence payloads.
This validates attribution/context only, not rubric rating correspondence.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from skala_rag.contracts.common import Count, Text
from skala_rag.contracts.evaluation import EvaluationSnapshot
from skala_rag.contracts.evidence import Evidence
from skala_rag.scoring.finance import Period, Unavailable, parse_period, to_amount


class ReviewedFinancialFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_id: Text
    snapshot_id: Text
    candidate_id: Text
    evaluation_round: Count
    evidence_revision: Count
    policy_version: Text
    reviewer_reference: Text
    accounting_entity: Text
    metric_role: Literal[
        "revenue",
        "cost_of_revenue",
        "operating_cash_flow",
        "cash",
        "funding",
        "valuation",
        "investment",
        "ownership",
        "stage",
        "customer_revenue",
        "operating_margin",
        "confirmed_pre_revenue",
    ]
    funding_round: Text | None
    valuation_basis: Literal["pre_money", "post_money"] | None
    period: Period | None
    evidence: Evidence


def validate_financial_facts(
    facts: tuple[ReviewedFinancialFact, ...], snapshot: EvaluationSnapshot
) -> dict[str, ReviewedFinancialFact]:
    """Reject stale, foreign, altered, unconfirmed or contextless receipts.

    The reviewer supplies semantic facts explicitly. A reviewer reference is an
    audit pointer, not authenticated approval. No source claim is parsed.
    """
    result = {}
    for fact in facts:
        for key in (
            "run_id",
            "snapshot_id",
            "candidate_id",
            "evaluation_round",
            "evidence_revision",
            "policy_version",
        ):
            if getattr(fact, key) != getattr(snapshot, key):
                raise ValueError("financial fact snapshot mismatch")
        e = fact.evidence
        if e.evidence_id in result:
            raise ValueError("duplicate financial fact")
        frozen = snapshot.evidence.get(e.evidence_id)
        if frozen is None or frozen.model_dump() != e.model_dump():
            raise ValueError("financial fact differs from frozen evidence")
        source = snapshot.sources.get(e.source_id)
        if (
            source is None
            or source.source_id != e.source_id
            or e.scope != "company"
            or e.candidate_id != snapshot.candidate_id
            or e.conflicts_with
            or e.evidence_kind != "reported"
            or not set(e.supporting_evidence_ids) <= set(snapshot.evidence)
        ):
            raise ValueError("unconfirmed financial fact")
        if fact.accounting_entity != snapshot.candidate_id:
            raise ValueError("financial accounting entity mismatch")
        if fact.period != parse_period(e.period):
            raise ValueError("financial period mismatch")
        if fact.period is not None and fact.period.end > snapshot.as_of:
            raise ValueError("financial period after snapshot")
        if e.value_as_of is not None and e.value_as_of > snapshot.as_of:
            raise ValueError("financial fact after snapshot")
        if (
            fact.metric_role
            in {
                "revenue",
                "cost_of_revenue",
                "operating_cash_flow",
                "customer_revenue",
                "operating_margin",
                "confirmed_pre_revenue",
            }
            and fact.period is None
        ):
            raise ValueError("financial accounting period required")
        if fact.metric_role in {
            "valuation",
            "investment",
            "ownership",
            "funding",
            "stage",
        }:
            if fact.funding_round is None:
                raise ValueError("financial round required")
        if (
            fact.metric_role in {"valuation", "ownership"}
            and fact.valuation_basis is None
        ):
            raise ValueError("pre/post valuation basis required")
        if fact.metric_role in {
            "revenue",
            "cost_of_revenue",
            "operating_cash_flow",
            "cash",
            "funding",
            "valuation",
            "investment",
            "customer_revenue",
        } and isinstance(to_amount(e), Unavailable):
            raise ValueError("financial currency/unit/value required")
        if fact.metric_role == "confirmed_pre_revenue" and e.value != 0:
            raise ValueError("confirmed zero revenue required")
        result[e.evidence_id] = fact
    return result
