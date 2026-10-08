"""Offline binding only; these tests do not authenticate GitHub or semantics."""

from pathlib import Path

import pytest


def test_pinned_factory_binds_existing_loader_without_admitting_live():
    from skala_rag.scoring.approval_registry import pinned_approval_registry
    from skala_rag.scoring.approved_policy import load_approved_policy

    root = Path(__file__).resolve().parents[2]
    registry = pinned_approval_registry(root)
    policy = load_approved_policy(
        root / "configs/scoring.v3.json",
        approvals=registry.policy_approvals(),
        approval_verifier=registry.verify_policy,
    )
    assert policy.policy_version == "v3-operational-1.0.0"
    diagnosis = registry.diagnose()
    assert all(row.content_binding == "matched" for row in diagnosis.artifacts)
    assert diagnosis.artifacts[1].artifact_status == "approved"
    assert diagnosis.artifacts[1].owner_dependency is None
    assert diagnosis.semantic_review == "unreviewed"
    assert diagnosis.runtime_admission == "not_admitted"
    assert diagnosis.campaign_approval == "unapproved"
    assert diagnosis.open_decisions == ("D05", "D06", "D08")


def test_exposed_pins_cannot_mutate_authority_even_via_frozen_bypass():
    from skala_rag.scoring.approval_registry import pinned_approval_registry

    registry = pinned_approval_registry(Path(__file__).resolve().parents[2])
    exposed = registry.pins[0]
    original = exposed.content_sha256
    object.__setattr__(exposed, "content_sha256", "0" * 64)
    assert registry.pins[0].content_sha256 == original
    assert not registry.verify_pin(exposed)


def test_duplicate_yaml_keys_are_not_silently_ignored(tmp_path):
    from skala_rag.scoring.approval_registry import pinned_approval_registry

    root = Path(__file__).resolve().parents[2]
    target = tmp_path / "configs/rubrics/core.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(
        "rubric_version: forged\n" + (root / "configs/rubrics/core.yaml").read_text()
    )
    registry = pinned_approval_registry(tmp_path)
    assert not registry.verify_core(registry.core_approval())
    assert registry.diagnose().artifacts[1].content_binding == "rejected"


def test_unexpected_reader_failure_is_closed(monkeypatch):
    from skala_rag.scoring.approval_registry import (
        PinnedApprovalRegistry,
        pinned_approval_registry,
    )
    from skala_rag.scoring.v3_policy import load_v3_policy

    root = Path(__file__).resolve().parents[2]
    registry = pinned_approval_registry(root)
    policy = load_v3_policy(root / "configs/scoring.v3.json", execution_mode="fixture")

    def broken(*args):
        raise RuntimeError("offline test reader failure")

    monkeypatch.setattr(PinnedApprovalRegistry, "_artifact", broken)
    assert not registry.verify_core(registry.core_approval())
    assert not registry.verify_policy(registry.policy_approvals().core, policy)
    assert all(
        row.content_binding == "rejected" for row in registry.diagnose().artifacts
    )


def test_diagnosis_separates_reference_from_content():
    from skala_rag.scoring.approval_registry import pinned_approval_registry

    registry = pinned_approval_registry(Path(__file__).resolve().parents[2])
    good = registry.policy_approvals()
    forged_core = good.core.model_copy(update={"reference": "unresolved-comment"})
    forged = good.model_copy(update={"core": forged_core})
    core = registry.diagnose(approvals=forged).artifacts[1]
    assert core.reference_binding == "rejected"
    assert core.content_binding == "matched"
    assert core.approval_reference == registry.pins[1].reference


def test_duck_typed_approval_and_policy_do_not_define_authority():
    from skala_rag.scoring.approval_registry import pinned_approval_registry
    from skala_rag.scoring.v3_policy import load_v3_policy

    root = Path(__file__).resolve().parents[2]
    registry = pinned_approval_registry(root)
    evidence = registry.policy_approvals().core
    policy = load_v3_policy(root / "configs/scoring.v3.json", execution_mode="fixture")

    class Spoof:
        def __init__(self, value):
            self.value = value

        def model_dump(self):
            return self.value.model_dump()

    assert not registry.verify_policy(Spoof(evidence), policy)
    assert not registry.verify_policy(evidence, Spoof(policy))


