"""Controlled wire responses through configured production analysis and lifecycle."""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import NotRequired, TypedDict

import httpx
import pytest
from tests.fixtures.narrative import gates_payload, runtime_binding
from tests.unit.test_company_store import (
    SyntheticEncoder,
    identity,
    material,
    settings,
)

from skala_rag.agents.company_analysis import (
    CompanyAnalysisAttempt,
    ContextualRoleAttempt,
    analysis_questions,
    analyze_company,
)
from skala_rag.agents.company_report_research import NarrativeResearchService
from skala_rag.contracts.company_report import (
    QUESTION_IDS,
    CompanyReportConfigError,
    CompositionReply,
)
from skala_rag.graph.company_report import NarrativeReportLifecycle
from skala_rag.rag.company_store import CompanyStore
from skala_rag.reporting.company_context import build_writer_context
from skala_rag.settings import load_runtime_document
from skala_rag.tools.runtime import Allowance
from skala_rag.tools.structured_llm import APPROVED_MODEL

VERSION = "narrative-report-1"


class FindingPayload(TypedDict):
    schema_version: str
    kind: str
    text: str
    evidence_ids: list[str]


class AnswerPayload(TypedDict):
    schema_version: str
    status: str
    findings: list[FindingPayload]
    limitation: str | None
    verification_task: str | None


class NeedPayload(TypedDict):
    schema_version: str
    question_id: str
    information_needed: str
    related_evidence_ids: list[str]


class AnalysisPayload(TypedDict):
    schema_version: str
    context_id: str
    answers: dict[str, AnswerPayload]
    needs: list[NeedPayload]
    score: NotRequired[int]


def reply_payload(context_id):
    """Synthetic missing answers plus a genuinely observed numeric zero."""
    payload: AnalysisPayload = {
        "schema_version": VERSION,
        "context_id": context_id,
        "answers": {
            qid: {
                "schema_version": VERSION,
                "status": "unknown",
                "findings": [],
                "limitation": "The supplied observations do not answer this question.",
                "verification_task": "Obtain an observation for this question.",
            }
            for qid in QUESTION_IDS
        },
        "needs": [],
    }
    payload["answers"]["business_model.1"] = {
        "schema_version": VERSION,
        "status": "answered",
        "findings": [
            {
                "schema_version": VERSION,
                "kind": "fact",
                "text": "Reported subscription revenue was USD 0 in 2025.",
                "evidence_ids": ["ev-a"],
            }
        ],
        "limitation": None,
        "verification_task": None,
    }
    return payload


def make_analysis_case(tmp_path: Path, *, mutate=lambda payload: None):
    """Actual store/projection/controller; synthetic observations and HTTP only."""
    gates = gates_payload()
    gates["limits"].update(
        max_calls=1,
        tool_max_calls={"synthetic-provider": 1},
        max_input_tokens=100000,
        max_output_tokens=2000,
    )
    binding = runtime_binding(payload=gates)
    store = CompanyStore(
        tmp_path / "store", settings=settings(), encoder=SyntheticEncoder()
    )
    original = material(text="Reported subscription revenue was USD 0 in 2025.")
    original = replace(
        original,
        evidence=(
            original.evidence[0].model_copy(
                update={
                    "value": 0,
                    "currency": "USD",
                    "unit": "USD",
                    "value_as_of": date(2025, 12, 31),
                    "period": "2025",
                }
            ),
        ),
    )
    snapshot = store.ingest_sources([original], companies=[identity()])
    context = build_writer_context(
        snapshot,
        company_name=identity().candidate.canonical_name,
        as_of=date(2026, 10, 10),
        questions=analysis_questions(),
        evidence_ids=["ev-a"],
        may_request_information=False,
    )
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        payload = reply_payload(context.context_id)
        mutate(payload)
        return httpx.Response(
            200,
            json={
                "model": APPROVED_MODEL,
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps(payload)}
                        ],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 1500},
            },
        )

    attempt = CompanyAnalysisAttempt(
        context,
        llm_settings=load_runtime_document().llm,
        clock=binding.runtime.clock,
        max_output_tokens=2000,
        http_transport=httpx.MockTransport(handler),
    )
    service = NarrativeResearchService(
        store=store,
        candidate=identity().candidate,
        as_of=context.writer_context.as_of,
        run_id=binding.call.run_id,
        clock=binding.runtime.clock,
        deadline=binding.budget.deadline,
        research=False,
        limits=None,
        admission=None,
        admit=lambda snapshot, ids, records: ids,
    )
    lifecycle = NarrativeReportLifecycle(
        context=context,
        snapshot=snapshot,
        service=service,
        binding=binding,
        allowance=attempt.allowance,
    )
    return lifecycle, attempt, seen, binding.runtime.ledger


