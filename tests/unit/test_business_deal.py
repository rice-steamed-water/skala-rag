"""Synthetic offline tests; no policy approval or live measurements."""

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.fixtures.loader import load_common_fixtures

from skala_rag.agents.business_deal import ApprovedVerifiers, evaluate_business_deal
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import LLMError
from skala_rag.fakes import FakeClock, FakeLLM
from skala_rag.scoring.catalog import load_policy


@pytest.fixture
def case():
    root = Path(__file__).resolve().parents[2]
    policy = load_policy(root / "configs/scoring.draft.json", execution_mode="fixture")
    snapshot = next(iter(load_common_fixtures(policy).snapshots.values()))
    output = {
        d: {
            "criteria": [
                dict(
                    schema_version="test",
                    criterion_id=c.criterion_id,
                    status="missing",
                    rating=None,
                    evidence_ids=[],
                    rationale="Not disclosed",
                    missing_reason="not_disclosed",
                )
                for c in policy.criteria
                if c.dimension == d
            ],
            "research_gaps": [],
            "caveats": [],
        }
        for d in ("traction", "deal_terms")
    }
    rubric = {
        "rubric_version": "fixture",
        "dimensions": {
            d: {
                "criteria": {
                    c.criterion_id: {} for c in policy.criteria if c.dimension == d
                }
            }
            for d in output
        },
    }
    return snapshot, policy, output, rubric


def run(case, response, *, approve=True):
    snapshot, policy, _, rubric = case
    llm = FakeLLM([response])
    result = evaluate_business_deal(
        snapshot,
        llm=llm,
        policy=policy,
        rubric=rubric,
        verifiers=ApprovedVerifiers(
            "fixture",
            "fixture",
            "fixture",
            lambda c, s: approve,
            lambda c, s: approve,
            lambda c, s: approve,
        ),
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=UTC)),
        schema_version="test",
    )
    assert len(llm.calls) == 1
    return result


def test_atomic_missing(case):
    result = run(case, case[2])
    assert result.status == "success"
    assert set(result.evaluations) == {"traction", "deal_terms"}


@pytest.mark.parametrize("fault", ["partial", "duplicate", "schema", "rating", "stale"])
def test_invalid_output_rejects_whole_branch(case, fault):
    output = deepcopy(case[2])
    if fault == "partial":
        del output["deal_terms"]
    elif fault == "duplicate":
        output["traction"]["criteria"].append(output["traction"]["criteria"][0])
    elif fault == "schema":
        del output["traction"]["criteria"][0]["rationale"]
    elif fault == "stale":
        output["snapshot_id"] = "old"
    else:
        output["traction"]["criteria"][0]["rating"] = 9
    result = run(case, output)
    assert result.status == "failure"
    assert result.evaluations is None


def test_technical_failure_is_not_missing(case):
    result = run(case, LLMError(ErrorCode.LLM_TIMEOUT, "secret raw error"))
    assert result.status == "failure"
    assert result.errors[0].error_code == "LLM_TIMEOUT"
    assert "secret" not in result.errors[0].message_redacted


@pytest.mark.parametrize("approve", [False, True])
def test_na_requires_injected_rule_validation(case, approve):
    output = deepcopy(case[2])
    c = output["traction"]["criteria"][0]
    eid = next(
        e.evidence_id
        for e in case[0].evidence.values()
        if c["criterion_id"] in e.criterion_ids
    )
    c.update(
        status="not_applicable",
        missing_reason=None,
        applicability_reason="Synthetic authorized applicability fact",
        applicability_rule_id="fixture-rule",
        applicability_evidence_ids=[eid],
    )
    result = run(case, output, approve=approve)
    assert result.status == ("success" if approve else "failure")


@pytest.mark.parametrize("fault", ["foreign", "finance", "unknown_unit"])
def test_observed_financial_gate(case, fault):
    output = deepcopy(case[2])
    c = output["traction"]["criteria"][0]
    eid = next(
        e.evidence_id
        for e in case[0].evidence.values()
        if c["criterion_id"] in e.criterion_ids
    )
    if fault == "unknown_unit":
        evidence = case[0].evidence[eid]
        evidence.value = 1
        evidence.currency = "KRW"
        evidence.unit = "mystery"
        evidence.value_as_of = case[0].as_of
    c.update(
        status="observed",
        rating=3,
        missing_reason=None,
        evidence_ids=["outside" if fault == "foreign" else eid],
    )
    result = run(case, output, approve=fault != "finance")
    assert result.status == "failure"
    assert result.evaluations is None
