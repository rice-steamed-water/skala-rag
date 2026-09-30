"""Graph 컨테이너 ``InvestmentState``와 실행 시작 상태.

계약: docs/implementation/contracts.md §6 "InvestmentState 계약과 단독 writer".

State는 JSON 직렬화 가능한 값만 담는다. DTO는 경계에서 Pydantic으로 검증한 뒤
``model_dump(mode="json")`` 형태의 payload(``JsonObject``)로 넣는다. API client,
DB connection, API key, LLM 모델 객체는 넣지 않는다(contracts §1).

필드별 단독 writer (contracts §6 표와 같게 유지한다):

- 실행 입력 — investment_theme, search_queries, run_input, run_manifest:
  controller 작성, 실행 중 정책 불변.
- 후보 — candidates, current_candidate_id, candidate_index, candidate_status,
  selected_candidate_id: 후보 controller만 변경.
- 기본 조사 — company_profiles, eligibility_results: 해당 후보 조사/판정 노드
  단독 writer.
- 출처/근거 — sources, chunks, evidence, retrieval_history: ID 기반 merge.
  Source/Evidence의 허용된 병합은 §3 규칙 적용.
- 조사 제어 — coverage_results, research_gaps, research_retry_count,
  evidence_revisions: coverage/controller만 변경.
- 평가 — evaluation_results, evaluations, evaluation_rounds, snapshots:
  evaluation_results만 병렬 merge. 성공 evaluations는 join/직렬 평가
  controller, rounds·snapshots는 Freeze controller.
- 판정/이력 — score_summaries, investment_decisions, candidate_outcomes:
  단계별 단독 writer, 후보 결과 덮어쓰기 금지.
- 보고서 — report_context, report_draft, report, report_validation,
  report_judgement, pdf_validation, report_revision_count: context는 controller가
  최초 고정, 나머지는 순차 갱신.
- 오류/종료 — errors, workflow_status, run_outcome: 오류는 ID 병합, 종료 상태는
  controller만 변경.

평가 key는 ``{candidate_id}:{evaluation_round}:{dimension}``이며
``evaluations``와 ``evaluation_results``가 같은 key를 쓴다.
Source·Evidence·Chunk·병렬 평가 결과·오류에 ID reducer를 지정한다.
Graph wiring은 별도 이슈 범위다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, TypedDict

from skala_rag.graph.reducers import (
    merge_errors,
    merge_evidence,
    merge_result_maps,
    merge_sources,
)

JsonObject = dict[str, Any]
"""JSON으로 직렬화되는 DTO payload."""


class CandidateStatus(StrEnum):
    DISCOVERED = "discovered"
    RESEARCHING = "researching"
    INELIGIBLE = "ineligible"
    ELIGIBILITY_UNKNOWN = "eligibility_unknown"
    EVALUATING = "evaluating"
    RECOMMEND = "recommend"
    WATCHLIST = "watchlist"
    PASS = "pass"
    FAILED = "failed"
    NOT_EVALUATED = "not_evaluated"


class WorkflowStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RunOutcome(StrEnum):
    """실행 결과. workflow 완료와 투자 추천은 별개다."""

    RECOMMENDED = "recommended"
    NO_RECOMMENDATION = "no_recommendation"
    NO_CANDIDATES = "no_candidates"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    TECHNICAL_FAILURE = "technical_failure"


class InvestmentState(TypedDict, total=False):
    """스타트업 탐색 → 검증 → 근거 수집 → 평가 → 투자 판단 → 보고서 State."""

    # 실행 입력
    investment_theme: str
    search_queries: list[str]
    run_input: JsonObject  # RunInput
    run_manifest: JsonObject | None  # RunManifest

    # 후보
    candidates: list[JsonObject]  # Candidate[]
    current_candidate_id: str | None
    candidate_index: int  # 처리 순서, 기업 ID 아님
    candidate_status: dict[str, CandidateStatus]  # candidate_id ->
    selected_candidate_id: str | None

    # 기본 조사
    company_profiles: dict[str, JsonObject]  # candidate_id -> CompanyProfile
    eligibility_results: dict[str, JsonObject]  # candidate_id -> EligibilityResult

    # 출처/근거
    sources: Annotated[dict[str, JsonObject], merge_sources]  # source_id -> Source
    chunks: Annotated[dict[str, JsonObject], merge_result_maps]  # chunk_id -> Chunk
    evidence: Annotated[
        dict[str, JsonObject], merge_evidence
    ]  # evidence_id -> Evidence
    retrieval_history: list[JsonObject]  # RetrievalRecord[]

    # 조사 제어
    coverage_results: dict[str, JsonObject]  # candidate_id -> CoverageResult
    research_gaps: dict[str, list[JsonObject]]  # candidate_id -> ResearchGap[]
    research_retry_count: dict[str, int]  # candidate_id ->
    evidence_revisions: dict[str, int]  # candidate_id ->

    # 평가
    evaluation_results: Annotated[
        dict[str, JsonObject], merge_result_maps
    ]  # 평가 key -> EvaluationResult
    evaluations: dict[str, JsonObject]  # 평가 key -> Evaluation (성공만)
    evaluation_rounds: dict[str, int]  # candidate_id ->
    snapshots: dict[str, JsonObject]  # snapshot_id -> EvaluationSnapshot

    # 판정/이력
    score_summaries: dict[str, JsonObject]  # candidate_id -> ScoreSummary
    investment_decisions: dict[str, JsonObject]  # candidate_id -> InvestmentDecision
    candidate_outcomes: dict[str, JsonObject]  # candidate_id -> CandidateOutcome

    # 보고서
    report_context: JsonObject | None  # ReportContext
    report_draft: JsonObject | None  # ReportDraft
    report: str | None  # 검증된 최종 Markdown
    report_validation: JsonObject | None  # ValidationResult
    report_judgement: JsonObject | None  # ReportJudgement
    pdf_validation: JsonObject | None  # ValidationResult
    report_revision_count: int

    # 오류/종료
    errors: Annotated[list[JsonObject], merge_errors]  # WorkflowError[]
    workflow_status: WorkflowStatus
    run_outcome: RunOutcome | None


def create_initial_state(run_input: Mapping[str, Any]) -> InvestmentState:
    """실행 시작 State를 만든다.

    ``run_input``은 JSON 직렬화 가능한 RunInput payload여야 한다. 호출자 객체와
    공유하지 않도록 JSON 왕복으로 복사하며, 직렬화할 수 없는 값(client, SecretStr
    등)이 있으면 ``TypeError``를 낸다.
    """
    run_input_copy = json.loads(json.dumps(run_input, allow_nan=False))
    return InvestmentState(
        investment_theme=run_input_copy["investment_theme"],
        search_queries=[],
        run_input=run_input_copy,
        run_manifest=None,
        candidates=[],
        current_candidate_id=None,
        candidate_index=0,
        candidate_status={},
        selected_candidate_id=None,
        company_profiles={},
        eligibility_results={},
        sources={},
        chunks={},
        evidence={},
        retrieval_history=[],
        coverage_results={},
        research_gaps={},
        research_retry_count={},
        evidence_revisions={},
        evaluation_results={},
        evaluations={},
        evaluation_rounds={},
        snapshots={},
        score_summaries={},
        investment_decisions={},
        candidate_outcomes={},
        report_context=None,
        report_draft=None,
        report=None,
        report_validation=None,
        report_judgement=None,
        pdf_validation=None,
        report_revision_count=0,
        errors=[],
        workflow_status=WorkflowStatus.RUNNING,
        run_outcome=None,
    )
