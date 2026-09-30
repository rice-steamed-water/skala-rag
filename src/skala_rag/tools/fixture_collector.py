"""주입된 가상 추출기를 실제 반환 Chunk와 검색 이력에 연결한다."""

from collections.abc import Callable, MutableMapping, Sequence

from skala_rag.contracts import (
    Candidate,
    Chunk,
    Evidence,
    EvidenceBundle,
    EvidenceProvenance,
    ResearchGap,
    RetrievalRequest,
    ToolBudget,
    ToolResult,
    WorkflowError,
)
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import Clock, Retrieve
from skala_rag.graph.reducers import merge_evidence, merge_sources
from skala_rag.rag.fixture import validate_bundle


def _fixture(model, **payload):
    return model.model_validate(payload, context={"execution_mode": "fixture"})


class FixtureEvidenceCollector:
    """CollectEvidence Protocol. 추출된 content만 사용하고 provenance는 새로 만든다."""

    def __init__(
        self,
        *,
        retrieve: Retrieve,
        chunk_store: MutableMapping[str, Chunk],
        request_builder: Callable[
            [Candidate, Sequence[ResearchGap]], Sequence[RetrievalRequest]
        ],
        extract: Callable[[Chunk], Sequence[Evidence]],
        run_id: str,
        schema_version: str,
        clock: Clock,
        error_id_factory: Callable[[], str],
        execution_mode: str,
    ):
        if execution_mode != "fixture":
            raise ValueError("fixture 수집은 fixture 모드에서만 사용할 수 있습니다")
        self.retrieve = retrieve
        self.chunk_store = chunk_store
        self.request_builder = request_builder
        self.extract = extract
        self.run_id = run_id
        self.schema_version = schema_version
        self.clock = clock
        self.error_id_factory = error_id_factory

    def __call__(
        self, candidate: Candidate, gaps: Sequence[ResearchGap], budget: ToolBudget
    ) -> ToolResult[EvidenceBundle]:
        requests = list(self.request_builder(candidate, gaps))
        records, sources, evidence = [], {}, {}
        if len(requests) > budget.max_calls:
            return self._failure(candidate, records, ErrorCode.BUDGET_EXHAUSTED)
        try:
            for request in requests:
                if request.candidate_id != candidate.candidate_id:
                    raise ValueError("요청 기업 불일치")
                result = self.retrieve(request)
                records.extend(
                    record.model_copy(deep=True) for record in result.retrieval_records
                )
                if result.status in ("failed", "unavailable"):
                    return _fixture(
                        ToolResult[EvidenceBundle],
                        schema_version=self.schema_version,
                        status=result.status,
                        data=None,
                        retrieval_records=records,
                        errors=result.errors,
                    )
                validate_bundle(result.data, request)
                if len(result.retrieval_records) != 1:
                    raise ValueError("fixture 검색 한 번에 이력 한 건이 필요합니다")
                record = records[-1]
                if (
                    record.run_id != self.run_id
                    or record.candidate_id != candidate.candidate_id
                    or set(record.chunk_ids) != {c.chunk_id for c in result.data.chunks}
                    or set(record.source_ids) != set(result.data.sources)
                ):
                    raise ValueError("검색 이력과 반환 payload 불일치")
                sources = merge_sources(
                    sources,
                    {
                        key: source.model_dump(mode="json")
                        for key, source in result.data.sources.items()
                    },
                )
                generated_ids = []
                for chunk in result.data.chunks:
                    if (
                        chunk.chunk_id in self.chunk_store
                        and self.chunk_store[chunk.chunk_id] != chunk
                    ):
                        raise ValueError("동일 Chunk ID의 payload 충돌")
                    self.chunk_store[chunk.chunk_id] = chunk.model_copy(deep=True)
                    for item in self.extract(chunk.model_copy(deep=True)):
                        if (
                            item.scope != chunk.scope
                            or item.source_id != chunk.source_id
                            or (
                                item.scope == "company"
                                and item.candidate_id != candidate.candidate_id
                            )
                            or (
                                item.scope == "industry"
                                and item.candidate_id is not None
                            )
                            or (
                                item.event_date is not None
                                and item.event_date > request.as_of
                            )
                        ):
                            raise ValueError("추출 Evidence 범위 위반")
                        payload = item.model_dump(mode="json")
                        payload["provenance"] = [
                            EvidenceProvenance(
                                schema_version=self.schema_version,
                                retrieval_id=record.retrieval_id,
                                method="rag",
                                chunk_id=chunk.chunk_id,
                            ).model_dump(mode="json")
                        ]
                        validated = Evidence.model_validate(
                            payload, context={"execution_mode": "fixture"}
                        )
                        evidence = merge_evidence(
                            evidence,
                            {validated.evidence_id: validated.model_dump(mode="json")},
                        )
                        if validated.evidence_id not in generated_ids:
                            generated_ids.append(validated.evidence_id)
                record.evidence_ids = generated_ids
        except ValueError:
            return self._failure(candidate, records, ErrorCode.TOOL_RESPONSE_INVALID)
        except Exception:
            return self._failure(candidate, records, ErrorCode.TOOL_FAILED)
        bundle = EvidenceBundle.model_validate(
            dict(
                schema_version=self.schema_version, sources=sources, evidence=evidence
            ),
            context={"execution_mode": "fixture"},
        )
        return _fixture(
            ToolResult[EvidenceBundle],
            schema_version=self.schema_version,
            status="ok" if evidence else "empty",
            data=bundle,
            retrieval_records=records,
            errors=[],
        )

    def _failure(self, candidate, records, code):
        error = WorkflowError(
            schema_version=self.schema_version,
            error_id=self.error_id_factory(),
            run_id=self.run_id,
            candidate_id=candidate.candidate_id,
            node="fixture-collect-evidence",
            error_code=code.value,
            message_redacted="가상 근거 수집이 실패했습니다.",
            retryable=False,
            attempt=1,
            timestamp=self.clock.now(),
        )
        return _fixture(
            ToolResult[EvidenceBundle],
            schema_version=self.schema_version,
            status="failed",
            data=None,
            retrieval_records=records,
            errors=[error],
        )
