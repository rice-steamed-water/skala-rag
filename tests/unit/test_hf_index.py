"""Mock HTTP and synthetic vectors only; no Hugging Face live execution."""

import json
import sqlite3
from dataclasses import replace

import httpx
import pytest
from tests.unit.test_index_v3 import FakeEmbedder, chunk, document, plan, settings

from skala_rag.rag.hf_embedding import HFDeployment, HFEmbeddingEncoder
from skala_rag.rag.index_v3 import EmbeddingVector, write_index
from skala_rag.rag.sqlite_index import SQLiteIndexStore


def api_setup():
    deployment = HFDeployment(
        endpoint="https://fixture.endpoints.huggingface.cloud",
        model_id="BAAI/bge-m3",
        model_revision="synthetic-revision-not-approved",
        tokenizer_revision="synthetic-tokenizer-not-approved",
        deployment_record="synthetic-deployment-record",
    )
    config = settings(
        tokenizer_settings={"truncate": False},
        preprocessing_settings={"prefix": ""},
        embedding_settings={
            "mode": "dense",
            "normalization": "l2",
            "runtime": "hf-http",
            "endpoint": deployment.endpoint,
            "deployment_record": deployment.deployment_record,
        },
    )
    return deployment, config


def test_api_and_sqlite_end_to_end_reopen(tmp_path):
    deployment, config = api_setup()
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-secret"
        assert json.loads(request.content) == {
            "inputs": [chunk(document()).text],
            "normalize": True,
            "truncate": False,
        }
        return httpx.Response(200, json=[[3, 4]])

    result = plan(settings=config)
    path = tmp_path / "index.sqlite"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        encoder = HFEmbeddingEncoder(
            deployment=deployment,
            token="synthetic-secret",
            client=client,
            timeout_seconds=10,
        )
        write_index(result, embedder=encoder, store=SQLiteIndexStore(path, plan=result))
    reopened = SQLiteIndexStore(path)
    assert reopened.read_metadata(result.metadata.index_version) == result.metadata
    query = EmbeddingVector("query", config.model_id, config.model_revision, (3, 4))
    hits = reopened.search(query, expected=result.metadata, top_k=1)
    assert hits[0].similarity == pytest.approx(1)
    assert hits[0].chunk.text == chunk(document()).text
    assert hits[0].source.source_id == "src-a"
    assert len(calls) == 1
    assert b"synthetic-secret" not in path.read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        write_index(
            result, embedder=FakeEmbedder(), store=SQLiteIndexStore(path, plan=result)
        )


@pytest.mark.parametrize(
    "payload",
    [[], [[1]], [[True, 1]], [[[1, 2], [3, 4]]], [[0, 0]], [[float("inf"), 1]]],
)
def test_api_rejects_invalid_vectors(payload):
    deployment, config = api_setup()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=json.dumps(payload))
        )
    ) as client:
        encoder = HFEmbeddingEncoder(
            deployment=deployment, token="secret", client=client, timeout_seconds=1
        )
        with pytest.raises(ValueError):
            encoder.encode((chunk(document()),), settings=config)


@pytest.mark.parametrize("status", [302, 401, 429, 503])
def test_api_error_does_not_expose_secret_or_body(status):
    deployment, config = api_setup()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, text="secret PRIVATE_BODY")
        )
    ) as client:
        encoder = HFEmbeddingEncoder(
            deployment=deployment, token="secret", client=client, timeout_seconds=1
        )
        with pytest.raises(ValueError) as error:
            encoder.encode((chunk(document()),), settings=config)
        assert "secret" not in str(error.value)
        assert "PRIVATE_BODY" not in str(error.value)


def test_api_identity_mismatch_before_http():
    deployment, config = api_setup()

    def forbidden(_):
        pytest.fail("identity mismatch must not call HTTP")

    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        encoder = HFEmbeddingEncoder(
            deployment=deployment, token="secret", client=client, timeout_seconds=1
        )
        with pytest.raises(ValueError, match="identity"):
            encoder.embed_texts(
                ("synthetic text",), settings=replace(config, model_revision="other")
            )


def test_sqlite_query_identity_and_corruption(tmp_path):
    result = plan()
    path = tmp_path / "index.sqlite"
    write_index(
        result, embedder=FakeEmbedder(), store=SQLiteIndexStore(path, plan=result)
    )
    store = SQLiteIndexStore(path)
    query = EmbeddingVector("query", result.metadata.model_id, "other", (1, 0))
    with pytest.raises(ValueError, match="space"):
        store.search(query, expected=result.metadata, top_k=1)
    with pytest.raises(ValueError, match="identity"):
        store.search(
            query, expected=replace(result.metadata, corpus_hash="other"), top_k=1
        )
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE indexes SET payload = '{}' ")
    with pytest.raises(ValueError, match="integrity"):
        store.read_metadata(result.metadata.index_version)


def test_failed_embedding_creates_no_store(tmp_path):
    result = plan()
    path = tmp_path / "index.sqlite"
    with pytest.raises(ValueError):
        write_index(
            result,
            embedder=FakeEmbedder(vectors=[]),
            store=SQLiteIndexStore(path, plan=result),
        )
    assert not path.exists()
