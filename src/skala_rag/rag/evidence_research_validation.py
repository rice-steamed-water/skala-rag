"""Opt-in #55 live smoke: 실제 로컬 BGE-M3 index 검색 → Evidence Research → Evidence.

#54 ``retrieve_validation``과 같은 #145 로컬 산출물(모델·SQLite index·receipt)을
재검증해 ``IndexedRetriever``를 만들고, #55 ``evidence_research_stage``로 최초 수집
1회와 gap 재조사 1회를 실행한다. 결과는 Git 제외 ``outputs/`` 아래에 쓴다.

- 최초 계획·gap의 criterion과 질의, top_k는 OPEN 정책이라 인자로만 받는다.
- ``--llm none``: 외부 호출 없이 검색 → 구간 → 이력 trace만 확인한다(주장 0개 LLM).
- ``--llm openai``: ``OPENAI_API_KEY``로 #47 adapter를 #45 runtime 안에서 호출한다.
  M2 승인 B 상한: LLM 요청 8회, 요청당 입력 8,000/출력 2,000 token, 전체 입력
  64,000/출력 16,000 token, USD 1.00. 요청당 입력 상한을 넘는 구간은 호출 전에 거절한다.

모델 다운로드는 하지 않는다(local_files_only). 원문·key는 출력하지 않고 발췌는 앞
160자만 남긴다. 이 smoke는 검색·추출 품질 benchmark가 아니다.
"""

import argparse
import hashlib
import json
import math
import os
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from skala_rag.agents.evidence_research import (
    EvidenceResearch,
    evidence_research_stage,
)
from skala_rag.contracts import ToolBudget
from skala_rag.contracts.candidates import Candidate
from skala_rag.contracts.coverage import ResearchGap
from skala_rag.graph.candidates import StageFailure
from skala_rag.prompts.evidence_extraction import PROMPT_VERSION, ExtractionOutput
from skala_rag.rag.adapter import IndexedRetriever
from skala_rag.rag.corpus import manifest_hash
from skala_rag.rag.dense import snapshot_from_plan
from skala_rag.rag.index_v3 import IndexSettings, build_index_plan
from skala_rag.rag.index_validation import prepare_corpus
from skala_rag.rag.local_bge_validation import MODEL, REVISION, LocalEncoder
from skala_rag.rag.query_local import LocalQueryEncoder
from skala_rag.rag.sqlite_index import SQLiteIndexStore
from skala_rag.rag.sqlite_retrieve import SQLiteDenseSearch
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt, byte_bound_allowance
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    BudgetLedger,
    CallContext,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
)
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM

SCHEMA = "issue55-live-v1"
RUN_ID = "issue55-evidence-research-smoke"
CANDIDATE_ID = "co-physical-intelligence"
AS_OF = date(2026, 9, 30)
RETRIEVE_TOOL = "local-retrieve"
LLM_TOOL = "openai-evidence-extraction"
# M2 승인 B(docs/implementation/m2-live-approval-proposal.md §3). 승인 코드 기본값 아님.
LLM_MAX_CALLS = 8
LLM_REQUEST_INPUT_TOKENS = 8_000
LLM_REQUEST_OUTPUT_TOKENS = 2_000
LLM_TOTAL_INPUT_TOKENS = 64_000
LLM_TOTAL_OUTPUT_TOKENS = 16_000
LLM_MAX_COST_USD = Decimal("1.00")
LLM_TIMEOUT_SECONDS = 30.0  # D08 시도별 timeout
# gpt-4.1-mini 공식 요금(#51 live smoke와 같은 2026-09-30 확인값).
USD_PER_INPUT_TOKEN = Decimal("0.40") / 1_000_000
USD_PER_OUTPUT_TOKEN = Decimal("1.60") / 1_000_000


class Clock:
    def now(self):
        return datetime.now(UTC)


class _RecordingResearch(EvidenceResearch):
    """batch별 ``ResearchOutcome``의 거절 사유·도구 결과를 리포트용으로 남긴다."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.outcomes = []

    def run(self, candidate, gaps, budget):
        outcome = super().run(candidate, gaps, budget)
        self.outcomes.append(outcome)
        return outcome


class NoClaimsLLM:
    """``--llm none``: 외부 호출 없이 주장 0개. 검색·구간·이력 trace 확인용."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, *, system, user, output_schema):
        self.calls += 1
        return ExtractionOutput(claims=[])


