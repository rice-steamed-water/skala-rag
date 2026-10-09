"""Approved consumers with explicit, non-serialized controller admission.

Without ActualAdmissionV3, live denial precedes all reads and callbacks.
Admission binds authority; semantic decisions still come from source reviewers.
"""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from types import MappingProxyType
from typing import Literal, assert_never

import httpx
from pydantic import JsonValue

from skala_rag.agents.moat_verification import _digest
from skala_rag.agents.source_fact_verification import (
    ReviewReceipt,
    SourceBoundReview,
    SourceBoundReviewResolver,
)
from skala_rag.contracts import EvaluationSnapshot, RunInput
from skala_rag.contracts.interfaces import Clock, StructuredLLM
from skala_rag.contracts.v3 import Evaluation, InvestmentDecision, ScoreSummary
from skala_rag.scoring.aggregate_v3 import (
    ApplicabilityVerifier,
    _aggregate_scores_v3,
)
from skala_rag.scoring.approval_registry import PinnedApprovalRegistry
from skala_rag.scoring.approved_policy import (
    ApprovalVerifier,
    ApprovedScoringPolicy,
    LiveGateVerifier,
    LiveScoringGates,
    PolicyApprovals,
    ScoringRuntimeBinding,
    load_approved_policy,
    observe_scoring_runtime,
)
from skala_rag.scoring.decision_v3 import _decide_v3
from skala_rag.scoring.selector_v3 import SelectionResultV3, _select_best_v3
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt
from skala_rag.tools.runtime import AdapterRuntime, BudgetLedger, CallContext
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM


