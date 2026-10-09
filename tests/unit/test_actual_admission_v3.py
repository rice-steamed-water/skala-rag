"""Mock-wire integration, SYNTHETIC semantic reviews and test-only budgets.

No provider access, actual scored data, billing or production approval is claimed.
"""

import shutil
import socket
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
from threading import Barrier, Event
from typing import Literal, assert_never

import httpx
import pytest
from tests.unit.test_approved_policy import gates_payload, runtime_binding
from tests.unit.test_openai_attempt import Output, body
from tests.unit.test_scoring_v3 import evaluations
from tests.unit.test_select_v3 import entry
from tests.unit.test_source_bound_review import capture_review

from skala_rag.agents.moat_verification import _digest, frozen_snapshot_digest
from skala_rag.agents.source_fact_verification import (
    SourceBoundReviewResolver,
    SourceFactError,
)
from skala_rag.contracts import RunInput
from skala_rag.fakes import FakeClock, FakeLLM
from skala_rag.scoring.approval_registry import pinned_approval_registry
from skala_rag.scoring.approved_consumers import (
    ActualAdmissionV3,
    ApprovedPolicySource,
    aggregate_scores_approved,
    decide_approved,
    select_best_approved,
)
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt
from skala_rag.tools.runtime import BudgetLedger
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch):
    def denied(*_args, **_kwargs):
        pytest.fail("network or observation-side runtime mutation is forbidden")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    return denied


@pytest.fixture
def configured(tmp_path: Path):
    root = Path(__file__).resolve().parents[2]
    for name in ("scoring.v3.json", "rubrics/core.yaml", "rubrics/finance.yaml"):
        target = tmp_path / "configs" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "configs" / name, target)
    local_registry = pinned_approval_registry(tmp_path)
    snapshot, _, sources, receipt, review, request = capture_review(tmp_path)
    evidence = snapshot.evidence["ev-capture"]
    record = next(iter(snapshot.retrieval_records.values())).model_copy(
        update={
            "source_ids": list(sources),
            "chunk_ids": [],
            "evidence_ids": [evidence.evidence_id],
        }
    )
    snapshot = snapshot.model_copy(
        update={
            "sources": {sid: trusted.source for sid, trusted in sources.items()},
            "chunks": {},
            "evidence": {evidence.evidence_id: evidence},
            "evidence_ids": [evidence.evidence_id],
            "retrieval_records": {record.retrieval_id: record},
        }
    )
    core = local_registry.rubric("core-0.1.0")
    review = review.model_copy(
        update={
            "snapshot_sha256": frozen_snapshot_digest(snapshot),
            "rubric_sha256": _digest(core),
        }
    )
    resolvers = {
        (snapshot.snapshot_id, version): SourceBoundReviewResolver(
            snapshot,
            local_registry.rubric(version),
            sources=sources,
            reviews=(review,) if version == "core-0.1.0" else (),
        )
        for version in ("core-0.1.0", "finance-0.1.0")
    }
    payload = gates_payload()
    payload["provider"] = "openai"
    payload["limits"].update(
        {
            "max_calls": 1,
            "tool_max_calls": {"openai": 1},
            "max_input_tokens": 10,
            "max_output_tokens": 10,
            "max_cost_usd": "0.10",
        }
    )
    binding = runtime_binding(
        run_id=snapshot.run_id, schema_version=snapshot.schema_version, payload=payload
    )

    def verify_gate(gate, gates):
        references = {
            "runtime_readiness": "synthetic:readiness",
            "call_budget": "synthetic:calls",
            "cost_budget": "synthetic:cost",
        }
        return (
            gates == binding.gates
            and getattr(gates, f"{gate}_reference") == references[gate]
            and binding.runtime.ledger.snapshot()["calls"] == 0
        )

    source = ApprovedPolicySource(
        path=local_registry.root / "configs/scoring.v3.json",
        approvals=local_registry.policy_approvals(),
        approval_verifier=local_registry.verify_policy,
        execution_mode="live",
        live_gates=binding.gates,
        live_gate_verifier=verify_gate,
    )
    admission = ActualAdmissionV3(
        source=source,
        runtime_binding=binding,
        registry=local_registry,
        run_input=RunInput(
            schema_version=snapshot.schema_version,
            investment_theme="Synthetic test",
            countries=["US"],
            languages=["en"],
            as_of=snapshot.as_of,
            corpus_version=snapshot.corpus_version,
            policy_version=snapshot.policy_version,
            execution_mode="live",
        ),
        index_version=snapshot.index_version,
        review_resolvers=resolvers,
        execution_scope="controlled_response",
    )
    llm = RuntimeStructuredLLM(
        runtime=binding.runtime,
        call=binding.call.model_copy(
            update={
                "candidate_id": snapshot.candidate_id,
                "node": "market_evaluation",
            }
        ),
        budget=binding.budget,
        readiness=binding.readiness,
        transport=OpenAIResponsesAttempt(
            api_key="SYNTHETIC-NOT-A-CREDENTIAL",
            prompt_version="synthetic-admission",
            schema_version=snapshot.schema_version,
            clock=binding.runtime.clock,
            http_transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200, json=body(usage={"input_tokens": 10, "output_tokens": 10})
                )
            ),
        ),
        allowance_for=lambda _system, _user, _schema: binding.allowance,
    )
    return admission, snapshot, llm, request, receipt, review


