"""Local reviewed corpus checks; no model or network used."""

from pathlib import Path

import pytest

from skala_rag.rag import index_validation as validation
from skala_rag.rag.corpus import manifest_hash


def test_actual_reviewed_corpus_preflight():
    root = Path(__file__).resolve().parents[2]
    if not (root / "data/local/issue49/2410.24164v4.pdf").exists():
        pytest.skip("git-excluded approved source PDFs unavailable")
    manifest, _, results, chunks = validation.prepare_corpus(
        root, model_id="not-embedded", model_revision="not-embedded"
    )
    assert len(chunks) == 36 and len(results) == 2
    assert {result.status for result in results.values()} == {"partial"}
    assert manifest_hash(manifest).startswith("sha256:")
