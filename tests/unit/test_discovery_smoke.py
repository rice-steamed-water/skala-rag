"""Offline HTTP observations, never provider execution evidence."""

import json
from datetime import date

import httpx
import pytest
from tests.unit.test_discovery_runtime import build

from skala_rag.contracts.inputs import RunInput
from skala_rag.tools.discovery_live import TavilyDiscovery


def configured(handler, extractor=lambda sources, request: []):
    bridge, budget, ledger = build(handler)
    adapter = TavilyDiscovery(
        schema_version="1",
        run_id="run",
        retrieval_id="discovery-1",
        clock=bridge.runtime.clock,
        api_key_configured=True,
        readiness=lambda: True,
        runtime=bridge,
        extractor=extractor,
        candidate_id=lambda i: f"candidate-{i}",
        public_theme="public synthetic",
        max_results=2,
        max_candidates=2,
    )
    request = RunInput(
        schema_version="1",
        investment_theme="public synthetic",
        countries=["US"],
        languages=["en"],
        as_of=date(2026, 9, 30),
        policy_version="synthetic-policy",
        corpus_version="synthetic-corpus",
        execution_mode="fixture",
    )
    return adapter, request, budget, ledger


def test_empty_receipt_keeps_exact_runtime_records(tmp_path):
    from skala_rag.tools.discovery_smoke import run_discovery_smoke

    adapter, request, budget, ledger = configured(
        lambda r: httpx.Response(200, json={"results": []})
    )
    path = run_discovery_smoke(
        adapter=adapter, request=request, budget=budget, output_directory=tmp_path
    )
    saved = json.loads(path.read_text())
    assert saved["request"] == request.model_dump(mode="json")
    assert saved["budget"] == budget.model_dump(mode="json")
    assert saved["result"]["status"] == "empty"
    assert len(saved["result"]["retrieval_records"]) == 2
    assert saved["observed_sources"] == {}
    assert saved["result"]["retrieval_records"][0]["cost"] is None
    assert saved["provider_request_id"] is None
    assert saved["http_requests"][0]["method"] == "POST"
    assert saved["http_requests"][0]["url"] == "https://api.tavily.com/search"
    assert saved["http_requests"][0]["json"]["max_results"] == adapter.max_results
    assert "headers" not in saved["http_requests"][0]
    assert ledger.snapshot()["calls"] == 1
    assert adapter.runtime.api_key not in path.read_text()


def test_write_failure_preserves_detached_receipt(tmp_path, monkeypatch):
    import skala_rag.tools.discovery_smoke as smoke

    adapter, request, budget, ledger = configured(
        lambda r: httpx.Response(200, json={"results": []})
    )

    def fail_link(*args):
        raise OSError("private filesystem error")

    monkeypatch.setattr(smoke.os, "link", fail_link)
    with pytest.raises(smoke.SmokePersistenceError) as caught:
        smoke.run_discovery_smoke(
            adapter=adapter, request=request, budget=budget, output_directory=tmp_path
        )
    assert caught.value.receipt.result.status == "empty"
    assert len(caught.value.receipt.result.retrieval_records) == 2
    assert "private" not in str(caught.value)
    assert not (tmp_path / "receipt.json").exists()


def test_saved_result_equals_detached_observation(tmp_path, monkeypatch):
    import skala_rag.tools.discovery_smoke as smoke

    original = smoke.search_with_receipt
    observed = []

    def capture(*args):
        receipt = original(*args)
        observed.append(receipt)
        return receipt

    monkeypatch.setattr(smoke, "search_with_receipt", capture)
    adapter, request, budget, ledger = configured(lambda r: httpx.Response(401))
    path = smoke.run_discovery_smoke(
        adapter=adapter, request=request, budget=budget, output_directory=tmp_path
    )
    assert json.loads(path.read_text())["result"] == observed[0].result.model_dump(
        mode="json"
    )


def test_snippets_survive_post_extractor_failure(tmp_path):
    from skala_rag.tools.discovery_smoke import run_discovery_smoke

    def extractor(sources, request):
        raise RuntimeError("private exception must not escape")

    adapter, request, budget, ledger = configured(
        lambda r: httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Synthetic company",
                        "url": "https://example.org/company",
                        "content": "Exact synthetic snippet. 한글.",
                    }
                ]
            },
        ),
        extractor,
    )
    path = run_discovery_smoke(
        adapter=adapter, request=request, budget=budget, output_directory=tmp_path
    )
    saved = json.loads(path.read_text())
    assert saved["result"]["status"] == "failed"
    assert saved["result"]["data"] is None
    assert len(saved["result"]["retrieval_records"]) == 2
    assert saved["observed_sources"] == {
        sid: source.model_dump(mode="json")
        for sid, source in adapter.observed_sources.items()
    }
    source = next(iter(saved["observed_sources"].values()))
    assert (
        source["bibliographic_metadata"]["snippet"] == "Exact synthetic snippet. 한글."
    )
    assert "private exception" not in path.read_text()
    assert ledger.snapshot()["calls"] == 1


