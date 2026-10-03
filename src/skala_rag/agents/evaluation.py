"""영역 평가 wrapper의 검증·조립 부분 — #22, contracts §4, T01·T22.

LLM은 ``DimensionAssessmentOutput``(criterion 판단·gap·caveat)만 만든다. run_id·
snapshot_id·policy_version 같은 envelope 필드, gap_id, 비중·점수는 wrapper가
snapshot과 catalog에서 채운다 — 모델 출력의 점수·ID는 받지 않는다(schema가
extra 필드를 거절). 점수는 이후 scoring.aggregate_scores가 rating으로만 계산한다.

``evaluate_dimension``은 StructuredLLM(#8)을 호출하고, schema·계약 위반이면
위반 내용을 붙여 구조 수정을 1회 요청한 뒤에도 실패하면 failure envelope를
돌려준다. 기술적 실패를 missing·0점으로 바꾸지 않는다.
"""

import json
import re
from collections.abc import Callable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from skala_rag.contracts import (
    CriterionAssessment,
    Evaluation,
    EvaluationResult,
    EvaluationSnapshot,
    ResearchGap,
    WorkflowError,
    evaluation_key,
)
from skala_rag.contracts.error_codes import ErrorCode, is_retryable
from skala_rag.contracts.evaluation import Dimension
from skala_rag.contracts.interfaces import Clock, LLMError, StructuredLLM
from skala_rag.scoring.catalog import ScoringPolicy


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CriterionOutput(_Output):
    """LLM이 criterion마다 내는 판단. 점수·비중 필드는 없다."""

    criterion_id: str = Field(min_length=1)
    status: Literal["observed", "missing"]
    rating: int | None = Field(default=None, ge=1, le=5)
    evidence_ids: list[str]
    rationale: str = Field(min_length=1)
    missing_reason: str | None = None
    applicability_note: str | None = None


class GapOutput(_Output):
    """LLM이 제안하는 추가 조사. gap_id·우선순위는 wrapper가 정한다."""

    criterion_id: str = Field(min_length=1)
    missing_fields: list[str]
    reason: str = Field(min_length=1)
    suggested_queries: list[str]


class DimensionAssessmentOutput(_Output):
    """StructuredLLM.generate(output_schema=...)에 넘길 LLM 출력 schema."""

    criteria: list[CriterionOutput]
    research_gaps: list[GapOutput] = []
    caveats: list[str] = []


