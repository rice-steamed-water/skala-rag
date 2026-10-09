"""Controlled live plumbing only: synthetic index, wire, budgets and eligibility."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from typing import Literal, assert_never, get_args

import httpx
import pytest
from tests.integration.test_v3_evidence_snapshot_consumer import actual_research_case
from tests.unit import test_actual_admission_v3 as shared
from tests.unit.test_approved_policy import runtime_binding

from skala_rag.agents.evidence_research import EvidenceResearch
from skala_rag.graph.research_artifacts_v3 import (
    EvidenceResearchBindingV3,
    bind_evidence_research_v3,
    consume_outcome_v3,
    initialize_artifacts_v3,
    validate_artifacts_v3,
)
from skala_rag.graph.snapshot import freeze_snapshot
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM

offline_fixture = shared.offline
actual_admission_fixture = shared.configured


research_case = actual_research_case


@pytest.fixture(params=[False, True], ids=["controlled_response", "actual_replay"])
def case(research_case, request):
    binding, *rest = research_case
    admission = binding.actual_admission
    assert admission is not None
    if request.param:
        admission = replace(
            admission, execution_scope="actual_replay", replay_verifier=lambda: True
        )
    return replace(binding, actual_admission=admission), *rest


def pin(binding: EvidenceResearchBindingV3) -> EvidenceResearchBindingV3:
    assert isinstance(binding.research, EvidenceResearch)
    return bind_evidence_research_v3(
        binding.research,
        binding.budget,
        run_input=binding.run_input,
        run_id=binding.run_id,
        schema_version=binding.schema_version,
        index_version=binding.index_version,
        allowed_source_ids=binding.allowed_source_ids,
        actual_admission=binding.actual_admission,
    )


@pytest.mark.parametrize("callable_path", [False, True])
def test_admitted_original_producer_and_consumer_preserve_closure(case, callable_path):
    # Given: a complete admission and synthetic independently seeded eligibility.
    binding, candidate, eligibility, seed, requests = case
    producer = binding.research
    original = binding.research._retrieve
    binding = pin(binding)
    owned = initialize_artifacts_v3(seed, eligibility, candidate.model_dump(), binding)
    before = validate_artifacts_v3(owned["state"], binding, candidate.candidate_id)
    # When: invoke the original callable or full-outcome path once.
    if callable_path:
        result = binding.research(candidate, (), binding.budget)
        assert result.data is not None
        assert result.status == "ok" and result.data.evidence
    else:
        outcome = binding.research.run(candidate, (), binding.budget)
        assert outcome.status == "ok", [
            (e.error_code, e.message_redacted) for e in outcome.errors
        ]
        assert consume_outcome_v3(
            owned, outcome, binding, candidate.candidate_id, initial=True
        )
        after = validate_artifacts_v3(owned["state"], binding, candidate.candidate_id)
        assert after.evidence_revision == before.evidence_revision + 1
        added = set(after.evidence) - set(before.evidence)
        assert len(added) == 1
        evidence = after.evidence[added.pop()]
        path = evidence.provenance[0]
        record = after.retrieval_records[path.retrieval_id]
        assert (
            path.chunk_id in record.chunk_ids
            and evidence.evidence_id in record.evidence_ids
        )
        assert (
            record.arguments_without_secrets["index_identity"]
            == binding._index_identity
        )
        assert owned["batches"][0]["admission"] == "admitted"
        outcome.evidence.clear()
        assert (
            validate_artifacts_v3(owned["state"], binding, candidate.candidate_id)
            == after
        )
        assert binding.actual_admission is not None
        with pytest.raises(ValueError, match="configured source-bound"):
            binding.actual_admission.verify_snapshot(
                after, binding.actual_admission.registry.rubric("core-0.1.0")
            )
        frozen = freeze_snapshot(
            candidate.candidate_id,
            owned["state"],
            binding.run_input,
            run_id=binding.run_id,
            index_version=binding.index_version,
            schema_version=binding.schema_version,
            allowed_source_ids=binding.allowed_source_ids,
            industry_evidence_ids=binding.industry_evidence_ids,
            clock=binding.actual_admission.runtime_binding.runtime.clock.now,
        )
        frozen.evidence.clear()
        assert owned["state"]["snapshots"][after.snapshot_id] == after.model_dump(
            mode="json"
        )
    # Then: one controlled extraction; the original retrieval owner survives.
    assert len(requests) == 1 and producer._retrieve is original
    assert len(before.evidence) == len(seed.evidence)


BindingDamage = Literal[
    "missing",
    "boolean",
    "source_only",
    "foreign_runtime",
    "run",
    "schema",
    "corpus",
    "index",
    "cutoff",
    "fixture_source",
    "index_content",
    "foreign_llm",
    "native",
]


@pytest.mark.parametrize("damage", get_args(BindingDamage))
@pytest.mark.parametrize("after_pin", [False, True])
def test_binding_denies_unadmitted_or_foreign_context_before_execution(
    case,
    damage: BindingDamage,
    after_pin: bool,
):
    # Given
    binding, _, _, _, requests = case
    producer = binding.research
    runtime = producer._retrieve._runtime
    before = runtime.ledger.snapshot()
    if after_pin:
        binding = pin(binding)
    match damage:
        case "missing":
            binding = replace(binding, actual_admission=None)
        case "boolean":
            binding = replace(binding, actual_admission=True)
        case "source_only":
            assert binding.actual_admission is not None
            binding = replace(binding, actual_admission=binding.actual_admission.source)
        case "foreign_runtime":
            producer._retrieve._runtime = runtime_binding().runtime
        case "run":
            binding = replace(binding, run_id="foreign")
        case "schema" | "corpus" | "cutoff":
            field = {
                "schema": "schema_version",
                "corpus": "corpus_version",
                "cutoff": "as_of",
            }[damage]
            value = (
                binding.run_input.as_of - timedelta(days=1)
                if damage == "cutoff"
                else "foreign"
            )
            binding = replace(
                binding, run_input=binding.run_input.model_copy(update={field: value})
            )
        case "index":
            binding = replace(binding, index_version="foreign")
        case "fixture_source":
            next(
                iter(producer._retrieve._snapshot.bundle.sources.values())
            ).url = "fixture://forged"
        case "index_content":
            producer._retrieve._snapshot.bundle.chunks[0].text = "changed"
        case "foreign_llm":
            producer._llm.runtime = runtime_binding().runtime
        case "native":
            producer._llm.transport._http_transport = None
        case unreachable:
            assert_never(unreachable)
    # When / Then
    with pytest.raises(ValueError):
        if after_pin:
            binding.authorized_mode()
        else:
            pin(binding)
    assert not requests and runtime.ledger.snapshot() == before


@pytest.mark.parametrize("scope", ["actual", "controlled_response", "actual_replay"])
@pytest.mark.parametrize("mock_transport", [False, True])
def test_research_transport_matrix_rejects_foreign_scope_before_request(
    research_case, scope, mock_transport: bool
):
    # Given: native actual and exact mock controlled/replay transports are disjoint.
    binding, _, _, _, requests = research_case
    admission = replace(
        binding.actual_admission,
        execution_scope=scope,
        replay_verifier=(lambda: True) if scope == "actual_replay" else None,
    )
    binding = replace(binding, actual_admission=admission)
    llm = binding.research._llm
    if not mock_transport:
        llm.transport._http_transport = None
    else:
        assert type(llm.transport._http_transport) is httpx.MockTransport
    before = llm.runtime.ledger.snapshot()
    valid = mock_transport == (scope != "actual")
    # When / Then: admission inspection never executes a native request.
    if valid:
        assert pin(binding).authorized_mode() == "live"
    else:
        with pytest.raises(ValueError, match="admission/runtime binding mismatch"):
            pin(binding)
    assert not requests and llm.runtime.ledger.snapshot() == before


def test_replay_native_transport_rejected_before_observed_research_request(
    research_case,
):
    # Given: a previously valid, pinned replay binding loses its mock transport.
    binding, candidate, _, _, requests = research_case
    llm = binding.research._llm
    assert type(llm) is RuntimeStructuredLLM
    binding = pin(
        replace(
            binding,
            actual_admission=replace(
                binding.actual_admission,
                execution_scope="actual_replay",
                replay_verifier=lambda: True,
            ),
        )
    )
    before = llm.runtime.ledger.snapshot()
    llm.transport._http_transport = None
    # When / Then: no retrieval or native provider dispatch occurs.
    with pytest.raises(ValueError, match="admission/runtime binding mismatch"):
        binding.research.run(candidate, (), binding.budget)
    assert not requests and llm.runtime.ledger.snapshot() == before


OutcomeDamage = Literal[
    "source_uri",
    "chunk_uri",
    "evidence_uri",
    "mode",
    "record_run",
    "index",
    "schema",
    "provenance",
    "future",
    "missing_chunk",
    "receipt",
    "stale",
    "chunk_content",
    "source_content",
]


@pytest.mark.parametrize("damage", get_args(OutcomeDamage))
def test_rejected_live_outcome_is_diagnostic_and_atomic(
    case,
    damage: OutcomeDamage,
):
    # Given
    binding, candidate, eligibility, seed, requests = case
    producer = binding.research
    binding = pin(binding)
    owned = initialize_artifacts_v3(seed, eligibility, candidate.model_dump(), binding)
    outcome = binding.research.run(candidate, (), binding.budget)
    assert outcome.status == "ok", [
        (e.error_code, e.message_redacted) for e in outcome.errors
    ]
    evidence = next(iter(outcome.evidence.values()))
    match damage:
        case "source_uri":
            next(iter(outcome.sources.values())).url = "fixture://forged"
        case "chunk_uri":
            next(iter(outcome.chunks.values())).locator = "fixture://forged"
        case "evidence_uri":
            evidence.locator = "fixture://forged"
        case "mode" | "index":
            key = "execution_mode" if damage == "mode" else "index_identity"
            outcome.records[0].arguments_without_secrets[key] = "fixture"
        case "record_run":
            outcome.records[0].run_id = "foreign"
        case "schema":
            evidence.schema_version = "foreign"
        case "provenance":
            evidence.provenance[0].schema_version = "foreign"
        case "future":
            evidence.event_date = binding.run_input.as_of + timedelta(days=1)
        case "missing_chunk":
            outcome.chunks.clear()
        case "chunk_content":
            next(iter(outcome.chunks.values())).text += " forged"
        case "source_content":
            next(iter(outcome.sources.values())).content_hash = "forged"
        case "receipt":
            outcome.records[0].arguments_without_secrets["call_id"] = "foreign"
        case "stale":
            producer._plan = lambda _c: []
            binding.research.run(candidate, (), binding.budget)
        case unreachable:
            assert_never(unreachable)
    before = deepcopy(owned["state"])
    # When / Then: no State promotion, retry or second extraction.
    with pytest.raises(ValueError):
        consume_outcome_v3(
            owned, outcome, binding, candidate.candidate_id, initial=True
        )
    assert owned["state"] == before and len(requests) == 1
    assert owned["batches"][-1]["admission"] == "rejected_input"


@pytest.mark.parametrize("after_pin", [False, True])
def test_failed_replay_verifier_rejects_before_research_request(
    research_case, after_pin: bool
):
    # Given: a verifier whose original pinned closure can be revoked.
    binding, candidate, _, _, requests = research_case
    valid = True
    admission = replace(
        binding.actual_admission,
        execution_scope="actual_replay",
        replay_verifier=lambda: valid,
    )
    binding = replace(binding, actual_admission=admission)
    runtime = admission.runtime_binding.runtime
    before = runtime.ledger.snapshot()
    if after_pin:
        binding = pin(binding)
    valid = False
    # When / Then: rejection precedes retrieval, reservation and HTTP dispatch.
    with pytest.raises(ValueError, match="verified actual-origin replay"):
        if after_pin:
            binding.research.run(candidate, (), binding.budget)
        else:
            pin(binding)
    assert not requests and runtime.ledger.snapshot() == before
