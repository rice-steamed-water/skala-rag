"""Synthetic OpenAI HTTP responses; socket access and actual API calls denied."""

import json
import socket
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from skala_rag.agents import m2_llm, m2_local_rag
from skala_rag.agents.m2_llm import M2LLMs
from skala_rag.contracts.interfaces import LLMError
from skala_rag.fakes import FakeClock
from skala_rag.settings import load_runtime_document
from skala_rag.tools.structured_llm import APPROVED_MODEL

KEY = "sk-synthetic-m2-key-not-a-real-credential"


class Output(BaseModel):
    answer: int


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("offline M2 runtime attempted network")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


def build(handler, **kwargs):
    clock = FakeClock(datetime(2026, 9, 30, tzinfo=UTC))
    return M2LLMs(
        api_key=KEY,
        run_id="synthetic-run",
        candidate_id="synthetic-company",
        schema_version="test",
        execution_mode="fixture",
        clock=clock,
        deadline=clock.now() + timedelta(minutes=10),
        http_transport=httpx.MockTransport(handler),
        **kwargs,
    )


def response():
    return httpx.Response(
        200,
        json={
            "model": APPROVED_MODEL,
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"answer":1}'}],
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 10},
        },
    )


def test_three_stages_share_eight_physical_requests_and_safe_observations():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return response()

    llms = build(handler)
    assert len({id(llm.runtime.ledger) for llm in llms.stages.values()}) == 1
    for i in range(8):
        llm = list(llms.stages.values())[i % 3]
        assert (
            llm.generate(
                system="synthetic system", user="synthetic user", output_schema=Output
            ).answer
            == 1
        )
    with pytest.raises(LLMError):
        llms.stages["technology"].generate(
            system="synthetic", user="synthetic", output_schema=Output
        )
    assert len(seen) == 8
    assert all(
        p["model"] == APPROVED_MODEL and p["max_output_tokens"] == 2000 for p in seen
    )
    observation = llms.observations()
    assert observation["ledger"]["calls"] == 8
    assert observation["actual_cost_usd"] is None
    assert KEY not in json.dumps(observation)
    assert "synthetic user" not in json.dumps(observation)


@pytest.mark.parametrize("failure", [401, 429, 500, "timeout"])
def test_transport_failure_never_retries_and_consumes_shared_budget(failure):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout(KEY)
        return httpx.Response(failure, text=KEY)

    llms = build(handler)
    with pytest.raises(LLMError) as error:
        llms.stages["eligibility"].generate(
            system="synthetic", user="synthetic", output_schema=Output
        )
    assert len(calls) == 1
    assert llms.runtime.ledger.snapshot()["calls"] == 1
    assert KEY not in str(error.value)
    assert KEY not in json.dumps(llms.observations())


def test_overlarge_request_is_blocked_before_http_or_budget_spending():
    seen = []
    llms = build(lambda request: seen.append(request) or response())
    with pytest.raises(LLMError):
        llms.stages["evidence"].generate(
            system="synthetic", user="x" * 8001, output_schema=Output
        )
    assert seen == []
    assert llms.runtime.ledger.snapshot()["calls"] == 0


def test_composition_loads_once_and_retains_injected_clock_and_transport(
    monkeypatch,
):
    document = load_runtime_document()
    loads = []

    def load():
        loads.append(True)
        return document

    monkeypatch.setattr(m2_llm, "load_runtime_document", load)
    seen = []
    llms = build(lambda request: seen.append(request) or response())
    for stage in llms.stages.values():
        assert stage.transport.llm_settings is document.llm
        assert stage.transport._clock is llms.runtime.clock
        stage.generate(system="s", user="u", output_schema=Output)
    assert loads == [True]
    assert len(seen) == 3


def test_empty_key_rejects_before_configuration_read(monkeypatch):
    def unexpected_load():
        raise AssertionError("empty credential must precede configuration access")

    monkeypatch.setattr(m2_llm, "load_runtime_document", unexpected_load)
    clock = FakeClock(datetime(2026, 9, 30, tzinfo=UTC))
    with pytest.raises(ValueError, match="api_key is required"):
        M2LLMs(
            api_key=" ",
            run_id="test",
            candidate_id="test",
            schema_version="test",
            execution_mode="fixture",
            clock=clock,
            deadline=clock.now() + timedelta(minutes=1),
        )