def test_six_domains_factual_measured_zero_and_missing_when_grounded(tmp_path):
    # Given: a source-neutral projection containing one observed zero.
    lifecycle, attempt, seen, ledger = make_analysis_case(tmp_path)
    # When: the configured analyzer runs through the captured runtime.
    result = analyze_company(lifecycle, transport=attempt)
    # Then: exactly 18 typed answers, unknown tasks and monetary zero survive.
    assert result.status == "ok"
    assert result.data is not None
    assert tuple(result.data.answers) == QUESTION_IDS
    assert len({qid.rsplit(".", 1)[0] for qid in result.data.answers}) == 6
    zero = result.data.answers["business_model.1"]
    assert zero.status == "answered"
    assert zero.findings[0].evidence_ids == ["ev-a"]
    missing = result.data.answers["finance_funding_deal.1"]
    assert missing.status == "unknown" and not missing.findings
    assert missing.limitation and missing.verification_task
    user = json.loads(seen[0]["input"][1]["content"])
    observation = user["writer_context"]["evidence"]["ev-a"]
    assert (observation["value"], observation["currency"], observation["period"]) == (
        0,
        "USD",
        "2025",
    )
    assert observation["value_as_of"] == "2025-12-31"
    assert set(user) == {"writer_context", "prompt_version", "question_version"}
    assert user["writer_context"] == lifecycle.context.writer_context.model_dump(
        mode="json"
    )
    schema = seen[0]["text"]["format"]["schema"]
    assert set(schema["properties"]["answers"]["properties"]) == set(QUESTION_IDS)
    assert len(seen) == ledger.snapshot()["calls"] == 1
    assert len(result.retrieval_records) == len(lifecycle.model_records) == 1
    assert lifecycle.repair_count == lifecycle.information_rounds == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_ids",
        "wrong_question",
        "missing_answer",
        "stale_context",
        "missing_task",
        "unsupported_applicability",
        "unsupported_answer",
        "disabled_need",
        "score",
    ],
)
def test_invalid_grounding_when_provider_returns_invalid_reply(tmp_path, mutation):
    # Given: a controlled invalid response at the HTTP seam.
    def mutate(payload):
        answer = payload["answers"]["business_model.1"]
        match mutation:
            case "wrong_ids":
                answer["findings"][0]["evidence_ids"] = ["invented-original"]
            case "wrong_question":
                payload["answers"]["technology_product.4"] = payload["answers"].pop(
                    "technology_product.1"
                )
            case "missing_answer":
                payload["answers"].pop("technology_product.1")
            case "stale_context":
                payload["context_id"] = "foreign-context"
            case "missing_task":
                payload["answers"]["team_execution.1"]["verification_task"] = None
            case "unsupported_applicability":
                payload["answers"]["team_execution.1"]["status"] = "not_applicable"
            case "unsupported_answer":
                answer["findings"] = []
            case "disabled_need":
                payload["needs"] = [
                    {
                        "schema_version": VERSION,
                        "question_id": "team_execution.1",
                        "information_needed": "Capacity observation",
                        "related_evidence_ids": [],
                    }
                ]
            case "score":
                payload["score"] = 0

    lifecycle, attempt, seen, ledger = make_analysis_case(tmp_path, mutate=mutate)
    # When: production parsing and trusted contextual validation run.
    result = analyze_company(lifecycle, transport=attempt)
    # Then: no invented/unsupported successful analysis or retry.
    assert result.status == "failed"
    assert result.data is None
    assert result.errors[-1].error_code == "TOOL_RESPONSE_INVALID"
    assert len(seen) == ledger.snapshot()["calls"] == 1
    assert lifecycle.repair_count == 0


