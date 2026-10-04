"""Offline integration through the existing normalizer, not a selector fake."""

import importlib
import random

import pytest
from tests.unit.test_run_settings import profile

from skala_rag.agents import discovery
from skala_rag.contracts import Candidate


def candidate(candidate_id, **overrides):
    return Candidate(
        schema_version="synthetic-common-1",
        candidate_id=candidate_id,
        canonical_name=f"Synthetic {candidate_id}",
        aliases=[],
        country="KR",
        homepage_url=None,
        legal_identifiers={},
        discovery_source_ids=[f"src-{candidate_id}"],
        **overrides,
    )


def test_actual_normalization_selection_and_receipt():
    module = importlib.import_module("skala_rag.run_settings")
    inputs = [candidate(i) for i in ("Z", "A", "c", "B", "e", "D")]
    inputs.insert(1, candidate("duplicate"))
    inputs[0].legal_identifiers["BRN"] = "one"
    inputs[1].legal_identifiers["brn"] = "one"
    before = random.getstate()
    result = module.normalize_and_select(
        inputs, profile=profile(), execution_mode="fixture"
    )
    expected = tuple(random.Random(42).sample(["A", "B", "D", "Z", "c", "e"], 5))
    assert result.receipt.selected_ids == expected
    assert tuple(c.candidate_id for c in result.candidates) == expected
    assert result.receipt.discovered_ids == ("Z", "duplicate", "A", "c", "B", "e", "D")
    assert result.receipt.normalized_ids == ("Z", "A", "c", "B", "e", "D")
    assert result.receipt.population_ids_ascii == ("A", "B", "D", "Z", "c", "e")
    assert result.receipt.excluded_ids == tuple(
        i for i in result.receipt.normalized_ids if i not in expected
    )
    assert result.receipt.merges[0].kept_candidate_id == "Z"
    assert result.receipt.merges[0].merged_candidate_id == "duplicate"
    assert result.receipt.merges[0].matched_on == ("legal_identifier:brn",)
    assert random.getstate() == before
    replayed = module.replay_selection(
        result.receipt.to_json(), inputs, profile=profile(), execution_mode="fixture"
    )
    assert replayed.receipt == result.receipt


@pytest.mark.parametrize("count", [0, 1, 5])
def test_actual_normalizer_bypasses_callback_and_rng_within_limit(monkeypatch, count):
    module = importlib.import_module("skala_rag.run_settings")
    actual = discovery.normalize_candidates
    calls = []

    def forbidden_rng(*args, **kwargs):
        raise AssertionError("RNG must not be constructed within the deduped limit")

    def observe(candidates, *, max_candidates, limit_policy):
        assert isinstance(limit_policy, discovery.CandidateLimitPolicy)
        calls.append(tuple(c.candidate_id for c in candidates))
        return actual(
            candidates, max_candidates=max_candidates, limit_policy=limit_policy
        )

    monkeypatch.setattr(module.random, "Random", forbidden_rng)
    monkeypatch.setattr(discovery, "normalize_candidates", observe)
    inputs = [candidate(f"co-{i}") for i in reversed(range(count))]
    result = module.normalize_and_select(
        inputs, profile=profile(), execution_mode="fixture"
    )
    assert calls == [tuple(c.candidate_id for c in inputs)]
    assert result.receipt.selected_ids == calls[0]


def test_six_discovered_five_after_dedup_never_draws_rng(monkeypatch):
    module = importlib.import_module("skala_rag.run_settings")

    def forbidden_rng(*args, **kwargs):
        raise AssertionError("dedup precedes cap and RNG")

    monkeypatch.setattr(module.random, "Random", forbidden_rng)
    inputs = [candidate(f"co-{i}") for i in reversed(range(5))]
    duplicate = candidate("other-original-ID")
    inputs[0].homepage_url = "https://www.same.example/a"
    duplicate.homepage_url = "https://same.example/b"
    inputs.append(duplicate)
    result = module.normalize_and_select(
        inputs, profile=profile(), execution_mode="fixture"
    )
    assert result.receipt.selected_ids == ("co-4", "co-3", "co-2", "co-1", "co-0")
    assert result.candidates[0].discovery_source_ids == [
        "src-co-4",
        "src-other-original-ID",
    ]
    assert result.receipt.merges[0].matched_on == ("homepage_host",)


