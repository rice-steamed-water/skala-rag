"""Evidence Collector — #19, contracts §3·§7, data-rag §1·§4, T13.

open gap의 suggested_queries마다 ``RetrievalRequest``를 만들어 주입된 ``Retrieve``를
실제로 호출하고, 반환된 Chunk에서만 Evidence를 만든다. rag provenance의
retrieval_id·chunk_id는 collector가 그 검색 결과로 채운다 — extractor 출력이나
사후 표기로 붙이지 않는다. 검색 기록의 evidence_ids도 여기서 채운다.

Chunk → 주장 추출은 ``ExtractClaims``로 주입한다. fixture에서는
``fixture_extract_claims``(문자열 일치)를, M2에서는 LLM 구조화 추출 wrapper를 쓴다.
excerpt가 Chunk 원문에 그대로 없으면 ``EvidenceExtractionError``를 낸다. Evidence ID는
``contracts.ids.evidence_id``(식별 core, 수집 경로 제외)이고 병합은 #15 reducer를 쓴다.

호출 수는 ``ToolBudget.max_calls``까지만 쓴다. 남은 질의는 실행하지 않고, gap의
attempted_retrieval_ids에는 실제 호출만 남는다(gap 갱신은 controller 몫).
"""

from collections.abc import Callable, Sequence
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from skala_rag.contracts.bundles import RetrievalBundle
from skala_rag.contracts.candidates import Candidate
from skala_rag.contracts.common import JSONMap
from skala_rag.contracts.coverage import ResearchGap
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode, is_retryable
from skala_rag.contracts.ids import evidence_id, normalize_claim
from skala_rag.contracts.interfaces import Clock, Retrieve
from skala_rag.contracts.retrieval import RetrievalRecord, RetrievalRequest
from skala_rag.contracts.sources import Chunk
from skala_rag.contracts.tools import EvidenceBundle, ToolBudget, ToolResult
from skala_rag.graph.reducers import merge_evidence


