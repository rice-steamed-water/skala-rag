"""#145 approved local BGE-M3 validation, with no hosted inference calls."""

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from skala_rag.rag.corpus import manifest_hash
from skala_rag.rag.index_v3 import (
    EmbeddingVector,
    IndexSettings,
    build_index_plan,
    write_index,
)
from skala_rag.rag.index_validation import prepare_corpus
from skala_rag.rag.sqlite_index import SQLiteIndexStore

MODEL = "BAAI/bge-m3"
REVISION = "5617a9f61b028005a4858fdac845db406aefb181"


class LocalEncoder:
    def __init__(self, model):
        self.model = model
        self.token_lengths = []

    def embed_texts(self, texts):
        lengths = [
            len(self.model.tokenizer(t, truncation=False)["input_ids"]) for t in texts
        ]
        if any(n > self.model.max_seq_length for n in lengths):
            raise ValueError(
                "input exceeds local tokenizer limit; no truncation allowed"
            )
        self.token_lengths.extend(lengths)
        rows = self.model.encode(
            list(texts),
            batch_size=1,
            normalize_embeddings=True,
            show_progress_bar=False,
            prompt="",
        )
        result = []
        for row in rows:
            values = tuple(float(v) for v in row)
            if len(values) != 1024 or any(not math.isfinite(v) for v in values):
                raise ValueError("invalid local embedding dimensions/values")
            if not math.isclose(math.hypot(*values), 1, abs_tol=1e-5):
                raise ValueError("local vector is not L2 normalized")
            result.append(values)
        if len(result) != len(texts):
            raise ValueError("local embedding count mismatch")
        return tuple(result)

    def encode(self, chunks, *, settings):
        if settings.model_id != MODEL or settings.model_revision != REVISION:
            raise ValueError("local BGE revision mismatch")
        rows = self.embed_texts(tuple(c.text for c in chunks))
        return tuple(
            EmbeddingVector(c.chunk_id, MODEL, REVISION, row)
            for c, row in zip(chunks, rows, strict=True)
        )


def run(root, model_path, output_dir, query, device):
    import sentence_transformers
    import torch
    import transformers
    from sentence_transformers import SentenceTransformer

    started = time.monotonic()
    for name in (
        "pytorch_model.bin",
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "modules.json",
        "1_Pooling/config.json",
    ):
        receipt = model_path / ".cache/huggingface/download" / (name + ".metadata")
        if not receipt.exists() or receipt.read_text().splitlines()[0] != REVISION:
            raise ValueError("model file lacks fixed-revision download receipt")
    manifest, sources, results, chunks = prepare_corpus(
        root, model_id=MODEL, model_revision=REVISION
    )
    model = SentenceTransformer(
        str(model_path), device=device, local_files_only=True, trust_remote_code=False
    )
    if model.max_seq_length != 8192 or model.get_embedding_dimension() != 1024:
        raise ValueError("local BGE-M3 configuration mismatch")
    settings = IndexSettings(
        model_id=MODEL,
        model_revision=REVISION,
        tokenizer_id=MODEL,
        tokenizer_revision=REVISION,
        tokenizer_settings={"truncate": False, "max_tokens": 8192},
        preprocessing_settings={"prefix": ""},
        chunk_settings=asdict(next(iter(results.values())).settings),
        embedding_settings={
            "mode": "dense",
            "normalization": "l2",
            "runtime": "sentence-transformers-local",
            "pooling": "cls",
            "device": device,
        },
        dimension=1024,
        store_schema_version="sqlite-local-validation-v1",
    )
    plan = build_index_plan(
        manifest=manifest,
        expected_corpus_hash=manifest_hash(manifest),
        sources=sources,
        chunks=chunks,
        settings=settings,
        extraction_results=results,
    )
    target = (root / output_dir).resolve()
    if not target.is_relative_to((root / "outputs").resolve()):
        raise ValueError("artifacts must remain inside outputs")
    target.mkdir(parents=True, exist_ok=False)
    encoder = LocalEncoder(model)
    write_index(
        plan,
        embedder=encoder,
        store=SQLiteIndexStore(target / "index.sqlite", plan=plan),
    )
    row = encoder.embed_texts((query,))[0]
    receipt = {
        "metadata": asdict(plan.metadata),
        "query_vector": asdict(EmbeddingVector("query", MODEL, REVISION, row)),
        "top_k": 5,
        "chunks": [json.loads(s) for s in plan.chunk_snapshots],
        "sources": [json.loads(s) for s in plan.source_snapshots],
    }
    receipt_path = target / "reopen-input.json"
    receipt_path.write_text(json.dumps(receipt, allow_nan=False))
    env = {
        k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "PYTHONPATH")
    }
    reopened = subprocess.run(
        [
            sys.executable,
            "-m",
            "skala_rag.rag.index_validation",
            "reopen",
            "--store",
            str(target / "index.sqlite"),
            "--receipt",
            str(receipt_path),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )
    hashes = {}
    for file in sorted(model_path.rglob("*")):
        if file.is_file() and ".cache" not in file.parts:
            digest = hashlib.sha256()
            with file.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            hashes[str(file.relative_to(model_path))] = digest.hexdigest()
    report = {
        "issue": 145,
        "execution_mode": "local_model",
        "status": "embedding_index_search_verified",
        "model_id": MODEL,
        "model_revision": REVISION,
        "tokenizer_revision": REVISION,
        "metadata": asdict(plan.metadata),
        "model_file_hashes": hashes,
        "chunks": len(chunks),
        "dimensions": 1024,
        "token_lengths": encoder.token_lengths,
        "query": query,
        "hits": json.loads(reopened.stdout),
        "fresh_process_reopen": True,
        "full_document_statuses": [r.status for r in results.values()],
        "versions": {
            "python": sys.version,
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "sentence_transformers": sentence_transformers.__version__,
        },
        "device": device,
        "elapsed_seconds": time.monotonic() - started,
        "inference_api_calls": 0,
        "inference_api_cost_usd": 0,
        "local_compute_cost": "not measured",
        "uv_lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
        "quality_benchmark": "not performed",
    }
    (target / "validation.json").write_text(
        json.dumps(report, indent=2, allow_nan=False)
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--device", choices=("cpu", "mps"), required=True)
    args = parser.parse_args()
    report = run(
        args.root.resolve(),
        args.model_path.resolve(),
        args.output_dir,
        args.query,
        args.device,
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("status", "chunks", "dimensions", "elapsed_seconds", "hits")
            }
        )
    )


if __name__ == "__main__":
    main()
