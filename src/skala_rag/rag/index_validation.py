"""Reviewed corpus preflight and fresh-process index metadata validation."""

import argparse
import json
from pathlib import Path

from skala_rag.contracts import Source
from skala_rag.rag.corpus import CorpusManifest, manifest_hash
from skala_rag.rag.extraction import PageChunkSettings, extract_local_document
from skala_rag.rag.index_v3 import EmbeddingVector, IndexMetadata
from skala_rag.rag.reviewed_extraction import verify_text_review
from skala_rag.rag.sqlite_index import SQLiteIndexStore


def prepare_corpus(root: Path, *, model_id: str, model_revision: str):
    manifest = CorpusManifest.model_validate_json(
        (root / "data/manifests/issue52-reviewed-text-v1.json").read_text()
    )
    sources = {
        key: Source.model_validate(value)
        for key, value in json.loads(
            (root / "data/manifests/issue49-source-snapshots.json").read_text()
        ).items()
    }
    results = {}
    for document in manifest.documents:
        review = document.text_index_review
        if review is None:
            raise ValueError("approved text review required")
        result = extract_local_document(
            manifest,
            document.document_id,
            sources[document.source_id],
            root=root,
            schema_version=manifest.schema_version,
            settings=PageChunkSettings(**review.extraction_settings),
            sections_by_page={},
            embedding_model=model_id,
            embedding_revision=model_revision,
        )
        verify_text_review(result, review)
        results[document.document_id] = result
    chunks = tuple(c for result in results.values() for c in result.chunks)
    if len(chunks) != 36 or len(results) != 2:
        raise ValueError("#145 expects the reviewed two-document/36-chunk corpus")
    return manifest, sources, results, chunks


def reopen_search(store_path: Path, receipt_path: Path):
    receipt = json.loads(receipt_path.read_text())
    payload = receipt["metadata"]
    for field in ("chunk_ids", "document_ids"):
        payload[field] = tuple(payload[field])
    metadata = IndexMetadata(**payload)
    query = EmbeddingVector(**receipt["query_vector"])
    store = SQLiteIndexStore(store_path)
    if store.read_metadata(metadata.index_version) != metadata:
        raise ValueError("fresh process metadata mismatch")
    hits = store.search(query, expected=metadata, top_k=len(metadata.chunk_ids))
    expected_chunks = {c["chunk_id"]: c for c in receipt["chunks"]}
    expected_sources = {s["source_id"]: s for s in receipt["sources"]}
    if len(hits) != len(expected_chunks) or any(
        h.chunk.model_dump(mode="json") != expected_chunks.get(h.chunk.chunk_id)
        or h.source.model_dump(mode="json") != expected_sources.get(h.source.source_id)
        for h in hits
    ):
        raise ValueError("fresh process Chunk/Source snapshot mismatch")
    hits = hits[: receipt["top_k"]]
    if not hits:
        raise ValueError("dense search returned no hits")
    return [
        {
            "chunk_id": h.chunk.chunk_id,
            "source_id": h.source.source_id,
            "page_start": h.chunk.page_start,
            "page_end": h.chunk.page_end,
            "locator": h.chunk.locator,
            "similarity": h.similarity,
            "text_only": h.source.bibliographic_metadata.get(
                "text_index_review", {}
            ).get("indexing_scope"),
        }
        for h in hits
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--root", type=Path, required=True)
    reopen = commands.add_parser("reopen")
    reopen.add_argument("--store", type=Path, required=True)
    reopen.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "preflight":
        manifest, _, results, chunks = prepare_corpus(
            args.root.resolve(), model_id="not-embedded", model_revision="not-embedded"
        )
        print(
            json.dumps(
                {
                    "live_executed": False,
                    "corpus_hash": manifest_hash(manifest),
                    "chunks": len(chunks),
                    "statuses": [r.status for r in results.values()],
                }
            )
        )
    else:
        print(json.dumps(reopen_search(args.store, args.receipt), allow_nan=False))


if __name__ == "__main__":
    main()
