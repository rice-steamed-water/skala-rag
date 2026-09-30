"""#55 Evidence Research 테스트용 가상 도구. 실제 모델·index·네트워크를 쓰지 않는다.

RAG는 #54 ``IndexedRetriever``를 그대로 쓰고 backend만 가상이다(질의 문자열이
Chunk 원문에 있으면 반환). LLM은 원문 줄을 그대로 주장으로 돌려주는 가상 구현이다.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

from skala_rag.agents.evidence_research import WebCapture, WebResult
from skala_rag.contracts import RetrievalBundle, ToolBudget
from skala_rag.contracts.candidates import Candidate
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode
from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.interfaces import LLMError
from skala_rag.contracts.retrieval import RetrievalRecord
from skala_rag.contracts.sources import Chunk, Source
from skala_rag.fakes import FakeClock
from skala_rag.prompts.evidence_extraction import ExtractionOutput
from skala_rag.rag.adapter import IndexedRetriever, IndexSnapshot
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    BudgetLedger,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
    TransportFailure,
)
from skala_rag.tools.source_fetch import RawSnapshot, to_source

SCHEMA = "synthetic-1"
MODEL = "synthetic-model"
REVISION = "synthetic-revision"
CONTEXT = {"execution_mode": "fixture"}


class QueryBackend:
    """``DenseSearch`` 가상 구현. 허용 Chunk 중 질의 문자열을 포함한 것만 순서대로."""

    retry_owner = "runtime"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.failure: Exception | None = None

    def search_once(self, request, *, snapshot, allowed_chunk_ids, timeout_seconds):
        self.calls.append(request.query)
        if self.failure is not None:
            raise self.failure
        chunks = [
            c
            for c in snapshot.bundle.chunks
            if c.chunk_id in allowed_chunk_ids and request.query in c.text
        ][: request.top_k]
        return RetrievalBundle.model_validate(
            dict(
                schema_version=SCHEMA,
                chunks=chunks,
                sources={
                    c.source_id: snapshot.bundle.sources[c.source_id] for c in chunks
                },
            ),
            context=CONTEXT,
        )


def chunk(chunk_id, source, text, *, candidate_ids=(), locator=None, corpus):
    return Chunk(
        schema_version=SCHEMA,
        chunk_id=chunk_id,
        source_id=source.source_id,
        corpus_version=corpus,
        text=text,
        locator=locator or f"{source.url}#section={chunk_id}",
        candidate_ids=list(candidate_ids),
        scope="company" if candidate_ids else "industry",
        language="ko",
        embedding_model=MODEL,
        embedding_revision=REVISION,
    )


def indexed_retriever(
    chunks: Sequence[Chunk],
    sources: Mapping[str, Source],
    *,
    corpus: str,
    index: str,
    clock: FakeClock,
    run_id: str = "run-synthetic",
    max_calls: int = 50,
) -> tuple[IndexedRetriever, QueryBackend]:
    snapshot = IndexSnapshot.model_validate(
        dict(
            schema_version=SCHEMA,
            corpus_version=corpus,
            corpus_hash="synthetic-hash",
            index_version=index,
            embedding_model=MODEL,
            embedding_revision=REVISION,
            search_settings={"metric": "synthetic"},
            bundle=dict(
                schema_version=SCHEMA,
                chunks=[c.model_dump(mode="json") for c in chunks],
                sources={
                    c.source_id: sources[c.source_id].model_dump(mode="json")
                    for c in chunks
                },
            ),
        ),
        context=CONTEXT,
    )
    backend = QueryBackend()
    runtime = AdapterRuntime(
        policy=RuntimePolicy(
            schema_version=SCHEMA,
            execution_mode="fixture",
            retry_delays_seconds=(),
            live_approval_reference=None,
            timing_approval_reference=None,
        ),
        ledger=BudgetLedger(
            RuntimeLimits(
                schema_version=SCHEMA,
                max_calls=max_calls,
                tool_max_calls={"retrieve": max_calls},
                max_input_tokens=0,
                max_output_tokens=0,
                max_cost_usd=Decimal(1),
            )
        ),
        clock=clock,
        sleep=lambda seconds: None,
    )
    retriever = IndexedRetriever(
        snapshot=snapshot,
        backend=backend,
        runtime=runtime,
        readiness=Readiness(
            schema_version=SCHEMA,
            required=True,
            configured=True,
            credential_required=False,
            credential_present=False,
            model_required=True,
            model_available=True,
            index_required=True,
            index_available=True,
        ),
        budget=ToolBudget(
            schema_version=SCHEMA, max_calls=1, max_retries=0, timeout_seconds=60
        ),
        allowance=Allowance(
            schema_version=SCHEMA,
            input_tokens=0,
            output_tokens=0,
            max_cost_usd=Decimal("0.1"),
        ),
        run_id=run_id,
        schema_version=SCHEMA,
        tool_name="retrieve",
    )
    return retriever, backend


def transport_failure(code: ErrorCode = ErrorCode.TOOL_UNAVAILABLE) -> Exception:
    return TransportFailure(code)


class LineLLM:
    """``StructuredLLM`` 가상 구현. 대상 기업명이 있는 원문 줄을 그대로 주장으로 낸다.

    ``extra``에 줄별 추가 필드(value·unit 등)를 줄 수 있다. ``error``면 매번 그 오류.
    """

    def __init__(self, extra: Mapping[str, dict] | None = None, error=None) -> None:
        self.extra = dict(extra or {})
        self.error = error
        self.calls = 0

    def generate(self, *, system, user, output_schema):
        self.calls += 1
        if self.error is not None:
            raise self.error
        payload = json.loads(user)
        names = payload["target_company_names"]
        claims = []
        for line in payload["untrusted_source_text"].splitlines():
            subject = next((n for n in names if n in line), None)
            if names and subject is None:
                continue
            claims.append(
                dict(
                    claim=line,
                    excerpt=line,
                    subject=subject,
                    **self.extra.get(line, {}),
                )
            )
        assert output_schema is ExtractionOutput
        return ExtractionOutput.model_validate({"claims": claims})


def llm_error(code: ErrorCode = ErrorCode.LLM_TIMEOUT) -> LLMError:
    return LLMError(code, "synthetic llm failure")


def web_page(
    url: str, content: str, *, retrieved_at: datetime
) -> tuple[RawSnapshot, Source]:
    raw = RawSnapshot(
        requested=url,
        locator=url,
        kind="web",
        redirects=(),
        content=content.encode(),
        content_type="text/plain",
        retrieved_at=retrieved_at,
    )
    source = to_source(
        raw,
        schema_version=SCHEMA,
        title="Synthetic page",
        source_kind="web",
        language="ko",
        access_notes="Synthetic only",
    )
    return raw, source


class StubWeb:
    """``WebSearch`` 가상 구현. 질의별 준비한 page 또는 오류 코드를 돌려준다."""

    def __init__(
        self,
        pages: Mapping[str, Sequence[tuple[RawSnapshot, Source, str, str | None]]],
        *,
        clock: FakeClock,
        error: ErrorCode | None = None,
        run_id: str = "run-synthetic",
        name: str = "web",
    ) -> None:
        self.pages = pages
        self.clock = clock
        self.error = error
        self.run_id = run_id
        self.name = name
        self.calls: list[str] = []

    def __call__(self, candidate: Candidate, query: str, *, as_of: date) -> WebResult:
        self.calls.append(query)
        rid = f"retrieval-{self.name}-{len(self.calls)}"
        now = self.clock.now()
        if self.error is not None:
            error = WorkflowError(
                schema_version=SCHEMA,
                error_id=f"error-{rid}",
                run_id=self.run_id,
                candidate_id=candidate.candidate_id,
                node=self.name,
                error_code=self.error,
                message_redacted=f"synthetic {self.error}",
                retryable=ERROR_SPECS[self.error].retryable,
                attempt=1,
                timestamp=now,
            )
            return WebResult(
                status=ERROR_SPECS[self.error].tool_status, errors=(error,)
            )
        found = list(self.pages.get(query, ()))
        record = RetrievalRecord(
            schema_version=SCHEMA,
            retrieval_id=rid,
            run_id=self.run_id,
            candidate_id=candidate.candidate_id,
            tool_name=self.name,
            query=None,
            arguments_without_secrets={"execution_mode": "fixture"},
            started_at=now,
            finished_at=now,
            status="ok" if found else "empty",
            source_ids=[s.source_id for _, s, _, _ in found],
            chunk_ids=[],
            evidence_ids=[],
            cache_hit=False,
        )
        return WebResult(
            status=record.status,
            captures=tuple(
                WebCapture(rid, raw, source, locator, "company", text)
                for raw, source, locator, text in found
            ),
            records=(record,),
        )


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)
