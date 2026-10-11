"""보고서의 실행 순서와 한도를 관리하는 수명주기·LangGraph 구성.

공개 함수가 준비한 근거를 분석·보강·작성·검증 노드에 순서대로 전달한다.
"""

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
    """자료 보강 한 번과 공유 수정 두 번을 관리하는 실행 소유자.

    근거 검증이 끝난 문맥만 다음 단계에 전달한다. 문맥은 모델이 볼 입력 묶음이다.
    모든 모델 호출은 같은 비용 원장·요청 예산·마감 시간을 사용한다.
    자료 보강이나 보고서 수정은 예산을 초기화하지 않는다.
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
        # 기업·기준일·원문·출처 장부가 이 문맥에 실제로 묶여 있는지 먼저 확인한다.
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
        # 여러 한도가 있으면 가장 이른 마감만 적용해 실행 시간을 늘리지 않는다.
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
        # 전달받은 문맥을 원본 snapshot으로 다시 만들었을 때 같아야 한다.
        # snapshot은 이 단계에서 사용할 원문 저장소의 특정 버전이다.
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
        # 새 자료를 저장하면 기존 chunk도 새 전체 자료 버전을 가리킨다.
        # 문맥 생성 단계에서 그 버전과 원본 연결을 검증했으므로 버전 연결을 맞춘다.
        version = (snapshot or self._snapshot).manifest.version
        for citation in old_citations.values():
            assert isinstance(citation, dict)
            chunks = citation["chunks"]
            assert isinstance(chunks, dict)
            for chunk in chunks.values():
                assert isinstance(chunk, dict)
                chunk["corpus_version"] = version
        # 보강은 근거를 추가할 수 있지만 기존 대상·질문·근거 내용을 바꿀 수는 없다.
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
        """모델의 자료 요청을 검증하고 실제 조사 전에 보강 기회를 소비한다."""
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
        # 조사 실패에도 다시 요청해 무한 반복하지 않도록 전송 전에 소비 상태를 기록한다.
        self._round_consumed = True
        return request

    def resolve_information(self, request: InformationRequest) -> InformationResponse:
        """자료 요청을 한 번 처리하고 검증된 새 문맥을 한꺼번에 반영한다."""
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
        # 중간 검증이 실패하면 여기까지 오지 않아 기존 문맥을 일부만 바꾸지 않는다.
        self._context, self._snapshot = projected, result.snapshot
        return response

    def finalize_information(self) -> CompanyWriterContext:
        """이미 확보한 근거를 유지하며 추가 자료 요청 가능 상태를 닫는다."""
        if self._context.writer_context.may_request_information:
            projected = self._project(self._snapshot, (), ())
            self._check_generation(projected)
            self._context = projected
        return self._context

    def finalize_analysis(self, reply: AnalysisReply) -> AnalysisReply:
        """보강 요청이 없으면 기존 분석 답변을 최종 문맥에 다시 연결해 재사용한다."""
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
        # 요청 가능 상태를 닫으면서 문맥 ID가 바뀐다. 근거가 같음을 확인한 뒤
        # ID를 다시 연결하므로 모델을 한 번 더 호출할 필요가 없다.
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
        """작성·의미·레이아웃의 공유 수정 횟수를 소비한다. 조사는 닫힌 상태다."""
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
        """어떤 모델 역할이든 같은 실행 예산·마감으로 호출하고 사용 기록을 남긴다."""
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
    """노드 간에는 직렬화된 결과만 넘긴다. 실행 환경·키·원장은 클로저에 둔다."""

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
    """분석·선택적 보강·작성·검증을 연결하고 검증된 PDF 결과를 반환한다."""
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
        # 이전 점수형 보고서의 검증 경로가 실수로 호출되면 명시적으로 거절한다.
        raise CompanyReportConfigError("LEGACY_PDF_PROOF_NOT_USED")

    def owner() -> NarrativeReportLifecycle:
        assert lifecycle is not None
        return lifecycle

    def provider(state: CompanyReportState) -> CompanyReportState:
        # 자료가 없으면 그래프를 종료한다. 근거 없이 분석 모델을 호출하지 않는다.
        nonlocal lifecycle
        lifecycle = provide()
        return {"has_evidence": lifecycle is not None}

    def analysis(state: CompanyReportState) -> CompanyReportState:
        # 분석 역할은 18개 질문의 답변과 부족한 자료 요청을 한 응답으로 돌려준다.
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
        # 첫 분석이 요청한 자료만 보강한다. 요청 검증과 한 번 제한은 수명주기가 맡는다.
        run = owner()
        reply = AnalysisReply.model_validate_json(
            state["analysis"], context={"writer_context": run.context.writer_context}
        )
        run.resolve_information(run.begin_information(reply))
        return {}

    def final_analysis(state: CompanyReportState) -> CompanyReportState:
        # 보강 후에는 자료 요청을 닫고 늘어난 근거로 분석을 한 번 다시 수행한다.
        owner().finalize_information()
        return analysis(state)

    def reuse_analysis(state: CompanyReportState) -> CompanyReportState:
        # 보강할 것이 없으면 첫 분석을 재사용해 불필요한 모델 호출을 줄인다.
        run = owner()
        reply = AnalysisReply.model_validate_json(
            state["analysis"], context={"writer_context": run.context.writer_context}
        )
        return {"analysis": run.finalize_analysis(reply).model_dump_json()}

    def composition(state: CompanyReportState) -> CompanyReportState:
        # 작성·Judge의 상세 흐름은 reporting/narrative.py에서 읽을 수 있다.
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
            # 검증된 근거 ID를 실제 참고문헌으로 연결한다.
            # 출처를 모델이 쓰게 하지 않는다.
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
            # 내용·인용 검증에 더해 PDF 레이아웃까지 통과해야 결과를 다음 단계로 넘긴다.
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
            # while 루프지만 무한 수정은 아니다. 다른 수정과 합쳐 두 번을 넘으면 예외다.
            run.consume_repair(context_id=run.context.context_id, kind="layout")

    # 노드는 처리 함수, 간선은 실행 순서다.
    # 조건부 간선이 자료·보강 여부로 갈림길을 정한다.
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
    # 보강 여부와 관계없이 최종 분석 뒤에는 작성·Judge·인용·PDF 단계로 합류한다.
    graph.add_edge("enrichment", "final_analysis")
    graph.add_edge("final_analysis", "composition_judge_citations_pdf")
    graph.add_edge("reuse_analysis", "composition_judge_citations_pdf")
    graph.add_edge("composition_judge_citations_pdf", END)
    return graph.compile().invoke({})
