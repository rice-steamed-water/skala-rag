"""Receipt checks are offline fixtures, not authenticated human review."""

from pathlib import Path

import pytest
from tests.fixtures.loader import load_common_fixtures

from skala_rag.agents.finance_verification import (
    ReviewedFinancialFact,
    validate_financial_facts,
)
from skala_rag.scoring.catalog import load_policy
from skala_rag.scoring.finance import parse_period


@pytest.fixture
def receipt():
    policy = load_policy(Path("configs/scoring.draft.json"), execution_mode="fixture")
    snapshot = next(iter(load_common_fixtures(policy).snapshots.values()))
    e = next(iter(snapshot.evidence.values())).model_copy(deep=True)
    e.locator = "https://example.com/offline-fixture"
    e.evidence_kind = "reported"
    e.value = -120
    e.currency = "KRW"
    e.unit = "one"
    e.period = "2026-01-01/2026-08-31"
    e.value_as_of = parse_period(e.period).end
    e.conflicts_with = []
    snapshot.evidence[e.evidence_id] = e
    fact = ReviewedFinancialFact(
        **{
            k: getattr(snapshot, k)
            for k in (
                "run_id",
                "snapshot_id",
                "candidate_id",
                "evaluation_round",
                "evidence_revision",
                "policy_version",
            )
        },
        reviewer_reference="fixture:review",
        accounting_entity=snapshot.candidate_id,
        metric_role="operating_cash_flow",
        funding_round=None,
        valuation_basis=None,
        period=parse_period(e.period),
        evidence=e,
    )
    return snapshot, fact


def test_observed_burn_anchor(receipt):
    import yaml

    from skala_rag.agents.finance_verification import validate_financial_rating

    snapshot, fact = receipt
    e = fact.evidence.model_copy(update={"value": 0})
    snapshot.evidence[e.evidence_id] = e
    fact = fact.model_copy(update={"evidence": e})
    rubric = yaml.safe_load(Path("configs/rubrics/finance.yaml").read_text())
    validate_financial_rating(
        "traction.burn",
        5,
        [e.evidence_id],
        snapshot,
        validate_financial_facts((fact,), snapshot),
        rubric,
    )
    with pytest.raises(ValueError):
        validate_financial_rating(
            "traction.burn",
            4,
            [e.evidence_id],
            snapshot,
            validate_financial_facts((fact,), snapshot),
            rubric,
        )


@pytest.fixture
def numeric_case(receipt):
    import yaml

    snapshot, template = receipt
    rubric = yaml.safe_load(Path("configs/rubrics/finance.yaml").read_text())

    def make(role, value, period="2025-01-01/2025-12-31", **updates):
        eid = f"fixture:{role}:{len(snapshot.evidence)}"
        e = template.evidence.model_copy(
            update={
                "evidence_id": eid,
                "value": value,
                "period": period,
                "value_as_of": parse_period(period).end if period else snapshot.as_of,
                "supporting_evidence_ids": [],
                **updates,
            }
        )
        snapshot.evidence[eid] = e
        snapshot.evidence_ids.append(eid)
        return template.model_copy(
            update={
                "metric_role": role,
                "period": parse_period(period),
                "evidence": e,
                "funding_round": "Series A"
                if role in {"investment", "valuation", "ownership", "funding"}
                else None,
                "valuation_basis": "post_money"
                if role in {"valuation", "ownership"}
                else None,
            }
        )

    return snapshot, rubric, make


def check_numeric(case, cid, rating, items):
    from skala_rag.agents.finance_verification import validate_financial_rating

    snapshot, rubric, _ = case
    facts = validate_financial_facts(tuple(items), snapshot)
    validate_financial_rating(
        cid, rating, [f.evidence.evidence_id for f in items], snapshot, facts, rubric
    )


@pytest.mark.parametrize(
    "cid, rating",
    [
        ("traction.revenue_growth", 4),
        ("traction.gross_margin", 5),
        ("traction.runway", 5),
        ("traction.concentration", 4),
        ("traction.rule_of_40", 5),
        ("deal_terms.ownership", 5),
    ],
)
def test_numeric_anchors(numeric_case, cid, rating):
    s, _, make = numeric_case
    rev = make("revenue", 150)
    if cid in {"traction.revenue_growth", "traction.rule_of_40"}:
        items = [rev, make("revenue", 100, "2024-01-01/2024-12-31")]
        if cid.endswith("rule_of_40"):
            items.append(make("operating_margin", 10, unit="percent", currency=None))
    elif cid.endswith("gross_margin"):
        items = [rev, make("cost_of_revenue", 75)]
    elif cid.endswith("runway"):
        items = [make("cash", 400), make("operating_cash_flow", -120)]
    elif cid.endswith("concentration"):
        items = [make("top1_customer_revenue_share", 15, unit="percent", currency=None)]
    else:
        items = [
            make("valuation", 100, event_date=s.as_of),
            make("investment", 10, event_date=s.as_of),
        ]
    check_numeric(numeric_case, cid, rating, items)
    with pytest.raises(ValueError, match="rating"):
        check_numeric(numeric_case, cid, 1, items)


