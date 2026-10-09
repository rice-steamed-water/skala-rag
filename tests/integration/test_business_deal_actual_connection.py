"""Real runtime/MockTransport; SYNTHETIC semantic receipts and test-only budgets.

No external socket, provider approval, billing or actual financial truth is claimed.
"""

import json
from dataclasses import replace
from typing import Literal, assert_never

import httpx
import pytest
from tests.unit.test_actual_admission_v3 import configured as configured
from tests.unit.test_actual_admission_v3 import offline as offline
from tests.unit.test_business_deal import finance_case as finance_case
from tests.unit.test_business_deal import invoke
from tests.unit.test_openai_attempt import body

from skala_rag.agents.business_deal import evaluate_business_deal_approved
from skala_rag.agents.source_fact_verification import SourceBoundReviewResolver
from skala_rag.fakes import FakeLLM

REQUEST = b"SYNTHETIC independent financial review; test budget only"


@pytest.mark.parametrize("metric", ["missing", "gross_margin", "runway", "rule_of_40"])
def test_atomic_result_when_financial_support_is_reviewed(finance_case, metric):
    # Given: accepted independent receipts, exact pins and one mock-wire request.
    _, _, llm, _, facts, output, seen, _, _ = finance_case
    if metric != "missing":
        criterion = next(
            c
            for c in output["traction"]["criteria"]
            if c["criterion_id"] == f"traction.{metric}"
        )
        if metric == "gross_margin":
            criterion.update(
                status="observed",
                rating=5,
                evidence_ids=["revenue", "cost_of_revenue"],
                missing_reason=None,
            )
        else:
            role = (
                "operating_cash_flow" if metric == "runway" else "confirmed_pre_revenue"
            )
            rule = (
                "runway-confirmed-nonnegative-ocf"
                if metric == "runway"
                else "rule40-confirmed-pre-revenue"
            )
            criterion.update(
                status="not_applicable",
                missing_reason=None,
                applicability_evidence_ids=[role],
                applicability_reason="SYNTHETIC reviewed",
                applicability_rule_id=f"finance-0.1.0:{rule}",
            )
    # When: empty facts exercise Missing, never invented metrics or N/A.
    result = invoke(finance_case, facts=() if metric == "missing" else facts)
    # Then: both dimensions promote together and physical usage settles once.
    assert result.status == "success", result.errors
    assert set(result.evaluations) == {"traction", "deal_terms"}
    ledger = llm.runtime.ledger.snapshot()
    assert len(seen) == ledger["calls"] == 1
    assert ledger["input_tokens_accounted"] == ledger["output_tokens_accounted"] == 10
    assert ledger["cost_usd_accounted"] == "0.10"
    if metric == "missing":
        assert all(
            c.status == "missing" and c.rating is None
            for e in result.evaluations.values()
            for c in e.criteria
        )
    else:
        actual = next(
            c
            for c in result.evaluations["traction"].criteria
            if c.criterion_id == f"traction.{metric}"
        )
        assert actual.rating == (5 if metric == "gross_margin" else None)
        assert actual.status == (
            "observed" if metric == "gross_margin" else "not_applicable"
        )


