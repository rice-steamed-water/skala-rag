"""단일 Evidence Research — 최초 수집과 Coverage gap 재조사의 같은 진입점 (#55).

contracts §4·§6·§7, data-rag §1·§4, delivery T07·T13·T25, v3 B-1·B-2·D-3.

- ``EvidenceResearch.run(candidate, gaps, budget)``: gaps가 비면 주입한 최초 계획
  (필수 근거)을, 있으면 해당 후보의 open gap만 조사한다. 별도 Targeted Research는 없다.
  최초 계획의 내용(최소 Evidence gate)은 OPEN 결정이라 기본값을 두지 않는다.
- 도구: RAG ``Retrieve``(#54 ``IndexedRetriever`` 등)와 선택적 Web/API 채널. 도구마다
  필수/선택을 정한다. 필수 도구가 ``unavailable``/``failed``면 batch 실패, 선택 도구는
  RetrievalRecord에 사유를 남기고 계속한다. ``empty``는 실패도 부정 사실도 아니다.
- 추출: #50 ``rag_segment``/``web_segment`` → ``extract_evidence``(StructuredLLM). 금액
  Evidence는 #53 ``to_amount``의 단위 해석을 통과해야 남는다. ``max_segment_bytes``를
  주면 긴 구간을 #163 ``split_segment``로 나눠 추출한다(LLM 입력 상한용, 값 주입).
- 호출 예산: batch ``ToolBudget.max_calls``를 RAG·Web/API 호출이 함께 쓴다(한도 값은
  OPEN이라 호출자가 주입). 남은 계획은 실행하지 않는다. 후보별 재조사 횟수는 Graph
  ``research_gate``(#25)가 센다. LLM 호출은 별도 wrapper 예산이다.
- ``evidence_research_stage``: baseline 후보 Graph의 ``collect`` 단계. Source·Chunk·
  이력을 담고 Evidence를 병합하며, State Evidence가 바뀔 때만 evidence_revision을
  올린다. snapshot은 건드리지 않는다.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol

from skala_rag.agents.evidence_extraction import (
    ExtractionResult,
    SegmentError,
    SourceSegment,
    extract_evidence,
    link_record,
    rag_segment,
    split_segment,
    verify_provenance,
    web_segment,
)
from skala_rag.contracts.candidates import Candidate
from skala_rag.contracts.coverage import ResearchGap
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode
from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.evidence import Evidence
from skala_rag.contracts.interfaces import Clock, LLMError, Retrieve, StructuredLLM
from skala_rag.contracts.retrieval import RetrievalRecord, RetrievalRequest
from skala_rag.contracts.sources import Chunk, Source
from skala_rag.contracts.tools import EvidenceBundle, ToolBudget, ToolResult
from skala_rag.graph.candidates import StageFailure
from skala_rag.graph.reducers import merge_evidence
from skala_rag.scoring.finance import Unavailable, to_amount
from skala_rag.tools.source_fetch import RawSnapshot, check_as_of

Status = Literal["ok", "empty", "unavailable", "failed"]
NODE = "evidence_research"

InitialPlan = Callable[[Candidate], Sequence[ResearchGap]]
"""최초 수집의 필수 근거 계획. gap 형태(대상·질의)로 돌려준다. 기본값 없음."""


@dataclass(frozen=True)
class WebCapture:
    """Web/API 도구가 실제로 받은 원문 한 벌과 그 fetch 이력 ID."""

    retrieval_id: str
    raw: RawSnapshot
    source: Source
    locator: str
    scope: Literal["company", "industry"]
    text: str | None = None


@dataclass(frozen=True)
class WebResult:
    status: Status
    captures: tuple[WebCapture, ...] = ()
    records: tuple[RetrievalRecord, ...] = ()
    errors: tuple[WorkflowError, ...] = ()


class WebSearch(Protocol):
    """질의 하나를 검색·수집한다. 도구 오류는 ``errors``로, 예외로 내지 않는다."""

    def __call__(
        self, candidate: Candidate, query: str, *, as_of: date
    ) -> WebResult: ...


@dataclass(frozen=True)
class WebChannel:
    name: str
    method: Literal["web", "api"]
    required: bool
    search: WebSearch


@dataclass(frozen=True)
class ToolCall:
    """도구 호출 하나의 결과 요약. 원문·질의는 담지 않는다."""

    tool: str
    gap_id: str
    required: bool
    status: Status
    retrieval_ids: tuple[str, ...]
    error_codes: tuple[str, ...]


@dataclass
class ResearchOutcome:
    status: Status
    initial: bool
    sources: dict[str, Source]
    chunks: dict[str, Chunk]
    records: list[RetrievalRecord]
    evidence: dict[str, Evidence]
    gaps: list[ResearchGap]
    calls: list[ToolCall]
    errors: list[WorkflowError]
    rejected: list[str] = field(default_factory=list)
    """거절 사유 코드만 담는다(예: ``claim:SUBJECT_MISMATCH``·``finance:unknown_unit``).
    """
    skipped: int = 0
    """호출 예산 소진으로 실행하지 않은 (gap, 질의, 도구) 수."""


class ResearchFailure(Exception):
    """ToolResult에 담을 수 없는 오류(LLM 등). 메시지에 원문·key를 넣지 않는다."""

    def __init__(self, errors: Sequence[WorkflowError]) -> None:
        super().__init__("evidence research failed")
        self.errors = list(errors)


class EvidenceResearch:
    """``CollectEvidence``. corpus·index·as_of·허용 출처·도구는 생성 시 고정한다."""

    def __init__(
        self,
        *,
        retrieve: Retrieve,
        rag_required: bool,
        llm: StructuredLLM,
        initial_plan: InitialPlan,
        web: Sequence[WebChannel] = (),
        run_id: str,
        corpus_version: str,
        index_version: str,
        as_of: date,
        top_k: int,
        allowed_source_ids: Sequence[str],
        clock: Clock,
        schema_version: str,
        execution_mode: Literal["fixture", "live"],
        rag_tool_name: str = "retrieve",
        max_segment_bytes: int | None = None,
    ) -> None:
        names = [rag_tool_name, *(c.name for c in web)]
        if len(set(names)) != len(names):
            raise ValueError("duplicate research tool name")
        self._retrieve = retrieve
        self._rag = (rag_tool_name, rag_required)
        self._llm = llm
        self._plan = initial_plan
        self._web = tuple(web)
        self._run_id = run_id
        self._corpus_version = corpus_version
        self._index_version = index_version
        self._as_of = as_of
        self._top_k = top_k
        self._allowed_source_ids = sorted(set(allowed_source_ids))
        self._clock = clock
        self._schema_version = schema_version
        self._mode = execution_mode
        if max_segment_bytes is not None and max_segment_bytes < 1:
            raise ValueError("max_segment_bytes must be positive")
        self._max_segment_bytes = max_segment_bytes
        self._context = {"execution_mode": execution_mode}
        self._seq = 0

    @property
    def execution_mode(self) -> Literal["fixture", "live"]:
        return self._mode

    def __call__(
        self, candidate: Candidate, gaps: Sequence[ResearchGap], budget: ToolBudget
    ) -> ToolResult[EvidenceBundle]:
        outcome = self.run(candidate, gaps, budget)
        if any(
            ERROR_SPECS[ErrorCode(e.error_code)].tool_status is None
            for e in outcome.errors
        ):
            raise ResearchFailure(outcome.errors)
        failed = outcome.status in ("unavailable", "failed")
        return ToolResult[EvidenceBundle].model_validate(
            {
                "schema_version": self._schema_version,
                "status": outcome.status,
                "data": None
                if failed
                else {
                    "schema_version": self._schema_version,
                    "sources": {
                        k: v.model_dump(mode="json") for k, v in outcome.sources.items()
                    },
                    "evidence": {
                        k: v.model_dump(mode="json")
                        for k, v in outcome.evidence.items()
                    },
                },
                "retrieval_records": [
                    r.model_dump(mode="json") for r in outcome.records
                ],
                "errors": [e.model_dump(mode="json") for e in outcome.errors]
                if failed
                else [],
            },
            context=self._context,
        )

    # --- 조사 batch ---------------------------------------------------------

    def run(
        self, candidate: Candidate, gaps: Sequence[ResearchGap], budget: ToolBudget
    ) -> ResearchOutcome:
        cid = candidate.candidate_id
        initial = not gaps
        if initial:
            targets = [ResearchGap.model_validate(g) for g in self._plan(candidate)]
        else:
            targets = [g for g in gaps if g.candidate_id == cid and g.status == "open"]
        if any(g.candidate_id != cid for g in targets):
            raise ValueError("research plan targets another candidate")

        tools: list[tuple[str, bool, WebChannel | None]] = [
            (self._rag[0], self._rag[1], None),
            *((c.name, c.required, c) for c in self._web),
        ]
        plan = [
            (gap, query, tool)
            for gap in targets
            for query in gap.suggested_queries
            for tool in tools
        ]
        out = ResearchOutcome(
            status="empty",
            initial=initial,
            sources={},
            chunks={},
            records=[],
            evidence={},
            gaps=[g.model_copy(deep=True) for g in targets],
            calls=[],
            errors=[],
        )
        if plan and budget.max_calls < 1:
            out.errors.append(self._error(cid, ErrorCode.BUDGET_EXHAUSTED))
            out.status = "failed"
            return out

        records: dict[str, RetrievalRecord] = {}
        evidence: dict[str, Any] = {}
        attempted: dict[str, list[str]] = {g.gap_id: [] for g in targets}
        for index, (gap, query, (name, required, channel)) in enumerate(plan):
            if index >= budget.max_calls:
                out.skipped = len(plan) - index
                break
            try:
                if channel is None:
                    call = self._rag_call(candidate, gap, query, records, out, evidence)
                else:
                    call = self._web_call(
                        candidate, gap, query, channel, records, out, evidence
                    )
            except LLMError as err:
                out.errors.append(
                    self._error(cid, err.error_code, err.message_redacted)
                )
                out.status = "failed"
                break
            attempted[gap.gap_id].extend(call.retrieval_ids)
            out.calls.append(call)
            if call.status in ("unavailable", "failed") and required:
                out.status = call.status
                break

        out.records = list(records.values())
        out.evidence = {
            k: Evidence.model_validate(v, context=self._context)
            for k, v in evidence.items()
        }
        out.gaps = [
            g.model_copy(
                update={
                    "attempted_retrieval_ids": [
                        *g.attempted_retrieval_ids,
                        *attempted[g.gap_id],
                    ]
                }
            )
            for g in out.gaps
        ]
        for item in out.evidence.values():
            problems = verify_provenance(item, records=records, chunks=out.chunks)
            if problems:
                raise ValueError(f"provenance 검증 실패: {problems}")
        if out.status == "empty" and out.evidence:
            out.status = "ok"
        return out

    def _rag_call(self, candidate, gap, query, records, out, evidence) -> ToolCall:
        name, required = self._rag
        result = self._retrieve(
            RetrievalRequest(
                schema_version=self._schema_version,
                query=query,
                candidate_id=candidate.candidate_id,
                corpus_version=self._corpus_version,
                index_version=self._index_version,
                as_of=self._as_of,
                top_k=self._top_k,
                allowed_source_ids=self._allowed_source_ids,
            )
        )
        got = self._keep(
            candidate,
            gap,
            name,
            required,
            result.status,
            result.retrieval_records,
            result.errors,
            records,
            out,
        )
        if result.status != "ok":
            return got
        bundle = result.data
        record = next(
            (
                records[r]
                for r in reversed(got.retrieval_ids)
                if records[r].status == "ok"
            ),
            None,
        )
        if record is None:
            return self._invalid(candidate, gap, name, required, got, records, out)
        # Source/Chunk를 먼저 담고, 그다음 Evidence를 추출·병합한다.
        for chunk in bundle.chunks:
            out.chunks[chunk.chunk_id] = chunk
            out.sources[chunk.source_id] = bundle.sources[chunk.source_id]
        try:
            segments = [
                rag_segment(c, bundle, record, schema_version=self._schema_version)
                for c in bundle.chunks
            ]
        except SegmentError:
            return self._invalid(candidate, gap, name, required, got, records, out)
        for segment in segments:
            self._extract(candidate, gap, segment, records, out, evidence)
        return got

    def _web_call(
        self, candidate, gap, query, channel: WebChannel, records, out, evidence
    ) -> ToolCall:
        result = channel.search(candidate, query, as_of=self._as_of)
        got = self._keep(
            candidate,
            gap,
            channel.name,
            channel.required,
            result.status,
            result.records,
            result.errors,
            records,
            out,
        )
        if result.status != "ok":
            return got
        segments = []
        try:
            for capture in result.captures:
                if capture.retrieval_id not in got.retrieval_ids:
                    raise SegmentError("capture 이력이 이번 호출 결과가 아님")
                decision = check_as_of(capture.source, self._as_of)
                if not decision.admitted:
                    out.rejected.append(f"as_of:{decision.reason}")
                    continue
                segments.append(
                    web_segment(
                        capture.raw,
                        capture.source,
                        records[capture.retrieval_id],
                        locator=capture.locator,
                        scope=capture.scope,
                        schema_version=self._schema_version,
                        method=channel.method,
                        text=capture.text,
                    )
                )
        except SegmentError:
            return self._invalid(
                candidate, gap, channel.name, channel.required, got, records, out
            )
        for segment in segments:
            out.sources[segment.source.source_id] = segment.source
            self._extract(candidate, gap, segment, records, out, evidence)
        return got

    def _extract(
        self,
        candidate: Candidate,
        gap: ResearchGap,
        segment: SourceSegment,
        records: dict[str, RetrievalRecord],
        out: ResearchOutcome,
        evidence: dict[str, Any],
    ) -> None:
        limit = self._max_segment_bytes
        if limit is not None and len(segment.text.encode()) > limit:
            # LLM 요청당 입력 상한에 맞춰 나눈다. 조각은 원문 부분 문자열이다(#163).
            for piece in split_segment(segment, limit):
                self._extract_one(candidate, gap, piece, records, out, evidence)
        else:
            self._extract_one(candidate, gap, segment, records, out, evidence)

    def _extract_one(
        self,
        candidate: Candidate,
        gap: ResearchGap,
        segment: SourceSegment,
        records: dict[str, RetrievalRecord],
        out: ResearchOutcome,
        evidence: dict[str, Any],
    ) -> None:
        result = extract_evidence(
            segment,
            llm=self._llm,
            candidate=candidate,
            criterion_ids=[gap.criterion_id] if gap.criterion_id else [],
            as_of=self._as_of,
            schema_version=self._schema_version,
            execution_mode=self._mode,
        )
        out.rejected.extend(f"claim:{r.reason}" for r in result.rejected)
        kept = {}
        for key, item in result.evidence.items():
            if item.currency is not None:
                amount = to_amount(item)
                if isinstance(amount, Unavailable):
                    out.rejected.append(f"finance:{amount.reason}")
                    continue
            kept[key] = item
        result = ExtractionResult(evidence=kept, rejected=result.rejected)
        record_id = segment.provenance.retrieval_id
        records[record_id] = link_record(records[record_id], result, segment)
        evidence.update(
            merge_evidence(
                evidence, {k: v.model_dump(mode="json") for k, v in kept.items()}
            )
        )

    # --- 이력·오류 ----------------------------------------------------------

    def _keep(
        self,
        candidate: Candidate,
        gap: ResearchGap,
        tool: str,
        required: bool,
        status: Status,
        returned: Sequence[RetrievalRecord],
        errors: Sequence[WorkflowError],
        records: dict[str, RetrievalRecord],
        out: ResearchOutcome,
    ) -> ToolCall:
        """도구가 돌려준 이력을 복사해 조사 맥락을 붙인다. 이력 없는 오류는 합성한다."""
        errors = [WorkflowError.model_validate(e) for e in errors]
        copies = [
            RetrievalRecord.model_validate(r).model_copy(deep=True) for r in returned
        ]
        if not copies and status != "ok":
            now = self._clock.now()
            self._seq += 1
            copies = [
                RetrievalRecord(
                    schema_version=self._schema_version,
                    retrieval_id=f"retrieval-{NODE}-{self._run_id}-{self._seq}",
                    run_id=self._run_id,
                    candidate_id=candidate.candidate_id,
                    tool_name=tool,
                    query=None,
                    arguments_without_secrets={},
                    started_at=now,
                    finished_at=now,
                    status=status,
                    source_ids=[],
                    chunk_ids=[],
                    evidence_ids=[],
                    error_id=errors[0].error_id if errors else None,
                    cost=None,
                    cache_hit=False,
                )
            ]
        for record in copies:
            if record.retrieval_id in records:
                raise ValueError("duplicate retrieval_id")
            record.arguments_without_secrets.update(
                research_tool=tool,
                research_required=required,
                research_gap_id=gap.gap_id,
                research_initial=out.initial,
                research_error_codes=[e.error_code for e in errors],
            )
            records[record.retrieval_id] = record
        if status in ("unavailable", "failed"):
            out.errors.extend(errors)
        return ToolCall(
            tool=tool,
            gap_id=gap.gap_id,
            required=required,
            status=status,
            retrieval_ids=tuple(r.retrieval_id for r in copies),
            error_codes=tuple(e.error_code for e in errors),
        )

    def _invalid(self, candidate, gap, tool, required, got, records, out) -> ToolCall:
        """구간 검증 실패는 도구 응답 위반이다. 성공 이력을 실패로 바꾸지 않는다."""
        error = self._error(candidate.candidate_id, ErrorCode.TOOL_RESPONSE_INVALID)
        out.errors.append(error)
        return ToolCall(
            tool=tool,
            gap_id=gap.gap_id,
            required=required,
            status="failed",
            retrieval_ids=got.retrieval_ids,
            error_codes=(*got.error_codes, error.error_code),
        )

    def _error(
        self, candidate_id: str, code: ErrorCode | str, message: str | None = None
    ) -> WorkflowError:
        code = ErrorCode(code)
        self._seq += 1
        return WorkflowError(
            schema_version=self._schema_version,
            error_id=f"error-{NODE}-{self._run_id}-{self._seq}",
            run_id=self._run_id,
            candidate_id=candidate_id,
            node=NODE,
            error_code=code,
            message_redacted=message or f"evidence research {code.value}",
            retryable=ERROR_SPECS[code].retryable,
            attempt=1,
            timestamp=self._clock.now(),
        )


# --- baseline 후보 Graph collect 단계 --------------------------------------------


def evidence_research_stage(
    research: EvidenceResearch, *, budget: ToolBudget
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """``CandidateNodes.collect``. 최초 호출(재조사 count 0)은 gaps=[]로 계획을 쓴다.

    필수 도구 실패·LLM 오류는 ``StageFailure``다. Graph가 최초 수집이면 후보 failed,
    재조사면 count를 되돌리지 않고 gap을 missing으로 둔다(#25).
    """

    def collect(state: Mapping[str, Any]) -> dict[str, Any]:
        cid = state["current_candidate_id"]
        payload = next(c for c in state["candidates"] if c["candidate_id"] == cid)
        candidate = Candidate.model_validate(
            payload, context={"execution_mode": research.execution_mode}
        )
        gaps = []
        if state["research_retry_count"][cid] > 0:
            gaps = [
                ResearchGap.model_validate(g)
                for g in state["research_gaps"].get(cid, [])
                if g["status"] == "open"
            ]
            if not gaps:
                return {}
        outcome = research.run(candidate, gaps, budget)
        if outcome.status in ("unavailable", "failed"):
            raise StageFailure(outcome.errors)

        incoming = {k: v.model_dump(mode="json") for k, v in outcome.evidence.items()}
        current = state.get("evidence", {})
        merged = merge_evidence(current, incoming)
        delta: dict[str, Any] = {
            "sources": {
                k: v.model_dump(mode="json") for k, v in outcome.sources.items()
            },
            "chunks": {k: v.model_dump(mode="json") for k, v in outcome.chunks.items()},
            "retrieval_history": [
                *state.get("retrieval_history", []),
                *(r.model_dump(mode="json") for r in outcome.records),
            ],
        }
        if incoming:
            delta["evidence"] = incoming
        if any(current.get(k) != merged[k] for k in incoming):
            revisions = state["evidence_revisions"]
            delta["evidence_revisions"] = {cid: revisions.get(cid, 0) + 1}
        return delta

    return collect