def test_lower_snapshot_request_bound_reaches_wire_and_stays_fixed(
    tmp_path,
):
    from pathlib import Path

    path = tmp_path / "runtime.json"
    content = json.loads(Path("configs/runtime.json").read_bytes())
    content["profiles"]["m2_shared"]["request_output_tokens"] = 100
    path.write_text(json.dumps(content))
    document = load_runtime_document(path=path)
    seen = []
    llms = build(
        lambda request: seen.append(request) or response(),
        runtime_document=document,
    )
    content["profiles"]["m2_shared"]["request_output_tokens"] = 200
    path.write_text(json.dumps(content))
    llms.stages["evidence"].generate(system="s", user="u", output_schema=Output)

    assert json.loads(seen[0].content)["max_output_tokens"] == 100
    assert llms.runtime_document is document
    later = load_runtime_document(path=path)
    assert later.profiles.m2_shared.request_output_tokens == 200


@pytest.mark.parametrize(
    "field,value", [("max_calls", 9), ("request_output_tokens", 2001)]
)
def test_injected_profile_cannot_raise_approval_bounds(field, value):
    document = load_runtime_document()
    profile = document.profiles.m2_shared.model_copy(update={field: value})
    injected = document.model_copy(
        update={"profiles": document.profiles.model_copy(update={"m2_shared": profile})}
    )
    seen = []
    with pytest.raises(ValueError):
        build(
            lambda request: seen.append(request) or response(),
            runtime_document=injected,
        )
    assert seen == []


def test_local_retrieval_profile_reaches_request_without_changing_artifact_identity(
    tmp_path, monkeypatch
):
    document = load_runtime_document()
    rag = m2_local_rag.LocalRAG.__new__(m2_local_rag.LocalRAG)
    rag.runtime_profile = document.profiles.m2_local_rag.model_copy(
        update={"top_k": 2, "timeout_seconds": 11.0}
    )
    rag.model_path = tmp_path / "approved-model"
    monkeypatch.setattr(
        rag,
        "settings",
        SimpleNamespace(embedding_settings={"device": "cpu"}),
        raising=False,
    )
    monkeypatch.setattr(rag, "store", SimpleNamespace(), raising=False)
    monkeypatch.setattr(
        rag,
        "snapshot",
        SimpleNamespace(
            index_version="approved-index",
            bundle=SimpleNamespace(sources={"approved-source": None}),
        ),
        raising=False,
    )
    monkeypatch.setattr(
        rag,
        "plan",
        SimpleNamespace(metadata=SimpleNamespace(index_version="approved-index")),
        raising=False,
    )
    model = SimpleNamespace(max_seq_length=8192, get_embedding_dimension=lambda: 1024)
    model_inputs = []
    adapter_inputs = []

    def load_model(path, **kwargs):
        model_inputs.append((path, kwargs))
        return model

    def adapter(**kwargs):
        adapter_inputs.append(kwargs)
        return lambda request: request

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=load_model),
    )
    monkeypatch.setattr(m2_local_rag, "LocalEncoder", lambda model: model)
    monkeypatch.setattr(
        m2_local_rag, "LocalQueryEncoder", lambda *args, **kwargs: model
    )
    monkeypatch.setattr(m2_local_rag, "SQLiteDenseSearch", lambda **kwargs: kwargs)
    monkeypatch.setattr(m2_local_rag, "IndexedRetriever", adapter)
    clock = FakeClock(datetime(2026, 9, 30, tzinfo=UTC))
    request = rag.retrieve(
        candidate=SimpleNamespace(candidate_id="co-approved"),
        run_input=SimpleNamespace(
            schema_version="test",
            corpus_version="approved-corpus",
            as_of=clock.now().date(),
        ),
        run_id="approved-run",
        query="robot",
        clock=clock,
        deadline=clock.now() + timedelta(minutes=1),
    )

    assert request.top_k == 2
    assert request.index_version == "approved-index"
    assert request.corpus_version == "approved-corpus"
    assert request.allowed_source_ids == ["approved-source"]
    assert adapter_inputs[0]["budget"].timeout_seconds == 11
    assert adapter_inputs[0]["snapshot"] is rag.snapshot
    assert adapter_inputs[0]["backend"]["store"] is rag.store
    assert model_inputs == [
        (
            str(rag.model_path),
            {
                "local_files_only": True,
                "trust_remote_code": False,
                "device": "cpu",
            },
        )
    ]
