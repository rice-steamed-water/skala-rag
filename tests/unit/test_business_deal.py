"""Synthetic offline tests; no policy approval or live measurements."""

# allow: SIZE_OK - preserve the legacy matrix and scoped synthetic Finance fixture.

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.fixtures.loader import load_common_fixtures
from tests.unit.test_actual_admission_v3 import configured as configured
from tests.unit.test_actual_admission_v3 import offline as offline

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


def test_approved_rubric_disallows_other_na_even_with_true_verifier(case):
    case[3]["status"] = "approved"
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
        applicability_reason="Not disclosed is not N/A",
        applicability_rule_id="fixture-rule",
        applicability_evidence_ids=[eid],
    )
    assert run(case, output).status == "failure"


@pytest.mark.parametrize("mode", ["real", "live"])
def test_actual_mode_blocked_before_fake_llm_call(case, mode):
    snapshot, policy, output, rubric = case
    llm = FakeLLM([output])
    with pytest.raises(ValueError, match="approved finance-0.1.0 semantic verifiers"):
        evaluate_business_deal(
            snapshot,
            llm=llm,
            policy=policy,
            rubric=rubric,
            verifiers=ApprovedVerifiers(
                "finance-0.1.0",
                "finance-0.1.0",
                "finance-0.1.0",
                lambda c, s: True,
                lambda c, s: True,
                lambda c, s: True,
            ),
            clock=FakeClock(datetime(2026, 9, 30, tzinfo=UTC)),
            schema_version="test",
            execution_mode=mode,
        )
    assert llm.calls == []


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


@pytest.mark.parametrize("wrong_rating", [False, True])
def test_approved_observed_gross_margin(case, wrong_rating):
    import yaml
    from tests.unit.test_approved_policy import approval_payload

    from skala_rag.agents.finance_verification import ReviewedFinancialFact
    from skala_rag.scoring.approved_policy import PolicyApprovals, load_approved_policy
    from skala_rag.scoring.finance import parse_period

    snapshot, _, output, _ = case
    policy = load_approved_policy(
        "configs/scoring.v3.json",
        approvals=PolicyApprovals.model_validate(approval_payload()),
        approval_verifier=lambda a, p: a.model_dump() == approval_payload()[a.scope],
    )
    snapshot.policy_version = policy.policy_version
    rubric = yaml.safe_load(Path("configs/rubrics/finance.yaml").read_text())
    template = next(iter(snapshot.evidence.values()))
    facts = []
    for role, value in [("revenue", 100), ("cost_of_revenue", 50)]:
        e = template.model_copy(
            update={
                "evidence_id": f"fixture:{role}",
                "locator": "https://example.com/offline-finance-fixture",
                "evidence_kind": "reported",
                "criterion_ids": ["traction.gross_margin"],
                "value": value,
                "unit": "one",
                "currency": "KRW",
                "period": "2025-01-01/2025-12-31",
                "value_as_of": snapshot.as_of,
                "supporting_evidence_ids": [],
                "conflicts_with": [],
            }
        )
        snapshot.evidence[e.evidence_id] = e
        snapshot.evidence_ids.append(e.evidence_id)
        facts.append(
            ReviewedFinancialFact.model_validate(
                {
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
                    "reviewer_reference": "fixture:review",
                    "accounting_entity": snapshot.candidate_id,
                    "metric_role": role,
                    "funding_round": None,
                    "valuation_basis": None,
                    "period": parse_period(e.period),
                    "evidence": e,
                }
            )
        )
    c = next(
        c
        for c in output["traction"]["criteria"]
        if c["criterion_id"] == "traction.gross_margin"
    )
    c.update(
        status="observed",
        rating=4 if wrong_rating else 5,
        missing_reason=None,
        evidence_ids=[f.evidence.evidence_id for f in facts],
    )
    result = evaluate_business_deal(
        snapshot,
        policy=policy,
        rubric=rubric,
        llm=FakeLLM([output]),
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=UTC)),
        schema_version="test",
        financial_facts=tuple(facts),
        verifiers=ApprovedVerifiers(
            "finance-0.1.0",
            "finance-0.1.0",
            "finance-0.1.0",
            lambda c, s: True,
            lambda c, s: True,
            lambda c, s: True,
        ),
    )
    assert result.status == ("failure" if wrong_rating else "success")
    if wrong_rating:
        assert result.evaluations is None