def test_last_authorized_call_leaves_math_available(configured, monkeypatch, offline):
    # Given: source integrity, pinned values and a single synthetic request budget.
    admission, snapshot, llm, _, _, _ = configured
    policy = admission.verify_evaluator(
        snapshot, admission.registry.rubric("core-0.1.0"), llm, "market"
    )
    items = evaluations(policy, missing={c.criterion_id for c in policy.criteria})
    for item in items:
        for name in (
            "run_id",
            "candidate_id",
            "schema_version",
            "snapshot_id",
            "evaluation_round",
            "evidence_revision",
        ):
            setattr(item, name, getattr(snapshot, name))
        for criterion in item.criteria:
            criterion.schema_version = snapshot.schema_version
        item.rubric_version = (
            "finance-0.1.0"
            if item.dimension in ("traction", "deal_terms")
            else "core-0.1.0"
        )
    assert (
        llm.generate(system="Synthetic", user="Synthetic", output_schema=Output).answer
        == 4
    )
    ledger = llm.runtime.ledger.snapshot()
    assert ledger["calls"] == ledger["input_tokens_accounted"] / 10 == 1
    assert ledger["output_tokens_accounted"] == 10
    assert ledger["cost_usd_accounted"] == "0.10"
    monkeypatch.setattr(BudgetLedger, "reserve", offline)
    monkeypatch.setattr(BudgetLedger, "settle", offline)
    monkeypatch.setattr(llm.runtime, "execute", offline)
    # When: all final deterministic stages run after the last authorized request.
    summary = aggregate_scores_approved(
        items,
        admission.source,
        snapshot=snapshot,
        applicability_verifier=None,
        actual_admission=admission,
    )
    decision = decide_approved(summary, admission.source, actual_admission=admission)
    selected = select_best_approved(
        [
            entry(
                snapshot.candidate_id,
                decision.label,
                summary.normalized_score,
                summary.weighted_missing_pct,
                summary.applicable_weight,
            )
        ],
        admission.source,
        run_id=snapshot.run_id,
        schema_version=snapshot.schema_version,
        actual_admission=admission,
    )
    # Then: approved missing arithmetic holds, with no new request or invented rating.
    assert (
        summary.observed_score,
        summary.applicable_weight,
        summary.missing_weight,
    ) == (0, 100, 100)
    assert summary.hold_reasons == ["WEIGHTED_MISSING", "LOW_MARKET", "LOW_TECHNOLOGY"]
    assert decision.label == "WATCHLIST" and selected.selected_candidate_id is None
    assert llm.runtime.ledger.snapshot() == ledger
    with pytest.raises(ValueError, match="exhausted"):
        admission.load_policy(require_capacity=True)


