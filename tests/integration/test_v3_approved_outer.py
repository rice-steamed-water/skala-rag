"""#217 real public consumers; code-owned approvals, synthetic evaluation only."""

import json
import socket
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
from itertools import count
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from tests.integration import test_v3_source_only_controller as source_only_fixture
from tests.integration.test_v3_evidence_snapshot_consumer import setup_case
from tests.unit.test_v3_report_pipeline import Stub

import skala_rag.graph.candidate_workflow_v3 as outer
import skala_rag.graph.snapshot as snapshot_module
import skala_rag.rag.adapter as rag_adapter
import skala_rag.tools.runtime as adapter_runtime
from skala_rag.reporting.v3_pipeline import ReportGeneratorV3, SemanticJudgeV3
from skala_rag.scoring import approved_consumers
from skala_rag.scoring.approval_registry import pinned_approval_registry
from skala_rag.scoring.approved_consumers import ApprovedPolicySource

ROOT = Path(__file__).resolve().parents[2]
source_only_inputs = source_only_fixture.inputs


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    attempts = Counter()

    def deny(kind):
        def denied(*args, **kwargs):
            attempts[kind] += 1
            pytest.fail(f"approved outer attempted {kind}")

        return denied

    for target, name, kind in (
        (socket.socket, "connect", "network"),
        (socket.socket, "connect_ex", "network"),
        (socket, "create_connection", "network"),
        (socket, "getaddrinfo", "dns"),
        (httpx.Client, "send", "provider"),
        (httpx.AsyncClient, "send", "provider"),
    ):
        monkeypatch.setattr(target, name, deny(kind))
    # No optional ML dependency import/download is needed by this fixture slice.
    import builtins

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in {
            "sentence_transformers",
            "transformers",
            "huggingface_hub",
            "torch",
            "openai",
        }:
            return deny("model/provider import")()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    yield attempts
    assert not attempts


def source(**overrides):
    registry = pinned_approval_registry(ROOT)
    return ApprovedPolicySource(
        **{
            "path": ROOT / "configs/scoring.v3.json",
            "approvals": registry.policy_approvals(),
            "approval_verifier": registry.verify_policy,
            "execution_mode": "fixture",
            **overrides,
        }
    )


