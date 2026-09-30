"""Synthetic tokenizer/encoder boundary; actual run remains an opt-in CLI."""

import pytest

from skala_rag.rag.local_bge_validation import LocalEncoder


class Model:
    max_seq_length = 4

    def __init__(self):
        self.calls = []

    def tokenizer(self, text, *, truncation):
        assert truncation is False
        return {"input_ids": list(range(len(text)))}

    def encode(self, texts, **kwargs):
        self.calls.append((texts, kwargs))
        return [[1.0] + [0.0] * 1023 for _ in texts]


def test_no_prefix_l2_and_all_input_lengths_checked_before_inference():
    model = Model()
    encoder = LocalEncoder(model)
    assert len(encoder.embed_texts(("ab", "abcd"))) == 2
    assert encoder.token_lengths == [2, 4]
    assert model.calls[0][1] == dict(
        batch_size=1, normalize_embeddings=True, show_progress_bar=False, prompt=""
    )
    with pytest.raises(ValueError, match="truncation"):
        encoder.embed_texts(("ab", "abcde"))
    assert len(model.calls) == 1


@pytest.mark.parametrize("row", [[0.0] * 1024, [float("nan")] * 1024, [1.0]])
def test_invalid_embedding_rejected(row):
    model = Model()
    model.encode = lambda *a, **kw: [row]
    with pytest.raises(ValueError):
        LocalEncoder(model).embed_texts(("ab",))
