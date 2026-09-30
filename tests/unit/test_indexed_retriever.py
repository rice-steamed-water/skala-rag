"""Synthetic pinned index boundary tests. No downloaded model or real index."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.fixtures.loader import load_common_fixtures
from tests.unit.test_fixture_retrieval import request

from skala_rag.contracts import RetrievalBundle, ToolBudget
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import Retrieve
from skala_rag.fakes import FakeClock
from skala_rag.rag.adapter import IndexedRetriever, IndexSnapshot, index_identity
from skala_rag.scoring.catalog import load_policy
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    BudgetLedger,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
    TransportFailure,
)


@pytest.fixture
def data():
    return load_common_fixtures(
        load_policy(
            Path(__file__).resolve().parents[2] / "configs/scoring.draft.json",
            execution_mode="fixture",
        )
    )


class Backend:
    retry_owner = "runtime"

    def __init__(self):
        self.calls = []
        self.transform = lambda bundle: bundle
        self.failure = None

    def search_once(self, req, *, snapshot, allowed_chunk_ids, timeout_seconds):
        self.calls.append((req, allowed_chunk_ids, timeout_seconds))
        if self.failure:
            raise self.failure
        chunks = [c for c in snapshot.bundle.chunks if c.chunk_id in allowed_chunk_ids][
            : req.top_k
        ]
        bundle = RetrievalBundle.model_validate(
            dict(
                schema_version="synthetic-1",
                chunks=chunks,
                sources={
                    c.source_id: snapshot.bundle.sources[c.source_id] for c in chunks
                },
            ),
            context={"execution_mode": "fixture"},
        )
        return self.transform(bundle)


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("external network forbidden")

    monkeypatch.setattr("socket.socket.connect", denied)


@pytest.fixture
def setup(data):
    bundle = RetrievalBundle.model_validate(
        dict(
            schema_version="synthetic-1",
            chunks=list(data.chunks.values()),
            sources=data.sources,
        ),
        context={"execution_mode": "fixture"},
    )
    # Keep only referenced sources and one model identity; all values are synthetic.
    bundle.sources = {c.source_id: bundle.sources[c.source_id] for c in bundle.chunks}
    for c in bundle.chunks:
        c.embedding_model, c.embedding_revision = (
            "synthetic-model",
            "synthetic-revision",
        )
    snapshot = IndexSnapshot.model_validate(
        dict(
            schema_version="synthetic-1",
            corpus_version="corpus-fixture-v1",
            corpus_hash="synthetic-hash",
            index_version="index-fixture-v1",
            embedding_model="synthetic-model",
            embedding_revision="synthetic-revision",
            search_settings={"metric": "synthetic"},
            bundle=bundle,
        ),
        context={"execution_mode": "fixture"},
    )
    backend = Backend()
    runtime = AdapterRuntime(
        policy=RuntimePolicy(
            schema_version="synthetic-1",
            execution_mode="fixture",
            retry_delays_seconds=(),
            live_approval_reference=None,
            timing_approval_reference=None,
        ),
        ledger=BudgetLedger(
            RuntimeLimits(
                schema_version="synthetic-1",
                max_calls=10,
                tool_max_calls={"retrieve": 10},
                max_input_tokens=0,
                max_output_tokens=0,
                max_cost_usd=Decimal(1),
            )
        ),
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=UTC)),
        sleep=lambda seconds: None,
    )
    readiness = Readiness(
        schema_version="synthetic-1",
        required=True,
        configured=True,
        credential_required=False,
        credential_present=False,
        model_required=True,
        model_available=True,
        index_required=True,
        index_available=True,
    )

    def build(**updates):
        kwargs = dict(
            snapshot=snapshot,
            backend=backend,
            runtime=runtime,
            readiness=readiness,
            budget=ToolBudget(
                schema_version="synthetic-1",
                max_calls=1,
                max_retries=0,
                timeout_seconds=1,
            ),
            allowance=Allowance(
                schema_version="synthetic-1",
                input_tokens=0,
                output_tokens=0,
                max_cost_usd=Decimal("0.1"),
            ),
            run_id="synthetic-run",
            schema_version="synthetic-1",
            tool_name="retrieve",
        )
        kwargs.update(updates)
        return IndexedRetriever(**kwargs)

    return snapshot, backend, runtime, readiness, build


def test_protocol_cache_copies_and_trace(data, setup):
    snapshot, backend, runtime, _, build = setup
    retrieve = build()
    assert isinstance(retrieve, Retrieve)
    first = retrieve(request(data))
    assert first.status == "ok"
    assert first.retrieval_records[0].chunk_ids == [
        c.chunk_id for c in first.data.chunks
    ]
    first.data.chunks[0].text = "caller mutation"
    second = retrieve(request(data))
    assert second.data.chunks[0].text != "caller mutation"
    assert second.retrieval_records[0].cache_hit
    assert (
        first.retrieval_records[0].retrieval_id
        != second.retrieval_records[0].retrieval_id
    )
    assert len(backend.calls) == runtime.ledger.snapshot()["calls"] == 1
    assert all(r.query is None for r in first.retrieval_records)
    assert "가상 로봇 기술" not in first.retrieval_records[0].model_dump_json()
    assert snapshot.bundle.chunks[0].text != "caller mutation"


@pytest.mark.parametrize(
    "update",
    [
        dict(query="different"),
        dict(candidate_id="other"),
        dict(as_of="2026-09-28"),
        dict(top_k=0),
        dict(allowed_source_ids=[]),
    ],
)
def test_scope_is_filtered_before_top_k_and_cache_separated(data, setup, update):
    _, backend, _, _, build = setup
    retrieve = build()
    retrieve(request(data))
    result = retrieve(request(data, **update))
    assert len(backend.calls) == 2
    assert len(retrieve.cache_keys) == 2
    for chunk in result.data.chunks:
        assert chunk.chunk_id in backend.calls[-1][1]


@pytest.mark.parametrize("field", ["corpus_version", "index_version"])
def test_wrong_version_never_searches(data, setup, field):
    _, backend, runtime, _, build = setup
    result = build()(request(data, **{field: "replacement"}))
    assert result.errors[0].error_code == "TOOL_RESPONSE_INVALID"
    assert not backend.calls
    assert runtime.ledger.snapshot()["calls"] == 0


@pytest.mark.parametrize("field", ["index_available", "model_available", "configured"])
def test_missing_readiness_zero_requests(data, setup, field):
    _, backend, runtime, ready, build = setup
    result = build(readiness=ready.model_copy(update={field: False}))(request(data))
    assert result.status == "unavailable"
    assert not backend.calls
    assert runtime.ledger.snapshot()["calls"] == 0


@pytest.mark.parametrize(
    "mutation",
    ["text", "locator", "page_start", "content_hash", "extra_source", "duplicate"],
)
def test_replacement_and_forged_payload_rejected(data, setup, mutation):
    _, backend, _, _, build = setup

    def corrupt(bundle):
        if mutation == "content_hash":
            next(iter(bundle.sources.values())).content_hash = "replaced"
        elif mutation == "extra_source":
            extra = next(iter(bundle.sources.values())).model_copy(deep=True)
            extra.source_id = "extra"
            bundle.sources["extra"] = extra
        elif mutation == "duplicate":
            bundle.chunks.append(bundle.chunks[0])
        else:
            setattr(
                bundle.chunks[0], mutation, 99 if mutation == "page_start" else "forged"
            )
        return bundle

    backend.transform = corrupt
    retrieve = build()
    result = retrieve(request(data))
    assert result.status == "failed"
    assert result.errors[0].error_code == "TOOL_RESPONSE_INVALID"
    assert not retrieve.cache_keys


def test_unknown_date_acquisition_and_future_snapshot(data, setup):
    snapshot, backend, _, _, build = setup
    for s in snapshot.bundle.sources.values():
        s.published_at = None
        s.retrieved_at = datetime(2026, 10, 1, tzinfo=UTC)
    result = build()(request(data))
    assert result.status == "empty"
    assert backend.calls[0][1] == ()


def test_exception_redaction_and_no_failure_cache(data, setup):
    _, backend, _, _, build = setup
    backend.failure = RuntimeError("secret-api-key https://secret/prompt")
    retrieve = build()
    result = retrieve(request(data))
    assert result.status == "failed"
    assert "secret-api-key" not in result.model_dump_json()
    assert "https://secret" not in result.model_dump_json()
    assert not retrieve.cache_keys


def test_index_unavailable_is_not_empty(data, setup):
    _, backend, _, _, build = setup
    backend.failure = TransportFailure(ErrorCode.TOOL_UNAVAILABLE)
    result = build()(request(data))
    assert result.status == "unavailable"
    assert result.data is None


def test_budget_stops_new_search(data, setup):
    _, backend, runtime, _, build = setup
    runtime.ledger.limits = runtime.ledger.limits.model_copy(update={"max_calls": 0})
    result = build()(request(data))
    assert result.errors[0].error_code == "BUDGET_EXHAUSTED"
    assert not backend.calls


def test_settings_hash_and_chunk_order(setup):
    snapshot, _, _, _, _ = setup
    reordered = snapshot.model_copy(deep=True)
    reordered.bundle.chunks.reverse()
    assert index_identity(snapshot) == index_identity(reordered)
    reordered.search_settings["metric"] = "other"
    assert index_identity(snapshot) != index_identity(reordered)


def test_snapshot_mutation_isolated(data, setup):
    snapshot, _, _, _, build = setup
    retrieve = build()
    snapshot.bundle.chunks[0].text = "changed after construction"
    assert retrieve(request(data)).status == "ok"


def test_hidden_retry_owner_rejected(data, setup):
    _, backend, _, _, build = setup
    backend.retry_owner = "backend"
    result = build()(request(data))
    assert result.errors[0].error_code == "TOOL_RESPONSE_INVALID"
    assert not backend.calls


def test_allowed_sources_are_a_set(data, setup):
    _, backend, _, _, build = setup
    retrieve = build()
    req = request(data)
    retrieve(req)
    result = retrieve(
        request(
            data,
            allowed_source_ids=list(reversed(req.allowed_source_ids))
            + req.allowed_source_ids,
        )
    )
    assert result.retrieval_records[0].cache_hit
    assert len(backend.calls) == 1


def test_backend_ignoring_company_filter_is_rejected(data, setup):
    snapshot, backend, _, _, build = setup
    backend.transform = lambda bundle: snapshot.bundle
    result = build()(request(data))
    assert result.status == "failed"
    assert result.errors[0].error_code == "TOOL_RESPONSE_INVALID"


def test_unknown_date_prior_snapshot_is_allowed(data, setup):
    snapshot, _, _, _, build = setup
    for source in snapshot.bundle.sources.values():
        source.published_at = None
        source.retrieved_at = datetime(2026, 9, 29, tzinfo=UTC)
    assert build()(request(data)).status == "ok"


def test_late_acquisition_cannot_backdate_published_source(data, setup):
    snapshot, _, _, _, build = setup
    for source in snapshot.bundle.sources.values():
        source.retrieved_at = datetime(2026, 10, 1, tzinfo=UTC)
    assert build()(request(data)).status == "empty"


def test_live_missing_approval_zero_calls(data, setup):
    _, backend, runtime, _, build = setup
    # Construct with fixture payloads first: locators must not pass as live data.
    retrieve = build()
    runtime.policy = runtime.policy.model_copy(update={"execution_mode": "live"})
    result = retrieve(request(data))
    assert result.status == "unavailable"
    assert not backend.calls
    assert runtime.ledger.snapshot()["calls"] == 0


def test_expired_deadline_does_not_serve_cache(data, setup):
    _, backend, runtime, _, build = setup
    retrieve = build(
        budget=ToolBudget(
            schema_version="synthetic-1",
            max_calls=1,
            max_retries=0,
            timeout_seconds=1,
            deadline=datetime(2026, 10, 1, tzinfo=UTC),
        )
    )
    retrieve(request(data))
    runtime.clock.advance(timedelta(days=1))
    result = retrieve(request(data))
    assert result.errors[0].error_code == "BUDGET_EXHAUSTED"
    assert len(backend.calls) == 1


def test_transport_retry_has_one_shared_owner(data, setup):
    _, backend, runtime, _, build = setup
    runtime.policy = runtime.policy.model_copy(update={"retry_delays_seconds": (0,)})
    original = backend.search_once
    attempts = []

    def once(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise TransportFailure(ErrorCode.TOOL_UNAVAILABLE)
        return original(*args, **kwargs)

    backend.search_once = once
    result = build(
        budget=ToolBudget(
            schema_version="synthetic-1", max_calls=2, max_retries=1, timeout_seconds=1
        )
    )(request(data))
    assert result.status == "ok"
    assert runtime.ledger.snapshot()["calls"] == len(attempts) == 2
    assert [r.status for r in result.retrieval_records] == ["unavailable", "ok"]
    assert result.errors == []


@pytest.mark.parametrize(
    "field", ["embedding_model", "embedding_revision", "corpus_version"]
)
def test_index_payload_identity_rejected_at_construction(setup, field):
    snapshot, _, _, _, build = setup
    setattr(snapshot.bundle.chunks[0], field, "other")
    with pytest.raises(ValueError, match="identity"):
        build()