@pytest.mark.parametrize(
    "status,kind", [("partial", "fact"), ("not_applicable", "interpretation")]
)
def test_partial_and_applicability_when_evidence_backed(tmp_path, status, kind):
    # Given: evidence-backed findings with explicit uncertainty/applicability.
    def mutate(payload):
        answer = payload["answers"]["business_model.1"]
        answer.update(
            status=status,
            limitation="Only reported subscription revenue is supplied.",
            verification_task="Verify other revenue mechanisms.",
        )
        answer["findings"][0]["kind"] = kind

    lifecycle, attempt, _, _ = make_analysis_case(tmp_path, mutate=mutate)
    # When: the role is executed once.
    result = analyze_company(lifecycle, transport=attempt)
    # Then: status remains distinct rather than collapsing to zero/missing.
    assert result.status == "ok"
    assert result.data is not None
    assert result.data.answers["business_model.1"].status == status


def test_stale_attempt_when_lifecycle_context_differs(tmp_path):
    # Given: an attempt for an earlier writer context.
    lifecycle, attempt, seen, ledger = make_analysis_case(tmp_path)
    attempt._writer = attempt._writer.model_copy(update={"context_id": "stale"})
    # When: the analyzer is asked to dispatch it.
    with pytest.raises(CompanyReportConfigError, match="STALE_ANALYSIS_CONTEXT"):
        analyze_company(lifecycle, transport=attempt)
    # Then: rejection precedes HTTP and budget consumption.
    assert not seen and ledger.snapshot()["calls"] == 0


def test_composition_context_when_using_shared_role_transport(tmp_path):
    # Given: task 7's contextual DTO, configured through the same role adapter.
    lifecycle, _, _, ledger = make_analysis_case(tmp_path)
    writer = lifecycle.context.writer_context
    payload = {
        "schema_version": VERSION,
        "context_id": writer.context_id,
        "markdown_without_reference": "Reported observation [@evidence:ev-a]",
        "cited_evidence_ids": ["ev-a"],
        "strengths": [],
        "weaknesses": [],
        "verification_tasks": [],
    }

    def handler(request):
        return httpx.Response(
            200,
            json={
                "model": APPROVED_MODEL,
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps(payload)}
                        ],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 100},
            },
        )

    transport = ContextualRoleAttempt(
        writer_context=writer,
        output_schema=CompositionReply,
        system="Controlled composition role",
        user=writer.model_dump_json(),
        prompt_version="controlled-composition-1",
        llm_settings=load_runtime_document().llm,
        clock=lifecycle._runtime.clock,
        max_output_tokens=2000,
        allowance=lifecycle._allowance,
        http_transport=httpx.MockTransport(handler),
    )
    # When: the existing lifecycle dispatches that contextual production role.
    result = lifecycle.execute_model(transport=transport)
    # Then: task 7 receives its actual DTO, validated against admitted originals.
    assert result.status == "ok"
    assert result.data is not None
    assert isinstance(result.data, CompositionReply)
    assert result.data.cited_evidence_ids == ["ev-a"]
    assert ledger.snapshot()["calls"] == 1


def test_shared_allowance_when_binding_or_bound_is_wrong(tmp_path):
    # Given: a bound analyzer and the same captured lifecycle.
    lifecycle, attempt, wire, ledger = make_analysis_case(tmp_path)
    too_small = Allowance(
        schema_version=VERSION, input_tokens=1, output_tokens=1, max_cost_usd="0"
    )
    # When: an undersized allowance or a different runtime reservation is supplied.
    with pytest.raises(CompanyReportConfigError, match="ROLE_ALLOWANCE_INSUFFICIENT"):
        CompanyAnalysisAttempt(
            lifecycle.context,
            llm_settings=load_runtime_document().llm,
            clock=lifecycle._runtime.clock,
            max_output_tokens=2000,
            allowance=too_small,
            http_transport=httpx.MockTransport(lambda request: pytest.fail("dispatch")),
        )
    attempt.allowance = attempt.allowance.model_copy(
        update={"input_tokens": attempt.allowance.input_tokens + 1}
    )
    with pytest.raises(
        CompanyReportConfigError, match="ANALYSIS_ALLOWANCE_BINDING_MISMATCH"
    ):
        analyze_company(lifecycle, transport=attempt)
    # Then: neither mismatch dispatches or consumes the cumulative ledger.
    assert not wire and ledger.snapshot()["calls"] == 0