@dataclass(frozen=True, kw_only=True)
class ApprovedPolicySource:
    """Explicit loader inputs, not approval or runtime authority."""

    path: str | Path
    approvals: PolicyApprovals
    approval_verifier: ApprovalVerifier
    execution_mode: Literal["fixture", "live"]
    live_gates: LiveScoringGates | None = None
    live_gate_verifier: LiveGateVerifier | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ActualAdmissionV3:
    """One run's trusted controller configuration, never a State payload.

    The live verifier is operator-owned code resolving exact references, not a
    model callback. Construction verifies it with positive capacity; later pure
    math rechecks content/context without reauthorizing another request. No
    runtime, ledger, review receipt or policy status is manufactured here.

    Evaluators use node ``<branch_id>_evaluation``. Only the existing OpenAI
    attempt is admitted: actual uses its native transport; controlled_response
    requires an exact MockTransport and is never actual-provider evidence.
    actual_replay also requires exact MockTransport, plus an operator verifier
    of externally pinned actual-origin capture/source closure. It preserves
    historical provenance, never claims a current provider call or approval.
    The optional operator factory resolves absent static keys after freeze;
    neither callback is model metadata or a semantic review decision.
    Explicit multi-argument methods preserve the downstream boundary contract.
    """

    source: ApprovedPolicySource
    runtime_binding: ScoringRuntimeBinding
    registry: PinnedApprovalRegistry
    run_input: RunInput
    index_version: str
    review_resolvers: Mapping[tuple[str, str], SourceBoundReviewResolver]
    execution_scope: Literal["actual", "controlled_response", "actual_replay"]
    review_resolver_for: (
        Callable[
            [EvaluationSnapshot, Mapping[str, JsonValue]], SourceBoundReviewResolver
        ]
        | None
    ) = None
    replay_verifier: Callable[[], bool] | None = None
    _approved: ApprovedScoringPolicy = field(init=False, repr=False)
    _runtime: AdapterRuntime = field(init=False, repr=False)
    _ledger: BudgetLedger = field(init=False, repr=False)
    _clock: Clock = field(init=False, repr=False)
    _configuration: str = field(init=False, repr=False)
    _gate_verifier: LiveGateVerifier = field(init=False, repr=False)
    _replay_verifier: Callable[[], bool] | None = field(init=False, repr=False)
    _resolver_factory: (
        Callable[
            [EvaluationSnapshot, Mapping[str, JsonValue]], SourceBoundReviewResolver
        ]
        | None
    ) = field(init=False, repr=False)
    _resolver_lock: Lock = field(
        default_factory=Lock, init=False, repr=False, compare=False
    )
    _snapshot_digests: dict[str, str] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _verified_resolvers: dict[tuple[str, str], SourceBoundReviewResolver] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        self._check_source()
        run_input = RunInput.model_validate(
            self.run_input.model_dump(), context={"execution_mode": "live"}
        )
        if (
            run_input.execution_mode != "live"
            or run_input.policy_version != self.runtime_binding.gates.policy_version
            or type(self.index_version) is not str
            or not self.index_version.strip()
            or self.execution_scope
            not in ("actual", "controlled_response", "actual_replay")
        ):
            raise ValueError("actual run input/index/scope required")
        if self.review_resolver_for is not None and not callable(
            self.review_resolver_for
        ):
            raise ValueError("operator-owned review resolver factory required")
        object.__setattr__(self, "_resolver_factory", self.review_resolver_for)
        object.__setattr__(self, "_replay_verifier", self.replay_verifier)
        self._verify_replay()
        object.__setattr__(self, "run_input", run_input)
        object.__setattr__(
            self, "review_resolvers", MappingProxyType(dict(self.review_resolvers))
        )
        self._observe_capacity()
        approved = load_approved_policy(
            self.source.path,
            approvals=self.source.approvals,
            approval_verifier=self.registry.verify_policy,
            execution_mode="live",
            live_gates=self.source.live_gates,
            live_gate_verifier=self.source.live_gate_verifier,
        )
        verifier = self.source.live_gate_verifier
        if verifier is None:
            raise ValueError("authoritative live gate verifier required")
        object.__setattr__(self, "_approved", approved)
        object.__setattr__(self, "_gate_verifier", verifier)
        object.__setattr__(self, "_runtime", self.runtime_binding.runtime)
        object.__setattr__(self, "_ledger", self.runtime_binding.runtime.ledger)
        object.__setattr__(self, "_clock", self.runtime_binding.runtime.clock)
        object.__setattr__(self, "_configuration", self._context_digest())

    def _check_source(self) -> None:
        if (
            type(self.source) is not ApprovedPolicySource
            or type(self.registry) is not PinnedApprovalRegistry
            or type(self.runtime_binding) is not ScoringRuntimeBinding
            or type(self.run_input) is not RunInput
            or self.source.execution_mode != "live"
            or self.source.approvals != self.registry.policy_approvals()
            or self.source.approval_verifier != self.registry.verify_policy
            or self.source.live_gates != self.runtime_binding.gates
            or Path(self.source.path).resolve()
            != (self.registry.root / "configs/scoring.v3.json").resolve()
        ):
            raise ValueError("exact pinned source and runtime binding required")

    def _context_digest(self) -> str:
        binding = self.runtime_binding
        return _digest(
            [
                self.run_input.model_dump(mode="json"),
                self.index_version,
                self.execution_scope,
                id(self.review_resolver_for),
                id(self.replay_verifier),
                [
                    [sid, version, id(resolver)]
                    for (sid, version), resolver in sorted(
                        self.review_resolvers.items()
                    )
                ],
                str(self.registry.root.resolve()),
                str(Path(self.source.path).resolve()),
                binding.provider,
                binding.tool_name,
                self.registry.rubric("core-0.1.0"),
                self.registry.rubric("finance-0.1.0"),
                *[
                    value.model_dump(mode="json", warnings="error")
                    for value in (
                        self.source.approvals,
                        binding.gates,
                        binding.policy,
                        binding.call,
                        binding.budget,
                        binding.readiness,
                        binding.allowance,
                        binding.runtime.policy,
                        binding.runtime.ledger.limits,
                    )
                ],
            ]
        )

    def _observe_capacity(self) -> None:
        observe_scoring_runtime(
            self.runtime_binding,
            run_id=self.runtime_binding.gates.run_id,
            policy_version=self.run_input.policy_version,
            schema_version=self.run_input.schema_version,
        )

    def _verify_replay(self) -> None:
        """Recheck operator authority, never a serialized capture's truthy flag."""
        if self.replay_verifier is not self._replay_verifier:
            raise ValueError("captured replay verifier changed")
        match self.execution_scope:
            case "actual_replay":
                if (
                    not callable(self.replay_verifier)
                    or self.replay_verifier() is not True
                ):
                    raise ValueError("verified actual-origin replay required")
            case "actual" | "controlled_response":
                if self.replay_verifier is not None:
                    raise ValueError("replay verifier requires actual_replay scope")
            case unreachable:
                assert_never(unreachable)

    def load_policy(self, require_capacity: bool = False) -> ApprovedScoringPolicy:
        """Fresh content/context checks; capacity is needed only before requests."""
        self._verify_replay()
        self._check_source()
        runtime = self.runtime_binding.runtime
        if (
            runtime is not self._runtime
            or runtime.ledger is not self._ledger
            or runtime.clock is not self._clock
            or self.source.live_gate_verifier is not self._gate_verifier
            or self.review_resolver_for is not self._resolver_factory
            or self._context_digest() != self._configuration
            or runtime.ledger.snapshot()["usage_invalid"]
        ):
            raise ValueError("captured admission context changed")
        if require_capacity:
            self._observe_capacity()
            return load_approved_policy(
                self.source.path,
                approvals=self.source.approvals,
                approval_verifier=self.registry.verify_policy,
                execution_mode="live",
                live_gates=self.source.live_gates,
                live_gate_verifier=self._gate_verifier,
            )
        fresh = load_approved_policy(
            self.source.path,
            approvals=self.source.approvals,
            approval_verifier=self.registry.verify_policy,
        )
        return ApprovedScoringPolicy(
            execution_mode="live",
            operational=fresh.operational,
            approvals=fresh.approvals,
            live_gates=self._approved.live_gates,
        )

    def verify_snapshot(
        self, snapshot: EvaluationSnapshot, rubric: Mapping[str, JsonValue]
    ) -> SourceBoundReviewResolver:
        """Bind live-parsed identity, pinned rubric and every original Source."""
        self.load_policy()
        if type(snapshot) is not EvaluationSnapshot:
            raise ValueError("exact frozen EvaluationSnapshot required")
        frozen = EvaluationSnapshot.model_validate(
            snapshot.model_dump(), context={"execution_mode": "live"}
        )
        if (
            frozen.run_id != self.runtime_binding.gates.run_id
            or frozen.schema_version != self.run_input.schema_version
            or frozen.policy_version != self.run_input.policy_version
            or frozen.corpus_version != self.run_input.corpus_version
            or frozen.as_of != self.run_input.as_of
            or frozen.index_version != self.index_version
        ):
            raise ValueError("snapshot run/corpus/cutoff/index mismatch")
        version = rubric.get("rubric_version")
        if not isinstance(version, str):
            raise ValueError("exact current pinned rubric required")
        pinned_rubric = self.registry.rubric(version)
        rubric_digest = _digest(pinned_rubric)
        if _digest(dict(rubric)) != rubric_digest:
            raise ValueError("exact current pinned rubric required")
        snapshot_digest = _digest(frozen.model_dump(mode="json"))
        key = (snapshot_digest, rubric_digest)
        # Mutable cache state is serialized; authority is rechecked on every use.
        with self._resolver_lock:
            self.load_policy()
            previous = self._snapshot_digests.setdefault(
                frozen.snapshot_id, snapshot_digest
            )
            if previous != snapshot_digest:
                raise ValueError("previously seen snapshot identity changed")
            resolver = self._verified_resolvers.get(key)
            if resolver is None:
                static_key = (frozen.snapshot_id, version)
                if static_key in self.review_resolvers:
                    resolver = self.review_resolvers[static_key]
                elif self._resolver_factory is not None:
                    resolver = self._resolver_factory(
                        frozen.model_copy(deep=True), deepcopy(pinned_rubric)
                    )
            if type(resolver) is not SourceBoundReviewResolver:
                raise ValueError("configured source-bound review resolver required")
            resolver.verify_snapshot(frozen, pinned_rubric)
            if any(resolver.verify_source(sid) is None for sid in frozen.sources):
                raise ValueError("snapshot source is not independently bound")
            self.load_policy()
            self._verified_resolvers[key] = resolver
            return resolver

    def verify_evaluator(
        self,
        snapshot: EvaluationSnapshot,
        rubric: Mapping[str, JsonValue],
        llm: StructuredLLM,
        branch_id: str,
    ) -> ApprovedScoringPolicy:
        """Reject foreign/fake callers before allowance or transport execution."""
        self.verify_snapshot(snapshot, rubric)
        binding = self.runtime_binding
        if (
            type(llm) is not RuntimeStructuredLLM
            or llm.runtime is not binding.runtime
            or llm.budget != binding.budget
            or llm.readiness != binding.readiness
            or llm.call.run_id != snapshot.run_id
            or llm.call.candidate_id != snapshot.candidate_id
            or llm.call.schema_version != snapshot.schema_version
            or llm.call.tool_name != binding.tool_name
            or branch_id
            not in ("founder", "market", "technology", "moat", "business_deal")
            or llm.call.node != f"{branch_id}_evaluation"
            or rubric.get("rubric_version")
            != ("finance-0.1.0" if branch_id == "business_deal" else "core-0.1.0")
        ):
            raise ValueError("evaluator runtime/run/candidate/branch mismatch")
        CallContext.model_validate(llm.call.model_dump(warnings="error"))
        attempt = llm.transport
        if type(attempt) is not OpenAIResponsesAttempt:
            raise ValueError("provider transport/clock/execution scope mismatch")
        match self.execution_scope:
            case "actual":
                valid_transport = attempt._http_transport is None
            case "controlled_response" | "actual_replay":
                valid_transport = type(attempt._http_transport) is httpx.MockTransport
            case unreachable:
                assert_never(unreachable)
        if (
            binding.provider != "openai"
            or attempt._clock is not binding.runtime.clock
            or attempt._schema_version != snapshot.schema_version
            or not valid_transport
        ):
            raise ValueError("provider transport/clock/execution scope mismatch")
        return self.load_policy(require_capacity=True)

    def resolve_review(
        self,
        snapshot: EvaluationSnapshot,
        rubric: Mapping[str, JsonValue],
        request: bytes,
        receipt: ReviewReceipt,
        *,
        subject: str,
    ) -> SourceBoundReview | None:
        """Preserve accepted/rejected/unresolved records and technical failures."""
        return self.verify_snapshot(snapshot, rubric).resolve_review(
            request, receipt, subject=subject
        )


