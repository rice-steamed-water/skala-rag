"""Controller-owned narrative lifecycle; public graph assembly belongs to task 10."""

import json
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from skala_rag.agents.company_report_research import (
    NarrativeResearchService,
    ResearchRuntimeBinding,
)
from skala_rag.contracts import RetrievalRecord
from skala_rag.contracts.company_report import (
    AnalysisReply,
    InformationRequest,
    InformationResponse,
    InformationResult,
)
from skala_rag.contracts.tools import ToolResult
from skala_rag.rag.company_store import StoreSnapshot
from skala_rag.reporting.company_context import (
    CompanyWriterContext,
    IssuerAssociation,
    build_writer_context,
)
from skala_rag.tools.runtime import Allowance, SingleAttempt

if TYPE_CHECKING:
    from skala_rag.reporting.narrative import NarrativeModelSettings
    from skala_rag.reporting.pdf import PdfProfile

ModelT = TypeVar("ModelT", bound=BaseModel)


class NarrativeLifecycleError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class NarrativeReportLifecycle:
    """Mutable run owner: one consumed enrichment round and two shared repairs.

    Detached context bytes are published only after complete admission. The same
    runtime ledger, allowance and absolute deadline govern every model
    attempt; neither repairs nor acquisition construct a fresh runtime.
    """

    def __init__(
        self,
        *,
        context: CompanyWriterContext,
        snapshot: StoreSnapshot,
        service: NarrativeResearchService,
        binding: ResearchRuntimeBinding,
        allowance: Allowance,
        issuer_association: IssuerAssociation | None = None,
    ) -> None:
        context.verify_binding()
        writer = context.writer_context
        if (
            service.candidate.candidate_id != writer.target.subject_id
            or service.as_of != writer.as_of
            or service.run_id != binding.call.run_id
            or service.clock is not binding.runtime.clock
            or binding.budget.deadline is None
            or (
                service.admission is not None
                and service.admission.runtime_binding.runtime is not binding.runtime
            )
        ):
            raise NarrativeLifecycleError("NARRATIVE_RUN_BINDING_MISMATCH")
        self._context = context
        self._snapshot = snapshot
        self._issuer_association = issuer_association
        self._runtime = binding.runtime
        self._call = binding.call.model_copy(deep=True)
        self._readiness = binding.readiness.model_copy(deep=True)
        self._allowance = Allowance.model_validate(allowance).model_copy(deep=True)
        deadline = min(service.deadline, binding.budget.deadline)
        if service.admission is not None:
            deadline = min(deadline, service.admission.deadline)
        self._deadline = deadline
        self._budget = binding.budget.model_copy(
            update={"deadline": deadline}, deep=True
        )
        self._round_consumed = bool(writer.information_results)
        self._repairs = 0
        self._request: InformationRequest | None = None
        self._dispatched = False
        self.collection_records: tuple[RetrievalRecord, ...] = ()
        self.model_records: list[RetrievalRecord] = []
        rebuilt = self._project(
            snapshot,
            tuple(writer.evidence),
            tuple(writer.information_results),
            may_request=writer.may_request_information,
        )
        if rebuilt != context:
            raise NarrativeLifecycleError("NARRATIVE_SNAPSHOT_BINDING_MISMATCH")

        def admit(
            retained: StoreSnapshot,
            ids: tuple[str, ...],
            records: tuple[RetrievalRecord, ...],
        ) -> tuple[str, ...]:
            projected = self._project(retained, ids, ())
            self._check_generation(projected, retained)
            return ids

        self._service = replace(service, deadline=deadline, admit=admit)

    @property
    def context(self) -> CompanyWriterContext:
        return self._context

    @property
    def information_rounds(self) -> int:
        return int(self._round_consumed)

    @property
    def repair_count(self) -> int:
        return self._repairs

    def _project(
        self,
        snapshot: StoreSnapshot,
        ids: tuple[str, ...],
        results: tuple[InformationResult, ...],
        *,
        may_request: bool = False,
    ) -> CompanyWriterContext:
        writer = self._context.writer_context
        return build_writer_context(
            snapshot,
            company_name=writer.target.name,
            as_of=writer.as_of,
            questions=writer.questions,
            evidence_ids=tuple(dict.fromkeys((*writer.evidence, *ids))),
            issuer_association=self._issuer_association,
            may_request_information=may_request,
            information_results=results,
        )

    def _check_generation(
        self,
        projected: CompanyWriterContext,
        snapshot: StoreSnapshot | None = None,
    ) -> None:
        projected.verify_binding()
        old, new = self._context.writer_context, projected.writer_context
        old_citations = self._context.citation_registry["citations"]
        new_citations = projected.citation_registry["citations"]
        assert isinstance(old_citations, dict) and isinstance(new_citations, dict)
        # Store publication rebinds every unchanged chunk to the complete corpus.
        # Projection has already verified that version and all original lineage.
        version = (snapshot or self._snapshot).manifest.version
        for citation in old_citations.values():
            assert isinstance(citation, dict)
            chunks = citation["chunks"]
            assert isinstance(chunks, dict)
            for chunk in chunks.values():
                assert isinstance(chunk, dict)
                chunk["corpus_version"] = version
        if (
            old.target != new.target
            or old.as_of != new.as_of
            or old.questions != new.questions
            or any(new.evidence.get(eid) != item for eid, item in old.evidence.items())
            or any(
                new_citations.get(eid) != citation
                for eid, citation in old_citations.items()
            )
        ):
            raise NarrativeLifecycleError("NARRATIVE_GENERATION_CHANGED")

    def _check_deadline(self) -> None:
        if self._runtime.clock.now() >= self._deadline:
            raise NarrativeLifecycleError("NARRATIVE_DEADLINE_EXCEEDED")

    def begin_information(self, reply: AnalysisReply) -> InformationRequest:
        """Validate model needs, then consume the round before any dispatch."""
        if (
            self._round_consumed
            or not self._context.writer_context.may_request_information
        ):
            raise NarrativeLifecycleError("INFORMATION_ROUND_EXHAUSTED")
        self._check_deadline()
        writer = self._context.writer_context
        parsed = AnalysisReply.model_validate(
            reply.model_dump(), context={"writer_context": writer}
        )
        request = InformationRequest.model_validate(
            {
                "schema_version": "narrative-report-1",
                "request_id": f"information-{uuid4().hex}",
                "base_context_id": writer.context_id,
                "needs": parsed.needs,
            },
            context={"writer_context": writer},
        )
        self._request = request.model_copy(deep=True)
        self._round_consumed = True
        return request

    def resolve_information(self, request: InformationRequest) -> InformationResponse:
        """Dispatch once and atomically publish the controller-built result context."""
        writer = self._context.writer_context
        parsed = InformationRequest.model_validate(
            request.model_dump(), context={"writer_context": writer}
        )
        if self._dispatched or self._request is None or parsed != self._request:
            raise NarrativeLifecycleError("INFORMATION_DISPATCH_REJECTED")
        self._check_deadline()
        self._dispatched = True
        manifest = self._snapshot.manifest
        result = self._service.resolve(
            parsed.needs,
            evidence={eid: manifest.evidence[eid] for eid in writer.evidence},
            sources=manifest.sources,
            snapshot=self._snapshot,
        )
        self.collection_records = result.collection_records
        self._check_deadline()
        if (
            self._context.context_id != parsed.base_context_id
            or result.snapshot is None
        ):
            raise NarrativeLifecycleError("STALE_INFORMATION_RESULT")
        projected = self._project(
            result.snapshot, tuple(result.evidence), result.results
        )
        self._check_generation(projected, result.snapshot)
        response = InformationResponse.model_validate(
            {
                "schema_version": "narrative-report-1",
                "request_id": parsed.request_id,
                "base_context_id": parsed.base_context_id,
                "result_context_id": projected.context_id,
                "results": list(result.results),
            },
            context={
                "writer_context": projected.writer_context,
                "information_request": self._request,
            },
        )
        self._context, self._snapshot = projected, result.snapshot
        return response

    def finalize_information(self) -> CompanyWriterContext:
        """Close unused/failed acquisition permanently without discarding evidence."""
        if self._context.writer_context.may_request_information:
            projected = self._project(self._snapshot, (), ())
            self._check_generation(projected)
            self._context = projected
        return self._context

    def finalize_analysis(self, reply: AnalysisReply) -> AnalysisReply:
        """Rebind validated no-needs answers after closing unused acquisition."""
        parsed = AnalysisReply.model_validate(
            reply.model_dump(),
            context={"writer_context": self._context.writer_context},
        )
        if parsed.needs:
            raise NarrativeLifecycleError("UNRESOLVED_INFORMATION_NEEDS")
        self._check_deadline()
        prior = self._context
        final = self.finalize_information()
        if (
            final.writer_context.evidence != prior.writer_context.evidence
            or final.writer_context.information_results
            != prior.writer_context.information_results
        ):
            raise NarrativeLifecycleError("NARRATIVE_GENERATION_CHANGED")
        return AnalysisReply.model_validate(
            {**parsed.model_dump(), "context_id": final.context_id},
            context={"writer_context": final.writer_context},
        )

    def consume_repair(
        self,
        *,
        context_id: str,
        kind: Literal["composition", "semantic", "layout"],
    ) -> int:
        """All repair stages consume the same counter; acquisition stays closed."""
        if (
            context_id != self._context.context_id
            or self._context.writer_context.may_request_information
        ):
            raise NarrativeLifecycleError("REPAIR_CONTEXT_NOT_FINAL")
        self._check_deadline()
        if self._repairs >= 2:
            raise NarrativeLifecycleError("REPAIR_BUDGET_EXHAUSTED")
        self._repairs += 1
        return self._repairs

    def execute_model(self, *, transport: SingleAttempt[ModelT]) -> ToolResult[ModelT]:
        """Run a task-10 role adapter under the same captured allowance/deadline."""
        self._check_deadline()
        self._context.verify_binding()
        writer = self._context.writer_context
        result = self._runtime.execute(
            self._call,
            budget=self._budget,
            readiness=self._readiness,
            allowance=self._allowance,
            transport=transport,
        )
        self.model_records.extend(result.retrieval_records)
        self._check_deadline()
        if self._context.context_id != writer.context_id:
            raise NarrativeLifecycleError("STALE_MODEL_RESULT")
        return result


