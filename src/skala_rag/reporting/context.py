"""ReportContext 조립·검증 — #26, contracts §5, T23.

보고서 controller가 ``ReportInput``과 완료된 State(JSON payload map)로부터
해소된 DTO payload만 담은 고정 context를 만든다. Generator·Validator·Judge는
이 context 밖의 State·저장소·인터넷을 조회하지 않는다.

- 참조 누락·다른 세대 점수·없는 decision·무효화된 적격성 근거·누락 Source
  → ``CONTEXT_INVALID``
- upstream 산출물 자체가 틀림(점수·판정이 평가와 불일치, payload schema 오류)
  → ``UPSTREAM_INVALID`` — 보고서 LLM이 고치지 않고 workflow failed.
"""

import hashlib
import json
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any, Literal

from pydantic import ValidationError

from skala_rag.contracts import (
    Chunk,
    EligibilityResult,
    Evaluation,
    EvaluationSnapshot,
    Evidence,
    InvestmentDecision,
    ReportContext,
    ReportInput,
    RetrievalRecord,
    ScoreSummary,
    Source,
    WorkflowError,
    evaluation_key,
)
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.scoring.aggregate import ScoringError, aggregate_scores
from skala_rag.scoring.catalog import ScoringPolicy
from skala_rag.scoring.decide import decide

DIMENSIONS = ("founder", "market", "technology", "moat", "traction", "deal_terms")
DECIDED = {"recommend": "RECOMMEND", "watchlist": "WATCHLIST", "pass": "PASS"}