def test_candidate_receipt_is_detached(tmp_path):
    from skala_rag.tools.discovery_live import CompanyObservation
    from skala_rag.tools.discovery_smoke import run_discovery_smoke

    def extractor(sources, request):
        return [CompanyObservation("Synthetic Company", "US", list(sources))]

    adapter, request, budget, ledger = configured(
        lambda r: httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Synthetic company",
                        "url": "https://example.org/company",
                        "content": "Synthetic fixture company description.",
                    }
                ]
            },
        ),
        extractor,
    )
    path = run_discovery_smoke(
        adapter=adapter, request=request, budget=budget, output_directory=tmp_path
    )
    before = path.read_bytes()
    saved = json.loads(before)
    assert saved["result"]["status"] == "ok"
    candidate = saved["result"]["data"]["candidates"][0]
    assert candidate["canonical_name"] == "Synthetic Company"
    assert set(candidate["discovery_source_ids"]) <= set(saved["observed_sources"])
    adapter(request, budget)
    assert path.read_bytes() == before


def test_secret_in_snippet_is_rejected_without_receipt_loss(tmp_path):
    from skala_rag.tools.discovery_smoke import UnsafeSmokeReceipt, run_discovery_smoke

    adapter, request, budget, ledger = configured(
        lambda r: httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Synthetic",
                        "url": "https://example.org/company",
                        "content": "synthetic-not-a-real-key",
                    }
                ]
            },
        ),
    )
    with pytest.raises(UnsafeSmokeReceipt) as caught:
        run_discovery_smoke(
            adapter=adapter, request=request, budget=budget, output_directory=tmp_path
        )
    assert len(caught.value.receipt.observed_sources) == 1
    assert "synthetic-not-a-real-key" not in str(caught.value)
    assert not (tmp_path / "receipt.json").exists()
    assert not list((tmp_path / ".discovery-smoke").iterdir())


@pytest.mark.parametrize("kind", ["auth", "timeout", "invalid"])
def test_failed_transport_receipt_is_exact(tmp_path, kind):
    from skala_rag.tools.discovery_smoke import run_discovery_smoke

    def handler(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("private transport error")
        return (
            httpx.Response(401)
            if kind == "auth"
            else httpx.Response(200, text="private invalid body")
        )

    adapter, request, budget, ledger = configured(handler)
    path = run_discovery_smoke(
        adapter=adapter, request=request, budget=budget, output_directory=tmp_path
    )
    saved = json.loads(path.read_text())
    assert saved["result"]["data"] is None
    assert len(saved["result"]["retrieval_records"]) == 1
    assert len(saved["result"]["errors"]) == 1
    assert saved["observed_sources"] == {}
    assert "private" not in path.read_text()
    assert ledger.snapshot()["calls"] == 1


@pytest.mark.parametrize("change", ["fake", "wrong_run", "wrong_mode"])
def test_inconsistent_or_fake_runtime_never_runs(tmp_path, change):
    from skala_rag.tools.discovery_smoke import run_discovery_smoke

    adapter, request, budget, ledger = configured(
        lambda r: pytest.fail("HTTP forbidden")
    )
    if change == "fake":
        adapter.runtime = lambda payload, budget: pytest.fail("callback forbidden")
        request = request.model_copy(update={"execution_mode": "live"})
        adapter.allow_live = True
    elif change == "wrong_run":
        adapter.run_id = "other-run"
    elif change == "wrong_mode":
        request = request.model_copy(update={"execution_mode": "live"})
        adapter.allow_live = True
    else:
        adapter.runtime.runtime.policy = adapter.runtime.runtime.policy.model_copy(
            update={"execution_mode": "live"}
        )
        request = request.model_copy(update={"execution_mode": "live"})
    with pytest.raises(ValueError):
        run_discovery_smoke(
            adapter=adapter, request=request, budget=budget, output_directory=tmp_path
        )
    assert ledger.snapshot()["calls"] == 0
    assert list(tmp_path.iterdir()) == []


def test_used_directory_refuses_cross_run_without_sending(tmp_path):
    from skala_rag.tools.discovery_smoke import run_discovery_smoke

    adapter, request, budget, ledger = configured(
        lambda r: httpx.Response(200, json={"results": []})
    )
    path = run_discovery_smoke(
        adapter=adapter, request=request, budget=budget, output_directory=tmp_path
    )
    before = path.read_bytes()
    adapter.run_id = "other-run"
    adapter.runtime.context = adapter.runtime.context.model_copy(
        update={"run_id": "other-run"}
    )
    with pytest.raises(FileExistsError):
        run_discovery_smoke(
            adapter=adapter, request=request, budget=budget, output_directory=tmp_path
        )
    assert ledger.snapshot()["calls"] == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "snippet", ["api_key=private-value", "Bearer private-value", "tvly-private-value"]
)
def test_unsafe_patterns_never_reach_disk(tmp_path, snippet):
    from skala_rag.tools.discovery_smoke import UnsafeSmokeReceipt, run_discovery_smoke

    adapter, request, budget, ledger = configured(
        lambda r: httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Synthetic",
                        "url": "https://example.org/company",
                        "content": snippet,
                    }
                ]
            },
        )
    )
    with pytest.raises(UnsafeSmokeReceipt):
        run_discovery_smoke(
            adapter=adapter, request=request, budget=budget, output_directory=tmp_path
        )
    assert not (tmp_path / "receipt.json").exists()