def _retriever(root, model_path, store_path, receipt_path, timeout_seconds, clock):
    """#54 retrieve_validation과 같은 순서로 #145 산출물을 재검증한다."""
    previous = json.loads(receipt_path.read_text())
    manifest, sources, results, chunks = prepare_corpus(
        root, model_id=MODEL, model_revision=REVISION
    )
    settings = IndexSettings(**json.loads(previous["metadata"]["settings_snapshot"]))
    plan = build_index_plan(
        manifest=manifest,
        expected_corpus_hash=manifest_hash(manifest),
        sources=sources,
        chunks=chunks,
        settings=settings,
        extraction_results=results,
    )
    if json.loads(json.dumps(plan.metadata.__dict__)) != previous["metadata"]:
        raise ValueError("#145 receipt/actual corpus metadata mismatch")
    for name, digest in previous["model_file_hashes"].items():
        h = hashlib.sha256()
        with (model_path / name).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        if h.hexdigest() != digest:
            raise ValueError("#145 model file changed")
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(
        str(model_path),
        local_files_only=True,
        trust_remote_code=False,
        device=settings.embedding_settings["device"],
    )
    if model.max_seq_length != 8192 or model.get_embedding_dimension() != 1024:
        raise ValueError("local model configuration mismatch")
    store = SQLiteIndexStore(store_path)
    snapshot = snapshot_from_plan(
        plan,
        manifest=manifest,
        reopened_metadata=store.read_metadata(plan.metadata.index_version),
        search_settings={"metric": "cosine"},
        execution_mode="live",
        extraction_results=results,
        source_inputs=sources,
    )
    local = LocalEncoder(model)
    runtime = AdapterRuntime(
        policy=RuntimePolicy(
            schema_version=SCHEMA,
            execution_mode="live",
            retry_delays_seconds=(),
            live_approval_reference="issue145-local-bge-validation",
            timing_approval_reference="explicit-local-validation-bound-not-provider-policy",
        ),
        ledger=BudgetLedger(
            RuntimeLimits(
                schema_version=SCHEMA,
                max_calls=4,
                tool_max_calls={RETRIEVE_TOOL: 4},
                max_input_tokens=0,
                max_output_tokens=0,
                max_cost_usd=0,
            )
        ),
        clock=clock,
        sleep=lambda _: None,
    )
    retriever = IndexedRetriever(
        snapshot=snapshot,
        backend=SQLiteDenseSearch(
            store=store,
            metadata=plan.metadata,
            snapshot=snapshot,
            encoder=LocalQueryEncoder(local, settings=settings),
        ),
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
            schema_version=SCHEMA,
            max_calls=1,
            max_retries=0,
            timeout_seconds=timeout_seconds,
            deadline=clock.now() + timedelta(seconds=timeout_seconds),
        ),
        allowance=Allowance(
            schema_version=SCHEMA, input_tokens=0, output_tokens=0, max_cost_usd=0
        ),
        run_id=RUN_ID,
        schema_version=SCHEMA,
        tool_name=RETRIEVE_TOOL,
    )
    return retriever, snapshot, sources, runtime, local


def _openai_llm(api_key: str, clock: Clock) -> RuntimeStructuredLLM:
    runtime = AdapterRuntime(
        policy=RuntimePolicy(
            schema_version=SCHEMA,
            execution_mode="live",
            retry_delays_seconds=(),
            live_approval_reference="m2-live-approval-proposal.md §3 B (#43)",
            timing_approval_reference="D08 시도별 timeout 30초",
        ),
        ledger=BudgetLedger(
            RuntimeLimits(
                schema_version=SCHEMA,
                max_calls=LLM_MAX_CALLS,
                tool_max_calls={LLM_TOOL: LLM_MAX_CALLS},
                max_input_tokens=LLM_TOTAL_INPUT_TOKENS,
                max_output_tokens=LLM_TOTAL_OUTPUT_TOKENS,
                max_cost_usd=LLM_MAX_COST_USD,
            )
        ),
        clock=clock,
        sleep=lambda _: None,
    )

    def allowance(system, user, schema):
        bound = byte_bound_allowance(
            system,
            user,
            schema,
            schema_version=SCHEMA,
            max_output_tokens=LLM_REQUEST_OUTPUT_TOKENS,
            usd_per_input_token=USD_PER_INPUT_TOKEN,
            usd_per_output_token=USD_PER_OUTPUT_TOKEN,
        )
        if bound.input_tokens > LLM_REQUEST_INPUT_TOKENS:
            # 요청당 입력 상한 초과: 호출하지 않고 LLM_FAILED로 batch를 멈춘다.
            raise ValueError("request input bound exceeds approval B")
        return bound

    return RuntimeStructuredLLM(
        runtime=runtime,
        call=CallContext(
            schema_version=SCHEMA,
            call_id=f"{RUN_ID}-llm",
            run_id=RUN_ID,
            candidate_id=CANDIDATE_ID,
            tool_name=LLM_TOOL,
            node="evidence_research",
        ),
        budget=ToolBudget(
            schema_version=SCHEMA,
            max_calls=1,
            max_retries=0,
            timeout_seconds=LLM_TIMEOUT_SECONDS,
            deadline=clock.now() + timedelta(minutes=10),
        ),
        readiness=Readiness(
            schema_version=SCHEMA,
            required=True,
            configured=True,
            credential_required=True,
            credential_present=True,
            model_required=False,
            model_available=False,
            index_required=False,
            index_available=False,
        ),
        transport=OpenAIResponsesAttempt(
            api_key=api_key,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA,
            clock=clock,
        ),
        allowance_for=allowance,
    )