def test_approved_contract_fixture_consumer(case):
    from tests.unit.test_approved_policy import approval_payload

    from skala_rag.scoring.approved_policy import PolicyApprovals, load_approved_policy

    snapshot, _, output, rubric = case
    policy = load_approved_policy(
        "configs/scoring.v3.json",
        approvals=PolicyApprovals.model_validate(approval_payload()),
        approval_verifier=lambda a, p: a.model_dump() == approval_payload()[a.scope],
    )
    snapshot = snapshot.model_copy(update={"policy_version": policy.policy_version})
    rubric.update(status="approved", rubric_version="finance-0.1.0")
    llm = FakeLLM([output])
    result = evaluate_business_deal(
        snapshot,
        policy=policy,
        rubric=rubric,
        llm=llm,
        verifiers=ApprovedVerifiers(
            "finance-0.1.0",
            "finance-0.1.0",
            "finance-0.1.0",
            lambda c, s: True,
            lambda c, s: True,
            lambda c, s: True,
        ),
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=UTC)),
        schema_version="test",
    )
    assert result.status == "success"
    assert result.evaluations is not None
    assert set(result.evaluations) == {"traction", "deal_terms"}
    assert len(llm.calls) == 1


REQUEST = b"SYNTHETIC independent financial review; test budget only"


@pytest.fixture
def finance_case(configured):
    import json
    from dataclasses import replace

    import httpx
    from tests.unit.test_openai_attempt import body

    from skala_rag.agents.finance_verification import ReviewedFinancialFact
    from skala_rag.agents.moat_verification import _digest, frozen_snapshot_digest
    from skala_rag.agents.source_fact_verification import (
        SourceBoundReviewResolver,
        SourceTextSpan,
        review_receipt_digest,
    )
    from skala_rag.rag.text_review import text_hash
    from skala_rag.scoring.finance import parse_period
    from skala_rag.tools.source_fetch import content_hash

    admission, snapshot, llm, _, _, template_review = configured
    rubric = admission.registry.rubric("finance-0.1.0")
    original = admission.review_resolvers[(snapshot.snapshot_id, "finance-0.1.0")]
    trusted = next(iter(original._sources.values()))
    rows = (
        ("revenue", 100, "Revenue 100 KRW in 2025.", "traction.gross_margin"),
        (
            "cost_of_revenue",
            50,
            "Cost of revenue 50 KRW in 2025.",
            "traction.gross_margin",
        ),
        (
            "operating_cash_flow",
            0,
            "Operating cash flow 0 KRW in 2025.",
            "traction.runway",
        ),
        (
            "confirmed_pre_revenue",
            0,
            "Confirmed pre-revenue in 2025.",
            "traction.rule_of_40",
        ),
    )
    text = "\n".join(row[2] for row in rows)
    digest = content_hash(text.encode())
    trusted.path.write_bytes(text.encode())
    trusted.extracted_path.write_text(text)
    source = trusted.source.model_copy(
        update={
            "content_hash": digest,
            "bibliographic_metadata": {
                "original_receipt": {
                    "source_id": trusted.source.source_id,
                    "raw_path": "raw/company.html",
                    "extracted_path": "text/company.txt",
                    "raw_sha256": digest.removeprefix("sha256:"),
                    "extracted_sha256": digest.removeprefix("sha256:"),
                    "charset": "utf-8",
                }
            },
        }
    )
    trusted = replace(
        trusted,
        source=source,
        format="text",
        extracted_text=text,
        extracted_sha256=digest,
    )
    template = snapshot.evidence["ev-capture"]
    evidence = {
        role: template.model_copy(
            update={
                "evidence_id": role,
                "excerpt": quote,
                "criterion_ids": [criterion],
                "value": value,
                "unit": "one",
                "currency": "KRW",
                "period": "2025-01-01/2025-12-31",
                "value_as_of": snapshot.as_of,
                "evidence_kind": "reported",
                "supporting_evidence_ids": [],
                "conflicts_with": [],
            }
        )
        for role, value, quote, criterion in rows
    }
    record = next(iter(snapshot.retrieval_records.values())).model_copy(
        update={"evidence_ids": list(evidence)}
    )
    snapshot = snapshot.model_copy(
        update={
            "sources": {source.source_id: source},
            "evidence": evidence,
            "evidence_ids": list(evidence),
            "retrieval_records": {record.retrieval_id: record},
        }
    )
    facts = tuple(
        ReviewedFinancialFact.model_validate(
            {
                **{
                    name: getattr(snapshot, name)
                    for name in (
                        "run_id",
                        "snapshot_id",
                        "candidate_id",
                        "evaluation_round",
                        "evidence_revision",
                        "policy_version",
                    )
                },
                "reviewer_reference": "SYNTHETIC independent Finance reviewer",
                "accounting_entity": snapshot.candidate_id,
                "metric_role": role,
                "funding_round": None,
                "valuation_basis": None,
                "period": parse_period(item.period),
                "evidence": item,
            }
        )
        for role, item in evidence.items()
    )
    reviews = tuple(
        template_review.model_copy(
            update={
                "request_sha256": content_hash(REQUEST).removeprefix("sha256:"),
                "receipt_sha256": review_receipt_digest(fact),
                "subject": snapshot.candidate_id,
                "snapshot_sha256": frozen_snapshot_digest(snapshot),
                "rubric_sha256": _digest(rubric),
                "spans": (
                    SourceTextSpan(
                        source.source_id,
                        text.index(fact.evidence.excerpt),
                        text.index(fact.evidence.excerpt) + len(fact.evidence.excerpt),
                        fact.evidence.excerpt,
                        text_hash(text),
                        None,
                    ),
                ),
            }
        )
        for fact in facts
    )
    admission = replace(
        admission,
        review_resolvers={
            (snapshot.snapshot_id, "finance-0.1.0"): SourceBoundReviewResolver(
                snapshot, rubric, sources={source.source_id: trusted}, reviews=reviews
            )
        },
    )
    policy = admission.load_policy()
    output = {
        dimension: {
            "criteria": [
                dict(
                    schema_version=snapshot.schema_version,
                    criterion_id=c.criterion_id,
                    status="missing",
                    rating=None,
                    evidence_ids=[],
                    rationale="SYNTHETIC not disclosed",
                    missing_reason="not_disclosed",
                    applicability_reason=None,
                    applicability_rule_id=None,
                    applicability_evidence_ids=None,
                    applicability_note=None,
                )
                for c in policy.criteria
                if c.dimension == dimension
            ],
            "research_gaps": [],
            "caveats": [],
        }
        for dimension in ("traction", "deal_terms")
    }
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json=body(
                json.dumps(output), usage={"input_tokens": 10, "output_tokens": 10}
            ),
        )

    llm.call = llm.call.model_copy(update={"node": "business_deal_evaluation"})
    llm.transport._http_transport = httpx.MockTransport(handler)
    return admission, snapshot, llm, rubric, facts, output, seen, trusted, reviews


