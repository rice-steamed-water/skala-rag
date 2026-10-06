"""#201 actual-data slice: approved local pi0.5 PDF source proof only.

Requires SKALA_APPROVED_SOURCE_ROOT (contains data/local + data/manifests) and
SKALA_APPROVED_INDEX (read-only SQLite). Skips when absent; never substitutes a
synthetic positive. Facts below are unreviewed assistant proposals: the only
correct semantic outcome is NOT_ESTABLISHED. Nothing here is human-reviewed.
This proves source closure and unreviewed denial, not real full-snapshot semantic
adjudication or meaningful refutation of reviewed claims; no positive is fabricated.
"""

import json
import os
import sqlite3
from pathlib import Path

import pytest

from skala_rag.agents.source_fact_verification import (
    AtomicFact,
    Measurement,
    SourceSpan,
    TrustedReviewRegistry,
    TrustedSource,
    assess_fact,
    verify_original_source,
    verify_span,
)
from skala_rag.contracts import Chunk, Source
from skala_rag.rag.corpus import CorpusManifest

PDF_SHA = "sha256:6a1029fd8ab6944b74cf22f5e5d30e60bc15699d964b2900af799b807a34b64c"
P4 = "chunk-page-v2-c96cf0b716eae2e302fdc6479b1ccf894b3d6b2318e036c8dacbdf5db04b4799"
P7 = "chunk-page-v2-5de40e48402f91f57f9435b2ff71010ec6fde1ae2cc92ce159fde1058dc0669c"
SPANS = {
    "p4-subtask": (
        P4,
        4,
        820,
        907,
        "first infers a high-level subtask, and then predicts the actions based "
        "on this subtask.",
        "sha256:9f0ffd46ba1f69b88dfd053abbc7962810461e8cffaf555699876683667ded87",
    ),
    "p7-50hz": (
        P7,
        7,
        2022,
        2085,
        "and the target base velocities at 50 Hz (with action chunking).",
        "sha256:aa12d444b3295c87e08b54ed0e5a82428bfeccc8826702c7180e4dabf5da83df",
    ),
    "p7-pd": (
        P7,
        7,
        2170,
        2231,
        "These targets are tracked with simple PD controllers, without",
        "sha256:aa12d444b3295c87e08b54ed0e5a82428bfeccc8826702c7180e4dabf5da83df",
    ),
}


def trusted_source() -> TrustedSource:
    root_env = os.environ.get("SKALA_APPROVED_SOURCE_ROOT")
    index_env = os.environ.get("SKALA_APPROVED_INDEX")
    if not root_env or not index_env:
        pytest.skip(
            "SKALA_APPROVED_SOURCE_ROOT/SKALA_APPROVED_INDEX not set: approved "
            "local pi0.5 PDF/index absent; no synthetic substitute"
        )
    root, index = Path(root_env), Path(index_env)
    manifest_path = root / "data/manifests/issue52-reviewed-text-v1.json"
    sources_path = root / "data/manifests/issue49-source-snapshots.json"
    if not (manifest_path.is_file() and sources_path.is_file() and index.is_file()):
        pytest.skip("approved manifest/index files absent at configured paths")
    manifest = CorpusManifest.model_validate_json(manifest_path.read_text())
    (document,) = [d for d in manifest.documents if d.content_hash == PDF_SHA]
    source = Source.model_validate(
        json.loads(sources_path.read_text())[document.source_id]
    )
    connection = sqlite3.connect(f"file:{index}?mode=ro", uri=True)
    try:
        rows = connection.execute("select payload from indexes").fetchall()
    finally:
        connection.close()
    chunks = []
    for (payload,) in rows:
        for item in json.loads(payload)["chunks"]:
            item = json.loads(item) if isinstance(item, str) else item
            if item["source_id"] == document.source_id:
                chunks.append(
                    Chunk.model_validate(item, context={"execution_mode": "live"})
                )
    assert chunks, "approved index lacks chunks for the approved source"
    return TrustedSource(
        path=root / document.local_path,
        allowed_root=root / "data/local",
        document=document,
        source=source,
        corpus_version=manifest.corpus_version,
        embedding_model=chunks[0].embedding_model,
        embedding_revision=chunks[0].embedding_revision,
        approved_chunks=tuple(chunks),
    )


def span(key) -> SourceSpan:
    chunk_id, page, start, end, quote, page_hash = SPANS[key]
    return SourceSpan(
        "src-8b9d0942c94fc60a1c8890d7", chunk_id, page, start, end, quote, page_hash
    )


def test_actual_pi05_source_proof_pass_semantic_not_established():
    trusted = trusted_source()
    proof = verify_original_source(trusted)
    assert proof.content_hash == PDF_SHA
    assert {P4, P7} <= set(proof.chunks)
    for key in SPANS:
        verify_span(proof, span(key))
    proposals = [
        AtomicFact(
            "subtask",
            "unassigned",
            span("p4-subtask"),
            "pi0.5",
            "high_level_subtask_conditions_low_level_actions",
            "actions",
        ),
        AtomicFact(
            "hz",
            "unassigned",
            span("p7-50hz"),
            "pi0.5",
            "base_velocity_target_command_frequency",
            "base velocity",
            Measurement("50", "Hz", "command_frequency", None),
        ),
        AtomicFact(
            "pd",
            "unassigned",
            span("p7-pd"),
            "pi0.5",
            "target_tracking_controller",
            "PD controller",
        ),
        # Same exact quotes, wrong claims.
        AtomicFact(
            "wrong-success",
            "unassigned",
            span("p7-50hz"),
            "pi0.5",
            "success_rate",
            "task",
            Measurement("50", "%", "success", None),
        ),
        AtomicFact(
            "wrong-contract",
            "unassigned",
            span("p7-pd"),
            "pi0.5",
            "paid_contract",
            "customer",
        ),
        AtomicFact(
            "wrong-self3",
            "unassigned",
            span("p4-subtask"),
            "pi0.5",
            "component_self_developed",
            "3 core components",
        ),
        AtomicFact(
            "wrong-stability",
            "unassigned",
            span("p7-pd"),
            "pi0.5",
            "customer_long_term_stability",
            "fleet",
        ),
    ]
    decisions = [assess_fact(f, proof, TrustedReviewRegistry({})) for f in proposals]
    assert all(d.source_proof == "PASS" for d in decisions)
    assert all(d.semantic == "NOT_ESTABLISHED" for d in decisions)
    assert {d.reason for d in decisions} == {"unreviewed"}