@pytest.mark.parametrize(
    "change",
    [
        "fake",
        "runtime",
        "node",
        "candidate",
        "run",
        "budget",
        "readiness",
        "clock",
        "scope",
        "native_transport",
    ],
)
def test_foreign_evaluator_denied_before_transport(
    configured,
    change: Literal[
        "fake",
        "runtime",
        "node",
        "candidate",
        "run",
        "budget",
        "readiness",
        "clock",
        "scope",
        "native_transport",
    ],
    monkeypatch,
    offline,
):
    # Given: a valid admission with a mismatched execution object.
    admission, snapshot, llm, _, _, _ = configured
    match change:
        case "fake":
            llm = FakeLLM([])
        case "runtime":
            llm.runtime = runtime_binding().runtime
        case "node" | "candidate" | "run":
            field = {"node": "node", "candidate": "candidate_id", "run": "run_id"}[
                change
            ]
            llm.call = llm.call.model_copy(update={field: "foreign"})
        case "budget":
            llm.budget = llm.budget.model_copy(update={"max_calls": 9})
        case "readiness":
            llm.readiness = llm.readiness.model_copy(update={"configured": False})
        case "clock":
            llm.transport._clock = FakeClock(llm.runtime.clock.now())
        case "scope":
            admission = replace(admission, execution_scope="actual")
        case "native_transport":
            llm.transport._http_transport = None
        case unreachable:
            assert_never(unreachable)
    monkeypatch.setattr(admission.runtime_binding.runtime, "execute", offline)
    # When / Then: no request or ledger reservation is made.
    with pytest.raises(ValueError, match="mismatch"):
        admission.verify_evaluator(
            snapshot, admission.registry.rubric("core-0.1.0"), llm, "market"
        )
    assert admission.runtime_binding.runtime.ledger.snapshot()["calls"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "foreign"),
        ("candidate_id", "foreign"),
        ("corpus_version", "foreign"),
        ("as_of", date(2020, 1, 1)),
        ("index_version", "foreign"),
        ("snapshot_id", "foreign"),
        ("evidence_revision", 99),
    ],
)
def test_changed_snapshot_rejected(configured, field, value):
    # Given / When / Then
    admission, snapshot, _, _, _, _ = configured
    with pytest.raises(ValueError):
        admission.verify_snapshot(
            snapshot.model_copy(update={field: value}),
            admission.registry.rubric("core-0.1.0"),
        )


@pytest.mark.parametrize(
    "change",
    [
        "fixture_locator",
        "rubric",
        "header",
        "source_bytes",
        "pinned_file",
        "unknown_source",
    ],
)
def test_tampering_cannot_reuse_admission(
    configured,
    tmp_path,
    change: Literal[
        "fixture_locator",
        "rubric",
        "header",
        "source_bytes",
        "pinned_file",
        "unknown_source",
    ],
):
    # Given
    admission, snapshot, _, _, _, _ = configured
    rubric = admission.registry.rubric("core-0.1.0")
    match change:
        case "fixture_locator":
            next(iter(snapshot.sources.values())).url = "fixture://forged"
        case "rubric":
            rubric["comparison"] = "changed"
        case "header":
            rubric["status"] = "approved"
        case "source_bytes":
            (tmp_path / "capture/raw/company.html").write_bytes(b"tampered")
        case "pinned_file":
            path = admission.registry.root / "configs/rubrics/finance.yaml"
            path.write_text(path.read_text() + "\nunapproved: true\n")
        case "unknown_source":
            admission = replace(
                admission,
                review_resolvers={
                    (snapshot.snapshot_id, "core-0.1.0"): SourceBoundReviewResolver(
                        snapshot, rubric, sources={}, reviews=()
                    )
                },
            )
        case unreachable:
            assert_never(unreachable)
    # When / Then
    with pytest.raises(ValueError):
        admission.verify_snapshot(snapshot, rubric)