def invoke(case, *, facts=None):
    from skala_rag.agents.business_deal import evaluate_business_deal_approved

    admission, snapshot, llm, rubric, reviewed, _, _, _, _ = case
    return evaluate_business_deal_approved(
        snapshot,
        rubric=rubric,
        admission=admission,
        llm=llm,
        review_request=REQUEST,
        review_subject=snapshot.candidate_id,
        financial_facts=reviewed if facts is None else facts,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"accounting_entity": "foreign"},
        {"metric_role": "cash"},
        {"period": None},
        {"evidence_revision": 99},
    ],
)
def test_fact_context_denied_when_reviewed_receipt_is_changed(finance_case, change):
    # Given: authentic source bytes and an altered metric-role/context receipt.
    fact = finance_case[4][0].model_copy(update=change)
    # When / Then: original review cannot authorize a different role or subject.
    with pytest.raises(ValueError):
        invoke(finance_case, facts=(fact,))
    assert finance_case[6] == []
    assert finance_case[2].runtime.ledger.snapshot()["calls"] == 0


@pytest.mark.parametrize(
    "change", [{"value": 999}, {"unit": "million"}, {"period": "2024"}]
)
def test_fact_value_denied_when_frozen_evidence_differs(finance_case, change):
    # Given
    fact = finance_case[4][0]
    fact = fact.model_copy(update={"evidence": fact.evidence.model_copy(update=change)})
    # When / Then
    with pytest.raises(ValueError):
        invoke(finance_case, facts=(fact,))
    assert finance_case[6] == []
