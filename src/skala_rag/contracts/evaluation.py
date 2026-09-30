"""Evaluation payloads. Freeze/join/wrapper behavior belongs to M1."""

from typing import Literal, Self

from pydantic import model_validator

from ._validation import validate_map_ids, validate_unique
from .assessment import CriterionAssessment
from .common import Contract, Count, ISODate, Text
from .coverage import ResearchGap
from .errors import WorkflowError
from .evidence import Evidence
from .retrieval import RetrievalRecord
from .sources import Chunk, Source

Dimension = Literal["founder", "market", "technology", "moat", "traction", "deal_terms"]


class EvaluationSnapshot(Contract):
    snapshot_id: Text
    run_id: Text
    candidate_id: Text
    evaluation_round: Count
    evidence_revision: Count
    policy_version: Text
    corpus_version: Text
    index_version: Text
    as_of: ISODate
    evidence_ids: list[Text]
    evidence: dict[Text, Evidence]
    sources: dict[Text, Source]
    chunks: dict[Text, Chunk]
    retrieval_records: dict[Text, RetrievalRecord]

    @model_validator(mode="after")
    def validate_payload_maps(self) -> Self:
        validate_unique(self.evidence_ids, "evidence_ids")
        if set(self.evidence_ids) != set(self.evidence):
            raise ValueError("evidence_ids must equal evidence map keys")
        for values, field in (
            (self.evidence, "evidence_id"),
            (self.sources, "source_id"),
            (self.chunks, "chunk_id"),
            (self.retrieval_records, "retrieval_id"),
        ):
            validate_map_ids(values, field)
        return self


class Evaluation(Contract):
    run_id: Text
    candidate_id: Text
    dimension: Dimension
    evaluation_round: Count
    snapshot_id: Text
    evidence_revision: Count
    policy_version: Text
    rubric_version: Text
    criteria: list[CriterionAssessment]
    research_gaps: list[ResearchGap]
    caveats: list[Text]


class EvaluationResult(Contract):
    run_id: Text
    candidate_id: Text
    dimension: Dimension
    evaluation_round: Count
    snapshot_id: Text
    evidence_revision: Count
    policy_version: Text
    status: Literal["success", "failure"]
    evaluation: Evaluation | None = None
    errors: list[WorkflowError]

    @model_validator(mode="after")
    def validate_envelope(self) -> Self:
        if self.status == "success":
            if self.evaluation is None or self.errors:
                raise ValueError("success requires evaluation and no errors")
            for field in (
                "run_id",
                "candidate_id",
                "dimension",
                "evaluation_round",
                "snapshot_id",
                "evidence_revision",
                "policy_version",
            ):
                if getattr(self, field) != getattr(self.evaluation, field):
                    raise ValueError(f"evaluation envelope mismatch: {field}")
        elif self.evaluation is not None or not self.errors:
            raise ValueError("failure requires evaluation=None and at least one error")
        for error in self.errors:
            if error.run_id != self.run_id or error.candidate_id not in (
                None,
                self.candidate_id,
            ):
                raise ValueError("error must belong to envelope run/candidate")
        return self
