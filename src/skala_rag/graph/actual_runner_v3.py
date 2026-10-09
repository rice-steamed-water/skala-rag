"""Direct Python composition of the original actual v3 consumers.

Default calls only preflight. Execution needs a separate operator authority and
explicit ``execute=True``. A completed workflow is never publication approval.
"""

import json
import os
from collections.abc import Callable, Mapping
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import Literal, assert_never

import httpx
from pydantic import BaseModel, JsonValue, TypeAdapter

from skala_rag.agents.business_deal import evaluate_business_deal_approved
from skala_rag.agents.evaluation_v3_adapter import adapt_baseline_branch_result
from skala_rag.agents.evidence_research import EvidenceResearch
from skala_rag.agents.founder import evaluate_founder_approved
from skala_rag.agents.market import evaluate_market_approved
from skala_rag.agents.moat import evaluate_moat_approved
from skala_rag.agents.source_fact_verification import (
    SourceBoundReviewResolver,
    SourceFactError,
)
from skala_rag.agents.technology import evaluate_technology_approved
from skala_rag.contracts import (
    ArtifactMetadata,
    EvaluationSnapshot,
    RunManifest,
    ToolBudget,
)
from skala_rag.contracts.state import RunOutcome, WorkflowStatus
from skala_rag.contracts.v3 import BRANCH_DIMENSIONS
from skala_rag.graph.actual_inputs_v3 import (
    ActualAuthorityV3,
    ActualInputError,
    EvaluationInputsV3,
    canonical,
    digest,
    file_digest,
    load_inputs,
    open_index,
    prepare_candidates,
    review_resolver,
    verify_model_files,
    verify_sources,
)
from skala_rag.graph.actual_replay_v3 import (
    CapturedLocalEncoder,
    ReplayInputs,
    implementation_commitments,
    load_replay,
    role_scope,
)
from skala_rag.graph.candidate_workflow_v3 import run_candidate_report_v3
from skala_rag.graph.candidates_v3 import CandidateStagesV3
from skala_rag.graph.research_artifacts_v3 import EvidenceResearchBindingV3
from skala_rag.rag.adapter import IndexedRetriever
from skala_rag.rag.sqlite_index import SQLiteIndexStore
from skala_rag.rag.sqlite_retrieve import SQLiteDenseSearch
from skala_rag.reporting.pdf import PDFLayoutValidator, PDFRenderer, load_pdf_profile
from skala_rag.reporting.v3_runtime import build_report_nodes_v3
from skala_rag.scoring.approval_registry import pinned_approval_registry
from skala_rag.scoring.approved_consumers import ActualAdmissionV3, ApprovedPolicySource
from skala_rag.scoring.approved_policy import LiveScoringGates, ScoringRuntimeBinding
from skala_rag.scoring.catalog import load_policy
from skala_rag.tools.actual_wire_capture import ActualWireCapture
from skala_rag.tools.company_archive import _load_archive
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt, byte_bound_allowance
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    BudgetLedger,
    CallContext,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
    TransportFailure,
)
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM
from skala_rag.tools.structured_llm import APPROVED_MODEL

ROOT = Path(__file__).resolve().parents[3]
Branch = Literal["founder", "technology", "market", "moat", "business_deal"]
Scope = Literal["actual", "controlled_response", "actual_replay"]


def _write(out: Path, name: str, value) -> None:
    (out / name).write_bytes(canonical(value))


def _allowance(schema_version: str, system: str, user: str, schema: type[BaseModel]):
    return byte_bound_allowance(
        system,
        user,
        schema,
        schema_version=schema_version,
        max_output_tokens=2000,
        usd_per_input_token=Decimal("0.0000004"),
        usd_per_output_token=Decimal("0.0000016"),
    )


def _no_retry(_seconds: float) -> None:
    raise ActualInputError("TRANSPORT_RETRY_FORBIDDEN")


def _legacy_denied(*_args):
    raise ActualInputError("LEGACY_RESEARCH_FORBIDDEN")


