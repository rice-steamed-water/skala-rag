"""공통 가상 fixture 로더. 운영 데이터 로더나 snapshot controller가 아니다."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from skala_rag.contracts import (
    Candidate,
    Chunk,
    CompanyProfile,
    EligibilityResult,
    Evaluation,
    EvaluationSnapshot,
    Evidence,
    RetrievalRecord,
    Source,
)
from skala_rag.contracts.ids import evaluation_key
from skala_rag.scoring.catalog import ScoringPolicy


class CommonFixtures(BaseModel):
    """공통 fixture의 DTO 구조와 파일 내 참조 폐쇄성을 검증한다."""

    model_config = ConfigDict(extra="forbid")
    schema_version: str
    execution_mode: Literal["fixture"]
    synthetic: Literal[True]
    locator: str
    description: str
    policy_version: str
    cases: dict[str, str]
    candidates: dict[str, Candidate]
    profiles: dict[str, CompanyProfile]
    eligibility_results: dict[str, EligibilityResult]
    sources: dict[str, Source]
    chunks: dict[str, Chunk]
    evidence: dict[str, Evidence]
    retrieval_records: dict[str, RetrievalRecord]
    snapshots: dict[str, EvaluationSnapshot]
    evaluations: dict[str, Evaluation]

    @model_validator(mode="after")
    def validate_references(self):
        if not self.locator.startswith("fixture://"):
            raise ValueError("공통 fixture locator는 fixture://여야 합니다")
        for values, field in [
            (self.candidates, "candidate_id"),
            (self.profiles, "candidate_id"),
            (self.eligibility_results, "eligibility_result_id"),
            (self.sources, "source_id"),
            (self.chunks, "chunk_id"),
            (self.evidence, "evidence_id"),
            (self.retrieval_records, "retrieval_id"),
            (self.snapshots, "snapshot_id"),
        ]:
            for key, value in values.items():
                if key != getattr(value, field):
                    raise ValueError(f"{field} map key 불일치")

        def require(ids, values):
            if not set(ids) <= set(values):
                raise ValueError("없는 ID 참조")

        require(self.cases.values(), self.candidates)
        for candidate in self.candidates.values():
            require(candidate.discovery_source_ids, self.sources)
            if not candidate.homepage_url or not candidate.homepage_url.startswith(
                "fixture://"
            ):
                raise ValueError("가상 후보 URL은 fixture://여야 합니다")
        for source in self.sources.values():
            if not source.url or not source.url.startswith("fixture://"):
                raise ValueError("가상 Source URL은 fixture://여야 합니다")
        for chunk in self.chunks.values():
            require([chunk.source_id], self.sources)
            require(chunk.candidate_ids, self.candidates)
            if not chunk.locator.startswith("fixture://"):
                raise ValueError("가상 Chunk locator는 fixture://여야 합니다")
        for item in self.evidence.values():
            require([item.source_id], self.sources)
            require([item.candidate_id] if item.candidate_id else [], self.candidates)
            require(item.supporting_evidence_ids + item.conflicts_with, self.evidence)
            require([item.supersedes] if item.supersedes else [], self.evidence)
            if not item.locator.startswith("fixture://"):
                raise ValueError("가상 Evidence locator는 fixture://여야 합니다")
            for path in item.provenance:
                require([path.retrieval_id], self.retrieval_records)
                record = self.retrieval_records[path.retrieval_id]
                require([item.source_id], record.source_ids)
                require([item.evidence_id], record.evidence_ids)
                if path.chunk_id:
                    require([path.chunk_id], self.chunks)
                    require([path.chunk_id], record.chunk_ids)
                    if self.chunks[path.chunk_id].source_id != item.source_id:
                        raise ValueError("provenance Chunk Source 불일치")
        for record in self.retrieval_records.values():
            require(
                [record.candidate_id] if record.candidate_id else [], self.candidates
            )
            require(record.source_ids, self.sources)
            require(record.chunk_ids, self.chunks)
            require(record.evidence_ids, self.evidence)
            if record.error_id is not None:
                raise ValueError("공통 정상 검색 fixture에 오류 참조가 있습니다")
        for profile in self.profiles.values():
            require([profile.candidate_id], self.candidates)
            require(profile.stage.source_ids, self.sources)
            for ids in profile.field_evidence_ids.values():
                require(ids, self.evidence)
        for eligibility in self.eligibility_results.values():
            require([eligibility.candidate_id], self.candidates)
            require(eligibility.evidence_ids, self.evidence)
            if eligibility.policy_version != self.policy_version:
                raise ValueError("적격성 정책 버전 불일치")
        for snapshot in self.snapshots.values():
            require([snapshot.candidate_id], self.candidates)
            for field in ["evidence", "sources", "chunks", "retrieval_records"]:
                for key, value in getattr(snapshot, field).items():
                    if getattr(self, field).get(key) != value:
                        raise ValueError("snapshot과 공통 payload 불일치")
            for item in snapshot.evidence.values():
                require([item.source_id], snapshot.sources)
                require(item.supporting_evidence_ids, snapshot.evidence)
                for provenance in item.provenance:
                    require([provenance.retrieval_id], snapshot.retrieval_records)
                    if provenance.chunk_id:
                        require([provenance.chunk_id], snapshot.chunks)
            if snapshot.policy_version != self.policy_version:
                raise ValueError("snapshot 정책 버전 불일치")
        for key, evaluation in self.evaluations.items():
            if key != evaluation_key(
                evaluation.candidate_id,
                evaluation.evaluation_round,
                evaluation.dimension,
            ):
                raise ValueError("평가 key 불일치")
            require([evaluation.snapshot_id], self.snapshots)
            snapshot = self.snapshots[evaluation.snapshot_id]
            for field in [
                "run_id",
                "candidate_id",
                "evaluation_round",
                "evidence_revision",
                "policy_version",
            ]:
                if getattr(evaluation, field) != getattr(snapshot, field):
                    raise ValueError("평가 세대 불일치")
            for assessment in evaluation.criteria:
                require(assessment.evidence_ids, snapshot.evidence)
                for identifier in assessment.evidence_ids:
                    item = snapshot.evidence[identifier]
                    if item.candidate_id not in (None, evaluation.candidate_id):
                        raise ValueError("다른 기업 평가 근거")
                    if assessment.criterion_id not in item.criterion_ids:
                        raise ValueError("criterion 근거 연결 불일치")
        return self


def load_common_fixtures(
    catalog: ScoringPolicy,
    path: Path = Path(__file__).with_name("common.json"),
) -> CommonFixtures:
    """매 호출마다 파일을 새로 검증해 독립된 DTO 객체를 반환한다."""
    data = CommonFixtures.model_validate(
        json.loads(path.read_text(encoding="utf-8")),
        context={"execution_mode": "fixture"},
    )
    if data.policy_version != catalog.policy_version:
        raise ValueError("fixture와 catalog 정책 버전 불일치")
    for evaluation in data.evaluations.values():
        expected = {
            c.criterion_id
            for c in catalog.criteria
            if c.dimension == evaluation.dimension
        }
        actual = [c.criterion_id for c in evaluation.criteria]
        if len(set(actual)) != len(actual) or set(actual) != expected:
            raise ValueError("평가 영역 criterion이 정확히 한 번씩 필요합니다")
    return data