class ExtractedClaim(BaseModel):
    """extractor 출력. 출처·위치·provenance·ID 필드는 없다(collector가 채운다)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    claim: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    value: int | float | None = None
    unit: str | None = None
    currency: str | None = None
    value_as_of: date | None = None
    period: str | None = None
    geography: str | None = None
    event_date: date | None = None
    confidence: Literal["high", "medium", "low", "unknown"] = "unknown"
    limitations: list[str] = []


ExtractClaims = Callable[[Chunk, ResearchGap], Sequence[ExtractedClaim]]


class EvidenceExtractionError(ValueError):
    """추출 결과가 검색 Chunk 원문과 맞지 않는다."""


def fixture_extract_claims(chunk: Chunk, gap: ResearchGap) -> list[ExtractedClaim]:
    """가상 fixture용: gap 대상 ID가 들어간 Chunk 줄을 그대로 주장·발췌로 쓴다."""
    target = gap.criterion_id or gap.eligibility_field
    return [
        ExtractedClaim(
            claim=line,
            excerpt=line,
            limitations=["가상 fixture 문자열 일치 추출; 실측 아님"],
        )
        for line in chunk.text.splitlines()
        if target and target in line
    ]


def _iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


class EvidenceCollector:
    """``CollectEvidence`` Protocol 구현. corpus·as_of·허용 출처는 생성 시 고정."""

    def __init__(
        self,
        retrieve: Retrieve,
        extract: ExtractClaims,
        *,
        run_id: str,
        corpus_version: str,
        index_version: str,
        as_of: date,
        top_k: int,
        allowed_source_ids: Sequence[str],
        clock: Clock,
        schema_version: str,
        execution_mode: Literal["fixture", "live"],
    ) -> None:
        self._retrieve = retrieve
        self._extract = extract
        self._run_id = run_id
        self._corpus_version = corpus_version
        self._index_version = index_version
        self._as_of = as_of
        self._top_k = top_k
        self._allowed_source_ids = list(allowed_source_ids)
        self._clock = clock
        self._schema_version = schema_version
        self._context = {"execution_mode": execution_mode}

    def __call__(
        self, candidate: Candidate, gaps: Sequence[ResearchGap], budget: ToolBudget
    ) -> ToolResult[EvidenceBundle]:
        plan = [
            (gap, query)
            for gap in gaps
            if gap.status == "open" and gap.candidate_id == candidate.candidate_id
            for query in gap.suggested_queries
        ]
        if plan and budget.max_calls == 0:
            return self._failure(candidate, ErrorCode.BUDGET_EXHAUSTED, [], [])

        records: list[RetrievalRecord] = []
        evidence: dict[str, JSONMap] = {}
        sources: dict[str, JSONMap] = {}
        for gap, query in plan[: budget.max_calls]:
            result = self._retrieve(self._request(candidate, query))
            # 검색 기록은 복사해서 evidence_ids를 채운다(retriever 결과는 그대로 둔다).
            records.extend(r.model_copy(deep=True) for r in result.retrieval_records)
            if result.status in ("unavailable", "failed"):
                return self._failure(
                    candidate, None, records, [e.model_dump() for e in result.errors]
                )
            self._collect(candidate, gap, records[-1], result.data, evidence, sources)

        status = "ok" if evidence else "empty"
        return ToolResult[EvidenceBundle].model_validate(
            {
                "schema_version": self._schema_version,
                "status": status,
                "data": {
                    "schema_version": self._schema_version,
                    "sources": sources,
                    "evidence": evidence,
                },
                "retrieval_records": [r.model_dump(mode="json") for r in records],
                "errors": [],
            },
            context=self._context,
        )

    def _request(self, candidate: Candidate, query: str) -> RetrievalRequest:
        return RetrievalRequest(
            schema_version=self._schema_version,
            query=query,
            candidate_id=candidate.candidate_id,
            corpus_version=self._corpus_version,
            index_version=self._index_version,
            as_of=self._as_of,
            top_k=self._top_k,
            allowed_source_ids=self._allowed_source_ids,
        )

    def _collect(
        self,
        candidate: Candidate,
        gap: ResearchGap,
        record: RetrievalRecord,
        bundle: RetrievalBundle,
        evidence: dict[str, JSONMap],
        sources: dict[str, JSONMap],
    ) -> None:
        for chunk in bundle.chunks:
            if chunk.chunk_id not in record.chunk_ids:
                raise EvidenceExtractionError(f"Chunk {chunk.chunk_id} 검색 기록 없음")
            for claim in self._extract(chunk, gap):
                if claim.excerpt not in chunk.text:
                    raise EvidenceExtractionError(
                        f"excerpt가 Chunk {chunk.chunk_id} 원문에 없음"
                    )
                company = chunk.scope == "company"
                owner = candidate.candidate_id if company else None
                # 공통 ID는 식별 core만 쓴다. 같은 주장을 다른 gap·검색으로 다시
                # 얻으면 같은 ID이고 reducer가 provenance·criterion_ids를 합친다.
                core = {
                    "source_id": chunk.source_id,
                    "locator": chunk.locator,
                    "claim": normalize_claim(claim.claim),
                    "candidate_id": owner,
                    "scope": chunk.scope,
                    "value": claim.value,
                    "unit": claim.unit,
                    "currency": claim.currency,
                    "value_as_of": claim.value_as_of,
                    "period": claim.period,
                    "geography": claim.geography,
                    "event_date": claim.event_date,
                    "evidence_kind": "reported",
                    "supporting_evidence_ids": [],
                    "derivation": None,
                    "supersedes": None,
                }
                key = evidence_id(**core)
                payload = {
                    "schema_version": self._schema_version,
                    "evidence_id": key,
                    **claim.model_dump(mode="json"),
                    **core,
                    "value_as_of": _iso(claim.value_as_of),
                    "event_date": _iso(claim.event_date),
                    "criterion_ids": [gap.criterion_id] if gap.criterion_id else [],
                    "provenance": [
                        {
                            "schema_version": self._schema_version,
                            "retrieval_id": record.retrieval_id,
                            "method": "rag",
                            "chunk_id": chunk.chunk_id,
                        }
                    ],
                    "conflicts_with": [],
                }
                evidence.update(merge_evidence(evidence, {key: payload}))
                if key not in record.evidence_ids:
                    record.evidence_ids.append(key)
                sources[chunk.source_id] = bundle.sources[chunk.source_id].model_dump(
                    mode="json"
                )

    def _failure(
        self,
        candidate: Candidate,
        code: ErrorCode | None,
        records: list[RetrievalRecord],
        errors: list[JSONMap],
    ) -> ToolResult[EvidenceBundle]:
        if code is not None:
            errors = [
                {
                    "schema_version": self._schema_version,
                    "error_id": f"error:{self._run_id}:collect:"
                    f"{candidate.candidate_id}",
                    "run_id": self._run_id,
                    "candidate_id": candidate.candidate_id,
                    "node": "collect_evidence",
                    "error_code": code,
                    "message_redacted": "tool call budget exhausted before retrieval",
                    "retryable": is_retryable(code),
                    "attempt": 1,
                    "timestamp": self._clock.now(),
                }
            ]
        status = ERROR_SPECS[ErrorCode(errors[0]["error_code"])].tool_status
        return ToolResult[EvidenceBundle].model_validate(
            {
                "schema_version": self._schema_version,
                "status": status,
                "data": None,
                "retrieval_records": [r.model_dump(mode="json") for r in records],
                "errors": errors,
            },
            context=self._context,
        )