class _Reviews:
    """Resolve exact new snapshots once, with no seed approval transplantation."""

    def __init__(self, authority, registry, out, expected=None):
        self.authority, self.registry, self.out = authority, registry, out
        self.expected = expected
        self.audit = {}
        self._lock = RLock()
        self._resolvers: dict[tuple[str, str], SourceBoundReviewResolver] = {}
        self.inputs: dict[str, EvaluationInputsV3] = {}
        self.requests: dict[str, dict] = {}

    def __call__(
        self, snapshot: EvaluationSnapshot, rubric: Mapping[str, JsonValue]
    ) -> SourceBoundReviewResolver:
        key = digest(canonical(snapshot.model_dump(mode="json")))
        version = str(rubric["rubric_version"])
        with self._lock:
            if key not in self.inputs:
                try:
                    supplied = self.authority.evaluation_inputs_for(
                        snapshot.model_copy(deep=True)
                    )
                    if type(supplied) is not EvaluationInputsV3:
                        raise ActualInputError("EVALUATION_INPUTS_MISSING")
                    selections = {
                        v: review_resolver(
                            snapshot,
                            self.registry.rubric(v),
                            self.authority,
                            self.registry,
                        )
                        for v in ("core-0.1.0", "finance-0.1.0")
                    }
                    resolvers = {v: selected[0] for v, selected in selections.items()}
                    for v, resolver in resolvers.items():
                        resolver.verify_snapshot(snapshot, self.registry.rubric(v))
                        if any(
                            resolver.verify_source(sid) is None
                            for sid in snapshot.sources
                        ):
                            raise ActualInputError("SOURCE_AUTHORITY_MISSING")
                    receipts = (
                        [
                            (
                                "core-0.1.0",
                                supplied.review_request,
                                supplied.review_subject,
                                receipt,
                            )
                            for receipt in (
                                *supplied.founder_anchors.values(),
                                *supplied.technology_anchors.values(),
                                *supplied.moat_anchors.values(),
                            )
                        ]
                        + [
                            ("core-0.1.0", r.request, r.subject, r.receipt)
                            for r in supplied.market_reviews.values()
                        ]
                        + [
                            (
                                "finance-0.1.0",
                                supplied.review_request,
                                supplied.review_subject,
                                receipt,
                            )
                            for receipt in supplied.financial_facts
                        ]
                    )
                    if not receipts:
                        raise ActualInputError("RATING_REVIEW_MISSING")
                    for v, request, subject, receipt in receipts:
                        resolved = resolvers[v].resolve_review(
                            request, receipt, subject=subject
                        )
                        if resolved is None or resolved.decision != "accepted":
                            raise ActualInputError("RATING_REVIEW_MISSING")
                    audit = {
                        "evaluation_inputs": TypeAdapter(
                            EvaluationInputsV3
                        ).dump_python(supplied, mode="json"),
                        "reviews": {
                            v: [r.model_dump(mode="json") for r in selected[1]]
                            for v, selected in selections.items()
                        },
                    }
                    if self.expected is not None and self.expected.get(key) != audit:
                        raise ActualInputError("REPLAY_REVIEW_MISMATCH")
                except (ValueError, TypeError, KeyError):
                    if len(self.requests) < 40:
                        self.requests[key] = {
                            "snapshot": snapshot.model_dump(mode="json"),
                            "rubrics": {
                                v: digest(canonical(self.registry.rubric(v)))
                                for v in ("core-0.1.0", "finance-0.1.0")
                            },
                            "reason": "independently_authenticated_review_required",
                        }
                        _write(self.out, "missing-review-requests.json", self.requests)
                    raise ActualInputError("SNAPSHOT_REVIEW_MISSING") from None
                self.inputs[key] = supplied
                self.audit[key] = audit
                _write(self.out, "reviews.json", self.audit)
                for v, resolver in resolvers.items():
                    self._resolvers[(key, v)] = resolver
            return self._resolvers[(key, version)]

    def for_snapshot(self, snapshot):
        self(snapshot, self.registry.rubric("core-0.1.0"))
        return self.inputs[digest(canonical(snapshot.model_dump(mode="json")))]


def run_actual(
    output_dir: Path,
    *,
    inputs: Path,
    inputs_sha256: str,
    authority: ActualAuthorityV3,
    execute: bool = False,
    api_key: str | None = None,
    execution_scope: Literal["actual", "controlled_response"] = "actual",
    transport_for: Callable[[str], httpx.MockTransport] | None = None,
) -> Path:
    """Preflight by default; explicitly execute original consumers when admitted.

    Native actual requires ``transport_for=None`` and an explicitly passed key.
    Controlled wires are labelled synthetic and cannot be actual replay origins.
    No environment, CLI, credential lookup, retry or automatic resume exists.
    """
    return _run(
        output_dir,
        inputs=inputs,
        inputs_sha256=inputs_sha256,
        authority=authority,
        execute=execute,
        api_key=api_key,
        scope=execution_scope,
        transport_for=transport_for,
    )


def run_replay(
    output_dir: Path,
    *,
    inputs: Path,
    inputs_sha256: str,
    authority: ActualAuthorityV3,
    original_capture: Path,
    original_capture_sha256: str,
) -> Path:
    """Replay only an externally pinned actual-origin campaign, without a key."""
    return _run(
        output_dir,
        inputs=inputs,
        inputs_sha256=inputs_sha256,
        authority=authority,
        execute=True,
        api_key=None,
        scope="actual_replay",
        transport_for=None,
        original_capture=original_capture,
        original_capture_sha256=original_capture_sha256,
    )


