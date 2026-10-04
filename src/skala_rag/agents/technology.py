"""Technology structured evaluation (#57, T01·T13).

frozen ``EvaluationSnapshot`` 안의 **허용 근거만** 써서 #10 Technology rubric을
#22 공통 wrapper(``evaluate_dimension``)와 versioned prompt로 평가한다.

- 노드 안에서 검색·조회하지 않는다. 입력은 snapshot뿐이다.
- 허용 근거: 대상 후보의 company scope, ``technology.*`` criterion, 그리고
  snapshot 안의 retrieval_record·chunk를 가리키는 RAG provenance가 있는 것.
  다른 회사·industry·비RAG·출처 불명 근거는 prompt와 출력 검증 양쪽에서 빠진다.
- 근거 부족은 ``missing``으로 남긴다. N/A 우회·유사도 점수화는 하지 않는다.
- 결과와 함께 criterion→evidence→retrieval/chunk→snapshot trace를 돌려준다.

fixture 실행과 실제 실행은 ``execution_mode``로 구분한다. fixture는 제안·승인
rubric 모두 허용한다. 실제 실행은 D14 core 승인 rubric이 있어야 하며, 입력
snapshot은 #55 Evidence 조사 산출물이어야 한다(Market #59와 같은 게이트).
"""

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from skala_rag.agents.evaluation import evaluate_dimension
from skala_rag.agents.moat_verification import CoreArtifactApproval
from skala_rag.agents.technology_verification import ReviewedTechnologyAnchor
from skala_rag.contracts.evaluation import EvaluationResult, EvaluationSnapshot
from skala_rag.contracts.evidence import Evidence
from skala_rag.contracts.interfaces import Clock, StructuredLLM
from skala_rag.prompts import technology_evaluation as prompt
from skala_rag.scoring.approved_policy import (
    ApprovalVerifier,
    ApprovedScoringPolicy,
    PolicyApprovals,
    load_approved_policy,
)
from skala_rag.scoring.catalog import ScoringPolicy

TECHNOLOGY_CRITERIA = frozenset(
    {
        "technology.maturity",
        "technology.reliability",
        "technology.integration",
        "technology.commercialization",
    }
)


@dataclass(frozen=True)
class TraceEntry:
    criterion_id: str
    evidence_id: str
    retrieval_id: str
    chunk_id: str
    snapshot_id: str


@dataclass(frozen=True)
class TechnologyEvaluation:
    result: EvaluationResult
    prompt_version: str
    allowed_evidence_ids: tuple[str, ...]
    trace: tuple[TraceEntry, ...]


def _check_criteria(
    rubric: Mapping[str, object], policy: ScoringPolicy | ApprovedScoringPolicy
) -> None:
    dims = rubric.get("dimensions")
    tech = dims.get("technology") if isinstance(dims, Mapping) else None
    criteria = tech.get("criteria") if isinstance(tech, Mapping) else None
    rubric_ids = set(criteria) if isinstance(criteria, Mapping) else set()
    policy_ids = {
        c.criterion_id for c in policy.criteria if c.dimension == "technology"
    }
    if rubric_ids != TECHNOLOGY_CRITERIA or policy_ids != TECHNOLOGY_CRITERIA:
        raise ValueError(
            "Technology criteria incomplete: rubric/policy must cover "
            + ", ".join(sorted(TECHNOLOGY_CRITERIA))
        )


def _check_mode(
    execution_mode: str, rubric: Mapping[str, object], policy: ScoringPolicy
) -> None:
    if execution_mode == "fixture":
        if (
            rubric.get("status") not in ("proposed", "approved")
            or policy.status != "draft"
        ):
            raise ValueError(
                "Technology fixture requires proposed/approved rubric and draft policy"
            )
    elif execution_mode == "real":
        if rubric.get("status") != "approved":
            raise ValueError("Technology real run requires approved rubric (D14 core)")
    else:
        raise ValueError(f"unknown execution_mode: {execution_mode!r}")


def _rag_provenance(evidence: Evidence, snapshot: EvaluationSnapshot):
    return [
        p
        for p in evidence.provenance
        if p.method == "rag"
        and p.chunk_id is not None
        and p.chunk_id in snapshot.chunks
        and p.retrieval_id in snapshot.retrieval_records
    ]


def select_technology_evidence(snapshot: EvaluationSnapshot) -> dict[str, Evidence]:
    """snapshot 근거 중 Technology 평가에 허용되는 것만 고른다(검색 없음)."""
    return {
        eid: e
        for eid, e in snapshot.evidence.items()
        if e.scope == "company"
        and e.candidate_id == snapshot.candidate_id
        and any(cid in TECHNOLOGY_CRITERIA for cid in e.criterion_ids)
        and _rag_provenance(e, snapshot)
    }


def build_trace(
    result: EvaluationResult, snapshot: EvaluationSnapshot
) -> tuple[TraceEntry, ...]:
    """observed criterion이 인용한 근거의 retrieval·chunk·snapshot 연결."""
    if result.evaluation is None:
        return ()
    entries = []
    for criterion in result.evaluation.criteria:
        for eid in criterion.evidence_ids:
            for p in _rag_provenance(snapshot.evidence[eid], snapshot):
                entries.append(
                    TraceEntry(
                        criterion_id=criterion.criterion_id,
                        evidence_id=eid,
                        retrieval_id=p.retrieval_id,
                        chunk_id=p.chunk_id or "",
                        snapshot_id=snapshot.snapshot_id,
                    )
                )
    return tuple(entries)


