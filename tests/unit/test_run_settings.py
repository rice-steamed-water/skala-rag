"""Run settings are explicit handoff choices, not global application defaults."""

import importlib
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
from tests.unit.test_discovery import candidate

from skala_rag.run_settings import (
    MergeReceipt,
    normalize_and_select,
    recommended_profile,
    replay_selection,
)
from skala_rag.settings import load_runtime_document


def test_profile_resolves_once_after_required_identity_guards(monkeypatch):
    # Given
    document = load_runtime_document()
    loads = []

    def load():
        loads.append(document)
        return document

    monkeypatch.setattr("skala_rag.run_settings.load_runtime_document", load)
    # When
    selected = profile()
    normalize_and_select([], profile=selected, execution_mode="fixture")
    # Then
    assert loads == [document]
    with pytest.raises(ValueError):
        profile(run_id=" ")
    assert loads == [document]


def test_supplied_profile_document_avoids_reload_and_preserves_fields(monkeypatch):
    # Given
    from dataclasses import asdict, fields

    document = load_runtime_document()

    def forbidden():
        pytest.fail("unused default document loaded")

    monkeypatch.setattr("skala_rag.run_settings.load_runtime_document", forbidden)
    # When
    selected = profile(runtime_document=document)
    # Then
    assert tuple(asdict(selected)) == tuple(f.name for f in fields(selected))
    assert "runtime_document" not in asdict(selected)
    assert type(selected.paid_cost_usd) is int
    assert selected.max_candidates == 5 and selected.seed == 42


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_candidates", 6),
        ("seed", 43),
        ("refill", True),
        ("evaluate_unknown", True),
        ("unknown_additional_retries", 1),
        ("paid_call_allowance", 1),
    ],
)
def test_operator_profile_cannot_change_approved_handoff(field, value):
    # Given
    document = load_runtime_document()
    requested = document.profiles.recommended_run.model_copy(update={field: value})
    supplied = document.model_copy(
        update={
            "profiles": document.profiles.model_copy(
                update={"recommended_run": requested}
            )
        }
    )
    # When / Then
    with pytest.raises(ValueError):
        profile(runtime_document=supplied)


def profile(**overrides):
    arguments = {
        "run_id": "run-199",
        "selection_source": "recommended-run-settings-20261004-v1",
        "authority_reference": "user:2026-10-04:recommended-settings",
        "policy_references": ("v3-operational-1.0.0", "core-0.1.0", "finance-0.1.0"),
        "code_version": None,
    }
    return recommended_profile(**(arguments | overrides))


def test_receipt_constructor_rejects_invalid_profile_ids():
    receipt = normalize_and_select(
        [], profile=profile(), execution_mode="fixture"
    ).receipt
    with pytest.raises(ValueError, match="ASCII"):
        replace(receipt, discovered_ids=(" co-A",))


def test_receipt_constructor_rejects_invalid_execution_context():
    receipt = normalize_and_select(
        [], profile=profile(), execution_mode="fixture"
    ).receipt
    with pytest.raises(ValueError, match="execution"):
        replace(receipt, execution_mode="implicit")


def test_recommended_profile_requires_explicit_run_and_authority():
    module = importlib.import_module("skala_rag.run_settings")
    assert Path(module.__file__).resolve() == (
        Path(__file__).resolve().parents[2] / "src/skala_rag/run_settings.py"
    )
    profile = module.recommended_profile(
        run_id="run-199",
        selection_source="recommended-run-settings-20261004-v1",
        authority_reference="user:2026-10-04:recommended-settings",
        policy_references=("v3-operational-1.0.0", "core-0.1.0", "finance-0.1.0"),
        code_version=None,
    )
    assert profile.max_candidates == 5
    assert profile.seed == 42
    assert profile.initial_company_research == 1
    assert profile.unknown_additional_retries == 0
    assert profile.evaluate_unknown is False
    assert profile.refill is False
    assert profile.paid_call_allowance == 0
    assert profile.paid_cost_usd == 0
    assert profile.past_paid_ledger == "not_supplied_unverified"
    assert profile.enforcement == "controller_handoff_only"
    assert profile.criterion_support == "actual_fact_verifier_approved_minimum_evidence"
    assert profile.coverage_target == "missing_weight*100 < 30*applicable_weight"