@pytest.mark.parametrize(
    "change", ["callback", "reference", "gates", "zero_capacity", "input", "index"]
)
def test_constructor_requires_more_than_truthy_approval(
    configured,
    change: Literal[
        "callback", "reference", "gates", "zero_capacity", "input", "index"
    ],
):
    # Given
    admission, _, _, _, _, _ = configured
    source, binding = admission.source, admission.runtime_binding
    kwargs = {}
    match change:
        case "callback":
            source = replace(source, approval_verifier=lambda _evidence, _policy: True)
        case "reference":
            source = replace(
                source,
                approvals=source.approvals.model_copy(
                    update={
                        "core": source.approvals.core.model_copy(
                            update={"reference": "model-approved"}
                        )
                    }
                ),
            )
        case "gates":
            source = replace(source, live_gate_verifier=lambda _gate, _gates: False)
        case "zero_capacity":
            assert binding.runtime.ledger.reserve(
                binding.tool_name, binding.allowance, live=True
            )
        case "input":
            kwargs["run_input"] = admission.run_input.model_copy(
                update={"execution_mode": "fixture"}
            )
        case "index":
            kwargs["index_version"] = ""
        case unreachable:
            assert_never(unreachable)
    # When / Then
    with pytest.raises(ValueError):
        replace(admission, source=source, **kwargs)


@pytest.mark.parametrize(
    "decision", ["accepted", "rejected", "unresolved", "unreviewed"]
)
def test_independent_review_distinctions_survive(configured, decision):
    # Given: records originate in the synthetic source-review channel.
    admission, snapshot, _, request, receipt, review = configured
    rubric = admission.registry.rubric("core-0.1.0")
    original = admission.review_resolvers[(snapshot.snapshot_id, "core-0.1.0")]
    resolver = SourceBoundReviewResolver(
        snapshot,
        rubric,
        sources=original._sources,
        reviews=()
        if decision == "unreviewed"
        else (review.model_copy(update={"decision": decision}),),
    )
    admission = replace(
        admission, review_resolvers={(snapshot.snapshot_id, "core-0.1.0"): resolver}
    )
    # When
    result = admission.resolve_review(
        snapshot, rubric, request, receipt, subject=review.subject
    )
    # Then
    if decision == "unreviewed":
        assert result is None
    else:
        assert result is not None and result.decision == decision


def test_observations_are_read_only_and_failures_propagate(
    configured, monkeypatch, offline, tmp_path
):
    # Given
    admission, snapshot, llm, request, receipt, review = configured
    runtime = admission.runtime_binding.runtime
    ledger = runtime.ledger.snapshot()
    monkeypatch.setattr(runtime.ledger, "reserve", offline)
    monkeypatch.setattr(runtime.ledger, "settle", offline)
    monkeypatch.setattr(runtime, "execute", offline)
    rubric = admission.registry.rubric("core-0.1.0")
    # When
    admission.verify_evaluator(snapshot, rubric, llm, "market")
    # Then
    assert runtime.ledger.snapshot() == ledger
    assert runtime.error_history == runtime.readiness_history == {}
    (tmp_path / "capture/raw/company.html").unlink()
    with pytest.raises(SourceFactError):
        admission.resolve_review(
            snapshot, rubric, request, receipt, subject=review.subject
        )


@pytest.mark.parametrize("field", ["ledger", "clock", "limits", "run_input"])
def test_changed_captured_context_denied(
    configured, field: Literal["ledger", "clock", "limits", "run_input"]
):
    # Given
    admission, _, _, _, _, _ = configured
    runtime = admission.runtime_binding.runtime
    match field:
        case "ledger":
            runtime.ledger = BudgetLedger(runtime.ledger.limits)
        case "clock":
            runtime.clock = FakeClock(runtime.clock.now())
        case "limits":
            runtime.ledger.limits.tool_max_calls["openai"] = 99
        case "run_input":
            admission.run_input.countries.append("foreign")
        case unreachable:
            assert_never(unreachable)
    # When / Then
    with pytest.raises(ValueError, match="context changed"):
        admission.load_policy()


