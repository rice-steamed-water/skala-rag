"""Local query bridge uses injected encoder; no model loads or downloads."""

import pytest
from tests.unit.test_index_v3 import settings

from skala_rag.rag.local_bge_validation import MODEL, REVISION
from skala_rag.rag.query_local import LocalQueryEncoder


class Encoder:
    def __init__(self):
        self.calls = []

    def embed_texts(self, texts):
        self.calls.append(texts)
        return ((1.0, 0.0),)


def config(**changes):
    return settings(model_id=MODEL, model_revision=REVISION, **changes)


def test_query_settings_and_single_encoding():
    injected = Encoder()
    c = config()
    encoder = LocalQueryEncoder(injected, settings=c)
    result = encoder.encode_once("robot", settings=c, timeout_seconds=1)
    assert injected.calls == [("robot",)]
    assert (result.model_id, result.model_revision) == (MODEL, REVISION)
    with pytest.raises(ValueError, match="settings"):
        encoder.encode_once(
            "other",
            settings=config(tokenizer_settings={"other": True}),
            timeout_seconds=1,
        )
    assert len(injected.calls) == 1


def test_late_local_query_rejected(monkeypatch):
    times = iter((0.0, 2.0))
    monkeypatch.setattr("skala_rag.rag.query_local.time.monotonic", lambda: next(times))
    c = config()
    with pytest.raises(TimeoutError):
        LocalQueryEncoder(Encoder(), settings=c).encode_once(
            "robot", settings=c, timeout_seconds=1
        )