@pytest.mark.parametrize("count", [0, 1, 5, 6, 10])
def test_population_boundaries_are_replayable(count):
    inputs = [candidate(f"co-{i}") for i in reversed(range(count))]
    result = normalize_and_select(inputs, profile=profile(), execution_mode="fixture")
    assert len(result.candidates) == min(count, 5)
    assert len(set(result.receipt.selected_ids)) == min(count, 5)
    if count <= 5:
        assert result.receipt.selected_ids == tuple(c.candidate_id for c in inputs)
        assert not result.receipt.excluded_ids
    assert (
        replay_selection(
            result.receipt.to_json(),
            inputs,
            profile=profile(),
            execution_mode="fixture",
        ).receipt
        == result.receipt
    )


@pytest.mark.parametrize(
    "bad", ["", " ", " co-A", "co-A ", "co\tA", "기업", "co\x00A", True, 1]
)
def test_profile_rejects_malformed_run_ids_without_repair(bad):
    with pytest.raises(ValueError):
        profile(run_id=bad)


@pytest.mark.parametrize(
    "field", ["selection_source", "authority_reference", "code_version"]
)
@pytest.mark.parametrize("bad", ["", " ", True, 1, []])
def test_profile_requires_strict_explicit_text(field, bad):
    with pytest.raises(ValueError):
        profile(**{field: bad})


@pytest.mark.parametrize("bad", [[], ["v1"], (), ("v1", "v1"), ("",), ([],), (True,)])
def test_policy_references_cannot_hide_mutability_or_coercion(bad):
    with pytest.raises(ValueError):
        profile(policy_references=bad)


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "selection_source",
        "authority_reference",
        "policy_references",
        "code_version",
    ],
)
def test_factory_has_no_invisible_required_defaults(field):
    arguments = dict(
        run_id="run",
        selection_source="source",
        authority_reference="authority",
        policy_references=("policy",),
        code_version=None,
    )
    del arguments[field]
    with pytest.raises(TypeError):
        recommended_profile(**arguments)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("seed", True),
        ("seed", 42.0),
        ("max_candidates", True),
        ("max_candidates", 5.0),
        ("initial_company_research", True),
        ("paid_call_allowance", False),
        ("refill", 0),
        ("evaluate_unknown", 0),
    ],
)
def test_poisoned_profile_settings_rejected_before_normalization(field, bad):
    p = profile()
    object.__setattr__(p, field, bad)
    with pytest.raises(ValueError, match="profile differs"):
        normalize_and_select([], profile=p, execution_mode="fixture")


@pytest.mark.parametrize("bad", [" co-A", "co-A ", "co\tA", "기업", "co\x00A"])
def test_ascii_profile_rejects_original_candidate_ids_without_dto_changes(bad):
    original = candidate(bad)
    assert original.candidate_id == bad
    with pytest.raises(ValueError, match="ASCII"):
        normalize_and_select([original], profile=profile(), execution_mode="fixture")
    assert original.candidate_id == bad


def test_frozen_nested_receipt_and_fresh_dto_copies():
    original = candidate("co-A", aliases=["alias"], legal_identifiers={"brn": "one"})
    result = normalize_and_select(
        [original], profile=profile(), execution_mode="fixture"
    )
    before = result.receipt.to_json()
    original.aliases.append("mutated-input")
    original.legal_identifiers["brn"] = "poison"
    returned = result.candidates[0]
    returned.aliases.clear()
    returned.discovery_source_ids.clear()
    returned.legal_identifiers.clear()
    assert result.receipt.to_json() == before
    assert result.candidates[0].aliases == ["alias"]
    assert result.candidates[0].legal_identifiers == {"brn": "one"}
    with pytest.raises(FrozenInstanceError):
        result.receipt.profile.run_id = "changed"
    with pytest.raises(FrozenInstanceError):
        result.receipt.selected_ids = ()
    with pytest.raises(ValueError, match="tuples"):
        replace(result.receipt, selected_ids=["co-A"])
    with pytest.raises(ValueError, match="immutable"):
        replace(result.receipt, merges=[])
    with pytest.raises(ValueError, match="tuples"):
        MergeReceipt("co-A", "co-B", ["candidate_id"])


def test_mutated_dto_revalidated_not_trusted():
    original = candidate("co-A")
    original.aliases.append(123)
    with pytest.warns(UserWarning), pytest.raises(ValueError):
        normalize_and_select([original], profile=profile(), execution_mode="fixture")


def test_declared_code_version_is_not_source_attestation():
    result = normalize_and_select([], profile=profile(), execution_mode="fixture")
    assert result.receipt.code_version_status == "not_supplied_unverified"
    declared = normalize_and_select(
        [], profile=profile(code_version="declared-head"), execution_mode="fixture"
    )
    assert declared.receipt.code_version_status == "caller_declared_unverified"
    assert len(declared.receipt.selection_code_sha256) == 64
    assert len(declared.receipt.normalizer_code_sha256) == 64


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "selection_source",
        "authority_reference",
        "code_version",
        "policy_references",
    ],
)
def test_replay_rejects_different_expected_profile(field):
    inputs = [candidate("co-A")]
    receipt = normalize_and_select(
        inputs, profile=profile(), execution_mode="fixture"
    ).receipt
    override = ("different-policy",) if field == "policy_references" else "different"
    with pytest.raises(ValueError, match="binding"):
        replay_selection(
            receipt.to_json(),
            inputs,
            profile=profile(**{field: override}),
            execution_mode="fixture",
        )