@pytest.mark.parametrize(
    "fault", ["currency", "period", "zero", "negative", "unreviewed", "support"]
)
def test_growth_rejects_bad_inputs(numeric_case, fault):
    _, _, make = numeric_case
    current = make("revenue", 120)
    prior = make("revenue", 100, "2024-01-01/2024-12-31")
    if fault in {"zero", "negative"}:
        prior = make("revenue", 0 if fault == "zero" else -100, "2024-01-01/2024-12-31")
    elif fault == "currency":
        prior = make("revenue", 100, "2024-01-01/2024-12-31", currency="USD")
    elif fault == "period":
        prior = make("revenue", 100, "2024-01-01/2024-06-30")
    elif fault == "support":
        current = make(
            "revenue", 120, supporting_evidence_ids=[prior.evidence.evidence_id]
        )
    items = [current] if fault == "unreviewed" else [current, prior]
    with pytest.raises(ValueError):
        check_numeric(numeric_case, "traction.revenue_growth", 3, items)


def test_pre_rounding_cutoff(numeric_case):
    _, _, make = numeric_case
    items = [make("revenue", 119.999999), make("revenue", 100, "2024-01-01/2024-12-31")]
    check_numeric(numeric_case, "traction.revenue_growth", 2, items)
    with pytest.raises(ValueError):
        check_numeric(numeric_case, "traction.revenue_growth", 3, items)


@pytest.mark.parametrize(
    "cid", ["deal_terms.stage", "deal_terms.valuation", "traction.concentration"]
)
def test_no_prose_or_generic_customer_anchors(numeric_case, cid):
    _, _, make = numeric_case
    with pytest.raises(ValueError):
        check_numeric(numeric_case, cid, 3, [make("customer_revenue", 10)])


def test_derived_formula_validation(numeric_case):
    from skala_rag.agents.finance_verification import validate_financial_rating
    from skala_rag.scoring.finance import gross_margin_pct

    snapshot, rubric, make = numeric_case
    rev, cost = make("revenue", 100), make("cost_of_revenue", 50)
    result = gross_margin_pct(rev.evidence, cost.evidence)
    e = rev.evidence.model_copy(
        update={
            "evidence_id": "fixture:derived",
            "evidence_kind": "derived",
            "value": float(result.value),
            "unit": result.unit,
            "currency": result.currency,
            "period": result.period,
            "value_as_of": result.value_as_of,
            "supporting_evidence_ids": list(result.supporting_evidence_ids),
            "derivation": result.derivation,
        }
    )
    snapshot.evidence[e.evidence_id] = e
    facts = validate_financial_facts((rev, cost), snapshot)
    validate_financial_rating(
        "traction.gross_margin", 5, [e.evidence_id], snapshot, facts, rubric
    )
    snapshot.evidence[e.evidence_id] = e.model_copy(update={"value": 51})
    with pytest.raises(ValueError, match="derived formula"):
        validate_financial_rating(
            "traction.gross_margin", 5, [e.evidence_id], snapshot, facts, rubric
        )


@pytest.mark.parametrize(
    "current_revenue,current_ocf,rating",
    [
        (90, -150, 1),
        (110, -150, 2),
        (150, -120, 3),
        (150, -80, 4),
        (150, 0, 5),
    ],
)
def test_burn_exact_annual_trend(numeric_case, current_revenue, current_ocf, rating):
    _, _, make = numeric_case
    items = [
        make("revenue", current_revenue),
        make("revenue", 100, "2024-01-01/2024-12-31"),
        make("operating_cash_flow", current_ocf),
        make("operating_cash_flow", -100, "2024-01-01/2024-12-31"),
    ]
    check_numeric(numeric_case, "traction.burn", rating, items)


@pytest.mark.parametrize("fault", ["round", "date", "currency", "base"])
def test_ownership_context_rejects(numeric_case, fault):
    snapshot, _, make = numeric_case
    val = make("valuation", 100, event_date=snapshot.as_of)
    inv = make("investment", 10, event_date=snapshot.as_of)
    if fault == "round":
        inv = inv.model_copy(update={"funding_round": "Series B"})
    elif fault == "date":
        inv = make("investment", 10, event_date=None)
    elif fault == "currency":
        inv = make("investment", 10, event_date=snapshot.as_of, currency="USD")
    else:
        val = make("valuation", 0, event_date=snapshot.as_of)
    with pytest.raises(ValueError):
        check_numeric(numeric_case, "deal_terms.ownership", 5, [val, inv])


def test_exact_frozen_fact(receipt):
    snapshot, fact = receipt
    assert (
        validate_financial_facts((fact,), snapshot)[fact.evidence.evidence_id] == fact
    )


@pytest.mark.parametrize("fault", ["snapshot", "entity", "payload", "period", "source"])
def test_reject_unverified_context(receipt, fault):
    snapshot, fact = receipt
    if fault == "snapshot":
        fact = fact.model_copy(update={"snapshot_id": "foreign"})
    elif fault == "entity":
        fact = fact.model_copy(update={"accounting_entity": "foreign"})
    elif fault == "period":
        fact = fact.model_copy(update={"period": None})
    elif fault == "payload":
        e = fact.evidence.model_copy(update={"value": 100})
        fact = fact.model_copy(update={"evidence": e})
    else:
        snapshot.sources.clear()
    with pytest.raises(ValueError):
        validate_financial_facts((fact,), snapshot)