@pytest.mark.parametrize(
    "fault",
    [
        "partial",
        "wrong_rating",
        "unsupported_observed",
        "unsupported_na",
        "empty_na",
        "deal",
    ],
)
def test_no_partial_promotion_when_domain_output_is_invalid(
    finance_case,
    fault: Literal[
        "partial",
        "wrong_rating",
        "unsupported_observed",
        "unsupported_na",
        "empty_na",
        "deal",
    ],
):
    # Given: a valid reviewed observed traction metric beside unsupported output.
    output = finance_case[5]
    criterion = next(
        c
        for c in output["traction"]["criteria"]
        if c["criterion_id"] == "traction.gross_margin"
    )
    criterion.update(
        status="observed",
        rating=5,
        missing_reason=None,
        evidence_ids=["revenue", "cost_of_revenue"],
    )
    match fault:
        case "partial":
            del output["deal_terms"]
        case "wrong_rating":
            criterion["rating"] = 4
        case "unsupported_observed":
            pass
        case "unsupported_na":
            criterion.update(
                status="not_applicable",
                rating=None,
                evidence_ids=[],
                applicability_reason="SYNTHETIC invented N/A",
                applicability_rule_id="finance-0.1.0:invented",
                applicability_evidence_ids=["revenue"],
            )
        case "empty_na":
            criterion.update(
                status="missing",
                rating=None,
                evidence_ids=[],
                missing_reason="not_disclosed",
            )
            next(
                c
                for c in output["traction"]["criteria"]
                if c["criterion_id"] == "traction.runway"
            ).update(
                status="not_applicable",
                missing_reason=None,
                applicability_reason="SYNTHETIC fabricated N/A",
                applicability_rule_id="finance-0.1.0:runway-confirmed-nonnegative-ocf",
                applicability_evidence_ids=["operating_cash_flow"],
            )
        case "deal":
            output["deal_terms"]["criteria"][0].update(
                status="observed",
                rating=5,
                missing_reason=None,
                evidence_ids=["revenue"],
            )
        case unreachable:
            assert_never(unreachable)
    # When
    result = invoke(
        finance_case,
        facts=() if fault in ("unsupported_observed", "empty_na") else None,
    )
    # Then
    assert result.status == "failure" and result.evaluations is None
    assert (
        len(finance_case[6]) == finance_case[2].runtime.ledger.snapshot()["calls"] == 1
    )


@pytest.mark.parametrize(
    "fault",
    [
        "receipt",
        "request",
        "subject",
        "rejected",
        "unresolved",
        "unreviewed",
        "source",
        "fake",
        "rubric",
    ],
)
def test_admission_denied_before_wire_when_authority_changes(
    finance_case,
    fault: Literal[
        "receipt",
        "request",
        "subject",
        "rejected",
        "unresolved",
        "unreviewed",
        "source",
        "fake",
        "rubric",
    ],
):
    # Given
    admission, snapshot, llm, rubric, facts, _, seen, trusted, reviews = finance_case
    request, subject = REQUEST, snapshot.candidate_id
    match fault:
        case "receipt":
            facts = (facts[0].model_copy(update={"reviewer_reference": "forged"}),)
        case "request":
            request = b'{"approval":true}'
        case "subject":
            subject = "foreign"
        case "rejected" | "unresolved" | "unreviewed":
            resolver = SourceBoundReviewResolver(
                snapshot,
                rubric,
                sources={trusted.source.source_id: trusted},
                reviews=()
                if fault == "unreviewed"
                else tuple(r.model_copy(update={"decision": fault}) for r in reviews),
            )
            admission = replace(
                admission,
                review_resolvers={(snapshot.snapshot_id, "finance-0.1.0"): resolver},
            )
        case "source":
            trusted.path.write_bytes(b"tampered original")
        case "fake":
            llm = FakeLLM([])
        case "rubric":
            rubric["comparison"] = "rounded"
        case unreachable:
            assert_never(unreachable)
    # When / Then
    with pytest.raises(ValueError):
        evaluate_business_deal_approved(
            snapshot,
            rubric=rubric,
            admission=admission,
            llm=llm,
            review_request=request,
            review_subject=subject,
            financial_facts=facts,
        )
    assert seen == []
    assert admission.runtime_binding.runtime.ledger.snapshot()["calls"] == 0


def test_source_tampering_during_wire_prevents_promotion(finance_case):
    # Given: original sources pass admission but change during the physical request.
    _, _, llm, _, _, output, seen, trusted, _ = finance_case

    def handler(request):
        seen.append(request)
        trusted.path.write_bytes(b"changed during wire")
        return httpx.Response(
            200,
            json=body(
                json.dumps(output), usage={"input_tokens": 10, "output_tokens": 10}
            ),
        )

    llm.transport._http_transport = httpx.MockTransport(handler)
    # When
    result = invoke(finance_case)
    # Then: accepted original reviews do not survive changed source bytes.
    assert result.status == "failure" and result.evaluations is None
    assert len(seen) == llm.runtime.ledger.snapshot()["calls"] == 1