def _load_fixture(source: ApprovedPolicySource) -> ApprovedScoringPolicy:
    if not isinstance(source, ApprovedPolicySource):
        raise ValueError("loader-backed ApprovedPolicySource required")
    if source.execution_mode == "live":
        raise ValueError("actual approved registry/runtime unavailable; live denied")
    return load_approved_policy(
        source.path,
        approvals=source.approvals,
        approval_verifier=source.approval_verifier,
        execution_mode=source.execution_mode,
        live_gates=source.live_gates,
        live_gate_verifier=source.live_gate_verifier,
    )


def _load_consumer(
    source: ApprovedPolicySource, actual_admission: ActualAdmissionV3 | None
) -> ApprovedScoringPolicy:
    if actual_admission is None:
        return _load_fixture(source)
    if (
        type(actual_admission) is not ActualAdmissionV3
        or source is not actual_admission.source
    ):
        raise ValueError("consumer requires its exact configured admission source")
    return actual_admission.load_policy()


def aggregate_scores_approved(
    evaluations: Sequence[Evaluation],
    source: ApprovedPolicySource,
    *,
    snapshot: EvaluationSnapshot,
    applicability_verifier: ApplicabilityVerifier | None,
    actual_admission: ActualAdmissionV3 | None = None,
) -> ScoreSummary:
    """Score promoted six-dimension input; upstream join owns semantic closure."""
    approved = _load_consumer(source, actual_admission)
    if not isinstance(snapshot, EvaluationSnapshot):
        raise ValueError("frozen EvaluationSnapshot required")
    frozen = EvaluationSnapshot.model_validate(
        snapshot.model_dump(), context={"execution_mode": approved.execution_mode}
    )
    if actual_admission is not None:
        for version in ("core-0.1.0", "finance-0.1.0"):
            actual_admission.verify_snapshot(
                frozen, actual_admission.registry.rubric(version)
            )
        if any(
            evaluation.rubric_version
            != (
                "finance-0.1.0"
                if evaluation.dimension in ("traction", "deal_terms")
                else "core-0.1.0"
            )
            for evaluation in evaluations
        ):
            raise ValueError("evaluation rubric does not match pinned dimension")
    return _aggregate_scores_v3(
        evaluations,
        criteria=approved.criteria,
        numeric=approved.operational.numeric,
        policy_version=approved.policy_version,
        applicability_verifier=applicability_verifier,
        snapshot=frozen,
    )