@pytest.mark.parametrize(
    "change", ["legal_identifier", "homepage", "provenance", "name", "order"]
)
def test_replay_binds_original_semantic_and_provenance_inputs(change):
    module = importlib.import_module("skala_rag.run_settings")
    inputs = [candidate("co-A"), candidate("co-B")]
    result = module.normalize_and_select(
        inputs, profile=profile(), execution_mode="fixture"
    )
    if change == "legal_identifier":
        inputs[0].legal_identifiers["brn"] = "changed"
    elif change == "homepage":
        inputs[0].homepage_url = "https://changed.example"
    elif change == "provenance":
        inputs[0].discovery_source_ids.append("different-source")
    elif change == "name":
        inputs[0].canonical_name = "Different original name"
    else:
        inputs.reverse()
    with pytest.raises(ValueError, match="binding"):
        module.replay_selection(
            result.receipt.to_json(),
            inputs,
            profile=profile(),
            execution_mode="fixture",
        )


def test_replay_binds_actual_python_implementation_and_full_version(monkeypatch):
    module = importlib.import_module("skala_rag.run_settings")
    result = module.normalize_and_select(
        [], profile=profile(), execution_mode="fixture"
    )
    assert result.receipt.python_version == module.sys.version
    assert (
        result.receipt.python_implementation == module.platform.python_implementation()
    )
    monkeypatch.setattr(
        module.platform, "python_implementation", lambda: "DifferentPython"
    )
    with pytest.raises(ValueError, match="binding"):
        module.replay_selection(
            result.receipt.to_json(), [], profile=profile(), execution_mode="fixture"
        )
    monkeypatch.undo()
    monkeypatch.setattr(
        module.sys,
        "version",
        module.sys.version.replace(module.sys.version.split()[0], "9.9.9", 1),
    )
    with pytest.raises(ValueError, match="binding"):
        module.replay_selection(
            result.receipt.to_json(), [], profile=profile(), execution_mode="fixture"
        )


def test_same_id_different_entity_remains_normalizer_error():
    module = importlib.import_module("skala_rag.run_settings")
    inputs = [candidate("co-A"), candidate("co-A")]
    inputs[1].country = "US"
    with pytest.raises(ValueError, match="reused"):
        module.normalize_and_select(inputs, profile=profile(), execution_mode="fixture")


@pytest.mark.parametrize(
    "bad_selection", [["unknown"], ["co-0", "co-0"], [], [f"co-{i}" for i in range(6)]]
)
def test_actual_normalizer_still_rejects_invalid_policy_draws(
    monkeypatch, bad_selection
):
    module = importlib.import_module("skala_rag.run_settings")

    class BadRNG:
        def __init__(self, seed):
            assert seed == 42

        def sample(self, population, maximum):
            assert population == [f"co-{i}" for i in range(6)]
            assert maximum == 5
            return bad_selection

    monkeypatch.setattr(module.random, "Random", BadRNG)
    with pytest.raises(ValueError):
        module.normalize_and_select(
            [candidate(f"co-{i}") for i in range(6)],
            profile=profile(),
            execution_mode="fixture",
        )


def test_fixture_locator_requires_explicit_fixture_context():
    module = importlib.import_module("skala_rag.run_settings")
    item = Candidate.model_validate(
        candidate("co-A").model_dump() | {"homepage_url": "fixture://company"},
        context={"execution_mode": "fixture"},
    )
    result = module.normalize_and_select(
        [item], profile=profile(), execution_mode="fixture"
    )
    assert result.candidates[0].homepage_url == "fixture://company"
    with pytest.raises(ValueError, match="fixture"):
        module.normalize_and_select([item], profile=profile(), execution_mode="live")