def test_recovered_attempt_error_is_preserved(tmp_path):
    from skala_rag.tools.discovery_smoke import run_discovery_smoke

    calls = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(500)
            if len(calls) == 1
            else httpx.Response(200, json={"results": []})
        )

    adapter, request, budget, ledger = configured(handler)
    bridge, budget, ledger = build(handler, retries=1)
    adapter.runtime = bridge
    path = run_discovery_smoke(
        adapter=adapter, request=request, budget=budget, output_directory=tmp_path
    )
    saved = json.loads(path.read_text())
    failed_record = saved["result"]["retrieval_records"][0]
    assert failed_record["status"] == "unavailable"
    assert saved["result"]["status"] == "empty"
    assert saved["attempt_errors"][0] == bridge.runtime.error_history[
        failed_record["error_id"]
    ].model_dump(mode="json")


@pytest.mark.parametrize("kind", ["missing", "symlink", "nonempty"])
def test_output_path_guard_never_sends(tmp_path, kind):
    from skala_rag.tools.discovery_smoke import run_discovery_smoke

    adapter, request, budget, ledger = configured(
        lambda r: pytest.fail("HTTP forbidden")
    )
    directory = tmp_path / "approved"
    if kind == "symlink":
        directory.symlink_to(tmp_path, target_is_directory=True)
    elif kind == "nonempty":
        directory.mkdir()
        (directory / "existing").write_text("keep")
    with pytest.raises((ValueError, FileExistsError)):
        run_discovery_smoke(
            adapter=adapter, request=request, budget=budget, output_directory=directory
        )
    assert ledger.snapshot()["calls"] == 0


@pytest.mark.parametrize("allow_live", [False, True])
def test_current_scope_receipt_persists_without_callbacks(tmp_path, allow_live):
    from skala_rag.tools.discovery_smoke import run_discovery_smoke

    adapter, request, budget, ledger = configured(
        lambda r: pytest.fail("HTTP forbidden")
    )
    adapter.allow_live = allow_live

    def forbidden(*args):
        pytest.fail("readiness or extractor forbidden")

    adapter.readiness = adapter.extractor = forbidden
    before_budget = budget.model_dump()
    before_ledger = ledger.snapshot()
    adapter.runtime.runtime.policy = adapter.runtime.runtime.policy.model_copy(
        update={"execution_mode": "live"}
    )
    request = request.model_copy(update={"execution_mode": "live"})
    path = run_discovery_smoke(
        adapter=adapter, request=request, budget=budget, output_directory=tmp_path
    )
    saved = json.loads(path.read_text())
    assert budget.model_dump() == before_budget
    assert ledger.snapshot() == before_ledger
    assert saved["result"]["data"] is None
    assert saved["observed_sources"] == {}
    assert (
        saved["result"]["retrieval_records"][0]["arguments_without_secrets"][
            "physical_attempts"
        ]
        == 0
    )
    assert (
        saved["result"]["retrieval_records"][0]["arguments_without_secrets"][
            "scope_reason"
        ]
        == "excluded_current_run"
    )
    assert saved["execution_mode"] == "live"
    assert saved["result"]["status"] == "unavailable"
    assert saved["result"]["errors"][0]["error_code"] == "TOOL_NOT_CONFIGURED"
    assert saved["http_requests"] == []
    assert ledger.snapshot()["calls"] == 0


def test_client_hooks_restored_after_run(tmp_path):
    from skala_rag.tools.discovery_smoke import run_discovery_smoke

    adapter, request, budget, ledger = configured(lambda r: httpx.Response(401))
    observed = []
    adapter.runtime.client.event_hooks["request"].append(observed.append)
    original = list(adapter.runtime.client.event_hooks["request"])
    run_discovery_smoke(
        adapter=adapter, request=request, budget=budget, output_directory=tmp_path
    )
    assert adapter.runtime.client.event_hooks["request"] == original
    assert len(observed) == 1