def decide_approved(
    summary: ScoreSummary,
    source: ApprovedPolicySource,
    *,
    evidence_ids: tuple[str, ...] = (),
    rationale: str = "Deterministic v3 policy decision",
    risks: tuple[str, ...] = (),
    limitations: tuple[str, ...] = (),
    actual_admission: ActualAdmissionV3 | None = None,
) -> InvestmentDecision:
    """Resolve fresh approvals before consuming a same-policy summary."""
    approved = _load_consumer(source, actual_admission)
    validated = ScoreSummary.model_validate(summary.model_dump())
    if actual_admission is not None and (
        validated.run_id != actual_admission.runtime_binding.gates.run_id
        or validated.schema_version != actual_admission.run_input.schema_version
    ):
        raise ValueError("decision admission run/schema mismatch")
    return _decide_v3(
        validated,
        numeric=approved.operational.numeric,
        policy_version=approved.policy_version,
        evidence_ids=evidence_ids,
        rationale=rationale,
        risks=risks,
        limitations=limitations,
    )


def select_best_approved(
    candidates: Sequence[Mapping[str, object]],
    source: ApprovedPolicySource,
    *,
    run_id: str,
    schema_version: str,
    actual_admission: ActualAdmissionV3 | None = None,
) -> SelectionResultV3:
    """Resolve fresh approvals before ordering eligible terminal results."""
    approved = _load_consumer(source, actual_admission)
    if actual_admission is not None and (
        run_id != actual_admission.runtime_binding.gates.run_id
        or schema_version != actual_admission.run_input.schema_version
    ):
        raise ValueError("selection admission run/schema mismatch")
    return _select_best_v3(
        candidates,
        numeric=approved.operational.numeric,
        policy_version=approved.policy_version,
        run_id=run_id,
        schema_version=schema_version,
    )
