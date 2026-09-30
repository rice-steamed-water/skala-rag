"""Offline Moat branch: #22 wrapper bridge, not a live research node."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from skala_rag.agents.evaluation import EvaluationValidationError, evaluate_dimension
from skala_rag.agents.moat_verification import (
    CoreArtifactApproval,
    ReviewedMoatAnchor,
    validate_core_artifact,
    validate_reviewed_anchor,
)
from skala_rag.contracts.evaluation import EvaluationSnapshot
from skala_rag.contracts.evidence import Evidence
from skala_rag.contracts.interfaces import Clock, StructuredLLM
from skala_rag.contracts.v3 import Evaluation, EvaluationBranchResult
from skala_rag.prompts.moat_evaluation import SYSTEM_PROMPT, build_user_prompt
from skala_rag.scoring.approved_policy import (
    ApprovalVerifier,
    ApprovedScoringPolicy,
    PolicyApprovals,
    load_approved_policy,
)
from skala_rag.scoring.catalog import ScoringPolicy

MOAT_CRITERIA = frozenset(
    {"moat.differentiation", "moat.ip", "moat.data", "moat.lock_in"}
)


@dataclass(frozen=True)
class VerifiedPatent:
    """Upstream verified facts, each backed by cited snapshot evidence."""

    holder_candidate_id: str
    rights_status: str
    claim_scope: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class IndependentComparison:
    competitor_candidate_id: str
    evidence_ids: tuple[str, ...]


def _evaluate_moat(
    snapshot: EvaluationSnapshot,
    *,
    rubric: Mapping[str, object],
    policy: ScoringPolicy | ApprovedScoringPolicy,
    llm: StructuredLLM,
    clock: Clock,
    schema_version: str,
    verify_observation: Callable[
        [object, Mapping[str, Evidence]], bool | ReviewedMoatAnchor
    ],
    verified_patents: Mapping[str, VerifiedPatent],
    independent_comparisons: Mapping[str, IndependentComparison],
) -> EvaluationBranchResult:
    """Injected fixture rubric/verifier; no numeric defaults, search or retries.

    #22 supports observed/missing only. This explicit bridge rejects N/A rather
    than silently converting it; approved applicability integration remains open.
    Verifier must return a snapshot-bound reviewed anchor for approved Core,
    checking minimum evidence and semantics rather than trusting provider prose.
    Approved core-0.1.0 anchors allow absent rights/applications and company
    comparisons. Their semantics (including negative facts and independent
    cross-checks for rating 5) belong to the trusted injected reviewer. Legacy proposed
    fixtures retain the active-patent/independent-comparison checks.
    """
    approved_core = (
        rubric.get("status") == "approved"
        and rubric.get("rubric_version") == "core-0.1.0"
    )
    if (not approved_core and rubric.get("status") != "proposed") or (
        not isinstance(policy, ApprovedScoringPolicy) and policy.status != "draft"
    ):
        raise ValueError("Moat offline fixture only; unsupported rubric or policy")
    if isinstance(policy, ApprovedScoringPolicy):
        if policy.execution_mode != "fixture" or not approved_core:
            raise ValueError("Approved Moat fixture requires approved Core rubric")
    snapshot = EvaluationSnapshot.model_validate(
        snapshot.model_dump(), context={"execution_mode": "fixture"}
    )
    ids = {c.criterion_id for c in policy.criteria if c.dimension == "moat"}
    dimensions = rubric.get("dimensions", {})
    moat = dimensions.get("moat", {}) if isinstance(dimensions, Mapping) else {}
    criteria = moat.get("criteria", {}) if isinstance(moat, Mapping) else {}
    if ids != MOAT_CRITERIA or set(criteria) != MOAT_CRITERIA:
        raise ValueError("Moat criterion catalog mismatch")
    if (
        not isinstance(rubric.get("rubric_version"), str)
        or not rubric["rubric_version"].strip()
    ):
        raise ValueError("Explicit rubric version required")
    if snapshot.policy_version != policy.policy_version:
        raise ValueError("Snapshot policy mismatch")
    allowed = {}
    for eid, evidence in snapshot.evidence.items():
        if (
            evidence.scope != "company"
            or evidence.candidate_id != snapshot.candidate_id
        ):
            continue
        if not MOAT_CRITERIA.intersection(evidence.criterion_ids):
            continue
        if (
            eid not in snapshot.evidence_ids
            or evidence.evidence_id != eid
            or evidence.source_id not in snapshot.sources
            or snapshot.sources[evidence.source_id].source_id != evidence.source_id
            or not evidence.provenance
        ):
            raise ValueError("Invalid frozen evidence source/provenance")
        for path in evidence.provenance:
            record = snapshot.retrieval_records.get(path.retrieval_id)
            if (
                record is None
                or record.run_id != snapshot.run_id
                or record.candidate_id not in (None, snapshot.candidate_id)
                or record.status != "ok"
                or eid not in record.evidence_ids
                or evidence.source_id not in record.source_ids
            ):
                raise ValueError("Invalid frozen retrieval attribution")
            for field, expected in (
                ("corpus_version", snapshot.corpus_version),
                ("index_version", snapshot.index_version),
                ("as_of", snapshot.as_of.isoformat()),
            ):
                if (
                    field in record.arguments_without_secrets
                    and record.arguments_without_secrets[field] != expected
                ):
                    raise ValueError("Stale frozen retrieval identity")
            if path.chunk_id is not None:
                chunk = snapshot.chunks.get(path.chunk_id)
                if (
                    chunk is None
                    or path.chunk_id not in record.chunk_ids
                    or chunk.source_id != evidence.source_id
                    or chunk.corpus_version != snapshot.corpus_version
                    or snapshot.candidate_id not in chunk.candidate_ids
                    or evidence.excerpt not in chunk.text
                ):
                    raise ValueError("Invalid frozen chunk attribution")
        allowed[eid] = evidence
    scoped = snapshot.model_copy(
        update={"evidence_ids": sorted(allowed), "evidence": allowed}, deep=True
    )

    class CheckedLLM:
        def generate(self, **kwargs):
            output = llm.generate(**kwargs)
            for criterion in output.criteria:
                if criterion.status != "observed":
                    continue
                cited = set(criterion.evidence_ids)
                if not cited or not cited <= set(allowed):
                    raise EvaluationValidationError(["MOAT_EVIDENCE_INVALID"])
                if not approved_core and criterion.criterion_id == "moat.ip":
                    patent = verified_patents.get(criterion.criterion_id)
                    if (
                        patent is None
                        or patent.holder_candidate_id != snapshot.candidate_id
                        or patent.rights_status != "active"
                        or not patent.claim_scope.strip()
                        or not patent.evidence_ids
                        or not set(patent.evidence_ids) <= cited
                    ):
                        raise EvaluationValidationError(["MOAT_PATENT_UNVERIFIED"])
                if (
                    not approved_core
                    and criterion.criterion_id == "moat.differentiation"
                ):
                    comparison = independent_comparisons.get(criterion.criterion_id)
                    if (
                        comparison is None
                        or not comparison.competitor_candidate_id.strip()
                        or comparison.competitor_candidate_id == snapshot.candidate_id
                        or not comparison.evidence_ids
                        or not set(comparison.evidence_ids) <= cited
                    ):
                        raise EvaluationValidationError(["MOAT_COMPARISON_UNVERIFIED"])
                review = verify_observation(
                    criterion, {eid: allowed[eid] for eid in cited}
                )
                if approved_core:
                    verified = validate_reviewed_anchor(
                        review,
                        rubric=rubric,
                        snapshot=snapshot,
                        criterion=criterion,
                        verifier=lambda receipt: receipt is review,
                    )
                else:
                    verified = review is True
                if not verified:
                    raise EvaluationValidationError(["MOAT_RUBRIC_UNVERIFIED"])
            return output

    result = evaluate_dimension(
        "moat",
        scoped,
        rubric,
        llm=CheckedLLM(),
        policy=policy,  # type: ignore[arg-type]  # Catalog-only shared wrapper.
        clock=clock,
        schema_version=schema_version,
        max_repairs=0,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=build_user_prompt(scoped, rubric, policy),
    )
    payload = result.model_dump(exclude={"dimension", "evaluation"})
    payload.update(branch_id="moat", evaluations=None)
    if result.evaluation is not None:
        payload["evaluations"] = {
            "moat": Evaluation.model_validate(result.evaluation.model_dump())
        }
    return EvaluationBranchResult.model_validate(payload)


def evaluate_moat(
    snapshot: EvaluationSnapshot,
    *,
    rubric: Mapping[str, object],
    policy: ScoringPolicy,
    llm: StructuredLLM,
    clock: Clock,
    schema_version: str,
    verify_observation: Callable[
        [object, Mapping[str, Evidence]], bool | ReviewedMoatAnchor
    ],
    verified_patents: Mapping[str, VerifiedPatent],
    independent_comparisons: Mapping[str, IndependentComparison],
    artifact_approval: CoreArtifactApproval | None = None,
    artifact_verifier: Callable[[CoreArtifactApproval], bool] | None = None,
) -> EvaluationBranchResult:
    """Legacy fixture entry; constructed approved contracts are not receipts."""
    if isinstance(policy, ApprovedScoringPolicy):
        raise ValueError("#168: approved contract has no trusted-loading receipt")
    if rubric.get("status") == "approved":
        if artifact_approval is None or artifact_verifier is None:
            raise ValueError("Explicit authoritative Core artifact approval required")
        validate_core_artifact(rubric, artifact_approval, artifact_verifier)
    return _evaluate_moat(
        snapshot,
        rubric=rubric,
        policy=policy,
        llm=llm,
        clock=clock,
        schema_version=schema_version,
        verify_observation=verify_observation,
        verified_patents=verified_patents,
        independent_comparisons=independent_comparisons,
    )


def evaluate_moat_approved_fixture(
    snapshot: EvaluationSnapshot,
    *,
    policy_path: str | Path,
    approvals: PolicyApprovals,
    approval_verifier: ApprovalVerifier,
    rubric: Mapping[str, object],
    llm: StructuredLLM,
    clock: Clock,
    schema_version: str,
    verify_observation: Callable[
        [object, Mapping[str, Evidence]], bool | ReviewedMoatAnchor
    ],
    artifact_approval: CoreArtifactApproval,
    artifact_verifier: Callable[[CoreArtifactApproval], bool],
    actual_runtime: bool = False,
) -> EvaluationBranchResult:
    """Load and consume approvals in this call, never infer a loading receipt.

    The trusted controller verifier must bind core approval to supplied rubric
    contents, not merely its claimed version. No live promotion is made.
    """
    if actual_runtime is not False:
        raise ValueError(
            "#168: actual runtime unavailable: no trusted-loading receipt; "
            "authoritative Core artifact and matched runtime admission required"
        )
    from skala_rag.fakes import FakeLLM

    if not isinstance(llm, FakeLLM):
        raise ValueError("Approved fixture requires FakeLLM; actual runtime is blocked")
    validate_core_artifact(rubric, artifact_approval, artifact_verifier)
    if artifact_approval.reference != approvals.core.reference:
        raise ValueError("Core artifact and policy approval reference mismatch")
    policy = load_approved_policy(
        policy_path,
        approvals=approvals,
        approval_verifier=approval_verifier,
        execution_mode="fixture",
    )
    return _evaluate_moat(
        snapshot,
        rubric=rubric,
        policy=policy,
        llm=llm,
        clock=clock,
        schema_version=schema_version,
        verify_observation=verify_observation,
        verified_patents={},
        independent_comparisons={},
    )
