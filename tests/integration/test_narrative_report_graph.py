"""Synthetic vectors/role attempts; actual controller, projection, SQLite and ledger."""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ValidationError
from tests.fixtures.narrative import (
    SyntheticLocalModel,
    gates_payload,
    narrative_questions,
    runtime_binding,
)
from tests.unit.test_company_store import identity, material, settings

from skala_rag.agents.company_report_research import (
    NarrativeResearchService,
)
from skala_rag.contracts.company_report import (
    QUESTION_IDS,
    AnalysisReply,
    InformationNeed,
)
from skala_rag.fakes import FakeClock
from skala_rag.graph.company_report import (
    NarrativeLifecycleError,
    NarrativeReportLifecycle,
)
from skala_rag.rag.company_store import CompanyStore, StoreSnapshot
from skala_rag.rag.local_bge_validation import MODEL, REVISION, LocalEncoder
from skala_rag.reporting.company_context import (
    CompanyContextError,
    build_writer_context,
)
from skala_rag.tools.runtime import AttemptResponse, Usage

AS_OF = date(2026, 10, 10)


class ModelObservation(BaseModel):
    accepted: bool


class SyntheticAttempt:
    """Exactly one synthetic model request at the real runtime seam."""

    retry_owner: Literal["runtime"] = "runtime"

    def __init__(self):
        self.timeouts: list[float] = []

    def __call__(self, *, timeout_seconds: float) -> AttemptResponse[ModelObservation]:
        self.timeouts.append(timeout_seconds)
        return AttemptResponse(
            schema_version="test",
            status="ok",
            data=ModelObservation(accepted=True),
            source_ids=[],
            chunk_ids=[],
            evidence_ids=[],
            usage=Usage(
                schema_version="test", input_tokens=1, output_tokens=1, cost_usd="0"
            ),
        )


@dataclass(frozen=True, slots=True)
class Case:
    controller: NarrativeReportLifecycle
    snapshot: StoreSnapshot
    attempt: SyntheticAttempt
    clock: FakeClock


def make_case(tmp_path: Path, *, max_calls: int = 3) -> Case:
    """Build real production components with explicitly synthetic local vectors."""
    payload = gates_payload()
    payload["limits"].update(
        max_calls=max_calls, tool_max_calls={payload["provider"]: max_calls}
    )
    binding = runtime_binding(payload=payload)
    local_settings = replace(
        settings(), model_id=MODEL, model_revision=REVISION, dimension=1024
    )
    store = CompanyStore(
        tmp_path / "store",
        settings=local_settings,
        encoder=LocalEncoder(SyntheticLocalModel()),
    )
    materials = [
        replace(
            seed,
            chunks=tuple(
                chunk.model_copy(
                    update={"embedding_model": MODEL, "embedding_revision": REVISION}
                )
                for chunk in seed.chunks
            ),
        )
        for seed in (
            material(),
            material("more", text="Controlled additional warehouse observation."),
        )
    ]
    snapshot = store.ingest_sources(materials, companies=[identity()])
    context = build_writer_context(
        snapshot,
        company_name=identity().candidate.canonical_name,
        as_of=AS_OF,
        questions=narrative_questions(),
        evidence_ids=["ev-a"],
    )
    service = NarrativeResearchService(
        store=store,
        candidate=identity().candidate,
        as_of=AS_OF,
        run_id=binding.call.run_id,
        clock=binding.runtime.clock,
        deadline=binding.budget.deadline,
        research=False,
        limits=None,
        admission=None,
        admit=lambda snapshot, ids, records: pytest.fail(
            "Controller must own admission"
        ),
    )
    controller = NarrativeReportLifecycle(
        context=context,
        snapshot=snapshot,
        service=service,
        binding=binding,
        allowance=binding.allowance,
    )
    assert isinstance(binding.runtime.clock, FakeClock)
    return Case(controller, snapshot, SyntheticAttempt(), binding.runtime.clock)


@pytest.fixture
def case(tmp_path):
    return make_case(tmp_path)


def analysis(case: Case, *, count: int = 1) -> AnalysisReply:
    writer = case.controller.context.writer_context
    return AnalysisReply.model_validate(
        {
            "schema_version": "narrative-report-1",
            "context_id": writer.context_id,
            "answers": {
                qid: {
                    "schema_version": "narrative-report-1",
                    "status": "unknown",
                    "findings": [],
                    "limitation": "Controlled missing observation.",
                    "verification_task": "Verify the observation.",
                }
                for qid in QUESTION_IDS
            },
            "needs": [
                InformationNeed(
                    schema_version="narrative-report-1",
                    question_id=qid,
                    information_needed="Warehouse observation",
                    related_evidence_ids=["ev-a"],
                )
                for qid in QUESTION_IDS[:count]
            ],
        },
        context={"writer_context": writer},
    )