class ReportContextError(ValueError):
    """context 조립 실패. code는 CONTEXT_INVALID 또는 UPSTREAM_INVALID."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code


def _context(message: str) -> ReportContextError:
    return ReportContextError(ErrorCode.CONTEXT_INVALID, message)


def _upstream(message: str) -> ReportContextError:
    return ReportContextError(ErrorCode.UPSTREAM_INVALID, message)


_MODE: ContextVar[dict[str, str] | None] = ContextVar("report_ctx_mode", default=None)


def _parse(model: type, payload: Any, what: str) -> Any:
    try:
        return model.model_validate(payload, context=_MODE.get())
    except ValidationError as err:
        raise _upstream(f"{what} payload schema 오류 ({err.error_count()}건)") from err


def context_id_for(report_input: ReportInput) -> str:
    """ReportInput 내용에 대한 결정적 context ID."""
    payload = json.dumps(
        ["skala-rag-context-v1", report_input.model_dump(mode="json")],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"context-v1-{hashlib.sha256(payload).hexdigest()}"


class _Collector:
    """State에서 필요한 payload만 모은다."""

    def __init__(self, state: Mapping[str, Any]) -> None:
        self.state = state
        self.snapshots: dict[str, EvaluationSnapshot] = {}
        self.eligibility: dict[str, EligibilityResult] = {}
        self.evaluations: dict[str, Evaluation] = {}
        self.summaries: dict[str, ScoreSummary] = {}
        self.decisions: dict[str, InvestmentDecision] = {}
        self.evidence: dict[str, Evidence] = {}
        self.sources: dict[str, Source] = {}
        self.chunks: dict[str, Chunk] = {}
        self.records: dict[str, RetrievalRecord] = {}
        self.errors: dict[str, WorkflowError] = {}

    def state_map(self, name: str) -> Mapping[str, Any]:
        value = self.state.get(name, {})
        if not isinstance(value, Mapping):
            raise _upstream(f"State.{name}가 map이 아니다")
        return value

    # --- 근거와 출처 ------------------------------------------------------

    def superseded_ids(self) -> set[str]:
        return {
            p["supersedes"]
            for p in self.state_map("evidence").values()
            if isinstance(p, Mapping) and p.get("supersedes")
        }

    def add_evidence(
        self,
        ev: Evidence,
        *,
        sources: Mapping[str, Source],
        chunks: Mapping[str, Chunk],
        records: Mapping[str, RetrievalRecord],
    ) -> None:
        if ev.source_id not in sources:
            raise _context(f"Source 누락: {ev.evidence_id} → {ev.source_id}")
        self.evidence[ev.evidence_id] = ev
        self.sources[ev.source_id] = sources[ev.source_id]
        for path in ev.provenance:
            record = records.get(path.retrieval_id)
            if record is None:
                raise _context(
                    f"검색 기록 누락: {ev.evidence_id} → {path.retrieval_id}"
                )
            self.records[record.retrieval_id] = record
            if path.chunk_id is not None:
                chunk = chunks.get(path.chunk_id)
                if chunk is None or path.chunk_id not in record.chunk_ids:
                    raise _context(f"Chunk 누락: {ev.evidence_id} → {path.chunk_id}")
                self.chunks[chunk.chunk_id] = chunk

    def state_payloads(self) -> tuple[dict, dict, dict]:
        sources = {
            k: _parse(Source, v, f"source {k}")
            for k, v in self.state_map("sources").items()
        }
        chunks = {
            k: _parse(Chunk, v, f"chunk {k}")
            for k, v in self.state_map("chunks").items()
        }
        records = {}
        for payload in self.state.get("retrieval_history") or []:
            record = _parse(RetrievalRecord, payload, "retrieval record")
            records[record.retrieval_id] = record
        return sources, chunks, records


def _check_generation(
    candidate_id: str,
    summary: ScoreSummary,
    snapshot: EvaluationSnapshot,
    evaluations: list[Evaluation],
    report_input: ReportInput,
    rounds: Mapping[str, Any],
) -> None:
    if summary.run_id != report_input.run_id:
        raise _context(f"{candidate_id}: 다른 실행의 점수")
    if summary.policy_version != report_input.policy_version:
        raise _context(f"{candidate_id}: 점수 policy_version 불일치")
    final_round = rounds.get(candidate_id)
    if final_round is not None and summary.evaluation_round != final_round:
        raise _context(
            f"{candidate_id}: 최종 세대 {final_round}가 아닌 점수"
            f"(round {summary.evaluation_round})"
        )
    for field in ("run_id", "candidate_id", "evaluation_round", "evidence_revision"):
        if getattr(snapshot, field) != getattr(summary, field):
            raise _context(f"{candidate_id}: 점수와 snapshot 세대 불일치({field})")
    for ev in evaluations:
        for field in ("snapshot_id", "evaluation_round", "evidence_revision"):
            if getattr(ev, field) != getattr(summary, field):
                raise _context(f"{candidate_id}: 평가와 점수 세대 불일치({field})")


def _check_upstream(
    candidate_id: str,
    summary: ScoreSummary,
    decision: InvestmentDecision,
    evaluations: list[Evaluation],
    policy: ScoringPolicy,
) -> None:
    """평가 → 점수 → 판정을 다시 계산해 upstream 산출물을 대조한다."""
    try:
        breakdown = aggregate_scores(evaluations, policy)
    except ScoringError as err:
        raise _upstream(f"{candidate_id}: 평가 집계 불가 — {err}") from err
    if (
        breakdown.observed_score != summary.observed_score
        or breakdown.missing_weight != summary.missing_weight
        or breakdown.dimension_ratings != dict(summary.dimension_ratings)
    ):
        raise _upstream(f"{candidate_id}: ScoreSummary가 평가 재계산과 다르다")
    expected = decide(
        summary.observed_score,
        summary.missing_weight,
        summary.dimension_ratings,
        policy.thresholds,
    )
    if (decision.label, decision.report_grade) != (
        expected.label,
        expected.report_grade,
    ):
        raise _upstream(f"{candidate_id}: 판정이 점수 재계산과 다르다")


def build_report_context(
    report_input: ReportInput,
    state: Mapping[str, Any],
    *,
    policy: ScoringPolicy | None = None,
    execution_mode: Literal["fixture", "live"] | None = None,
) -> ReportContext:
    """ReportInput과 최종 State에서 고정 ReportContext를 만든다.

    ``policy``를 주면 평가·점수·판정을 재계산해 upstream 일치도 확인한다.
    ``execution_mode``가 없으면 State.run_input.execution_mode를 쓰고, 그것도
    없으면 live로 본다(``fixture://`` locator 거절).
    """
    if execution_mode is None:
        run_input = state.get("run_input") or {}
        execution_mode = run_input.get("execution_mode", "live")
    token = _MODE.set({"execution_mode": execution_mode})
    try:
        return _build(report_input, state, policy)
    finally:
        _MODE.reset(token)


def _build(
    report_input: ReportInput,
    state: Mapping[str, Any],
    policy: ScoringPolicy | None,
) -> ReportContext:
    c = _Collector(state)
    sources, chunks, records = c.state_payloads()
    superseded = c.superseded_ids()
    state_evidence = c.state_map("evidence")
    state_errors = {}
    for payload in state.get("errors") or []:
        err = _parse(WorkflowError, payload, "workflow error")
        state_errors[err.error_id] = err
    rounds = c.state_map("evaluation_rounds")

    for outcome in report_input.candidate_outcomes:
        cid = outcome.candidate_id

        for fid in outcome.failure_ids:
            if fid not in state_errors:
                raise _context(f"{cid}: 없는 오류 ID {fid}")
            if state_errors[fid].run_id != report_input.run_id:
                raise _context(f"{cid}: 다른 실행의 오류 {fid}")
            c.errors[fid] = state_errors[fid]
        if outcome.status == "failed" and not outcome.failure_ids:
            raise _context(f"{cid}: failed 후보에 오류 ID가 없다")

        if outcome.eligibility_result_id is not None:
            payload = c.state_map("eligibility_results").get(cid)
            if payload is None:
                raise _context(f"{cid}: 적격성 결과 없음")
            er = _parse(EligibilityResult, payload, f"eligibility {cid}")
            if er.eligibility_result_id != outcome.eligibility_result_id:
                raise _context(f"{cid}: 적격성 결과 ID 불일치")
            if er.run_id != report_input.run_id:
                raise _context(f"{cid}: 다른 실행의 적격성 결과")
            for eid in er.evidence_ids:
                if eid in superseded:
                    raise _context(f"{cid}: 정정으로 무효화된 적격성 근거 {eid}")
                if eid not in state_evidence:
                    raise _context(f"{cid}: 적격성 근거 누락 {eid}")
                ev = _parse(Evidence, state_evidence[eid], f"evidence {eid}")
                c.add_evidence(ev, sources=sources, chunks=chunks, records=records)
            c.eligibility[er.eligibility_result_id] = er

        if outcome.status not in DECIDED:
            # 평가 안 된·실패·부적격 후보: 사유·오류만, 빈 평가를 만들지 않는다.
            if outcome.decision_id is not None:
                raise _context(f"{cid}: {outcome.status} 후보에 decision_id가 있다")
            continue

        if outcome.decision_id is None:
            raise _context(f"{cid}: {outcome.status} 후보에 decision_id가 없다")
        dpayload = c.state_map("investment_decisions").get(cid)
        spayload = c.state_map("score_summaries").get(cid)
        if dpayload is None or spayload is None:
            raise _context(f"{cid}: 판정 또는 점수 없음")
        decision = _parse(InvestmentDecision, dpayload, f"decision {cid}")
        summary = _parse(ScoreSummary, spayload, f"score {cid}")
        if decision.decision_id != outcome.decision_id:
            raise _context(f"{cid}: 없는 decision ID {outcome.decision_id}")
        if decision.score_summary_id != summary.score_summary_id:
            raise _context(f"{cid}: 판정이 다른 점수를 참조")
        if decision.label != DECIDED[outcome.status]:
            raise _upstream(f"{cid}: outcome {outcome.status} ≠ 판정 {decision.label}")

        spay = c.state_map("snapshots").get(summary.snapshot_id)
        if spay is None:
            raise _context(f"{cid}: 점수의 snapshot 없음")
        snapshot = _parse(EvaluationSnapshot, spay, f"snapshot {summary.snapshot_id}")
        evaluations = []
        for dim in DIMENSIONS:
            key = evaluation_key(cid, summary.evaluation_round, dim)
            payload = c.state_map("evaluations").get(key)
            if payload is None:
                raise _context(f"{cid}: 최종 세대 평가 누락 {dim}")
            evaluations.append(_parse(Evaluation, payload, f"evaluation {key}"))
        _check_generation(cid, summary, snapshot, evaluations, report_input, rounds)
        if policy is not None:
            _check_upstream(cid, summary, decision, evaluations, policy)

        c.snapshots[snapshot.snapshot_id] = snapshot
        for ev in evaluations:
            c.evaluations[
                evaluation_key(ev.candidate_id, ev.evaluation_round, ev.dimension)
            ] = ev
        c.summaries[summary.score_summary_id] = summary
        c.decisions[decision.decision_id] = decision
        for ev in snapshot.evidence.values():
            c.add_evidence(
                ev,
                sources={**sources, **snapshot.sources},
                chunks={**chunks, **snapshot.chunks},
                records={**records, **snapshot.retrieval_records},
            )

    if report_input.mode == "single_candidate":
        selected = next(
            o
            for o in report_input.candidate_outcomes
            if o.candidate_id == report_input.selected_candidate_id
        )
        if selected.status != "recommend":
            raise _context("단일 기업 보고서의 선택 후보가 RECOMMEND가 아니다")

    if set(report_input.permitted_evidence_ids) != set(c.evidence):
        extra = sorted(set(report_input.permitted_evidence_ids) - set(c.evidence))
        lacking = sorted(set(c.evidence) - set(report_input.permitted_evidence_ids))
        raise _context(f"permitted_evidence_ids 불일치: 초과 {extra}, 누락 {lacking}")

    try:
        return ReportContext.model_validate(
            {
                "schema_version": report_input.schema_version,
                "context_id": context_id_for(report_input),
                "input": report_input,
                "snapshots": c.snapshots,
                "eligibility_results": c.eligibility,
                "evaluations": c.evaluations,
                "score_summaries": c.summaries,
                "decisions": c.decisions,
                "evidence": c.evidence,
                "sources": c.sources,
                "chunks": c.chunks,
                "retrieval_records": c.records,
                "errors": c.errors,
            },
            context=_MODE.get(),
        )
    except ValidationError as err:
        raise _context(f"ReportContext 구조 오류 ({err.error_count()}건)") from err


def permitted_evidence_ids(
    report_input_outcomes: list[Any], state: Mapping[str, Any]
) -> list[str]:
    """ReportInput을 만들 때 쓸 허용 근거 목록(최종 snapshot + 적격성 근거)."""
    ids: dict[str, None] = {}
    elig = state.get("eligibility_results") or {}
    summaries = state.get("score_summaries") or {}
    snapshots = state.get("snapshots") or {}
    for outcome in report_input_outcomes:
        cid = outcome.candidate_id
        if outcome.eligibility_result_id is not None and cid in elig:
            for eid in elig[cid].get("evidence_ids", []):
                ids[eid] = None
        if outcome.status in DECIDED and cid in summaries:
            snap = snapshots.get(summaries[cid].get("snapshot_id"), {})
            for eid in snap.get("evidence_ids", []):
                ids[eid] = None
    return list(ids)