def test_public_original_join_consumes_approved_once_and_reports(
    monkeypatch, offline, tmp_path
):
    # Independent runs normally mint UUIDs. Pin test-only transport IDs so the
    # complete DTO and original artifact parity assertion is deterministic.
    identifiers = count(1)
    monkeypatch.setattr(adapter_runtime, "uuid4", lambda: UUID(int=next(identifiers)))
    monkeypatch.setattr(rag_adapter, "uuid4", lambda: UUID(int=next(identifiers)))
    baseline_case = setup_case(count=2)
    baseline = outer.run_candidate_workflow_v3(
        baseline_case["stages"], baseline_case["callbacks"], **baseline_case["options"]
    )
    identifiers = count(1)
    c = setup_case(count=2)
    consumed, frozen, verification = [], [], []
    configured = source()
    trusted = configured.approval_verifier

    def verify(evidence, policy):
        verification.append(evidence.scope)
        return trusted(evidence, policy)

    configured = source(approval_verifier=verify)
    real_freeze = snapshot_module.freeze_snapshot

    def observe_freeze(*args, **kwargs):
        result = real_freeze(*args, **kwargs)
        frozen.append(result.model_dump(mode="json"))
        return result

    monkeypatch.setattr(snapshot_module, "freeze_snapshot", observe_freeze)

    def observer(name):
        real = getattr(approved_consumers, name)

        def call(payload, binding, **kwargs):
            consumed.append((name, deepcopy(payload), deepcopy(kwargs)))
            return real(payload, binding, **kwargs)

        return call

    for name in (
        "aggregate_scores_approved",
        "decide_approved",
        "select_best_approved",
    ):
        monkeypatch.setattr(outer, name, observer(name), raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("approved path used the omitted numeric entry")

    for name in ("aggregate_scores_v3", "decide_v3", "select_best_v3"):
        monkeypatch.setattr(outer, name, forbidden)
    generator, judge = Stub(), Stub()
    result, context, report = outer.run_candidate_report_v3(
        c["stages"],
        c["callbacks"],
        run_input=c["stages"].evidence_research.run_input,
        generate=ReportGeneratorV3(generator),
        judge=SemanticJudgeV3(judge),
        approved_policy_source=configured,
        graph_events=c["events"],
        **c["options"],
    )
    assert result == baseline
    assert [name for name, _, _ in consumed] == [
        "aggregate_scores_approved",
        "decide_approved",
        "aggregate_scores_approved",
        "decide_approved",
        "select_best_approved",
    ]
    joins = [
        updates["evaluation_join"]["data"]
        for namespace, updates in c["events"]
        if not namespace and "evaluation_join" in updates
    ]
    payload = context.snapshot()
    scores = [entry for entry in consumed if entry[0] == "aggregate_scores_approved"]
    assert len(joins) == len(scores) == len(frozen) == 2
    for (_, dims, kwargs), join, original in zip(scores, joins, frozen, strict=True):
        assert len(dims) == 6
        assert {e.dimension for e in dims} == {
            "founder",
            "market",
            "technology",
            "moat",
            "traction",
            "deal_terms",
        }
        assert {
            f"{e.candidate_id}:{e.evaluation_round}:{e.dimension}": e.model_dump(
                mode="json"
            )
            for e in dims
        } == join["evaluated"]["evaluations_v3"]
        snapshot = kwargs["snapshot"].model_dump(mode="json")
        cid = original["candidate_id"]
        assert snapshot == original == join["snapshot"] == payload["snapshots"][cid]
        assert (
            result.research_artifacts[cid]["state"]["snapshots"][
                original["snapshot_id"]
            ]
            == original
        )
        assert all(
            e.snapshot_id == original["snapshot_id"]
            and e.evidence_revision == original["evidence_revision"]
            and e.evaluation_round == original["evaluation_round"]
            for e in dims
        )
        assert (
            kwargs["applicability_verifier"] is c["options"]["applicability_verifier"]
        )
    assert len(c["seen"]) == 10 and result.candidate_index == 2
    assert consumed[-1][2] == {
        "run_id": result.run_id,
        "schema_version": result.schema_version,
    }
    assert len(verification) == 3 * 7
    assert all(
        verification[i : i + 3] == ["operational", "core", "finance"]
        for i in range(0, len(verification), 3)
    )
    assert (
        report.status == "completed" and report.validation.valid and not report.warning
    )
    assert report.pdf_validation is None and not report.final_allowed
    assert len(generator.calls) == len(judge.calls) == 1
    assert generator.calls[0][1]["context"] == judge.calls[0][1]["context"] == payload
    assert not offline
    (tmp_path / "approved-outer-original.json").write_text(
        json.dumps(
            {
                "evidence_kind": "synthetic_evaluation_code_owned_approval_content",
                "source_path": str(configured.path),
                "source_json": json.loads(Path(configured.path).read_text()),
                "approvals": configured.approvals.model_dump(mode="json"),
                "frozen_snapshots": frozen,
                "promoted_evaluations": [
                    join["evaluated"]["evaluations_v3"] for join in joins
                ],
                "context": payload,
                "scores": {
                    cid: s.model_dump(mode="json") for cid, s in result.scores.items()
                },
                "decisions": {
                    cid: d.model_dump(mode="json")
                    for cid, d in result.decisions.items()
                },
                "selection": asdict(result.selection),
                "consumer_order": [name for name, _, _ in consumed],
                "approval_verifications": verification,
                "forbidden_attempts": {
                    key: offline[key]
                    for key in ("network", "dns", "provider", "model/provider import")
                },
                "report": {
                    "status": report.status,
                    "final_allowed": report.final_allowed,
                    "pdf_validation": None,
                },
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("entry", ["build", "run", "report"])
def test_policy_and_source_data_are_detached_but_verifier_is_not(entry):
    c = setup_case()
    configured = source()
    stages = c["stages"]

    def discover():
        object.__setattr__(configured.approvals.core, "reference", "caller corruption")
        object.__setattr__(
            c["options"]["policy"].research, "additional_requests_per_candidate", 9
        )
        return stages.discover()

    mutated_stages = replace(stages, discover=discover)
    kwargs = {**c["options"], "approved_policy_source": configured}
    if entry == "build":
        result = (
            outer.build_candidate_workflow_v3(mutated_stages, c["callbacks"], **kwargs)
            .compile()
            .invoke({})["result"]
        )
    elif entry == "run":
        result = outer.run_candidate_workflow_v3(
            mutated_stages, c["callbacks"], **kwargs
        )
    else:
        result, _, report = outer.run_candidate_report_v3(
            mutated_stages,
            c["callbacks"],
            run_input=stages.evidence_research.run_input,
            generate=ReportGeneratorV3(Stub()),
            judge=SemanticJudgeV3(Stub()),
            **kwargs,
        )
        assert not report.final_allowed
    assert result.status == "ready_for_v3_reporting" and len(result.scores) == 1
    assert not result.errors and result.research_retry_count == {"co-0": 0}


@pytest.mark.parametrize("revoke_at", ["research", "decision", "selector"])
def test_report_retains_live_trusted_verifier_state_and_archives_denial(
    monkeypatch, revoke_at
):
    c = setup_case()
    registry = pinned_approval_registry(ROOT)

    class TrustedVerifier:
        revoked = False

        def __call__(self, evidence, policy):
            return not self.revoked and registry.verify_policy(evidence, policy)

    trusted = TrustedVerifier()
    stages = c["stages"]

    def research(candidate):
        if revoke_at == "research":
            trusted.revoked = True
        return stages.research(candidate)

    if revoke_at != "research":
        name = (
            "aggregate_scores_approved"
            if revoke_at == "decision"
            else "decide_approved"
        )
        real = getattr(outer, name)

        def revoke_after(*args, **kwargs):
            result = real(*args, **kwargs)
            trusted.revoked = True
            return result

        monkeypatch.setattr(outer, name, revoke_after)

    generator, judge = Stub(), Stub()
    with pytest.raises(ValueError, match="approval rejected"):
        outer.run_candidate_report_v3(
            replace(stages, research=research),
            c["callbacks"],
            run_input=stages.evidence_research.run_input,
            generate=ReportGeneratorV3(generator),
            judge=SemanticJudgeV3(judge),
            approved_policy_source=source(approval_verifier=trusted),
            graph_events=c["events"],
            **c["options"],
        )
    archived = [
        u["archive"]["data"] for ns, u in c["events"] if not ns and "archive" in u
    ]
    assert len(archived) == 1
    if revoke_at == "selector":
        assert archived[0]["outcomes"]["co-0"]["status"] == "recommend"
        assert set(archived[0]["scores"]) == set(archived[0]["decisions"]) == {"co-0"}
    else:
        assert archived[0]["outcomes"]["co-0"]["status"] == "failed"
        assert not archived[0]["scores"] and not archived[0]["decisions"]
        assert archived[0]["errors"] and archived[0]["outcomes"]["co-0"]["failure_ids"]
    assert generator.calls == judge.calls == []


@pytest.mark.parametrize("field", ["numeric", "research", "selection", "catalog"])
def test_fresh_loaded_policy_cannot_change_after_trusted_verification(
    monkeypatch, field
):
    c = setup_case()
    registry = pinned_approval_registry(ROOT)
    armed, numeric_calls = [], []
    stages = c["stages"]

    def research(candidate):
        armed.append(True)
        return stages.research(candidate)

    def verifier(evidence, policy):
        accepted = registry.verify_policy(evidence, policy)
        if armed and evidence.scope == "finance":
            # Synthetic fault AFTER real content verification, not new authority.
            if field == "numeric":
                object.__setattr__(policy.numeric, "priority_score", 79)
            elif field == "research":
                object.__setattr__(
                    policy.research, "additional_requests_per_candidate", 9
                )
            elif field == "selection":
                object.__setattr__(
                    policy.selection,
                    "ordering",
                    tuple(reversed(policy.selection.ordering)),
                )
            else:
                object.__setattr__(
                    policy.criteria[0], "display_name", "same-version changed name"
                )
        return accepted

    def observe(name):
        real = getattr(approved_consumers, name)

        def invoke(*args, **kwargs):
            numeric_calls.append(name)
            return real(*args, **kwargs)

        return invoke

    for name in ("_aggregate_scores_v3", "_decide_v3", "_select_best_v3"):
        monkeypatch.setattr(approved_consumers, name, observe(name))
    with pytest.raises(ValueError):
        outer.run_candidate_workflow_v3(
            replace(stages, research=research),
            c["callbacks"],
            approved_policy_source=source(approval_verifier=verifier),
            **c["options"],
        )
    assert len(c["seen"]) == 5 and numeric_calls == []


@pytest.mark.parametrize(
    "damage", ["live", "invalid", "live_extras", "approval", "whole_policy"]
)
@pytest.mark.parametrize("entry", ["run", "report"])
def test_invalid_source_refused_before_any_product_callback(monkeypatch, damage, entry):
    c = setup_case()
    calls, approvals = [], []
    trusted = source().approval_verifier

    def verify(evidence, policy):
        approvals.append(evidence.scope)
        return False if damage == "approval" else trusted(evidence, policy)

    configured = source(approval_verifier=verify)
    if damage == "live":
        configured = replace(
            configured, execution_mode="live", path=ROOT / "must-not-read"
        )
    elif damage == "invalid":
        configured = object()
    elif damage == "live_extras":
        configured = replace(
            configured, live_gate_verifier=lambda *args: calls.append("live gate")
        )
    elif damage == "whole_policy":
        # Same version/catalog IDs, changed full policy, bypassing a frozen setter.
        policy = c["options"]["policy"]
        c["options"]["policy"] = policy.model_copy(
            update={
                "research": policy.research.model_copy(
                    update={"additional_requests_per_candidate": 9}
                )
            }
        )

    def forbidden(*args, **kwargs):
        calls.append("product")
        pytest.fail("invalid source reached a product callback")

    stages = replace(c["stages"], discover=forbidden, research=forbidden)
    kwargs = {**c["options"], "approved_policy_source": configured}
    with pytest.raises(ValueError):
        if entry == "run":
            outer.run_candidate_workflow_v3(stages, c["callbacks"], **kwargs)
        else:
            outer.run_candidate_report_v3(
                stages,
                c["callbacks"],
                run_input=stages.evidence_research.run_input,
                generate=forbidden,
                judge=forbidden,
                check_pdf=forbidden,
                **kwargs,
            )
    assert calls == c["seen"] == c["backend"].calls == []
    if damage in ("live", "invalid", "live_extras"):
        assert approvals == []


def test_source_only_mixing_rejected_before_discovery_or_provider(source_only_inputs):
    from skala_rag.source_only_v3 import prepare_source_only_v3

    inputs, configured, requests = source_only_inputs
    boundary = prepare_source_only_v3(**inputs)
    c = setup_case(count=0)
    with pytest.raises(ValueError, match="source-only"):
        outer.run_candidate_workflow_v3(
            None,
            {},
            source_only=boundary,
            run_profile=boundary.run_profile,
            approved_policy_source=source(),
            **{
                **c["options"],
                "run_id": boundary.run_id,
                "schema_version": boundary.run_input.schema_version,
            },
        )
    assert configured == requests == c["events"] == c["backend"].calls == []


@pytest.mark.parametrize("change_at", ["normalize", "research"])
def test_changed_policy_file_is_reopened_before_score_and_selector(tmp_path, change_at):
    c = setup_case()
    path = tmp_path / "scoring.v3.json"
    original = (ROOT / "configs/scoring.v3.json").read_text()
    path.write_text(original)
    stages = c["stages"]

    def research(candidate):
        if change_at == "research":
            path.write_text("{}")
        return stages.research(candidate)

    def normalize(candidates):
        if change_at == "normalize":
            path.write_text("{}")
        return stages.normalize(candidates)

    with pytest.raises(ValueError):
        outer.run_candidate_workflow_v3(
            replace(stages, research=research, normalize=normalize),
            c["callbacks"],
            approved_policy_source=source(path=path),
            graph_events=c["events"],
            **c["options"],
        )
    archives = [
        u["archive"]["data"] for ns, u in c["events"] if not ns and "archive" in u
    ]
    if change_at == "normalize":
        assert archives == c["seen"] == c["backend"].calls == []
    else:
        assert (
            len(archives) == 1 and archives[0]["outcomes"]["co-0"]["status"] == "failed"
        )
        assert not archives[0]["scores"] and not archives[0]["decisions"]
        assert any(
            row["step"] == "selector" and row["status"] == "failed"
            for row in c["trace"]
        )