def test_core_claim_requires_exact_string_fields():
    from skala_rag.agents.moat_verification import CoreArtifactApproval
    from skala_rag.scoring.approval_registry import pinned_approval_registry

    registry = pinned_approval_registry(Path(__file__).resolve().parents[2])
    good = registry.core_approval()

    class EqualAnything:
        def __eq__(self, other):
            return True

    bad = CoreArtifactApproval(
        EqualAnything(), good.rubric_version, good.content_sha256
    )
    assert not registry.verify_core(bad)


def test_rubric_callback_also_binds_supplied_operational_content(tmp_path):
    import json
    import shutil

    from skala_rag.scoring.approval_registry import pinned_approval_registry
    from skala_rag.scoring.v3_policy import load_v3_policy

    root = Path(__file__).resolve().parents[2]
    shutil.copytree(root / "configs", tmp_path / "configs")
    path = tmp_path / "configs/scoring.v3.json"
    payload = json.loads(path.read_text())
    payload["criteria"][0]["display_name"] = "Changed at same version"
    path.write_text(json.dumps(payload))
    registry = pinned_approval_registry(tmp_path)
    changed = load_v3_policy(path, execution_mode="fixture")
    assert not registry.verify_policy(registry.policy_approvals().core, changed)
    assert not registry.verify_policy(registry.policy_approvals().finance, changed)