@pytest.mark.parametrize(
    "field",
    [
        "profile",
        "execution_mode",
        "discovered_ids",
        "input_candidate_json",
        "input_fingerprint",
        "normalized_ids",
        "normalized_candidate_json",
        "population_ids_ascii",
        "selected_ids",
        "selected_candidate_json",
        "excluded_ids",
        "merges",
        "python_implementation",
        "python_version",
        "algorithm",
        "algorithm_version",
        "selection_code_sha256",
        "normalizer_code_sha256",
        "code_version_status",
        "receipt_version",
    ],
)
def test_replay_rejects_each_receipt_field_tamper(field):
    inputs = [candidate("co-A")]
    receipt = normalize_and_select(
        inputs, profile=profile(), execution_mode="fixture"
    ).receipt
    supplied = json.loads(receipt.to_json())
    supplied[field] = "tampered"
    with pytest.raises(ValueError, match="binding"):
        replay_selection(
            json.dumps(supplied), inputs, profile=profile(), execution_mode="fixture"
        )


@pytest.mark.parametrize(
    "field,bad",
    [
        ("seed", 42.0),
        ("initial_company_research", True),
        ("paid_call_allowance", False),
        ("refill", 0),
    ],
)
def test_replay_rejects_profile_type_tampering(field, bad):
    receipt = normalize_and_select(
        [], profile=profile(), execution_mode="fixture"
    ).receipt
    supplied = json.loads(receipt.to_json())
    supplied["profile"][field] = bad
    with pytest.raises(ValueError, match="binding"):
        replay_selection(
            json.dumps(supplied), [], profile=profile(), execution_mode="fixture"
        )


def test_replay_rejects_duplicate_keys_unknown_fields_and_wrong_context():
    receipt = normalize_and_select(
        [], profile=profile(), execution_mode="fixture"
    ).receipt
    with pytest.raises(ValueError, match="duplicate"):
        replay_selection(
            '{"profile":1,"profile":2}', [], profile=profile(), execution_mode="fixture"
        )
    supplied = json.loads(receipt.to_json()) | {"unknown": 1}
    with pytest.raises(ValueError, match="binding"):
        replay_selection(
            json.dumps(supplied), [], profile=profile(), execution_mode="fixture"
        )
    with pytest.raises(ValueError, match="binding"):
        replay_selection(
            receipt.to_json(), [], profile=profile(), execution_mode="live"
        )


def test_structurally_valid_normalization_and_selection_tampering_rejected():
    inputs = [candidate(f"co-{i}") for i in range(6)]
    inputs[0].legal_identifiers["brn"] = "one"
    inputs.append(candidate("alias-ID", legal_identifiers={"brn": "one"}))
    receipt = normalize_and_select(
        inputs, profile=profile(), execution_mode="fixture"
    ).receipt
    original = json.loads(receipt.to_json())
    mutations = []
    changed = json.loads(receipt.to_json())
    changed["merges"][0]["matched_on"] = ["homepage_host"]
    mutations.append(changed)
    changed = json.loads(receipt.to_json())
    candidate_payload = json.loads(changed["normalized_candidate_json"][0])
    candidate_payload["legal_identifiers"]["brn"] = "different"
    changed["normalized_candidate_json"][0] = json.dumps(candidate_payload)
    mutations.append(changed)
    changed = json.loads(receipt.to_json())
    changed["selected_ids"].reverse()
    changed["selected_candidate_json"].reverse()
    mutations.append(changed)
    changed = json.loads(receipt.to_json())
    changed["population_ids_ascii"].reverse()
    mutations.append(changed)
    for changed in mutations:
        assert changed != original
        with pytest.raises(ValueError, match="binding"):
            replay_selection(
                json.dumps(changed), inputs, profile=profile(), execution_mode="fixture"
            )


def test_profile_handoff_cannot_call_adapter_or_claim_runtime_enforcement():
    calls = []
    with pytest.raises(TypeError):
        normalize_and_select(
            [],
            profile=profile(),
            execution_mode="fixture",
            adapter=lambda: calls.append("paid"),
        )
    assert calls == []
    assert profile().enforcement == "controller_handoff_only"