@pytest.mark.parametrize(
    "branch", ["founder", "market", "technology", "moat", "business_deal"]
)
def test_one_admission_supports_another_configured_candidate(configured, branch):
    # Given: two independent snapshot bindings under one run/runtime/ledger.
    admission, snapshot, llm, _, _, _ = configured
    another = snapshot.model_copy(deep=True)
    another.snapshot_id = "second-snapshot"
    another.candidate_id = "second-candidate"
    for evidence in another.evidence.values():
        evidence.candidate_id = another.candidate_id
    for record in another.retrieval_records.values():
        record.candidate_id = another.candidate_id
    version = "finance-0.1.0" if branch == "business_deal" else "core-0.1.0"
    rubric = admission.registry.rubric(version)
    original = admission.review_resolvers[(snapshot.snapshot_id, version)]
    resolver = SourceBoundReviewResolver(
        another, rubric, sources=original._sources, reviews=()
    )
    admission = replace(
        admission,
        review_resolvers={
            **admission.review_resolvers,
            (another.snapshot_id, version): resolver,
        },
    )
    llm.call = llm.call.model_copy(
        update={
            "candidate_id": another.candidate_id,
            "node": f"{branch}_evaluation",
        }
    )
    # When
    policy = admission.verify_evaluator(another, rubric, llm, branch)
    # Then: admission is not a per-candidate runtime or a semantic approval.
    assert policy.policy_version == another.policy_version
    assert llm.runtime is admission.runtime_binding.runtime
    assert llm.runtime.ledger.snapshot()["calls"] == 0


def test_changed_current_header_is_not_cached_policy_authority(configured):
    # Given: the registry deliberately separates Core value approval from status.
    admission, _, _, _, _, _ = configured
    path = admission.registry.root / "configs/rubrics/core.yaml"
    path.write_text(path.read_text().replace("status: proposed", "status: approved"))
    # When / Then: an admitted run still requires its exact captured content.
    with pytest.raises(ValueError, match="context changed"):
        admission.load_policy()


@pytest.mark.parametrize("stage", ["aggregate", "decision", "selection"])
def test_other_source_cannot_borrow_admission(
    configured, stage: Literal["aggregate", "decision", "selection"]
):
    # Given
    admission, snapshot, _, _, _, _ = configured
    foreign = replace(admission.source)
    # When / Then: fail before consuming even malformed scoring payloads.
    with pytest.raises(ValueError, match="exact configured admission source"):
        match stage:
            case "aggregate":
                aggregate_scores_approved(
                    [],
                    foreign,
                    snapshot=snapshot,
                    applicability_verifier=None,
                    actual_admission=admission,
                )
            case "decision":
                # A valid summary is unnecessary: source binding is the first gate.
                from skala_rag.contracts.v3 import ScoreSummary

                decide_approved(
                    ScoreSummary.model_construct(), foreign, actual_admission=admission
                )
            case "selection":
                select_best_approved(
                    [],
                    foreign,
                    run_id=snapshot.run_id,
                    schema_version=snapshot.schema_version,
                    actual_admission=admission,
                )
            case unreachable:
                assert_never(unreachable)


@pytest.fixture
def dynamic(configured):
    """Construct only integrity bindings; no synthetic seed review is transferred."""
    admission, snapshot, llm, _, _, _ = configured
    snapshot = snapshot.model_copy(update={"snapshot_id": "generated-after-freeze"})
    original = next(iter(admission.review_resolvers.values()))
    constructed = []

    def factory(frozen, pinned_rubric):
        resolver = SourceBoundReviewResolver(
            frozen, pinned_rubric, sources=original._sources, reviews=()
        )
        constructed.append(resolver)
        return resolver

    return (
        replace(admission, review_resolvers={}, review_resolver_for=factory),
        snapshot,
        llm,
        constructed,
    )


