"""Reopen approved #145 artifacts for #62; no downloads or index writes."""

import hashlib
import json
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
from skala_rag.settings import RuntimeDocument, load_runtime_document
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    BudgetLedger,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
)


class LocalRAG:
    """Metadata preflight now; load the existing model only after admission."""

    def __init__(
        self,
        *,
        root: Path,
        model_path: Path,
        store_path: Path,
        receipt_path: Path,
        runtime_document: RuntimeDocument | None = None,
    ):
        self.model_path = model_path
        receipt = json.loads(receipt_path.read_text())
        manifest, sources, results, chunks = prepare_corpus(
            root, model_id=MODEL, model_revision=REVISION
        )
        self.settings = IndexSettings(
            **json.loads(receipt["metadata"]["settings_snapshot"])
        )
        self.plan = build_index_plan(
            manifest=manifest,
            expected_corpus_hash=manifest_hash(manifest),
            sources=sources,
            chunks=chunks,
            settings=self.settings,
            extraction_results=results,
        )
        if json.loads(json.dumps(self.plan.metadata.__dict__)) != receipt["metadata"]:
            raise ValueError("local index receipt/corpus mismatch")
        for name, digest in receipt["model_file_hashes"].items():
            path = (model_path / name).resolve()
            if not path.is_relative_to(model_path.resolve()):
                raise ValueError("model receipt path escapes model directory")
            h = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(block)
            if h.hexdigest() != digest:
                raise ValueError("local model file differs from approved receipt")
        self.store = SQLiteIndexStore(store_path)
        self.snapshot = snapshot_from_plan(
            self.plan,
            manifest=manifest,
            reopened_metadata=self.store.read_metadata(
                self.plan.metadata.index_version
            ),
            search_settings={"metric": "cosine"},
            execution_mode="live",
            extraction_results=results,
            source_inputs=sources,
        )
        document = (
            runtime_document
            if runtime_document is not None
            else load_runtime_document()
        )
        if runtime_document is not None:
            RuntimeDocument.model_validate_json(
                runtime_document.model_dump_json(), strict=True
            )
        self.runtime_profile = document.profiles.m2_local_rag

    def retrieve(self, *, candidate, run_input, run_id, query, clock, deadline):
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(
            str(self.model_path),
            local_files_only=True,
            trust_remote_code=False,
            device=self.settings.embedding_settings["device"],
        )
        if model.max_seq_length != 8192 or model.get_embedding_dimension() != 1024:
            raise ValueError("local model configuration mismatch")
        schema = run_input.schema_version
        profile = self.runtime_profile
        runtime = AdapterRuntime(
            policy=RuntimePolicy(
                schema_version=schema,
                execution_mode="live",
                retry_delays_seconds=(),
                live_approval_reference="#54/#145 local artifacts",
                timing_approval_reference="#62 timeout 30s/retries 0",
            ),
            ledger=BudgetLedger(
                RuntimeLimits(
                    schema_version=schema,
                    max_calls=profile.max_calls,
                    tool_max_calls={"local-retrieve": profile.max_calls},
                    max_input_tokens=profile.max_input_tokens,
                    max_output_tokens=profile.max_output_tokens,
                    max_cost_usd=profile.max_cost_usd,
                )
            ),
            clock=clock,
            sleep=lambda _: None,
        )
        adapter = IndexedRetriever(
            snapshot=self.snapshot,
            backend=SQLiteDenseSearch(
                store=self.store,
                metadata=self.plan.metadata,
                snapshot=self.snapshot,
                encoder=LocalQueryEncoder(LocalEncoder(model), settings=self.settings),
            ),
            runtime=runtime,
            readiness=Readiness(
                schema_version=schema,
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
                schema_version=schema,
                max_calls=profile.max_calls,
                max_retries=profile.max_retries,
                timeout_seconds=profile.timeout_seconds,
                deadline=deadline,
            ),
            allowance=Allowance(
                schema_version=schema, input_tokens=0, output_tokens=0, max_cost_usd=0
            ),
            run_id=run_id,
            schema_version=schema,
            tool_name="local-retrieve",
        )
        return adapter(
            RetrievalRequest(
                schema_version=schema,
                query=query,
                candidate_id=candidate.candidate_id,
                corpus_version=run_input.corpus_version,
                index_version=self.snapshot.index_version,
                as_of=run_input.as_of,
                top_k=profile.top_k,
                allowed_source_ids=list(self.snapshot.bundle.sources),
            )
        )