def evaluate_technology(
    snapshot: EvaluationSnapshot,
    *,
    rubric: Mapping[str, object],
    llm: StructuredLLM,
    policy: ScoringPolicy,
    clock: Clock,
    schema_version: str,
    execution_mode: Literal["fixture", "real"] = "fixture",
    max_repairs: int = 1,
) -> TechnologyEvaluation:
    _check_mode(execution_mode, rubric, policy)
    _check_criteria(rubric, policy)
    return _run_technology(
        snapshot,
        rubric=rubric,
        llm=llm,
        policy=policy,
        clock=clock,
        schema_version=schema_version,
        max_repairs=max_repairs,
    )


def _run_technology(
    snapshot: EvaluationSnapshot,
    *,
    rubric: Mapping[str, object],
    llm: StructuredLLM,
    policy: ScoringPolicy | ApprovedScoringPolicy,
    clock: Clock,
    schema_version: str,
    max_repairs: int,
) -> TechnologyEvaluation:
    allowed = select_technology_evidence(snapshot)
    scoped = snapshot.model_copy(
        update={"evidence_ids": sorted(allowed), "evidence": allowed}, deep=True
    )
    result = evaluate_dimension(
        "technology",
        scoped,
        rubric,
        llm=llm,
        policy=policy,
        clock=clock,
        schema_version=schema_version,
        max_repairs=max_repairs,
        system_prompt=prompt.SYSTEM_PROMPT,
        user_prompt=prompt.build_user_prompt(scoped, rubric, policy),
    )
    return TechnologyEvaluation(
        result=result,
        prompt_version=prompt.PROMPT_VERSION,
        allowed_evidence_ids=tuple(sorted(allowed)),
        trace=build_trace(result, scoped),
    )


def evaluate_technology_approved_fixture(
    snapshot: EvaluationSnapshot,
    *,
    policy_path: str | Path,
    approvals: PolicyApprovals,
    approval_verifier: ApprovalVerifier,
    rubric: Mapping[str, object],
    llm: StructuredLLM,
    clock: Clock,
    schema_version: str,
    verify_observation: Callable[[object, Mapping[str, Evidence]], object],
    review_verifier: Callable[[ReviewedTechnologyAnchor], bool],
    artifact_approval: CoreArtifactApproval,
    artifact_verifier: Callable[[CoreArtifactApproval], bool],
    actual_runtime: bool = False,
) -> TechnologyEvaluation:
    """Loader-backed fixture only; no runtime admission or semantic authority.

    Review is post-baseline validation, never an additional model repair. The
    caller must authenticate exact receipts with its external review registry.
    """
    from skala_rag.agents.moat_verification import validate_core_artifact
    from skala_rag.agents.technology_verification import (
        TechnologyReviewError,
        checked_fixture_output,
        checked_snapshot,
        validate_technology_anchor,
    )
    from skala_rag.contracts.error_codes import ErrorCode
    from skala_rag.contracts.interfaces import LLMError
    from skala_rag.fakes import FakeLLM

    if actual_runtime is not False:
        raise ValueError("#168: actual runtime unavailable")
    if type(llm) is not FakeLLM:
        raise ValueError("Approved fixture requires exact FakeLLM")
    try:
        rubric = deepcopy(dict(rubric))
        if rubric.get("status") != "approved":
            raise ValueError("Approved Technology fixture requires approved Core")
        snapshot = checked_snapshot(snapshot)
        if schema_version != snapshot.schema_version:
            raise ValueError("Snapshot schema mismatch")
        if not callable(verify_observation) or not callable(review_verifier):
            raise ValueError("External Technology review callbacks required")
        validate_core_artifact(rubric, artifact_approval, artifact_verifier)
        approvals = PolicyApprovals.model_validate(approvals.model_dump())
        if artifact_approval.reference != approvals.core.reference:
            raise ValueError("Core artifact and policy approval reference mismatch")
        policy = load_approved_policy(
            policy_path,
            approvals=approvals,
            approval_verifier=approval_verifier,
            execution_mode="fixture",
        )
        if snapshot.policy_version != policy.policy_version:
            raise ValueError("Snapshot policy mismatch")
        _check_criteria(rubric, policy)
    except Exception:
        raise ValueError("Technology approved fixture preflight rejected") from None

    class CheckedFixtureLLM:
        def generate(self, **kwargs):
            # Preserve upstream LLM errors; only local validation is redacted.
            output = llm.generate(**kwargs)
            try:
                return checked_fixture_output(output)
            except Exception:
                raise LLMError(
                    ErrorCode.LLM_OUTPUT_INVALID, "TECHNOLOGY_FIXTURE_OUTPUT_INVALID"
                ) from None

    result = _run_technology(
        snapshot,
        rubric=rubric,
        llm=CheckedFixtureLLM(),
        policy=policy,
        clock=clock,
        schema_version=schema_version,
        max_repairs=0,
    )
    if result.result.status == "failure":
        return result
    # Detached callback arguments cannot mutate the original evaluation/snapshot.
    try:
        for criterion in result.result.evaluation.criteria:
            if criterion.status != "observed":
                continue
            receipt = verify_observation(
                criterion.model_copy(deep=True),
                {
                    eid: snapshot.evidence[eid].model_copy(deep=True)
                    for eid in criterion.evidence_ids
                },
            )
            if not validate_technology_anchor(
                receipt,
                rubric=rubric,
                snapshot=snapshot,
                criterion=criterion,
                verifier=review_verifier,
            ):
                raise TechnologyReviewError("TECHNOLOGY_REVIEW_REJECTED")
    except Exception:
        raise TechnologyReviewError("TECHNOLOGY_REVIEW_REJECTED") from None
    return result
