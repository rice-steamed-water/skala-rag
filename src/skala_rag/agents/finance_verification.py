"""Caller-reviewed financial facts; no prose classification or approval token.

Receipts bind human-reviewed metric roles to exact frozen Evidence payloads.
This validates attribution/context only, not rubric rating correspondence.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal, localcontext
from typing import Any, Literal

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
        "top1_customer_revenue_share",
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
        if e.value is not None and not Decimal(str(e.value)).is_finite():
            raise ValueError("finite financial value required")
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
        if e.event_date is not None and e.event_date > snapshot.as_of:
            raise ValueError("financial event after snapshot")
        if (
            fact.metric_role
            in {
                "revenue",
                "cost_of_revenue",
                "operating_cash_flow",
                "customer_revenue",
                "top1_customer_revenue_share",
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


def validate_financial_rating(
    criterion_id: str,
    rating: int,
    evidence_ids: Sequence[str],
    snapshot: EvaluationSnapshot,
    facts: Mapping[str, ReviewedFinancialFact],
    rubric: Mapping[str, Any],
) -> None:
    """Bind approved anchors to cited reviewed facts without creating Evidence."""

    from skala_rag.scoring.finance import (
        gross_margin_pct,
        revenue_growth_pct,
        runway_months,
    )

    cited = [snapshot.evidence[eid] for eid in evidence_ids]
    derived = [e for e in cited if e.evidence_kind == "derived"]
    input_ids = {e.evidence_id for e in cited if e.evidence_kind == "reported"}
    for e in derived:
        if not e.supporting_evidence_ids or not e.derivation:
            raise ValueError("derived support/formula required")
        input_ids.update(e.supporting_evidence_ids)
    if not input_ids or not input_ids <= facts.keys():
        raise ValueError("cited reviewed reported facts required")
    selected = [facts[eid] for eid in sorted(input_ids)]
    if any(f.evidence.supporting_evidence_ids for f in selected):
        raise ValueError("reported supporting formula unsupported")
    roles = {}
    for f in selected:
        roles.setdefault(f.metric_role, []).append(f)

    def one(role):
        if len(roles.get(role, [])) != 1:
            raise ValueError("unique reviewed metric required")
        return roles[role][0]

    def amount(f):
        a = to_amount(f.evidence)
        if isinstance(a, Unavailable) or not a.value.is_finite():
            raise ValueError("invalid amount")
        return a

    def percent(f):
        e = f.evidence
        if e.unit != "percent" or e.currency is not None or e.value is None:
            raise ValueError("explicit percent required")
        v = Decimal(str(e.value))
        if not v.is_finite():
            raise ValueError("invalid percent")
        return v

    def growth():
        rev = roles.get("revenue", [])
        if len(rev) != 2 or any(f.period is None or f.period.months != 12 for f in rev):
            raise ValueError("two annual revenue periods required for YoY")
        prior, current = sorted(rev, key=lambda f: f.period.start)
        return revenue_growth_pct(current.evidence, prior.evidence), current

    result = None
    value = None
    expected = None
    with localcontext() as ctx:
        ctx.prec = 28
        if criterion_id == "traction.revenue_growth":
            required = {"revenue"}
            result, _ = growth()
        elif criterion_id == "traction.gross_margin":
            required = {"revenue", "cost_of_revenue"}
            result = gross_margin_pct(
                one("revenue").evidence, one("cost_of_revenue").evidence
            )
        elif criterion_id == "traction.runway":
            required = {"cash", "operating_cash_flow"}
            if "funding" in roles:
                required.add("funding")
            cash = one("cash")
            if amount(cash).value < 0 or any(
                amount(f).value <= 0 for f in roles.get("funding", [])
            ):
                raise ValueError("invalid cash/funding")
            result = runway_months(
                cash.evidence,
                one("operating_cash_flow").evidence,
                snapshot.as_of,
                [f.evidence for f in roles.get("funding", [])],
            )
        elif criterion_id == "traction.rule_of_40":
            required = {"revenue", "operating_margin"}
            g, current = growth()
            if isinstance(g, Unavailable):
                raise ValueError(g.reason)
            margin = one("operating_margin")
            if margin.period != current.period:
                raise ValueError("period_mismatch")
            value = g.value + percent(margin)
        elif criterion_id == "traction.concentration":
            required = {"top1_customer_revenue_share"}
            value = percent(one("top1_customer_revenue_share"))
            if not 0 <= value <= 100:
                raise ValueError("invalid concentration")
        elif criterion_id == "deal_terms.ownership":
            if set(roles) == {"ownership"}:
                required = {"ownership"}
                f = one("ownership")
                if f.valuation_basis != "post_money" or f.evidence.event_date is None:
                    raise ValueError("confirmed transaction context required")
                value = percent(f)
            else:
                required = {"valuation", "investment"}
                val, inv = one("valuation"), one("investment")
                a, b = amount(val), amount(inv)
                if (
                    val.funding_round != inv.funding_round
                    or val.evidence.event_date is None
                    or val.evidence.event_date != inv.evidence.event_date
                    or a.currency != b.currency
                    or a.value <= 0
                    or b.value <= 0
                ):
                    raise ValueError("round/date/currency/base mismatch")
                post = (
                    a.value + b.value if val.valuation_basis == "pre_money" else a.value
                )
                value = b.value / post * 100
            if not 0 < value <= 100:
                raise ValueError("invalid ownership")
        elif criterion_id == "traction.burn":
            required = {"operating_cash_flow"}
            flows = roles.get("operating_cash_flow", [])
            if len(flows) == 1:
                if amount(flows[0]).value < 0:
                    raise ValueError("trend_unavailable")
                expected = 5
            elif len(flows) == 2:
                required.add("revenue")
                g, current = growth()
                if isinstance(g, Unavailable):
                    raise ValueError(g.reason)
                prior, cur = sorted(flows, key=lambda f: f.period.start)
                revenues = sorted(roles["revenue"], key=lambda f: f.period.start)
                if [prior.period, cur.period] != [f.period for f in revenues]:
                    raise ValueError("period_mismatch")
                a, b = amount(prior), amount(cur)
                if a.currency != b.currency or a.currency != amount(current).currency:
                    raise ValueError("currency_mismatch")
                if b.value >= 0:
                    expected = 5
                elif a.value >= 0:
                    raise ValueError("non_positive_base")
                elif b.value > a.value:
                    expected = 4
                elif b.value == a.value:
                    raise ValueError("flat burn anchor unspecified")
                elif g.value < 0:
                    expected = 1
                else:
                    burn_growth = (b.value - a.value) / a.value * 100
                    expected = 2 if burn_growth > g.value else 3
            else:
                raise ValueError("trend_unavailable")
        else:
            raise ValueError("structured qualitative anchor unavailable")
        if set(roles) != required:
            raise ValueError("unexpected cited metric roles")
        if isinstance(result, Unavailable):
            raise ValueError(result.reason)
        if result is not None:
            value = result.value
        for e in derived:
            if result is None:
                raise ValueError("composite derived formula unsupported")
            if (
                e.value is None
                or Decimal(str(e.value)) != result.value
                or e.unit != result.unit
                or e.currency != result.currency
                or e.period != result.period
                or e.value_as_of != result.value_as_of
                or set(e.supporting_evidence_ids) != set(result.supporting_evidence_ids)
                or e.derivation != result.derivation
            ):
                raise ValueError("derived formula/context mismatch")
        if expected is None:
            if value is None or not value.is_finite():
                raise ValueError("invalid metric")
            bands = rubric["dimensions"][criterion_id.split(".")[0]]["criteria"][
                criterion_id
            ]["bands"]
            matches = [
                b["rating"]
                for b in bands
                if (b.get("min") is None or value >= Decimal(str(b["min"])))
                and (b.get("max") is None or value < Decimal(str(b["max"])))
            ]
            if len(matches) != 1:
                raise ValueError("ambiguous approved band")
            expected = matches[0]
        if rating != expected:
            raise ValueError("rating does not match approved anchor")