def test_local_only_enrichment_publishes_bound_final_context(case):
    controller = case.controller
    original = controller.context
    request = controller.begin_information(analysis(case, count=6))
    assert controller.information_rounds == 1
    response = controller.resolve_information(request)
    assert response.request_id == request.request_id
    assert response.base_context_id == original.context_id
    assert response.result_context_id == controller.context.context_id
    assert response.result_context_id != original.context_id
    assert all(item.status == "provided" for item in response.results)
    assert set(controller.context.writer_context.evidence) == {"ev-a", "ev-more"}
    assert not controller.context.writer_context.may_request_information
    assert original.writer_context.information_results == []
    assert controller._runtime.ledger.snapshot()["calls"] == 0
    assert len(controller.collection_records) == 8  # two retained + six searches
    assert case.snapshot.payload == controller._snapshot.payload


def test_no_needs_rebinds_answers_without_model_or_acquisition(case):
    # Given: valid initial answers and open optional acquisition.
    controller = case.controller
    reply = analysis(case, count=0)
    prior = controller.context
    # When: the controller finalizes the initial answers.
    final = controller.finalize_analysis(reply)
    # Then: only context binding changes, with acquisition permanently closed.
    assert final.answers == reply.answers
    assert final.context_id == controller.context.context_id != reply.context_id
    assert controller.context.writer_context.evidence == prior.writer_context.evidence
    assert not controller.context.writer_context.may_request_information
    assert controller.information_rounds == 0
    assert controller.collection_records == ()
    assert controller._runtime.ledger.snapshot()["calls"] == 0
    with pytest.raises(NarrativeLifecycleError, match="ROUND_EXHAUSTED"):
        controller.begin_information(final)


@pytest.mark.parametrize("mutation", ["stale", "needs", "forged"])
def test_invalid_initial_answers_cannot_be_rebound(case, mutation):
    # Given: stale, unresolved, or unsupported initial analysis.
    controller = case.controller
    reply = analysis(case, count=0)
    if mutation == "stale":
        reply = reply.model_copy(update={"context_id": "stale"})
    elif mutation == "needs":
        reply = analysis(case)
    else:
        reply.answers[QUESTION_IDS[0]] = reply.answers[QUESTION_IDS[0]].model_copy(
            update={"status": "answered"}
        )
    prior = controller.context
    # When: finalization revalidates before closing acquisition.
    with pytest.raises((ValidationError, NarrativeLifecycleError)):
        controller.finalize_analysis(reply)
    # Then: rejected answers cannot mutate the bound context or dispatch.
    assert controller.context == prior
    assert controller._runtime.ledger.snapshot()["calls"] == 0


@pytest.mark.parametrize("mutation", ["stale", "forged_id", "changed_needs"])
def test_invalid_request_cannot_dispatch_or_change_prior_context(case, mutation):
    controller = case.controller
    original = controller.context
    request = controller.begin_information(analysis(case))
    match mutation:
        case "stale":
            request = request.model_copy(update={"base_context_id": "stale"})
        case "forged_id":
            request = request.model_copy(update={"request_id": "model-owned"})
        case "changed_needs":
            request.needs[0] = request.needs[0].model_copy(
                update={"information_needed": "Altered observation"}
            )
    with pytest.raises((ValidationError, NarrativeLifecycleError)):
        controller.resolve_information(request)
    assert controller.context == original
    assert controller.collection_records == ()
    assert not controller._dispatched


def test_duplicate_round_and_dispatch_cannot_reopen_acquisition(case):
    controller = case.controller
    reply = analysis(case)
    request = controller.begin_information(reply)
    with pytest.raises(NarrativeLifecycleError, match="ROUND_EXHAUSTED"):
        controller.begin_information(reply)
    controller.resolve_information(request)
    final = controller.context
    records = controller.collection_records
    with pytest.raises((ValidationError, NarrativeLifecycleError)):
        controller.resolve_information(request)
    assert controller.context == final
    assert controller.collection_records == records


def test_seven_needs_and_stale_analysis_cannot_consume_round(case):
    reply = analysis(case)
    for changed in (
        reply.model_copy(update={"context_id": "other-subject-context"}),
        reply.model_copy(update={"needs": analysis(case, count=6).needs + reply.needs}),
    ):
        with pytest.raises(ValidationError):
            case.controller.begin_information(changed)
    assert case.controller.information_rounds == 0
    assert case.controller.collection_records == ()