class CompanyReportState(TypedDict, total=False):
    """Only serialized outputs cross graph edges; runtime owners stay in closures."""

    has_evidence: bool
    needs_information: bool
    analysis: str
    composition: str
    judgment: str
    render: str
    pdf_validation: str
    markdown: str
    pdf_path: str
    references: str


def run_narrative_graph(
    *,
    provide: Callable[[], NarrativeReportLifecycle | None],
    settings: "NarrativeModelSettings",
    output_dir: Path,
    pdf_profile: "PdfProfile",
) -> CompanyReportState:
    """Run the production roles, finite enrichment and validation-gated PDF path."""
    from langgraph.graph import END, START, StateGraph

    from skala_rag.agents.company_analysis import (
        CompanyAnalysisAttempt,
        analyze_company,
    )
    from skala_rag.contracts import ReportDraft, ReportJudgement, ValidationResult
    from skala_rag.contracts.company_report import CompanyReportConfigError
    from skala_rag.reporting.format import finalize_narrative
    from skala_rag.reporting.narrative import compose_and_judge
    from skala_rag.reporting.pdf import PDFLayoutValidator, PDFRenderer

    lifecycle: NarrativeReportLifecycle | None = None

    def legacy_proof(draft: ReportDraft) -> tuple[ValidationResult, ReportJudgement]:
        raise CompanyReportConfigError("LEGACY_PDF_PROOF_NOT_USED")

    def owner() -> NarrativeReportLifecycle:
        assert lifecycle is not None
        return lifecycle

    def provider(state: CompanyReportState) -> CompanyReportState:
        nonlocal lifecycle
        lifecycle = provide()
        return {"has_evidence": lifecycle is not None}

    def analysis(state: CompanyReportState) -> CompanyReportState:
        run = owner()
        result = analyze_company(
            run,
            transport=CompanyAnalysisAttempt(
                run.context,
                llm_settings=settings.llm_settings,
                clock=run._runtime.clock,
                max_output_tokens=settings.max_output_tokens,
                allowance=run._allowance,
                api_key=settings.api_key,
                http_transport=settings.http_transport,
            ),
        )
        if result.data is None:
            raise CompanyReportConfigError(result.errors[-1].error_code)
        return {
            "analysis": result.data.model_dump_json(),
            "needs_information": bool(result.data.needs),
        }

    def enrichment(state: CompanyReportState) -> CompanyReportState:
        run = owner()
        reply = AnalysisReply.model_validate_json(
            state["analysis"], context={"writer_context": run.context.writer_context}
        )
        run.resolve_information(run.begin_information(reply))
        return {}

    def final_analysis(state: CompanyReportState) -> CompanyReportState:
        owner().finalize_information()
        return analysis(state)

    def reuse_analysis(state: CompanyReportState) -> CompanyReportState:
        run = owner()
        reply = AnalysisReply.model_validate_json(
            state["analysis"], context={"writer_context": run.context.writer_context}
        )
        return {"analysis": run.finalize_analysis(reply).model_dump_json()}

    def composition(state: CompanyReportState) -> CompanyReportState:
        run = owner()
        reply = AnalysisReply.model_validate_json(
            state["analysis"], context={"writer_context": run.context.writer_context}
        )
        while True:
            result = compose_and_judge(
                run,
                reply,
                settings=settings,
                feedback=("composition",) if run.repair_count else (),
            )
            finalized = finalize_narrative(result.composition, run.context)
            rendered = PDFRenderer(
                profile=pdf_profile,
                output_dir=output_dir / "drafts" / f"revision-{run.repair_count}",
                proof=legacy_proof,
                execution_mode="live",
            ).render_narrative(result, run.context, pdf_profile.version)
            if rendered.artifact_path is None:
                raise CompanyReportConfigError("PDF_RENDER_FAILED")
            validated = PDFLayoutValidator().validate_narrative(
                result, run.context, rendered
            )
            if validated.valid:
                return {
                    "composition": result.composition.model_dump_json(),
                    "judgment": result.judgment.model_dump_json(),
                    "render": rendered.model_dump_json(),
                    "pdf_validation": validated.model_dump_json(),
                    "markdown": finalized.markdown,
                    "pdf_path": rendered.artifact_path,
                    "references": json.dumps(
                        {
                            "context_id": finalized.context_id,
                            "references": [
                                asdict(reference) for reference in finalized.references
                            ],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                }
            run.consume_repair(context_id=run.context.context_id, kind="layout")

    graph = StateGraph(CompanyReportState)
    graph.add_node("provider", provider)
    graph.add_node("analysis", analysis)
    graph.add_node("enrichment", enrichment)
    graph.add_node("final_analysis", final_analysis)
    graph.add_node("reuse_analysis", reuse_analysis)
    graph.add_node("composition_judge_citations_pdf", composition)
    graph.add_edge(START, "provider")
    graph.add_conditional_edges(
        "provider",
        lambda state: "analysis" if state["has_evidence"] else END,
    )
    graph.add_conditional_edges(
        "analysis",
        lambda state: "enrichment" if state["needs_information"] else "reuse_analysis",
    )
    graph.add_edge("enrichment", "final_analysis")
    graph.add_edge("final_analysis", "composition_judge_citations_pdf")
    graph.add_edge("reuse_analysis", "composition_judge_citations_pdf")
    graph.add_edge("composition_judge_citations_pdf", END)
    return graph.compile().invoke({})
