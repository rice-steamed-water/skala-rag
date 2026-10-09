import base64
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest

from skala_rag.tools.actual_wire_capture import (
    MAX_RECORDS,
    ActualWireCapture,
    WireCaptureError,
)

URL = "https://api.openai.com/v1/responses"


def _recorded() -> ActualWireCapture:
    capture = ActualWireCapture()
    a = capture.observe("cand:a")
    b = capture.observe("cand:b")
    a(b'{"q":1}', 200, b'{"ok":true}')
    b(b'{"q":"b1"}', 429, b"\xff\xfenot json")
    a(b'{"q":2}', 500, b"")
    return capture


def _saved(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "wire.json"
    return path, _recorded().save(path)


def _post(transport: httpx.MockTransport, body: bytes) -> httpx.Response:
    with httpx.Client(transport=transport) as client:
        return client.post(URL, content=body)


def _rewrite(path: Path, mutate) -> str:
    document = json.loads(path.read_bytes())
    mutate(document)
    payload = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def test_exact_bytes_roundtrip_with_malformed_and_rejected_responses(tmp_path):
    path, pin = _saved(tmp_path)
    assert pin == hashlib.sha256(path.read_bytes()).hexdigest()
    loaded = ActualWireCapture.load(path, pin)
    assert loaded.verify() is True
    a = loaded.replay_transport("cand:a")
    b = loaded.replay_transport("cand:b")
    first = _post(a, b'{"q":1}')
    assert (first.status_code, first.content) == (200, b'{"ok":true}')
    rejected = _post(b, b'{"q":"b1"}')
    assert (rejected.status_code, rejected.content) == (429, b"\xff\xfenot json")
    second = _post(a, b'{"q":2}')
    assert (second.status_code, second.content) == (500, b"")
    loaded.assert_consumed()


def test_interleaved_roles_are_occurrence_bound(tmp_path):
    path, pin = _saved(tmp_path)
    loaded = ActualWireCapture.load(path, pin)
    a = loaded.replay_transport("cand:a")
    with pytest.raises(WireCaptureError):
        _post(a, b'{"q":2}')
    assert _post(a, b'{"q":1}').status_code == 200
    with pytest.raises(WireCaptureError):
        _post(a, b'{"q":1}')
    assert _post(a, b'{"q":2}').status_code == 500


def test_role_exhaustion_and_extra_request_fail_consumption(tmp_path):
    path, pin = _saved(tmp_path)
    loaded = ActualWireCapture.load(path, pin)
    b = loaded.replay_transport("cand:b")
    assert _post(b, b'{"q":"b1"}').status_code == 429
    with pytest.raises(WireCaptureError):
        _post(b, b'{"q":"b1"}')
    with pytest.raises(WireCaptureError):
        loaded.assert_consumed()


def test_missing_records_fail_assert_consumed(tmp_path):
    path, pin = _saved(tmp_path)
    loaded = ActualWireCapture.load(path, pin)
    _post(loaded.replay_transport("cand:a"), b'{"q":1}')
    with pytest.raises(WireCaptureError):
        loaded.assert_consumed()


def test_request_mismatch_is_rejected_without_consuming(tmp_path):
    path, pin = _saved(tmp_path)
    loaded = ActualWireCapture.load(path, pin)
    b = loaded.replay_transport("cand:b")
    with pytest.raises(WireCaptureError):
        _post(b, b'{"q":"other"}')
    with pytest.raises(WireCaptureError):
        loaded.assert_consumed()


def test_verify_does_not_consume(tmp_path):
    path, pin = _saved(tmp_path)
    loaded = ActualWireCapture.load(path, pin)
    assert loaded.verify() and loaded.verify()
    assert _post(loaded.replay_transport("cand:b"), b'{"q":"b1"}').status_code == 429


def test_wrong_external_pin_and_file_tamper_rejected(tmp_path):
    path, pin = _saved(tmp_path)
    with pytest.raises(WireCaptureError):
        ActualWireCapture.load(path, "0" * 64)
    with pytest.raises(WireCaptureError):
        ActualWireCapture.load(path, pin.upper())
    data = bytearray(path.read_bytes())
    data[-5] ^= 1
    path.write_bytes(bytes(data))
    with pytest.raises(WireCaptureError):
        ActualWireCapture.load(path, pin)


def test_repinned_mutations_fail_record_closure(tmp_path):
    def status(document):
        document["records"][0]["status"] = 201

    def payload(document):
        document["records"][0]["response_b64"] = base64.b64encode(b"x").decode()

    def drop_first(document):
        del document["records"][0]

    def reorder(document):
        document["records"].reverse()

    def extra_field(document):
        document["records"][0]["headers"] = {"authorization": "x"}

    def bad_hash(document):
        document["records"][0]["sha256"] = "0" * 64

    for mutate in (status, payload, drop_first, reorder, extra_field, bad_hash):
        path = tmp_path / "m.json"
        path.unlink(missing_ok=True)
        _recorded().save(path)
        new_pin = _rewrite(path, mutate)
        with pytest.raises(WireCaptureError):
            ActualWireCapture.load(path, new_pin)


def test_noncanonical_but_valid_json_rejected(tmp_path):
    path, _ = _saved(tmp_path)
    document = json.loads(path.read_bytes())
    pretty = json.dumps(document, indent=2).encode()
    path.write_bytes(pretty)
    with pytest.raises(WireCaptureError):
        ActualWireCapture.load(path, hashlib.sha256(pretty).hexdigest())


def test_blank_and_invalid_roles_rejected():
    observe: Any = ActualWireCapture().observe
    for role in ("", "   ", " a", "a ", "x" * 257, None, 1):
        with pytest.raises(WireCaptureError):
            observe(role)


def test_unknown_role_and_unloaded_replay_rejected(tmp_path):
    path, pin = _saved(tmp_path)
    loaded = ActualWireCapture.load(path, pin)
    with pytest.raises(WireCaptureError):
        loaded.replay_transport("cand:missing")
    with pytest.raises(WireCaptureError):
        loaded.replay_transport(" ")
    recording = _recorded()
    with pytest.raises(WireCaptureError):
        recording.replay_transport("cand:a")
    with pytest.raises(WireCaptureError):
        recording.assert_consumed()


def test_observer_rejects_bad_wire_types():
    observer: Any = ActualWireCapture().observe("r")
    for args in (
        ("x", 200, b""),
        (b"", True, b""),
        (b"", 99, b""),
        (b"", 600, b""),
        (b"", 200, bytearray()),
    ):
        with pytest.raises(WireCaptureError):
            observer(*args)


def test_bounded_record_count():
    capture = ActualWireCapture(max_records=2)
    observer = capture.observe("r")
    observer(b"1", 200, b"")
    observer(b"2", 200, b"")
    with pytest.raises(WireCaptureError):
        observer(b"3", 200, b"")
    with pytest.raises(WireCaptureError):
        ActualWireCapture(max_records=MAX_RECORDS + 1)


def test_save_once_preserves_existing_and_closes_capture(tmp_path):
    existing = tmp_path / "wire.json"
    existing.write_bytes(b"keep")
    capture = _recorded()
    with pytest.raises(WireCaptureError):
        capture.save(existing)
    assert existing.read_bytes() == b"keep"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["wire.json"]
    target = tmp_path / "new.json"
    pin = capture.save(target)
    assert pin == hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(WireCaptureError):
        capture.save(tmp_path / "again.json")
    with pytest.raises(WireCaptureError):
        capture.observe("cand:a")(b"", 200, b"")
    assert capture.verify() is True


def test_empty_capture_cannot_be_saved(tmp_path):
    with pytest.raises(WireCaptureError):
        ActualWireCapture().save(tmp_path / "e.json")
    assert list(tmp_path.iterdir()) == []


def test_save_is_stable_and_loaded_capture_is_closed(tmp_path):
    first = _recorded().save(tmp_path / "one.json")
    second = _recorded().save(tmp_path / "two.json")
    assert first == second
    loaded = ActualWireCapture.load(tmp_path / "one.json", first)
    with pytest.raises(WireCaptureError):
        loaded.save(tmp_path / "three.json")
    with pytest.raises(WireCaptureError):
        loaded.observe("cand:a")(b"", 200, b"")


def test_verify_detects_in_memory_tamper(tmp_path):
    path, pin = _saved(tmp_path)
    loaded = ActualWireCapture.load(path, pin)
    record = loaded._records[0]
    loaded._records[0] = record.model_copy(update={"status": 201})
    assert loaded.verify() is False
    with pytest.raises(WireCaptureError):
        loaded.replay_transport("cand:a")


def test_concurrent_observers_keep_per_role_order(tmp_path):
    capture = ActualWireCapture()
    start = threading.Barrier(4)

    def work(role: str) -> None:
        observer = capture.observe(role)
        start.wait(timeout=10)
        for index in range(5):
            observer(f"{role}:{index}".encode(), 200, b"r")

    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in [pool.submit(work, f"role{n}") for n in range(4)]:
            future.result(timeout=30)
    path = tmp_path / "c.json"
    loaded = ActualWireCapture.load(path, capture.save(path))
    for n in range(4):
        transport = loaded.replay_transport(f"role{n}")
        for index in range(5):
            assert _post(transport, f"role{n}:{index}".encode()).content == b"r"
    loaded.assert_consumed()


def test_capture_through_real_client_preserves_request_bytes(tmp_path):
    capture = ActualWireCapture()
    observer = capture.observe("node:1")
    sent: list[bytes] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(418, content=b"teapot")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = client.post(URL, json={"a": [1, 2]})
        sent.append(response.request.content)
        observer(response.request.content, response.status_code, response.content)
    path = tmp_path / "r.json"
    loaded = ActualWireCapture.load(path, capture.save(path))
    replayed = _post(loaded.replay_transport("node:1"), sent[0])
    assert (replayed.status_code, replayed.content) == (418, b"teapot")
    loaded.assert_consumed()