def _run(
    output_dir,
    *,
    inputs,
    inputs_sha256,
    authority,
    execute,
    api_key,
    scope: Scope,
    transport_for,
    original_capture=None,
    original_capture_sha256=None,
) -> Path:
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=False)
    runtime = None
    capture = ActualWireCapture()
    journal = ReplayInputs()
    saved = None
    expected_reviews = None
    observations: dict[str, int] = {
        "observed_native_responses": 0,
        "observed_controlled_responses": 0,
        "replayed_responses": 0,
        "uncertain_transport_dispatch_failures": 0,
    }
    receipt: dict[str, JsonValue] = {
        "execution_scope": scope,
        "synthetic": scope == "controlled_response",
        "actual_provider_calls": 0,
        "reserved_model_attempts": 0,
        "publication_allowed": False,
        "final_allowed": False,
        "status": "preflight_blocked",
        "reason": None,
    }
    try:
        if type(execute) is not bool:
            raise ActualInputError("EXPLICIT_EXECUTION_BOOLEAN_REQUIRED")
        if type(authority) is not ActualAuthorityV3:
            raise ActualInputError("EXTERNAL_AUTHORITY_REQUIRED")
        if not all(
            callable(callback)
            for callback in (
                authority.authenticate_inputs,
                authority.live_gate_verifier,
                authority.reviews_for,
                authority.evaluation_inputs_for,
                authority.support_check,
                authority.applicability_assessments,
                authority.applicability_check,
                authority.verify_replay_origin,
            )
        ):
            raise ActualInputError("EXTERNAL_AUTHORITY_REQUIRED")
        if (
            type(authority.campaign_id) is not str
            or not authority.campaign_id
            or authority.campaign_id != authority.campaign_id.strip()
        ):
            raise ActualInputError("STABLE_CAMPAIGN_ID_REQUIRED")
        if (
            not authority.campaign_directory.is_absolute()
            or authority.campaign_directory.resolve().is_relative_to(out)
        ):
            raise ActualInputError("PERSISTENT_CAMPAIGN_DIRECTORY_REQUIRED")
        receipt["campaign_id"] = authority.campaign_id
        receipt["campaign_marker"] = str(authority.campaign_marker)
        if scope != "actual_replay" and authority.campaign_marker.exists():
            raise ActualInputError("CAMPAIGN_ALREADY_STARTED")
        if (
            scope == "actual"
            and transport_for is not None
            or scope == "controlled_response"
            and execute
            and (transport_for is None or api_key is not None)
        ):
            raise ActualInputError("TRANSPORT_SCOPE_MISMATCH")
        packet = load_inputs(inputs, inputs_sha256)
        commitments = implementation_commitments(ROOT)
        _write(out, "commitments.json", commitments)
        if scope == "actual_replay":
            if original_capture is None or original_capture_sha256 is None:
                raise ActualInputError("REPLAY_PIN_REQUIRED")
            saved, capture, journal = load_replay(
                original_capture,
                original_capture_sha256,
                inputs_sha256=inputs_sha256,
                commitments=commitments,
                packet=packet,
            )
            original_campaign = json.loads(
                (original_capture.parent / "campaign.json").read_bytes()
            )
            if (
                original_campaign["campaign_id"] != authority.campaign_id
                or original_campaign["approval_reference"] != packet.approval_reference
            ):
                raise ActualInputError("REPLAY_CAMPAIGN_MISMATCH")
            expected_reviews = json.loads(
                (original_capture.parent / "reviews.json").read_bytes()
            )
        snapshot, metadata = open_index(packet)
        verify_sources(
            snapshot.bundle.sources, authority, packet.run_input.corpus_version
        )
        verify_model_files(packet)
        if packet.discovery.data is None:
            raise ActualInputError("DISCOVERY_NOT_FOUND")
        verify_sources(
            packet.discovery.data.sources, authority, packet.run_input.corpus_version
        )
        for candidate in packet.candidates.values():
            verify_sources(
                candidate.sources, authority, packet.run_input.corpus_version
            )
        for archive in packet.archives:
            _load_archive(archive.root.absolute(), archive.index_sha256)
        verify_sources(
            {sid: trusted.source for sid, trusted in authority.sources.items()},
            authority,
            packet.run_input.corpus_version,
        )
        source_commitments = {
            sid: {
                "original_path": str(trusted.path.resolve()),
                "original_sha256": file_digest(trusted.path),
                "source": trusted.source.model_dump(mode="json"),
                "chunks": {
                    c.chunk_id: digest(canonical(c.model_dump(mode="json")))
                    for c in trusted.approved_chunks
                },
            }
            for sid, trusted in authority.sources.items()
        }
        if saved is not None and saved["files"]["source-commitments.json"] != digest(
            canonical(source_commitments)
        ):
            raise ActualInputError("REPLAY_SOURCE_COMMITMENT_MISMATCH")
        _write(out, "source-commitments.json", source_commitments)
        # Replay source/index/model closure is checked before any callback.
        if saved is not None and authority.verify_replay_origin(saved) is not True:
            raise ActualInputError("REPLAY_AUTHORITY_REJECTED")
        if authority.authenticate_inputs(packet.model_copy(deep=True)) is not True:
            raise ActualInputError("INPUT_AUTHORITY_REJECTED")
        with journal.installed():
            started = journal.now()
            schema = packet.run_input.schema_version
            limits = RuntimeLimits(
                schema_version=schema,
                max_calls=40,
                tool_max_calls={"openai": 40, "retrieve": 40},
                max_input_tokens=2_000_000,
                max_output_tokens=120_000,
                max_cost_usd=Decimal("1"),
            )
            runtime_policy = RuntimePolicy(
                schema_version=schema,
                execution_mode="live",
                retry_delays_seconds=(),
                live_approval_reference=packet.approval_reference,
                timing_approval_reference=packet.approval_reference,
            )
            runtime = AdapterRuntime(
                policy=runtime_policy,
                ledger=BudgetLedger(limits),
                clock=journal,
                sleep=_no_retry,
            )
            budget = ToolBudget(
                schema_version=schema,
                max_calls=40,
                max_retries=0,
                timeout_seconds=60,
                deadline=started + timedelta(hours=1),
            )
            readiness = Readiness(
                schema_version=schema,
                required=True,
                configured=True,
                credential_required=scope == "actual" and api_key is not None,
                credential_present=scope == "actual"
                and api_key is not None
                and bool(api_key.strip()),
                model_required=True,
                model_available=True,
                index_required=True,
                index_available=True,
            )
            allowance = Allowance(
                schema_version=schema,
                input_tokens=1,
                output_tokens=2000,
                max_cost_usd=Decimal("0.0032004"),
            )
            gates = LiveScoringGates(
                run_id=packet.run_id,
                policy_version="v3-operational-1.0.0",
                provider="openai",
                open_decisions=[],
                readiness=readiness,
                limits=limits,
                allowance=allowance,
                runtime_readiness_reference=packet.approval_reference,
                call_budget_reference=packet.approval_reference,
                cost_budget_reference=packet.approval_reference,
            )
            call = CallContext(
                schema_version=schema,
                call_id=f"{packet.run_id}:preflight",
                run_id=packet.run_id,
                candidate_id=None,
                tool_name="openai",
                node="actual_preflight",
            )
            registry = pinned_approval_registry(ROOT)
            approved_source = ApprovedPolicySource(
                path=ROOT / "configs/scoring.v3.json",
                approvals=registry.policy_approvals(),
                approval_verifier=registry.verify_policy,
                execution_mode="live",
                live_gates=gates,
                live_gate_verifier=authority.live_gate_verifier,
            )
            reviews = _Reviews(
                authority,
                registry,
                out,
                expected=expected_reviews,
            )
            admission = ActualAdmissionV3(
                source=approved_source,
                runtime_binding=ScoringRuntimeBinding(
                    runtime=runtime,
                    gates=gates,
                    policy=runtime_policy,
                    call=call,
                    budget=budget,
                    readiness=readiness,
                    allowance=allowance,
                    provider="openai",
                    tool_name="openai",
                ),
                registry=registry,
                run_input=packet.run_input,
                index_version=snapshot.index_version,
                review_resolvers={},
                execution_scope=scope,
                review_resolver_for=reviews,
                replay_verifier=(
                    (
                        lambda: (
                            capture.verify() is True
                            and authority.verify_replay_origin(saved) is True
                        )
                    )
                    if saved is not None
                    else None
                ),
            )
            prepared, discovery_receipt = prepare_candidates(
                packet,
                authority,
                clock=journal,
                budget=budget,
                index_version=snapshot.index_version,
            )
            _write(out, "inputs.json", packet.model_dump(mode="json"))
            _write(out, "discovery-normalization.json", discovery_receipt)
            eligibility_payloads = {
                p.candidate.candidate_id: p.eligibility.model_dump(mode="json")
                for p in prepared
            }
            eligible = [p for p in prepared if p.snapshot is not None]
            if not eligible:
                _write(out, "eligibility.json", eligibility_payloads)
                if any(p.eligibility.status == "unknown" for p in prepared):
                    raise ActualInputError("ELIGIBILITY_FACTS_MISSING")
                raise ActualInputError("NO_ELIGIBLE_CANDIDATES")
            for item in eligible:
                if item.snapshot is None:
                    raise ActualInputError("ELIGIBILITY_FACTS_MISSING")
                for version in ("core-0.1.0", "finance-0.1.0"):
                    admission.verify_snapshot(item.snapshot, registry.rubric(version))
                for observation in packet.candidates[
                    item.candidate.candidate_id
                ].observations:
                    reviewed = admission.resolve_review(
                        item.snapshot,
                        registry.rubric("core-0.1.0"),
                        observation.review_request.encode(),
                        observation.observation,
                        subject=observation.review_subject,
                    )
                    if reviewed is None or reviewed.decision != "accepted":
                        raise ActualInputError("ELIGIBILITY_REVIEW_MISSING")
                if not packet.candidates[item.candidate.candidate_id].initial_gaps:
                    raise ActualInputError("INITIAL_RESEARCH_PLAN_MISSING")
            _write(out, "eligibility.json", eligibility_payloads)
            if not execute:
                receipt.update(
                    status="preflight_ready", reason="execution_not_requested"
                )
                return out
            if scope == "actual" and (api_key is None or not api_key.strip()):
                raise ActualInputError("EXPLICIT_CREDENTIAL_REQUIRED")
            if saved is None:
                # Exclusive create before any encoder/provider action. Never remove
                # the marker on success or failure: relaunch needs a new approval.
                campaign = {
                    "campaign_id": authority.campaign_id,
                    "approval_reference": packet.approval_reference,
                    "campaign_marker": str(authority.campaign_marker),
                    "inputs_sha256": inputs_sha256,
                    "run_id": packet.run_id,
                    "output": str(out),
                    "scope": scope,
                    "limits": limits.model_dump(mode="json"),
                    "started_at": started.isoformat(),
                    "deadline": (started + timedelta(hours=1)).isoformat(),
                    "input_usd_per_token": "0.0000004",
                    "output_usd_per_token": "0.0000016",
                    "full_token_caps_upper_bound_usd": "0.992",
                    "transport_retries": 0,
                }
                authority.campaign_directory.mkdir(parents=True, exist_ok=True)
                with authority.campaign_marker.open("x", encoding="utf-8") as marker:
                    marker.write(canonical(campaign).decode())
                    marker.flush()
                    os.fsync(marker.fileno())
                directory_fd = os.open(authority.campaign_marker.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                _write(out, "campaign.json", campaign)
            receipt["status"] = "execution_failed"
            policy = admission.load_policy().operational
            catalog = load_policy(
                ROOT / "configs/scoring.draft.json", execution_mode="fixture"
            )
            paid_lock = RLock()
            original_execute = runtime.execute

            def serialized(call_context, **kwargs):
                with paid_lock:
                    if reviews.requests:
                        raise ActualInputError("SNAPSHOT_REVIEW_MISSING")
                    if saved is not None and capture.verify() is not True:
                        raise ActualInputError("REPLAY_WIRE_CHANGED")
                    if call_context.tool_name == "openai":
                        original_transport = kwargs["transport"]

                        class ObservedDispatch:
                            retry_owner = original_transport.retry_owner

                            def __call__(self, *, timeout_seconds):
                                before = sum(
                                    observations[key]
                                    for key in (
                                        "observed_native_responses",
                                        "observed_controlled_responses",
                                        "replayed_responses",
                                    )
                                )
                                try:
                                    return original_transport(
                                        timeout_seconds=timeout_seconds
                                    )
                                except TransportFailure:
                                    after = sum(
                                        observations[key]
                                        for key in (
                                            "observed_native_responses",
                                            "observed_controlled_responses",
                                            "replayed_responses",
                                        )
                                    )
                                    if before == after:
                                        observations[
                                            "uncertain_transport_dispatch_failures"
                                        ] += 1
                                    raise

                        kwargs["transport"] = ObservedDispatch()
                    try:
                        return original_execute(call_context, **kwargs)
                    finally:
                        _write(out, "ledger.json", runtime.ledger.snapshot())
                        _write(out, "request-observations.json", observations)

            setattr(runtime, "execute", serialized)
            attempts = []

            def transport(role):
                wire = None
                if saved is not None:
                    wire = capture.replay_transport(role)
                elif transport_for is not None:
                    wire = transport_for(role)
                if (
                    scope == "actual"
                    and wire is not None
                    or scope != "actual"
                    and type(wire) is not httpx.MockTransport
                ):
                    raise ActualInputError("TRANSPORT_SCOPE_MISMATCH")
                record_wire = capture.observe(role) if saved is None else None

                def observe_wire(request: bytes, status: int, response: bytes):
                    # Reached only after native post returned a real response.
                    # Count before persistence so a capture write rejection does
                    # not turn an observed response into an invented uncertainty.
                    key = (
                        "replayed_responses"
                        if saved is not None
                        else "observed_native_responses"
                        if scope == "actual"
                        else "observed_controlled_responses"
                    )
                    observations[key] += 1
                    if record_wire is not None:
                        record_wire(request, status, response)

                attempt = OpenAIResponsesAttempt(
                    api_key=api_key if scope == "actual" else None,
                    prompt_version="actual-v3-1",
                    schema_version=schema,
                    clock=journal,
                    http_transport=wire,
                    observe_wire=observe_wire,
                )
                attempts.append(attempt)
                return attempt

            def llm(role, cid):
                return RuntimeStructuredLLM(
                    runtime=runtime,
                    call=call.model_copy(
                        update={
                            "call_id": f"{packet.run_id}:{cid}:{role}",
                            "candidate_id": cid,
                            "node": (
                                "evidence_research"
                                if role == "evidence_research"
                                else f"{role}_evaluation"
                            ),
                        }
                    ),
                    budget=budget,
                    readiness=readiness,
                    transport=transport(role),
                    allowance_for=lambda s, u, t: _allowance(schema, s, u, t),
                )

            def evaluate(role: Branch, frozen):
                with role_scope(f"{frozen.candidate_id}/{role}"):
                    supplied = reviews.for_snapshot(frozen)
                    model = llm(role, frozen.candidate_id)
                    options = {
                        "review_request": supplied.review_request,
                        "review_subject": supplied.review_subject,
                    }
                    match role:
                        case "founder":
                            result = evaluate_founder_approved(
                                frozen,
                                actual_admission=admission,
                                llm=model,
                                founder_person_ids=supplied.founder_person_ids,
                                verified_person_by_evidence_id=(
                                    supplied.verified_person_by_evidence_id
                                ),
                                reviewed_anchors=supplied.founder_anchors,
                                **options,
                            )
                        case "technology":
                            result = evaluate_technology_approved(
                                frozen,
                                actual_admission=admission,
                                llm=model,
                                receipts=supplied.technology_anchors,
                                **options,
                            ).result
                        case "market":
                            result = evaluate_market_approved(
                                frozen,
                                actual_admission=admission,
                                llm=model,
                                rubric=registry.rubric("core-0.1.0"),
                                target_market=supplied.market_target,
                                market_links=supplied.market_links,
                                reviewed_observations=supplied.market_reviews,
                            )
                        case "moat":
                            return evaluate_moat_approved(
                                frozen,
                                actual_admission=admission,
                                llm=model,
                                reviewed_anchors=supplied.moat_anchors,
                                **options,
                            )
                        case "business_deal":
                            return evaluate_business_deal_approved(
                                frozen,
                                admission=admission,
                                llm=model,
                                rubric=registry.rubric("finance-0.1.0"),
                                financial_facts=supplied.financial_facts,
                                **options,
                            )
                        case unreachable:
                            assert_never(unreachable)
                    return adapt_baseline_branch_result(
                        result,
                        branch_id=role,
                        snapshot=frozen,
                        criteria=policy.criteria,
                        industry_evidence_dimensions=packet.industry_evidence_dimensions,
                        execution_mode="live",
                    )

            retrieve = IndexedRetriever(
                snapshot=snapshot,
                backend=SQLiteDenseSearch(
                    store=SQLiteIndexStore(packet.index.path),
                    metadata=metadata,
                    snapshot=snapshot,
                    encoder=CapturedLocalEncoder(packet, journal),
                ),
                runtime=runtime,
                readiness=readiness,
                budget=budget,
                allowance=Allowance(
                    schema_version=schema,
                    input_tokens=0,
                    output_tokens=0,
                    max_cost_usd=Decimal(0),
                ),
                run_id=packet.run_id,
                schema_version=schema,
                tool_name="retrieve",
            )
            research_llm = llm("evidence_research", eligible[0].candidate.candidate_id)
            producer = EvidenceResearch(
                retrieve=retrieve,
                rag_required=True,
                llm=research_llm,
                initial_plan=lambda candidate: (
                    packet.candidates[candidate.candidate_id].initial_gaps
                ),
                run_id=packet.run_id,
                corpus_version=packet.run_input.corpus_version,
                index_version=snapshot.index_version,
                as_of=packet.run_input.as_of,
                top_k=3,
                allowed_source_ids=tuple(packet.allowed_source_ids),
                clock=journal,
                schema_version=schema,
                execution_mode="live",
            )
            by_id = {p.candidate.candidate_id: p for p in prepared}

            def research(candidate):
                cid = candidate["candidate_id"]
                research_llm.call = research_llm.call.model_copy(
                    update={"candidate_id": cid}
                )
                return by_id[cid].seed

            stages = CandidateStagesV3(
                discover=lambda: [p.candidate for p in prepared],
                normalize=lambda candidates: candidates,
                research=research,
                eligibility=lambda candidate, _: (
                    by_id[candidate["candidate_id"]].eligibility
                ),
                collect=_legacy_denied,
                freeze=_legacy_denied,
                evidence_research=EvidenceResearchBindingV3(
                    research=producer,
                    budget=budget,
                    run_input=packet.run_input,
                    run_id=packet.run_id,
                    schema_version=schema,
                    index_version=snapshot.index_version,
                    allowed_source_ids=packet.allowed_source_ids,
                    industry_evidence_ids=packet.industry_evidence_ids,
                    actual_admission=admission,
                ),
            )
            generator, judge = build_report_nodes_v3(
                runtime=runtime,
                generator_call=call.model_copy(update={"node": "report_generator"}),
                judge_call=call.model_copy(update={"node": "report_judge"}),
                budget=budget,
                readiness=readiness,
                generator_transport=transport("generator"),
                judge_transport=transport("judge"),
                allowance_for=lambda s, u, t: _allowance(schema, s, u, t),
            )
            renders = []

            def check_pdf(draft, context, structural, judged):
                render = PDFRenderer(
                    profile=load_pdf_profile(ROOT / "configs/pdf.layout.v1.json"),
                    output_dir=out,
                    proof=lambda _: (structural, judged),
                    execution_mode="live",
                )(draft, load_pdf_profile(ROOT / "configs/pdf.layout.v1.json").version)
                # Preserve measured renderer eligibility. Publication permission
                # belongs exclusively to the runner receipt/manifest.
                renders.append(render)
                return PDFLayoutValidator()(draft, context, render)

            result, context, report = run_candidate_report_v3(
                stages,
                {
                    role: lambda s, role=role: evaluate(role, s)
                    for role in BRANCH_DIMENSIONS
                },
                run_input=packet.run_input,
                generate=generator,
                judge=judge,
                check_pdf=check_pdf,
                policy=policy,
                catalog=catalog,
                catalog_policy_version=catalog.policy_version,
                run_id=packet.run_id,
                schema_version=schema,
                support_check=authority.support_check,
                applicability_assessments=authority.applicability_assessments,
                applicability_check=authority.applicability_check,
                applicability_verifier=authority.applicability_verifier,
                industry_evidence_dimensions=packet.industry_evidence_dimensions,
                clock=journal.now,
                approved_policy_source=approved_source,
                actual_admission=admission,
            )
            states = {
                cid: owned["state"] for cid, owned in result.research_artifacts.items()
            }
            _write(out, "states.json", states)
            _write(out, "context.json", context.snapshot())
            _write(
                out,
                "candidate-result.json",
                TypeAdapter(type(result)).dump_python(result, mode="json"),
            )
            _write(
                out,
                "report-result.json",
                TypeAdapter(type(report)).dump_python(report, mode="json"),
            )
            if report.draft is not None:
                _write(out, "draft.json", report.draft.model_dump(mode="json"))
                (out / "draft.md").write_text(report.draft.markdown, encoding="utf-8")
            stable: dict[str, JsonValue] = {
                name: file_digest(out / name)
                for name in ("states.json", "context.json", "draft.json")
                if (out / name).exists()
            }
            if renders and renders[-1].artifact_path is not None:
                stable["pdf"] = file_digest(Path(renders[-1].artifact_path))
                _write(out, "render.json", renders[-1].model_dump(mode="json"))
            if saved is not None:
                capture.assert_consumed()
                journal.assert_consumed()
                if stable != saved["stable_hashes"]:
                    raise ActualInputError("REPLAY_ARTIFACT_MISMATCH")
            elif capture.verify():
                capture.save(out / "wire.json")
            observed = sum(
                criterion["status"] == "observed"
                for state in states.values()
                for evaluation in state.get("evaluations_v3", {}).values()
                for criterion in evaluation["criteria"]
            )
            complete = (
                report.status == "completed"
                and not report.warning
                and report.judgement is not None
                and report.judgement.verdict == "pass"
                and not any(
                    finding.severity == "stub" for finding in report.judgement.findings
                )
                and report.pdf_validation is not None
                and report.pdf_validation.valid
                and bool(result.scores)
                and observed > 0
                and not reviews.requests
                and not result.errors
                and all(
                    len(states[p.candidate.candidate_id].get("evaluations_v3", {})) == 6
                    for p in eligible
                )
            )
            receipt.update(
                status="completed" if complete else "execution_blocked",
                reason=(
                    "STUB_JUDGE_FORBIDDEN"
                    if report.judgement is not None
                    and any(
                        finding.severity == "stub"
                        for finding in report.judgement.findings
                    )
                    else None
                    if complete
                    else "original_consumers_not_complete"
                ),
                stable_hashes=stable,
                observed_criteria=observed,
                candidate_count=result.candidate_index,
                report_status=report.status,
                error_count=len(result.errors),
            )
            validations = {
                key: value
                for key, value in (
                    ("structural", report.validation),
                    ("judge", report.judgement),
                    ("pdf", report.pdf_validation),
                )
                if value is not None
            }
            artifacts = {
                path.name: ArtifactMetadata(
                    schema_version=schema,
                    artifact_path=path.name,
                    artifact_hash=f"sha256:{file_digest(path)}",
                )
                for path in out.iterdir()
                if path.is_file()
            }
            manifest = RunManifest(
                schema_version=schema,
                run_id=packet.run_id,
                run_input=packet.run_input,
                code_revision=None,
                uncommitted=True,
                policy_version=packet.run_input.policy_version,
                corpus_version=packet.run_input.corpus_version,
                prompt_versions={"actual_composition": "actual-v3-1"},
                model_versions={
                    "openai": APPROVED_MODEL,
                    "embedding": metadata.model_revision,
                },
                corpus_hash=metadata.corpus_hash,
                tool_status={"execution_scope": scope, "publication_allowed": False},
                budgets=limits.model_dump(mode="json"),
                usage={
                    **runtime.ledger.snapshot(),
                    **observations,
                    "reserved_model_attempts": runtime.ledger.snapshot()[
                        "tool_calls"
                    ].get("openai", 0),
                },
                artifacts=artifacts,
                validation_results=validations,
                workflow_status=WorkflowStatus.COMPLETED
                if complete
                else WorkflowStatus.FAILED,
                run_outcome=(
                    RunOutcome.RECOMMENDED
                    if result.selection.selected_candidate_id
                    else RunOutcome.NO_RECOMMENDATION
                )
                if complete
                else (
                    RunOutcome.TECHNICAL_FAILURE
                    if result.errors or report.status == "failed"
                    else RunOutcome.INSUFFICIENT_EVIDENCE
                ),
            )
            _write(out, "manifest.json", manifest.model_dump(mode="json"))
            if saved is None and (out / "wire.json").exists():
                _write(
                    out,
                    "capture.json",
                    {
                        "execution_scope": scope,
                        "inputs_sha256": inputs_sha256,
                        "publication_allowed": False,
                        "commitments": commitments,
                        "files": {
                            p.name: file_digest(p)
                            for p in out.iterdir()
                            if p.is_file() and p.name != "receipt.json"
                        },
                        "nondeterminism": journal.values,
                        "stable_hashes": stable,
                    },
                )
    except (ValueError, TypeError, KeyError, OSError) as exc:
        receipt["reason"] = (
            exc.code
            if isinstance(exc, ActualInputError)
            else "CAMPAIGN_ALREADY_STARTED"
            if isinstance(exc, FileExistsError)
            else "SOURCE_REVIEW_REJECTED"
            if isinstance(exc, SourceFactError)
            else "INPUT_OR_CONSUMER_REJECTED"
        )
    finally:
        if runtime is not None:
            ledger = runtime.ledger.snapshot()
            receipt["ledger"] = ledger
            receipt["reserved_model_attempts"] = ledger["tool_calls"].get("openai", 0)
            receipt["actual_provider_calls"] = (
                (
                    None
                    if observations["uncertain_transport_dispatch_failures"]
                    else observations["observed_native_responses"]
                )
                if scope == "actual"
                else 0
            )
            _write(out, "ledger.json", ledger)
            _write(
                out,
                "runtime-errors.json",
                {
                    key: error.model_dump(mode="json")
                    for key, error in runtime.error_history.items()
                },
            )
            _write(out, "nondeterminism.json", journal.values)
            if saved is None and not (out / "wire.json").exists() and capture.verify():
                capture.save(out / "wire.json")
        receipt.update(observations)
        _write(out, "request-observations.json", observations)
        _write(out, "receipt.json", receipt)
    return out
