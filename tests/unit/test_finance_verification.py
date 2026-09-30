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
