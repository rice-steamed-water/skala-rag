"""Real domain composition with synthetic wires; external sockets are forbidden."""

import hashlib
import importlib.util
import json
import socket
from pathlib import Path
from types import ModuleType

import pytest

from skala_rag.contracts.v3 import BRANCH_DIMENSIONS

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def example(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "v3_connection_check", ROOT / "examples/v3_connection_check.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def denied(*_args, **_kwargs):
        pytest.fail("external socket requested by controlled composition")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    return module


def test_original_graph_calls_all_real_domains_and_report_pipeline(
    example: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Given: spies keep the real evaluator implementations, never replace results.
    called = []
    for branch in BRANCH_DIMENSIONS:
        name = f"evaluate_{branch}_approved"
        original = getattr(example, name)

        def observed(*args, original=original, branch=branch, **kwargs):
            called.append(branch)
            return original(*args, **kwargs)

        monkeypatch.setattr(example, name, observed)
    # When: the public direct composition drives original graph and report nodes.
    output = example.run_connection_check(tmp_path / "controlled")
    # Then: five real functions promote six all-Missing dimensions atomically.
    receipt = json.loads((output / "receipt.json").read_text())
    state = json.loads((output / "state.json").read_text())
    context = json.loads((output / "context.json").read_text())
    assert sorted(called) == sorted(BRANCH_DIMENSIONS)
    assert receipt["branches"] == sorted(BRANCH_DIMENSIONS)
    assert set(receipt["promoted_dimensions"]) == set().union(
        *BRANCH_DIMENSIONS.values()
    )
    assert len(set(receipt["evaluator_snapshot_hashes"].values())) == 1
    assert context["evaluations"] == state["evaluations_v3"]
    assert (
        context["snapshots"]["controlled-candidate"]
        == state["snapshots"][receipt["snapshot_id"]]
    )
    criteria = [c for e in state["evaluations_v3"].values() for c in e["criteria"]]
    assert len(criteria) == 23
    assert all(c["status"] == "missing" and c["rating"] is None for c in criteria)
    assert receipt["mock_transport_requests"] == 8
    assert receipt["ledger"]["calls"] == 9  # Real indexed retrieval plus eight wires.
    assert receipt["ledger"]["input_tokens_accounted"] == 80
    assert receipt["ledger"]["output_tokens_accounted"] == 80
    assert receipt["actual_provider_calls"] == receipt["external_requests"] == 0
    assert receipt["synthetic"] and context["provenance"]["synthetic"]
    assert context["execution_scope"] == "controlled_response"
    assert not context["publication_allowed"]
    assert not receipt["final_allowed"] and not receipt["publication_allowed"]
    assert receipt["report_status"] == "completed"
    assert receipt["pdf"]["verified"] and not receipt["pdf"]["final_allowed"]
    assert 1 <= receipt["pdf"]["page_count"] <= 5
    assert receipt["pdf"]["summary_fraction"] <= 0.5
    assert (
        hashlib.sha256(Path(receipt["pdf"]["path"]).read_bytes()).hexdigest()
        == receipt["pdf"]["sha256"]
    )
    assert state["retrieval_history"][-1]["tool_name"] == "retrieve"


def test_replay_consumes_saved_wires_with_zero_network(
    example: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Given: original response bytes and immutable artifact commitments.
    original = example.run_connection_check(tmp_path / "controlled")
    capture = original / "capture.json"
    # A different proposed response cannot affect replay: APPROVED_MODEL is used
    # only when constructing a new response, not when consuming the saved wire.
    monkeypatch.setattr(example, "APPROVED_MODEL", "must-not-generate-new-responses")
    # When: replay executes the same runtime, domain functions, graph and report.
    replayed = example.run_connection_check(tmp_path / "replay", replay_capture=capture)
    # Then: all stable semantic artifacts and cumulative accounting are identical.
    first = json.loads((original / "receipt.json").read_text())
    second = json.loads((replayed / "receipt.json").read_text())
    assert second["replay"] and second["stable_hashes"] == first["stable_hashes"]
    assert second["ledger"] == first["ledger"]
    assert second["evaluator_snapshot_hashes"] == first["evaluator_snapshot_hashes"]
    assert second["actual_provider_calls"] == second["external_requests"] == 0
    assert (original / "capture.json").read_bytes() == (
        replayed / "capture.json"
    ).read_bytes()
    assert second["pdf"]["verified"]


@pytest.mark.parametrize("artifact", ["capture.json", "source.capture", "source.txt"])
def test_tampered_capture_or_original_is_denied_before_callbacks(
    example: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str
):
    # Given: saved integrity proof, followed by changed original or response bytes.
    original = example.run_connection_check(tmp_path / "controlled")
    path = original / artifact
    path.write_bytes(path.read_bytes() + b"tampered")
    callbacks = []

    def denied(*_args, **_kwargs):
        callbacks.append(True)
        pytest.fail("evaluator executed before tamper rejection")

    for branch in BRANCH_DIMENSIONS:
        monkeypatch.setattr(example, f"evaluate_{branch}_approved", denied)
    destination = tmp_path / "replay"
    # When / Then: admission fails before output creation and domain callbacks.
    with pytest.raises(example.ConnectionCheckError):
        example.run_connection_check(
            destination, replay_capture=original / "capture.json"
        )
    assert callbacks == [] and not destination.exists()