def test_generated_binding_is_cached_without_minting_reviews(dynamic, configured):
    # Given: a generated identity and an independent seed review/receipt.
    admission, snapshot, _, constructed = dynamic
    _, _, _, request, receipt, review = configured
    core = admission.registry.rubric("core-0.1.0")
    finance = admission.registry.rubric("finance-0.1.0")
    # When: graph freeze, repeated evaluation and the other rubric verify.
    first = admission.verify_snapshot(snapshot, core)
    repeated = admission.verify_snapshot(snapshot.model_copy(deep=True), dict(core))
    other = admission.verify_snapshot(snapshot, finance)
    result = admission.resolve_review(
        snapshot, core, request, receipt, subject=review.subject
    )
    # Then: exact bindings are reused, but a seed review is not operational approval.
    assert first is repeated is constructed[0]
    assert other is constructed[1] and other is not first
    assert len(constructed) == 2
    assert result is None


@pytest.mark.parametrize("configured_key", [True, False])
def test_static_map_precedes_factory_and_absence_still_denies(
    configured, configured_key, offline
):
    # Given: a factory that must never be used for an explicitly configured key.
    admission, snapshot, _, _, _, _ = configured
    original = next(iter(admission.review_resolvers.values()))
    admission = replace(
        admission,
        review_resolvers=admission.review_resolvers if configured_key else {},
        review_resolver_for=offline if configured_key else None,
    )
    rubric = admission.registry.rubric("core-0.1.0")
    # When / Then: old static semantics work and default None has no authority.
    if configured_key:
        assert admission.verify_snapshot(snapshot, rubric) is original
    else:
        with pytest.raises(ValueError, match="configured source-bound"):
            admission.verify_snapshot(snapshot, rubric)


@pytest.mark.parametrize(
    "change", ["snapshot", "rubric", "source", "source_payload", "type"]
)
def test_invalid_factory_binding_denied_before_allowance(
    configured,
    change: Literal["snapshot", "rubric", "source", "source_payload", "type"],
    monkeypatch,
    offline,
):
    # Given: operator construction must still satisfy exact source/snapshot closure.
    admission, snapshot, llm, _, _, _ = configured
    original = next(iter(admission.review_resolvers.values()))

    def factory(frozen, pinned_rubric):
        sources = original._sources
        match change:
            case "snapshot":
                frozen.evidence_revision += 1
            case "rubric":
                pinned_rubric["status"] = "forged"
            case "source":
                sources = {}
            case "source_payload":
                sources = deepcopy(original._sources)
                next(iter(sources.values())).source.title = "foreign source"
            case "type":
                return None
            case unreachable:
                assert_never(unreachable)
        return SourceBoundReviewResolver(
            frozen, pinned_rubric, sources=sources, reviews=()
        )

    admission = replace(admission, review_resolvers={}, review_resolver_for=factory)
    monkeypatch.setattr(llm, "allowance_for", offline)
    monkeypatch.setattr(llm.runtime, "execute", offline)
    # When / Then: no malformed resolver can authorize evaluation.
    with pytest.raises(ValueError):
        admission.verify_evaluator(
            snapshot, admission.registry.rubric("core-0.1.0"), llm, "market"
        )
    assert llm.runtime.ledger.snapshot()["calls"] == 0
    assert admission._verified_resolvers == {}


@pytest.mark.parametrize("version", ["core-0.1.0", "finance-0.1.0"])
def test_seen_snapshot_identity_rejects_changed_payload(dynamic, version):
    # Given: the first full snapshot digest is pinned across every rubric.
    admission, snapshot, llm, constructed = dynamic
    admission.verify_snapshot(snapshot, admission.registry.rubric("core-0.1.0"))
    changed = snapshot.model_copy(deep=True)
    next(iter(changed.evidence.values())).claim = "Changed under the same identity"
    # When / Then: even a factory able to construct a new resolver is not called.
    with pytest.raises(ValueError, match="snapshot identity changed"):
        admission.verify_snapshot(changed, admission.registry.rubric(version))
    assert len(constructed) == 1
    assert llm.runtime.ledger.snapshot()["calls"] == 0


