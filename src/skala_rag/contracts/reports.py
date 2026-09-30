"""Typed report payloads, without context assembly or semantic validation."""

from typing import Literal, Self

from pydantic import StrictBool, model_validator

from ._validation import validate_map_ids, validate_unique
from .candidates import EligibilityResult
from .common import Contract, Count, ISODate, JSONMap, Text
from .decisions import InvestmentDecision, ScoreSummary
from .errors import WorkflowError
from .evaluation import Evaluation, EvaluationSnapshot
from .evidence import Evidence
from .ids import evaluation_key
from .retrieval import RetrievalRecord
from .sources import Chunk, Source

OutcomeStatus = Literal[
    "ineligible",
    "eligibility_unknown",
    "recommend",
    "watchlist",
    "pass",
    "failed",
    "not_evaluated",
]


class CandidateOutcome(Contract):
    candidate_id: Text
    status: OutcomeStatus
    eligibility_result_id: Text | None = None
    decision_id: Text | None = None
    failure_ids: list[Text]
    summary_reason: Text


class ReportInput(Contract):
    run_id: Text
    mode: Literal["single_candidate", "no_recommendation"]
    selected_candidate_id: Text | None = None
    candidate_outcomes: list[CandidateOutcome]
    permitted_evidence_ids: list[Text]
    as_of: ISODate
    corpus_version: Text
    policy_version: Text

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        validate_unique(
            [outcome.candidate_id for outcome in self.candidate_outcomes],
            "candidate_outcomes candidate_id",
        )
        validate_unique(self.permitted_evidence_ids, "permitted_evidence_ids")
        if self.mode == "single_candidate":
            if self.selected_candidate_id is None or self.selected_candidate_id not in {
                outcome.candidate_id for outcome in self.candidate_outcomes
            }:
                raise ValueError(
                    "single_candidate requires a selected candidate outcome"
                )
        elif self.selected_candidate_id is not None:
            raise ValueError("no_recommendation requires selected_candidate_id=None")
        return self


class ReportContext(Contract):
    context_id: Text
    input: ReportInput
    snapshots: dict[Text, EvaluationSnapshot]
    eligibility_results: dict[Text, EligibilityResult]
    evaluations: dict[Text, Evaluation]
    score_summaries: dict[Text, ScoreSummary]
    decisions: dict[Text, InvestmentDecision]
    evidence: dict[Text, Evidence]
    sources: dict[Text, Source]
    chunks: dict[Text, Chunk]
    retrieval_records: dict[Text, RetrievalRecord]
    errors: dict[Text, WorkflowError]

    @model_validator(mode="after")
    def validate_map_identity(self) -> Self:
        for values, field in (
            (self.snapshots, "snapshot_id"),
            (self.eligibility_results, "eligibility_result_id"),
            (self.score_summaries, "score_summary_id"),
            (self.decisions, "decision_id"),
            (self.evidence, "evidence_id"),
            (self.sources, "source_id"),
            (self.chunks, "chunk_id"),
            (self.retrieval_records, "retrieval_id"),
            (self.errors, "error_id"),
        ):
            validate_map_ids(values, field)
        for key, evaluation in self.evaluations.items():
            if key != evaluation_key(
                evaluation.candidate_id,
                evaluation.evaluation_round,
                evaluation.dimension,
            ):
                raise ValueError(
                    "evaluations map key must match candidate/round/dimension"
                )
        if set(self.input.permitted_evidence_ids) != set(self.evidence):
            raise ValueError("permitted_evidence_ids must equal context evidence keys")
        return self


class ReportDraft(Contract):
    report_id: Text
    context_id: Text
    revision: Count
    markdown: Text
    cited_evidence_ids: list[Text]
    reference_source_ids: list[Text]
    limitations: list[Text]


class ValidationErrorDetail(Contract):
    code: Text
    location: Text
    message: Text


class ValidationResult(Contract):
    valid: StrictBool
    context_id: Text
    checks: JSONMap
    errors: list[ValidationErrorDetail]
    artifact_hash: Text

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.valid and self.errors:
            raise ValueError("valid result must not contain errors")
        if not self.valid and not self.errors:
            raise ValueError("invalid result requires at least one error")
        return self


class ReportFinding(Contract):
    severity: Text
    claim_location: Text
    evidence_ids: list[Text]
    reason: Text


class ReportJudgement(Contract):
    verdict: Literal["pass", "revise", "fail"]
    context_id: Text
    findings: list[ReportFinding]
    revision_instructions: list[Text]
    judged_artifact_hash: Text