def _gap(criterion: str, query: str, gap_id: str) -> ResearchGap:
    return ResearchGap(
        schema_version=SCHEMA,
        gap_id=gap_id,
        candidate_id=CANDIDATE_ID,
        criterion_id=criterion,
        missing_fields=["claim"],
        reason="#55 live smoke 입력(승인 정책 아님)",
        suggested_queries=[query],
        attempted_retrieval_ids=[],
        status="open",
    )


def run(
    *,
    root: Path,
    model_path: Path,
    store_path: Path,
    receipt_path: Path,
    output_dir: Path,
    timeout_seconds: float,
    llm: str,
    initial_criterion: str,
    initial_query: str,
    gap_criterion: str,
    gap_query: str,
    top_k: int,
):
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("explicit positive finite local bound required")
    if not 1 <= top_k <= 4:
        # 두 batch × top_k 구간이 LLM 요청 8회 안에 들어야 한다.
        raise ValueError("top_k must be 1..4 to stay within 8 LLM requests")
    if not output_dir.resolve().is_relative_to((root / "outputs").resolve()):
        raise ValueError("validation artifacts must remain in outputs")
    if output_dir.exists():
        raise ValueError("validation artifacts already exist")
    started = time.monotonic()
    clock = Clock()
    retriever, snapshot, sources, retrieve_runtime, local = _retriever(
        root, model_path, store_path, receipt_path, timeout_seconds, clock
    )
    if llm == "openai":
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise ValueError("--llm openai requires OPENAI_API_KEY")
        extractor = _openai_llm(key, clock)
    else:
        key, extractor = "", NoClaimsLLM()

    research = _RecordingResearch(
        retrieve=retriever,
        rag_required=True,
        llm=extractor,
        initial_plan=lambda c: [
            _gap(initial_criterion, initial_query, f"initial:{initial_criterion}")
        ],
        run_id=RUN_ID,
        corpus_version=snapshot.corpus_version,
        index_version=snapshot.index_version,
        as_of=AS_OF,
        top_k=top_k,
        allowed_source_ids=list(sources),
        clock=clock,
        schema_version=SCHEMA,
        execution_mode="live",
        rag_tool_name=RETRIEVE_TOOL,
    )
    # smoke 전용 batch 호출 한도(질의 1개 × 도구 1개). 승인된 batch 한도가 아니다.
    stage = evidence_research_stage(
        research,
        budget=ToolBudget(
            schema_version=SCHEMA,
            max_calls=1,
            max_retries=0,
            timeout_seconds=timeout_seconds,
        ),
    )
    candidate = Candidate(
        schema_version=SCHEMA,
        candidate_id=CANDIDATE_ID,
        canonical_name="Physical Intelligence",
        aliases=[],
        country="US",
        homepage_url=None,
        legal_identifiers={},
        discovery_source_ids=[],
    )
    state = dict(
        current_candidate_id=CANDIDATE_ID,
        candidates=[candidate.model_dump(mode="json")],
        research_retry_count={CANDIDATE_ID: 0},
        research_gaps={},
        evidence_revisions={CANDIDATE_ID: 0},
        evidence={},
        sources={},
        chunks={},
        retrieval_history=[],
    )
    batches = []
    for name in ("initial", "gap_retry"):
        if name == "gap_retry":
            # Coverage 역할의 smoke 입력: gap 하나를 open으로 넘긴다.
            state["research_retry_count"][CANDIDATE_ID] = 1
            state["research_gaps"] = {
                CANDIDATE_ID: [
                    _gap(gap_criterion, gap_query, f"gap:{gap_criterion}").model_dump(
                        mode="json"
                    )
                ]
            }
        try:
            delta = stage(state)
        except StageFailure as exc:
            batches.append(
                dict(
                    batch=name,
                    status="failed",
                    errors=[(e.error_code, e.message_redacted) for e in exc.errors],
                )
            )
            break
        for field in ("evidence", "sources", "chunks"):
            state[field] = {**state[field], **delta.get(field, {})}
        state["retrieval_history"] = delta["retrieval_history"]
        state["evidence_revisions"].update(delta.get("evidence_revisions", {}))
        batches.append(
            dict(
                batch=name,
                status="ok" if delta.get("evidence") else "no_evidence",
                new_evidence_ids=sorted(delta.get("evidence", {})),
                evidence_revision=state["evidence_revisions"][CANDIDATE_ID],
            )
        )

    chunks = state["chunks"]
    report = dict(
        issue=55,
        execution_mode="local_model",
        llm=llm,
        as_of=AS_OF.isoformat(),
        index_version=snapshot.index_version,
        corpus_version=snapshot.corpus_version,
        model_revision=REVISION,
        inputs=dict(
            initial=[initial_criterion, initial_query],
            gap=[gap_criterion, gap_query],
            top_k=top_k,
            note="OPEN 정책 대신 smoke 인자로 준 값",
        ),
        batches=batches,
        records=[
            dict(
                retrieval_id=r["retrieval_id"],
                tool=r["tool_name"],
                status=r["status"],
                cache_hit=r["cache_hit"],
                gap_id=r["arguments_without_secrets"].get("research_gap_id"),
                initial=r["arguments_without_secrets"].get("research_initial"),
                chunk_pages=[
                    (cid, chunks[cid]["page_start"], chunks[cid]["page_end"])
                    for cid in r["chunk_ids"]
                    if cid in chunks
                ],
                evidence_ids=r["evidence_ids"],
            )
            for r in state["retrieval_history"]
        ],
        evidence={
            k: dict(
                claim=e["claim"][:160],
                excerpt_head=e["excerpt"][:160],
                criterion_ids=e["criterion_ids"],
                locator=e["locator"],
                value=e["value"],
                unit=e["unit"],
                provenance=e["provenance"],
            )
            for k, e in state["evidence"].items()
        },
        retrieve_ledger=retrieve_runtime.ledger.snapshot(),
        query_token_lengths=local.token_lengths,
        elapsed_seconds=time.monotonic() - started,
        outcomes=[
            dict(
                status=o.status,
                initial=o.initial,
                calls=[(c.tool, c.status, list(c.error_codes)) for c in o.calls],
                rejected=sorted(Counter(o.rejected).items()),
                errors=[e.error_code for e in o.errors],
            )
            for o in research.outcomes
        ],
        quality_benchmark="not performed",
    )
    if llm == "openai":
        report["llm_ledger"] = extractor.runtime.ledger.snapshot()
        report["llm_records"] = [
            dict(status=r.status, **r.arguments_without_secrets)
            for r in extractor.retrieval_records
        ]
        report["llm_errors"] = [
            e.error_code for e in extractor.runtime.error_history.values()
        ]
    else:
        report["llm_calls"] = extractor.calls
    dumped = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if key and key in dumped:
        raise ValueError("secret leaked into report")
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "validation.json").write_text(dumped)
    return dict(
        batches=[(b["batch"], b["status"]) for b in batches],
        evidence=len(state["evidence"]),
        records=len(state["retrieval_history"]),
        elapsed_seconds=report["elapsed_seconds"],
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "model-path", "store-path", "receipt-path", "output-dir"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--timeout-seconds", type=float, required=True)
    p.add_argument("--llm", choices=("none", "openai"), required=True)
    for name in ("initial-criterion", "initial-query", "gap-criterion", "gap-query"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--top-k", type=int, required=True)
    a = p.parse_args()
    print(json.dumps(run(**vars(a)), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