@pytest.mark.parametrize(
    "change", ["factory", "context", "rubric", "source", "static_map"]
)
def test_cached_binding_rechecks_authority_and_original_bytes(
    dynamic,
    tmp_path,
    monkeypatch,
    offline,
    change: Literal["factory", "context", "rubric", "source", "static_map"],
):
    # Given: a previously successful dynamic binding.
    admission, snapshot, llm, constructed = dynamic
    rubric = admission.registry.rubric("core-0.1.0")
    admission.verify_snapshot(snapshot, rubric)
    match change:
        case "factory":
            object.__setattr__(admission, "review_resolver_for", offline)
        case "context":
            admission.run_input.countries.append("foreign")
        case "rubric":
            rubric["status"] = "approved"
        case "source":
            (tmp_path / "capture/raw/company.html").write_bytes(b"changed")
        case "static_map":
            object.__setattr__(
                admission,
                "review_resolvers",
                {(snapshot.snapshot_id, "core-0.1.0"): constructed[0]},
            )
        case unreachable:
            assert_never(unreachable)
    monkeypatch.setattr(llm, "allowance_for", offline)
    monkeypatch.setattr(llm.runtime, "execute", offline)
    # When / Then: a cached resolver is not a cached PASS.
    with pytest.raises(ValueError):
        admission.verify_evaluator(snapshot, rubric, llm, "market")
    assert len(constructed) == 1
    assert llm.runtime.ledger.snapshot()["calls"] == 0


def test_factory_context_mutation_is_rejected_before_caching(dynamic):
    # Given: the trusted callable cannot silently change its admission context.
    admission, snapshot, llm, constructed = dynamic
    factory = admission.review_resolver_for

    def changing_factory(frozen, rubric):
        admission.run_input.countries.append("foreign")
        return factory(frozen, rubric)

    admission = replace(admission, review_resolver_for=changing_factory)
    # When / Then: post-construction context checks prevent both caching and calls.
    with pytest.raises(ValueError, match="context changed"):
        admission.verify_evaluator(
            snapshot, admission.registry.rubric("core-0.1.0"), llm, "market"
        )
    assert len(constructed) == 1
    assert admission._verified_resolvers == {}
    assert llm.runtime.ledger.snapshot()["calls"] == 0


def test_concurrent_verification_constructs_one_resolver(dynamic):
    # Given: both workers start together; factory completion is event-gated.
    admission, snapshot, _, constructed = dynamic
    factory = admission.review_resolver_for
    start = Barrier(3, timeout=5)
    entered, release = Event(), Event()

    def blocked_factory(frozen, rubric):
        entered.set()
        assert release.wait(timeout=5)
        return factory(frozen, rubric)

    admission = replace(admission, review_resolver_for=blocked_factory)
    rubric = admission.registry.rubric("core-0.1.0")

    def verify():
        start.wait()
        return admission.verify_snapshot(snapshot, rubric)

    # When: simultaneous verifications race for the same exact binding.
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(verify), pool.submit(verify)
        try:
            start.wait()
            assert entered.wait(timeout=5)
        finally:
            release.set()
        results = (first.result(timeout=5), second.result(timeout=5))
    # Then: one construction and one resolver identity, without timing sleeps.
    assert len(constructed) == 1
    assert results[0] is results[1] is constructed[0]