@pytest.mark.parametrize(
    "mutation",
    ["subject", "prior_evidence", "prior_chunk", "prior_retrieval", "results"],
)
def test_changed_subject_or_result_cannot_overwrite_context(
    case, monkeypatch, mutation
):
    controller = case.controller
    original = controller.context
    request = controller.begin_information(analysis(case))
    resolve = NarrativeResearchService.resolve

    def altered(service, *args, **kwargs):
        result = resolve(service, *args, **kwargs)
        assert result.snapshot is not None
        manifest = result.snapshot.manifest
        match mutation:
            case "subject":
                stored = manifest.companies.pop("co-a")
                stored.candidate.candidate_id = "co-other"
                manifest.companies["co-other"] = stored
            case "prior_evidence":
                manifest.evidence["ev-a"].limitations.append("Changed prior fact.")
            case "prior_chunk":
                chunk_id = manifest.evidence["ev-a"].provenance[0].chunk_id
                assert chunk_id is not None
                manifest.chunks[chunk_id].section = "Changed prior section."
            case "prior_retrieval":
                retrieval_id = manifest.evidence["ev-a"].provenance[0].retrieval_id
                manifest.retrieval_records[retrieval_id].query = "Changed prior query."
            case "results":
                return replace(
                    result,
                    results=tuple(
                        item.model_copy(update={"question_id": QUESTION_IDS[-1]})
                        for item in result.results
                    ),
                )
        return replace(
            result,
            snapshot=StoreSnapshot(
                result.snapshot.root, manifest.model_dump_json().encode()
            ),
        )

    monkeypatch.setattr(NarrativeResearchService, "resolve", altered)
    with pytest.raises((ValidationError, NarrativeLifecycleError, CompanyContextError)):
        controller.resolve_information(request)
    assert controller.context == original
    assert controller.information_rounds == 1
    assert controller._dispatched
    with pytest.raises(NarrativeLifecycleError, match="DISPATCH_REJECTED"):
        controller.resolve_information(request)


def test_dispatch_failure_consumes_round_before_service_and_preserves_context(
    case, monkeypatch
):
    controller = case.controller
    original = controller.context
    request = controller.begin_information(analysis(case))

    def failure(service, *args, **kwargs):
        assert controller.information_rounds == 1 and controller._dispatched
        raise OSError("Synthetic acquisition failure")

    monkeypatch.setattr(NarrativeResearchService, "resolve", failure)
    with pytest.raises(OSError):
        controller.resolve_information(request)
    assert controller.context == original
    with pytest.raises(NarrativeLifecycleError, match="DISPATCH_REJECTED"):
        controller.resolve_information(request)
    final = controller.finalize_information()
    assert not final.writer_context.may_request_information
    assert final.writer_context.evidence == original.writer_context.evidence


@pytest.mark.parametrize(
    "kinds",
    [("composition", "semantic", "layout"), ("layout", "composition", "semantic")],
)
def test_repair_budget_is_shared_and_never_reopens_acquisition(case, kinds):
    controller = case.controller
    final = controller.finalize_information()
    for index, kind in enumerate(kinds[:2], 1):
        assert (
            controller.consume_repair(context_id=final.context_id, kind=kind) == index
        )
    with pytest.raises(NarrativeLifecycleError, match="REPAIR_BUDGET_EXHAUSTED"):
        controller.consume_repair(context_id=final.context_id, kind=kinds[2])
    assert controller.repair_count == 2
    assert controller.finalize_information() == final
    with pytest.raises(NarrativeLifecycleError, match="ROUND_EXHAUSTED"):
        controller.begin_information(analysis(case, count=0))


def test_model_allowance_and_ledger_are_shared_across_repairs(case):
    controller = case.controller
    assert controller.execute_model(transport=case.attempt).status == "ok"
    final = controller.finalize_information()
    for kind in ("composition", "layout"):
        controller.consume_repair(context_id=final.context_id, kind=kind)
        assert controller.execute_model(transport=case.attempt).status == "ok"
    result = controller.execute_model(transport=case.attempt)
    assert result.status != "ok"
    assert result.errors[-1].error_code == "BUDGET_EXHAUSTED"
    assert len(case.attempt.timeouts) == 3
    assert controller._runtime.ledger.snapshot()["calls"] == 3


@pytest.mark.parametrize("action", ["dispatch", "repair", "model"])
def test_absolute_deadline_never_resets(case, action):
    controller = case.controller
    request = controller.begin_information(analysis(case))
    if action == "repair":
        controller.finalize_information()
    prior = controller.context
    case.clock.current += timedelta(seconds=31)
    with pytest.raises(NarrativeLifecycleError, match="DEADLINE_EXCEEDED"):
        match action:
            case "dispatch":
                controller.resolve_information(request)
            case "repair":
                controller.consume_repair(context_id=prior.context_id, kind="layout")
            case "model":
                controller.execute_model(transport=case.attempt)
    assert controller.context == prior
    assert case.attempt.timeouts == []
    assert controller.collection_records == ()