@pytest.fixture
def local_registry(tmp_path):
    import shutil

    from skala_rag.scoring.approval_registry import pinned_approval_registry

    root = Path(__file__).resolve().parents[2]
    for name in (
        "configs/scoring.v3.json",
        "configs/rubrics/core.yaml",
        "configs/rubrics/finance.yaml",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    return pinned_approval_registry(tmp_path)


@pytest.mark.parametrize("scope", ("operational", "core", "finance"))
@pytest.mark.parametrize(
    "field,value",
    (
        ("scope", "unknown"),
        ("version", "same-name-new-version"),
        ("reference", "unresolved"),
    ),
)
def test_unknown_or_altered_approval_identity_rejected(
    local_registry, scope, field, value
):
    from skala_rag.scoring.v3_policy import load_v3_policy

    evidence = getattr(local_registry.policy_approvals(), scope).model_copy(
        update={field: value}
    )
    policy = load_v3_policy(
        local_registry.root / "configs/scoring.v3.json", execution_mode="fixture"
    )
    assert not local_registry.verify_policy(evidence, policy)


@pytest.mark.parametrize("index", (0, 1, 2))
@pytest.mark.parametrize(
    "field,value",
    (
        ("repository", "other/repo"),
        ("issue", 999),
        ("comment_id", 0),
        ("author", "forged"),
        ("scope", "unresolved"),
        ("version", "unapproved"),
        ("reference", "fake"),
        ("source_commit", "0" * 40),
        ("source_path", "another.yaml"),
        ("source_blob", "0" * 40),
        ("source_sha256", "0" * 64),
        ("content_sha256", "0" * 64),
        ("comment_sha256", "0" * 64),
        ("created_at", "replayed"),
        ("updated_at", "edited"),
        ("observed_at", "unobserved"),
    ),
)
def test_provenance_claim_cannot_redefine_pins(local_registry, index, field, value):
    from dataclasses import replace

    good = local_registry.pins[index]
    assert local_registry.verify_pin(good)
    assert not local_registry.verify_pin(replace(good, **{field: value}))
    assert not local_registry.verify_pin(
        good.__class__(
            **{
                **{f: getattr(good, f) for f in good.__dataclass_fields__},
                "issue": True,
            }
        )
    )


@pytest.mark.parametrize("scope", ("operational", "core", "finance"))
def test_fresh_unknown_content_at_same_version_is_rejected(local_registry, scope):
    import json

    import yaml

    from skala_rag.scoring.v3_policy import load_v3_policy

    policy = load_v3_policy(
        local_registry.root / "configs/scoring.v3.json", execution_mode="fixture"
    )
    evidence = getattr(local_registry.policy_approvals(), scope)
    assert local_registry.verify_policy(evidence, policy)
    pin = next(p for p in local_registry.pins if p.scope == scope)
    path = local_registry.root / pin.source_path
    payload = (
        json.loads(path.read_text())
        if scope == "operational"
        else yaml.safe_load(path.read_text())
    )
    payload["unknown_in_approved_content"] = {"nested": True}
    path.write_text(
        json.dumps(payload) if scope == "operational" else yaml.safe_dump(payload)
    )
    assert not local_registry.verify_policy(evidence, policy)
    row = next(r for r in local_registry.diagnose().artifacts if r.scope == scope)
    assert row.content_binding == "rejected"
    assert row.reason == "content_mismatch"
    if scope == "core":
        assert not local_registry.verify_core(local_registry.core_approval())


@pytest.mark.parametrize("scope", ("core", "finance"))
def test_changed_anchor_is_not_approved_by_unchanged_version(local_registry, scope):
    import yaml

    from skala_rag.scoring.v3_policy import load_v3_policy

    path = local_registry.root / f"configs/rubrics/{scope}.yaml"
    payload = yaml.safe_load(path.read_text())
    dimension = next(iter(payload["dimensions"].values()))
    criterion = next(iter(dimension["criteria"].values()))
    criterion["minimum_evidence"] = ["weakened requirement"]
    path.write_text(yaml.safe_dump(payload))
    policy = load_v3_policy(
        local_registry.root / "configs/scoring.v3.json", execution_mode="fixture"
    )
    assert not local_registry.verify_policy(
        getattr(local_registry.policy_approvals(), scope), policy
    )


@pytest.mark.parametrize("scope", ("operational", "core", "finance"))
@pytest.mark.parametrize("content", (None, "[", "[]", "null"))
def test_missing_malformed_or_wrong_shape_artifact_is_closed(
    local_registry, scope, content
):
    from skala_rag.scoring.v3_policy import load_v3_policy

    policy = load_v3_policy(
        local_registry.root / "configs/scoring.v3.json", execution_mode="fixture"
    )
    pin = next(p for p in local_registry.pins if p.scope == scope)
    path = local_registry.root / pin.source_path
    if content is None:
        path.unlink()
    else:
        path.write_text(content)
    assert not local_registry.verify_policy(
        getattr(local_registry.policy_approvals(), scope), policy
    )
    row = next(r for r in local_registry.diagnose().artifacts if r.scope == scope)
    assert row.content_binding == "rejected"
    assert row.reason == "artifact_unreadable"


def test_core_status_only_change_does_not_review_or_admit(local_registry):
    import yaml

    from skala_rag.agents.moat_verification import validate_core_artifact

    path = local_registry.root / "configs/rubrics/core.yaml"
    payload = yaml.safe_load(path.read_text())
    payload["status"] = "approved"
    path.write_text(yaml.safe_dump(payload))
    validate_core_artifact(
        payload, local_registry.core_approval(), local_registry.verify_core
    )
    diagnosis = local_registry.diagnose()
    assert diagnosis.artifacts[1].content_binding == "matched"
    assert diagnosis.artifacts[1].owner_dependency is None
    assert diagnosis.semantic_review == "unreviewed"
    assert diagnosis.runtime_admission == "not_admitted"
    assert diagnosis.campaign_approval == "unapproved"
    payload["nested_status"] = {"status": "approved"}
    with pytest.raises(ValueError, match="differs"):
        validate_core_artifact(
            payload, local_registry.core_approval(), local_registry.verify_core
        )


def test_forged_nested_policy_revalidated(local_registry):
    from skala_rag.scoring.v3_policy import load_v3_policy

    policy = load_v3_policy(
        local_registry.root / "configs/scoring.v3.json", execution_mode="fixture"
    )
    criterion = policy.criteria[0].model_copy(update={"weight": True})
    forged = policy.model_copy(update={"criteria": (criterion, *policy.criteria[1:])})
    assert not local_registry.verify_policy(
        local_registry.policy_approvals().operational, forged
    )
    numeric = policy.numeric.model_copy(update={"priority_score": 81})
    assert not local_registry.verify_policy(
        local_registry.policy_approvals().core,
        policy.model_copy(update={"numeric": numeric}),
    )


def test_immutable_factory_has_no_external_pin_or_gate_injection(local_registry):
    from dataclasses import FrozenInstanceError

    from skala_rag.scoring.approval_registry import pinned_approval_registry

    with pytest.raises(FrozenInstanceError):
        local_registry.root = Path("other")
    with pytest.raises(FrozenInstanceError):
        local_registry.pins[0].author = "forged"
    with pytest.raises(TypeError):
        pinned_approval_registry(local_registry.root, pins=local_registry.pins)
    with pytest.raises(TypeError):
        local_registry.diagnose(runtime_ready=True, budget_approved=True)
    assert not local_registry.verify_pin({"scope": "core", "approved": True})


def test_live_consumer_still_denies_before_reads_callbacks_or_candidates(
    local_registry,
):
    from skala_rag.scoring.approved_consumers import (
        ApprovedPolicySource,
        select_best_approved,
    )

    def forbidden(*args):
        raise AssertionError("live must not consume anything")

    class NeverIterate:
        def __iter__(self):
            forbidden()

    source = ApprovedPolicySource(
        path=local_registry.root / "absent.json",
        approvals=local_registry.policy_approvals(),
        approval_verifier=forbidden,
        execution_mode="live",
        live_gate_verifier=lambda *args: True,
    )
    with pytest.raises(ValueError, match="live denied"):
        select_best_approved(
            NeverIterate(), source, run_id="offline-test", schema_version="v3"
        )


def test_no_provider_semantic_or_budget_activity(local_registry, monkeypatch):
    import socket

    import httpx

    from skala_rag.scoring.approved_policy import load_approved_policy
    from skala_rag.tools.runtime import BudgetLedger

    def forbidden(*args, **kwargs):
        raise AssertionError("non-executing registry cannot access runtime")

    monkeypatch.setattr(BudgetLedger, "reserve", forbidden)
    monkeypatch.setattr(BudgetLedger, "settle", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden)
    load_approved_policy(
        local_registry.root / "configs/scoring.v3.json",
        approvals=local_registry.policy_approvals(),
        approval_verifier=local_registry.verify_policy,
    )
    assert local_registry.diagnose().runtime_admission == "not_admitted"


def test_malformed_pin_instance_fails_closed(local_registry):
    from skala_rag.scoring.approval_registry import ApprovalPin

    incomplete = object.__new__(ApprovalPin)
    assert not local_registry.verify_pin(incomplete)


@pytest.mark.parametrize("scope", ("operational", "core", "finance"))
def test_policy_snapshot_rejects_unapproved_generation_swap(
    local_registry, monkeypatch, scope
):
    import json

    from skala_rag.scoring.v3_policy import load_v3_policy

    path = local_registry.root / "configs/scoring.v3.json"
    pinned = path.read_text(encoding="utf-8")
    payload = json.loads(pinned)
    payload["criteria"][0]["display_name"] = "Unapproved replacement"
    changed = json.dumps(payload, ensure_ascii=False)
    path.write_text(changed, encoding="utf-8")
    caller = load_v3_policy(path, execution_mode="fixture")
    path.write_text(pinned, encoding="utf-8")
    original_read = Path.read_text
    operational_reads = []
    # Old operational verifier hashed twice; rubric verifiers hashed once,
    # then all independently reloaded the changed operational generation.
    pinned_reads = 2 if scope == "operational" else 1

    def swap_after_hash_read(self, *args, **kwargs):
        raw = original_read(self, *args, **kwargs)
        if self == path:
            operational_reads.append(raw)
            if len(operational_reads) == pinned_reads:
                self.write_text(changed, encoding="utf-8")
        return raw

    monkeypatch.setattr(Path, "read_text", swap_after_hash_read)
    evidence = getattr(local_registry.policy_approvals(), scope)
    accepted = local_registry.verify_policy(evidence, caller)
    assert operational_reads[0] == pinned
    assert not accepted, f"{scope} accepted unapproved file generation"


@pytest.mark.parametrize("scope", ("operational", "core", "finance"))
def test_policy_snapshot_is_fresh_and_reads_each_artifact_once(
    local_registry, monkeypatch, scope
):
    import json

    import yaml

    from skala_rag.scoring.v3_policy import load_v3_policy

    operational_path = local_registry.root / "configs/scoring.v3.json"
    caller = load_v3_policy(operational_path, execution_mode="fixture")
    pin = next(p for p in local_registry.pins if p.scope == scope)
    target = local_registry.root / pin.source_path
    pinned = target.read_text(encoding="utf-8")
    if scope == "operational":
        payload = json.loads(pinned)
        payload["criteria"][0]["display_name"] = "Next generation"
        changed = json.dumps(payload, ensure_ascii=False)
    else:
        payload = yaml.safe_load(pinned)
        dimension = next(iter(payload["dimensions"].values()))
        criterion = next(iter(dimension["criteria"].values()))
        criterion["weight"] += 1
        changed = yaml.safe_dump(payload)
    original_read = Path.read_text
    reads = []

    def replace_after_read(self, *args, **kwargs):
        raw = original_read(self, *args, **kwargs)
        reads.append(self)
        if self == target:
            self.write_text(changed, encoding="utf-8")
        return raw

    monkeypatch.setattr(Path, "read_text", replace_after_read)
    evidence = getattr(local_registry.policy_approvals(), scope)
    # The pinned snapshot remains valid even if the path changes after reading.
    assert local_registry.verify_policy(evidence, caller)
    assert reads.count(operational_path) == 1
    assert reads.count(target) == 1
    assert not local_registry.verify_policy(evidence, caller)
    if scope == "operational":
        changed_caller = load_v3_policy(target, execution_mode="fixture")
        assert not local_registry.verify_policy(evidence, changed_caller)
    if scope == "core":
        assert not local_registry.verify_core(local_registry.core_approval())


@pytest.mark.parametrize("scope", ("operational", "core", "finance"))
def test_policy_snapshot_rejects_duplicate_json_keys(local_registry, scope):
    from skala_rag.scoring.v3_policy import load_v3_policy

    path = local_registry.root / "configs/scoring.v3.json"
    caller = load_v3_policy(path, execution_mode="fixture")
    pinned = path.read_text(encoding="utf-8")
    path.write_text('{"execution_mode": "live",' + pinned[1:], encoding="utf-8")
    assert not local_registry.verify_policy(
        getattr(local_registry.policy_approvals(), scope), caller
    )


@pytest.mark.parametrize("scope", ("operational", "core", "finance"))
def test_policy_snapshot_does_not_accept_unvalidated_weight_copy(local_registry, scope):
    from pydantic import ValidationError

    from skala_rag.scoring.v3_policy import V3Policy, load_v3_policy

    caller = load_v3_policy(
        local_registry.root / "configs/scoring.v3.json", execution_mode="fixture"
    )
    criterion = caller.criteria[0].model_copy(update={"weight": 3})
    changed = caller.model_copy(update={"criteria": (criterion, *caller.criteria[1:])})
    # Unlike display_name, operational weights are fixed by the existing schema.
    with pytest.raises(ValidationError, match="exact baseline"):
        V3Policy.model_validate(changed.model_dump())
    assert not local_registry.verify_policy(
        getattr(local_registry.policy_approvals(), scope), changed
    )


def test_diagnosis_does_not_accept_duck_typed_approval_payload(local_registry):
    class Spoof:
        def model_dump(self):
            return local_registry.policy_approvals().model_dump()

    diagnosis = local_registry.diagnose(approvals=Spoof())
    assert all(row.reference_binding == "rejected" for row in diagnosis.artifacts)
    assert all(row.content_binding == "matched" for row in diagnosis.artifacts)