class EvaluationValidationError(ValueError):
    """LLM 출력이 계약을 어김. wrapper가 구조 수정 1회 후 failure로 옮긴다."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


def _safe_violation_codes(violations: list[str]) -> str:
    """내부 진단 코드만 남기고 모델이 만든 ID·문장을 버린다."""
    codes = set()
    for violation in violations:
        code = violation.partition(":")[0]
        codes.add(code if re.fullmatch(r"[A-Z][A-Z0-9_]*", code) else "OUTPUT_INVALID")
    return ", ".join(sorted(codes))


def _industry_dimensions(rubric: Mapping[str, object]) -> set[str] | None:
    """rubric의 산업 근거 허용 영역. 지정이 없으면 None(제한 없음)."""
    rules = rubric.get("common_rules")
    if isinstance(rules, Mapping) and "industry_evidence_dimensions" in rules:
        return set(rules["industry_evidence_dimensions"])  # type: ignore[arg-type]
    return None


def validate_output(
    output: DimensionAssessmentOutput,
    *,
    dimension: Dimension,
    snapshot: EvaluationSnapshot,
    policy: ScoringPolicy,
    rubric: Mapping[str, object],
) -> list[str]:
    """계약 위반 목록을 돌려준다. 빈 목록이면 통과.

    - 영역 criterion이 정확히 한 번씩
    - observed는 rating·근거 필수, missing은 rating 없음·사유 필수
    - 근거는 snapshot 안에 있고, 같은 후보 또는 허용된 산업 scope
    - gap은 이 영역 criterion만
    """
    violations: list[str] = []
    expected = [c.criterion_id for c in policy.criteria if c.dimension == dimension]
    got = [c.criterion_id for c in output.criteria]
    if sorted(got) != sorted(expected) or len(set(got)) != len(got):
        violations.append(f"CRITERIA_SET: 기대 {sorted(expected)}, 출력 {sorted(got)}")

    industry_dims = _industry_dimensions(rubric)
    industry_ok = industry_dims is None or dimension in industry_dims
    for c in output.criteria:
        if c.status == "observed":
            if c.rating is None:
                violations.append(f"RATING_REQUIRED: {c.criterion_id}")
            if not c.evidence_ids:
                violations.append(
                    f"EVIDENCE_REQUIRED: {c.criterion_id} 근거 없는 rating"
                )
            if c.missing_reason is not None:
                violations.append(f"STATUS_CONFLICT: {c.criterion_id}")
        else:
            if c.rating is not None:
                violations.append(
                    f"STATUS_CONFLICT: {c.criterion_id} missing인데 rating"
                )
            if not c.missing_reason:
                violations.append(f"MISSING_REASON_REQUIRED: {c.criterion_id}")
        for eid in c.evidence_ids:
            ev = snapshot.evidence.get(eid)
            if ev is None:
                violations.append(f"EVIDENCE_NOT_IN_SNAPSHOT: {c.criterion_id} → {eid}")
            elif ev.scope == "company" and ev.candidate_id != snapshot.candidate_id:
                violations.append(f"OTHER_COMPANY_EVIDENCE: {c.criterion_id} → {eid}")
            elif c.criterion_id not in ev.criterion_ids:
                violations.append(
                    f"EVIDENCE_CRITERION_MISMATCH: {c.criterion_id} → {eid}"
                )
            elif ev.scope == "industry" and not industry_ok:
                violations.append(
                    f"INDUSTRY_EVIDENCE_NOT_ALLOWED: {c.criterion_id} → {eid}"
                )

    for gap in output.research_gaps:
        if gap.criterion_id not in expected:
            violations.append(f"GAP_OUTSIDE_DIMENSION: {gap.criterion_id}")
    return violations


def assemble_evaluation(
    output: DimensionAssessmentOutput,
    *,
    dimension: Dimension,
    snapshot: EvaluationSnapshot,
    policy: ScoringPolicy,
    rubric: Mapping[str, object],
    schema_version: str,
) -> Evaluation:
    """검증을 통과한 출력으로 Evaluation을 만든다. 위반이 있으면 예외.

    envelope는 snapshot에서, gap 우선순위는 catalog 비중에서 채운다.
    """
    if snapshot.policy_version != policy.policy_version:
        raise EvaluationValidationError(["POLICY_MISMATCH: snapshot과 정책이 다르다"])
    rubric_version = rubric.get("rubric_version")
    if not isinstance(rubric_version, str) or not rubric_version:
        raise EvaluationValidationError(["RUBRIC_VERSION_REQUIRED"])
    violations = validate_output(
        output, dimension=dimension, snapshot=snapshot, policy=policy, rubric=rubric
    )
    if violations:
        raise EvaluationValidationError(violations)

    weights = {c.criterion_id: c.weight for c in policy.criteria}
    key = evaluation_key(snapshot.candidate_id, snapshot.evaluation_round, dimension)
    return Evaluation(
        schema_version=schema_version,
        run_id=snapshot.run_id,
        candidate_id=snapshot.candidate_id,
        dimension=dimension,
        evaluation_round=snapshot.evaluation_round,
        snapshot_id=snapshot.snapshot_id,
        evidence_revision=snapshot.evidence_revision,
        policy_version=snapshot.policy_version,
        rubric_version=rubric_version,
        criteria=[
            CriterionAssessment(
                schema_version=schema_version,
                **c.model_dump(),
            )
            for c in output.criteria
        ],
        research_gaps=[
            ResearchGap(
                schema_version=schema_version,
                gap_id=f"gap:{key}:{g.criterion_id}",
                candidate_id=snapshot.candidate_id,
                criterion_id=g.criterion_id,
                missing_fields=g.missing_fields,
                reason=g.reason,
                priority_weight=weights[g.criterion_id],
                suggested_queries=g.suggested_queries,
                attempted_retrieval_ids=[],
                status="open",
            )
            for g in output.research_gaps
        ],
        caveats=output.caveats,
    )


# --- LLM 호출 wrapper -------------------------------------------------------

SYSTEM_PROMPT = (
    "당신은 Physical AI/Robotics 스타트업 투자 평가자다. 주어진 rubric과 근거만 "
    "사용해 각 criterion을 판단한다. 근거가 부족하면 status=missing과 "
    "missing_reason을 쓰고 추측하지 않는다. observed에는 rating(1~5)과 "
    "snapshot 안의 evidence_ids를 반드시 쓴다. 점수·비중·ID는 출력하지 않는다."
)
"""fixture용 최소 prompt. 실제 prompt 문구는 M2(#47·#57~#61) 범위다."""


def build_user_prompt(
    dimension: Dimension,
    snapshot: EvaluationSnapshot,
    rubric: Mapping[str, object],
    policy: ScoringPolicy,
    context: Mapping[str, object] | None = None,
) -> str:
    """rubric 해당 영역과 snapshot 근거만 담은 결정적 JSON prompt.

    ``context``는 영역별 호출자가 검증해 넘긴 추가 맥락(예: 시장 정의)이다.
    """
    dims = rubric.get("dimensions")
    rubric_dim = dims.get(dimension, {}) if isinstance(dims, Mapping) else {}
    payload = {
        "dimension": dimension,
        "criteria": [
            c.criterion_id for c in policy.criteria if c.dimension == dimension
        ],
        "rubric_version": rubric.get("rubric_version"),
        "rubric": rubric_dim,
        "evidence": [
            {
                "evidence_id": e.evidence_id,
                "scope": e.scope,
                "criterion_ids": e.criterion_ids,
                "claim": e.claim,
                "excerpt": e.excerpt,
                "value": e.value,
                "unit": e.unit,
                "currency": e.currency,
                "period": e.period,
                "evidence_kind": e.evidence_kind,
                "limitations": e.limitations,
            }
            for e in snapshot.evidence.values()
        ],
    }
    if context is not None:
        payload["context"] = dict(context)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _failure(
    dimension: Dimension,
    snapshot: EvaluationSnapshot,
    *,
    code: ErrorCode,
    message: str,
    attempt: int,
    clock: Clock,
    schema_version: str,
) -> EvaluationResult:
    key = evaluation_key(snapshot.candidate_id, snapshot.evaluation_round, dimension)
    error = WorkflowError(
        schema_version=schema_version,
        error_id=f"err:{key}:{attempt}",
        run_id=snapshot.run_id,
        candidate_id=snapshot.candidate_id,
        node=f"{dimension}_evaluation",
        error_code=code.value,
        message_redacted=message[:500],
        retryable=is_retryable(code),
        attempt=attempt,
        timestamp=clock.now(),
    )
    return EvaluationResult(
        schema_version=schema_version,
        run_id=snapshot.run_id,
        candidate_id=snapshot.candidate_id,
        dimension=dimension,
        evaluation_round=snapshot.evaluation_round,
        snapshot_id=snapshot.snapshot_id,
        evidence_revision=snapshot.evidence_revision,
        policy_version=snapshot.policy_version,
        status="failure",
        evaluation=None,
        errors=[error],
    )


def evaluate_dimension(
    dimension: Dimension,
    snapshot: EvaluationSnapshot,
    rubric: Mapping[str, object],
    *,
    llm: StructuredLLM,
    policy: ScoringPolicy,
    clock: Clock,
    schema_version: str,
    max_repairs: int = 1,
    system_prompt: str = SYSTEM_PROMPT,
    user_prompt: str | None = None,
    prompt_context: Mapping[str, object] | None = None,
    extra_validator: Callable[[DimensionAssessmentOutput], list[str]] | None = None,
) -> EvaluationResult:
    """한 영역을 평가해 terminal ``EvaluationResult``를 돌려준다(contracts §4).

    영역별 호출자는 versioned ``system_prompt``·``prompt_context``와 영역 고유
    계약 검사 ``extra_validator``를 넘길 수 있다. 추가 위반도 공통 위반과 같이
    구조 수정 1회 → failure로 처리한다(위반 문자열은 ``CODE: 내용`` 형식).

    - schema 오류(LLM_OUTPUT_INVALID·ValidationError)와 계약 위반은 위반 내용을
      붙여 ``max_repairs``회 구조 수정을 요청한다. 그래도 실패하면 failure.
    - 그 밖의 LLM 오류(timeout 등)는 이 wrapper에서 재시도하지 않고 failure
      (재시도 예산은 M2 adapter 범위).
    - ``system_prompt``/``user_prompt``는 영역별 versioned prompt(#57~#61)가
      주입한다. 생략하면 fixture용 기본 prompt를 쓴다. 출력 검증은 동일하다.
    """
    user = (
        user_prompt
        if user_prompt is not None
        else build_user_prompt(dimension, snapshot, rubric, policy, prompt_context)
    )
    prompt = user
    last_problem = ""
    for attempt in range(1, max_repairs + 2):
        try:
            output = llm.generate(
                system=system_prompt,
                user=prompt,
                output_schema=DimensionAssessmentOutput,
            )
            evaluation = assemble_evaluation(
                output,
                dimension=dimension,
                snapshot=snapshot,
                policy=policy,
                rubric=rubric,
                schema_version=schema_version,
            )
            if extra_validator is not None and (extra := extra_validator(output)):
                raise EvaluationValidationError(extra)
        except LLMError as err:
            if err.error_code != ErrorCode.LLM_OUTPUT_INVALID:
                return _failure(
                    dimension,
                    snapshot,
                    code=err.error_code,
                    message=err.message_redacted,
                    attempt=attempt,
                    clock=clock,
                    schema_version=schema_version,
                )
            last_problem = ErrorCode.LLM_OUTPUT_INVALID.value
        except ValidationError as err:
            last_problem = f"schema 오류 {err.error_count()}건"
        except EvaluationValidationError as err:
            last_problem = _safe_violation_codes(err.violations)
        else:
            return EvaluationResult(
                schema_version=schema_version,
                run_id=evaluation.run_id,
                candidate_id=evaluation.candidate_id,
                dimension=dimension,
                evaluation_round=evaluation.evaluation_round,
                snapshot_id=evaluation.snapshot_id,
                evidence_revision=evaluation.evidence_revision,
                policy_version=evaluation.policy_version,
                status="success",
                evaluation=evaluation,
                errors=[],
            )
        prompt = (
            f"{user}\n\n이전 출력이 계약을 어겼다. 다음 문제를 고쳐 다시 출력하라: "
            f"{last_problem}"
        )
    return _failure(
        dimension,
        snapshot,
        code=ErrorCode.LLM_OUTPUT_INVALID,
        message=f"구조 수정 {max_repairs}회 후에도 실패: {last_problem}",
        attempt=max_repairs + 1,
        clock=clock,
        schema_version=schema_version,
    )


def make_evaluate_dimension(
    *,
    llm: StructuredLLM,
    policy: ScoringPolicy,
    clock: Clock,
    schema_version: str,
    max_repairs: int = 1,
) -> Callable[[Dimension, EvaluationSnapshot, Mapping[str, object]], EvaluationResult]:
    """contracts ``EvaluateDimension`` Protocol을 만족하는 함수를 만든다."""

    def _evaluate(
        dimension: Dimension,
        snapshot: EvaluationSnapshot,
        rubric: Mapping[str, object],
    ) -> EvaluationResult:
        return evaluate_dimension(
            dimension,
            snapshot,
            rubric,
            llm=llm,
            policy=policy,
            clock=clock,
            schema_version=schema_version,
            max_repairs=max_repairs,
        )

    return _evaluate


def output_from_evaluation(evaluation: Evaluation) -> DimensionAssessmentOutput:
    """fixture Evaluation을 FakeLLM 응답으로 바꾼다 (5개 영역 + deal_terms)."""
    return DimensionAssessmentOutput(
        criteria=[
            CriterionOutput(
                criterion_id=c.criterion_id,
                status=c.status,
                rating=c.rating,
                evidence_ids=list(c.evidence_ids),
                rationale=c.rationale,
                missing_reason=c.missing_reason,
                applicability_note=c.applicability_note,
            )
            for c in evaluation.criteria
        ],
        research_gaps=[
            GapOutput(
                criterion_id=g.criterion_id,
                missing_fields=list(g.missing_fields),
                reason=g.reason,
                suggested_queries=list(g.suggested_queries),
            )
            for g in evaluation.research_gaps
            if g.criterion_id is not None
        ],
        caveats=list(evaluation.caveats),
    )
