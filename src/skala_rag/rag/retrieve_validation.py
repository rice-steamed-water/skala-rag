"""Opt-in #54 real local retrieval validation over the existing #145 artifact."""

import argparse
import hashlib
import json
import math
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from skala_rag.contracts import RetrievalRequest, ToolBudget
from skala_rag.rag.adapter import IndexedRetriever
from skala_rag.rag.corpus import manifest_hash
from skala_rag.rag.dense import snapshot_from_plan
from skala_rag.rag.index_v3 import IndexSettings, build_index_plan
from skala_rag.rag.index_validation import prepare_corpus
from skala_rag.rag.local_bge_validation import MODEL, REVISION, LocalEncoder
from skala_rag.rag.query_local import LocalQueryEncoder
from skala_rag.rag.sqlite_index import SQLiteIndexStore
from skala_rag.rag.sqlite_retrieve import SQLiteDenseSearch
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    BudgetLedger,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
)


class Clock:
    def now(self):
        return datetime.now(UTC)


def run(
    *,
    root: Path,
    model_path: Path,
    store_path: Path,
    receipt_path: Path,
    output_dir: Path,
    timeout_seconds: float,
):
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("explicit positive finite local bound required")
    if not output_dir.resolve().is_relative_to((root / "outputs").resolve()):
        raise ValueError("validation artifacts must remain in outputs")
    if output_dir.exists():
        raise ValueError("validation artifacts already exist")
    started = time.monotonic()
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
    backend = SQLiteDenseSearch(
        store=store,
        metadata=plan.metadata,
        snapshot=snapshot,
        encoder=LocalQueryEncoder(local, settings=settings),
    )
    runtime = AdapterRuntime(
        policy=RuntimePolicy(
            schema_version="issue54-local-v1",
            execution_mode="live",
            retry_delays_seconds=(),
            live_approval_reference="issue145-local-bge-validation",
            timing_approval_reference="explicit-local-validation-bound-not-provider-policy",
        ),
        ledger=BudgetLedger(
            RuntimeLimits(
                schema_version="issue54-local-v1",
                max_calls=4,
                tool_max_calls={"local-retrieve": 4},
                max_input_tokens=0,
                max_output_tokens=0,
                max_cost_usd=0,
            )
        ),
        clock=Clock(),
        sleep=lambda _: None,
    )
    adapter = IndexedRetriever(
        snapshot=snapshot,
        backend=backend,
        runtime=runtime,
        readiness=Readiness(
            schema_version="issue54-local-v1",
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
            schema_version="issue54-local-v1",
            max_calls=1,
            max_retries=0,
            timeout_seconds=timeout_seconds,
            deadline=datetime.now(UTC) + timedelta(seconds=timeout_seconds),
        ),
        allowance=Allowance(
            schema_version="issue54-local-v1",
            input_tokens=0,
            output_tokens=0,
            max_cost_usd=0,
        ),
        run_id="issue54-local-validation",
        schema_version="issue54-local-v1",
        tool_name="local-retrieve",
    )
    request = RetrievalRequest(
        schema_version="issue54-local-v1",
        query=previous["query"],
        candidate_id="co-physical-intelligence",
        corpus_version=snapshot.corpus_version,
        index_version=snapshot.index_version,
        as_of="2026-09-30",
        top_k=5,
        allowed_source_ids=list(sources),
    )
    requests = {
        "all": request,
        "cache": request,
        "source_only": request.model_copy(
            update={"allowed_source_ids": [manifest.documents[0].source_id]}
        ),
        "other_company": request.model_copy(update={"candidate_id": "co-unrelated"}),
        "historical": request.model_copy(update={"as_of": datetime(2025, 1, 1).date()}),
        "no_sources": request.model_copy(update={"allowed_source_ids": []}),
    }
    receipts = {}
    for name, req in requests.items():
        result = adapter(req)
        expected = (
            "empty" if name in ("other_company", "historical", "no_sources") else "ok"
        )
        if result.status != expected:
            raise ValueError(f"retrieval validation failed: {name}/{result.status}")
        if name == "cache" and not result.retrieval_records[0].cache_hit:
            raise ValueError("repeat query did not use cache")
        if name == "source_only" and set(result.data.sources) != set(
            req.allowed_source_ids
        ):
            raise ValueError("allowed Source isolation failed")
        receipts[name] = {
            "status": result.status,
            "records": [r.model_dump(mode="json") for r in result.retrieval_records],
            "hits": [
                dict(
                    chunk_id=c.chunk_id,
                    source_id=c.source_id,
                    page_start=c.page_start,
                    page_end=c.page_end,
                    locator=c.locator,
                    text_only=result.data.sources[c.source_id].bibliographic_metadata[
                        "text_index_review"
                    ]["indexing_scope"],
                )
                for c in result.data.chunks
            ],
        }
    if runtime.ledger.snapshot()["calls"] != 2:
        raise ValueError("cache/filter empty unexpectedly encoded query")
    output_dir.mkdir(parents=True, exist_ok=False)
    report = dict(
        issue=54,
        execution_mode="local_model",
        status="retrieve_verified",
        index_version=snapshot.index_version,
        corpus_hash=snapshot.corpus_hash,
        model_revision=REVISION,
        cases=receipts,
        local_query_calls=2,
        token_lengths=local.token_lengths,
        api_calls=0,
        api_cost_usd=0,
        local_compute_cost="not measured",
        elapsed_seconds=time.monotonic() - started,
        quality_benchmark="not performed",
        text_only=True,
        uv_lock_sha256=hashlib.sha256(
            (Path(__file__).resolve().parents[3] / "uv.lock").read_bytes()
        ).hexdigest(),
    )
    (output_dir / "validation.json").write_text(json.dumps(report, indent=2))
    return {
        k: report[k]
        for k in ("status", "local_query_calls", "api_calls", "elapsed_seconds")
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "model-path", "store-path", "receipt-path", "output-dir"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--timeout-seconds", type=float, required=True)
    a = p.parse_args()
    print(json.dumps(run(**vars(a))))


if __name__ == "__main__":
    main()