@pytest.mark.parametrize("scope", ["actual", "controlled_response", "actual_replay"])
@pytest.mark.parametrize("transport_kind", ["native", "mock", "subclass"])
def test_scope_transport_matrix(
    configured,
    scope,
    transport_kind: Literal["native", "mock", "subclass"],
    monkeypatch,
    offline,
):
    # Given: only replay supplies separate operator-owned original-capture authority.
    admission, snapshot, llm, _, _, _ = configured
    admission = replace(
        admission,
        execution_scope=scope,
        replay_verifier=(lambda: True) if scope == "actual_replay" else None,
    )
    match transport_kind:
        case "native":
            llm.transport._http_transport = None
        case "mock":
            pass
        case "subclass":

            class DerivedTransport(httpx.MockTransport):
                pass

            llm.transport._http_transport = DerivedTransport(offline)
        case unreachable:
            assert_never(unreachable)
    monkeypatch.setattr(llm, "allowance_for", offline)
    monkeypatch.setattr(llm.runtime, "execute", offline)
    allowed = (scope == "actual" and transport_kind == "native") or (
        scope in ("controlled_response", "actual_replay") and transport_kind == "mock"
    )
    # When / Then: verification itself never allocates or executes a provider call.
    if allowed:
        assert (
            admission.verify_evaluator(
                snapshot, admission.registry.rubric("core-0.1.0"), llm, "market"
            ).execution_mode
            == "live"
        )
    else:
        with pytest.raises(ValueError, match="execution scope mismatch"):
            admission.verify_evaluator(
                snapshot, admission.registry.rubric("core-0.1.0"), llm, "market"
            )
    assert llm.runtime.ledger.snapshot()["calls"] == 0


@pytest.mark.parametrize("verifier", [None, True, lambda: False, lambda: 1])
def test_replay_requires_callable_exact_true_authority(configured, verifier):
    # Given / When / Then: capture booleans and truthy answers are not authority.
    admission, _, llm, _, _, _ = configured
    with pytest.raises(ValueError, match="verified actual-origin replay required"):
        replace(admission, execution_scope="actual_replay", replay_verifier=verifier)
    assert llm.runtime.ledger.snapshot()["calls"] == 0


@pytest.mark.parametrize("scope", ["actual", "controlled_response"])
def test_replay_callback_is_rejected_outside_replay(configured, scope, offline):
    # Given / When / Then: an out-of-scope callback is rejected without invocation.
    admission, _, llm, _, _, _ = configured
    with pytest.raises(ValueError, match="requires actual_replay scope"):
        replace(admission, execution_scope=scope, replay_verifier=offline)
    assert llm.runtime.ledger.snapshot()["calls"] == 0


@pytest.mark.parametrize("change", ["result", "identity"])
def test_replay_verifier_rechecked_before_allowance(
    configured, change, monkeypatch, offline
):
    # Given: replay verification passed at construction, then authority changes.
    admission, snapshot, llm, _, _, _ = configured
    valid = True
    admission = replace(
        admission, execution_scope="actual_replay", replay_verifier=lambda: valid
    )
    if change == "identity":
        object.__setattr__(admission, "replay_verifier", offline)
    else:
        valid = False
    monkeypatch.setattr(llm, "allowance_for", offline)
    monkeypatch.setattr(llm.runtime, "execute", offline)
    # When / Then: no request, even with unchanged scope and exact MockTransport.
    with pytest.raises(ValueError, match="replay"):
        admission.verify_evaluator(
            snapshot, admission.registry.rubric("core-0.1.0"), llm, "market"
        )
    assert llm.runtime.ledger.snapshot()["calls"] == 0


def test_verified_replay_uses_only_mock_wire(configured):
    # Given: a synthetic test verifier stands in for the later pinned runner check.
    admission, snapshot, llm, _, _, _ = configured
    admission = replace(
        admission, execution_scope="actual_replay", replay_verifier=lambda: True
    )
    admission.verify_evaluator(
        snapshot, admission.registry.rubric("core-0.1.0"), llm, "market"
    )
    # When: exercise the real adapter surface with sockets blocked by the fixture.
    result = llm.generate(system="Synthetic", user="Synthetic", output_schema=Output)
    # Then: one recorded mock request, not actual-provider evidence.
    assert result.answer == 4
    assert llm.runtime.ledger.snapshot()["calls"] == 1
