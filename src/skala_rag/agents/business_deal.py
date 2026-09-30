"""Atomic v3 snapshot boundary; actual Finance semantic verification is blocked."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from skala_rag.agents.evaluation import (
    CriterionOutput,
    DimensionAssessmentOutput,
    GapOutput,
    assemble_evaluation,
    validate_output,
)
from skala_rag.contracts.error_codes import ErrorCode, is_retryable
from skala_rag.contracts.evaluation import EvaluationSnapshot
from skala_rag.contracts.interfaces import Clock, LLMError, StructuredLLM
from skala_rag.contracts.v3 import (
    CriterionAssessment,
    Evaluation,
    EvaluationBranchResult,
    WorkflowError,
)
from skala_rag.prompts.business_deal_evaluation import SYSTEM_PROMPT, build_user_prompt
from skala_rag.scoring.catalog import ScoringPolicy
from skala_rag.scoring.finance import Unavailable, to_amount
from skala_rag.scoring.v3_policy import CATALOG

DOMAINS = ("traction", "deal_terms")
Verifier = Callable[[CriterionAssessment, EvaluationSnapshot], bool]


@dataclass(frozen=True)
class ApprovedVerifiers:
    """Explicit externally authorized versions; synthetic versions are not approval.

    Verifiers receive actual snapshot payloads, not only IDs. Finance verification
    must check derivation/period/unit/currency/source semantics using finance helpers.
    Rubric verifier checks anchor correspondence; applicability checks rule approval.
    """

    rubric_version: str
    finance_version: str
    applicability_version: str
    rubric: Verifier
    finance: Verifier
    applicability: Verifier

    def __post_init__(self):
        if any(
            not isinstance(v, str) or not v.strip()
            for v in (
                self.rubric_version,
                self.finance_version,
                self.applicability_version,
            )
        ):
            raise ValueError("explicit verifier versions required")


class DomainOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    criteria: list[CriterionAssessment]
    research_gaps: list[GapOutput]
    caveats: list[str]


class BusinessDealOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    traction: DomainOutput
    deal_terms: DomainOutput


def evaluate_business_deal(
    snapshot: EvaluationSnapshot,
    *,
    rubric: Mapping[str, object],
    policy: ScoringPolicy,
    llm: StructuredLLM,
    clock: Clock,
    schema_version: str,
    verifiers: ApprovedVerifiers,
    execution_mode: Literal["fixture", "real"] = "fixture",
) -> EvaluationBranchResult:
    """One structured call; any invalid domain invalidates the entire branch.

    No local repair/retry. RuntimeStructuredLLM owns transport budgets. Reuses
    #22 catalog/evidence validation and assembly, then promotes to v3 atomically.
    """
    if execution_mode != "fixture":
        raise ValueError(
            "actual evaluation blocked: approved finance-0.1.0 semantic verifiers "
            "and authoritative metric-role/round/accounting-subject facts required; "
            "#55 merge alone is not evaluation readiness"
        )
    identity = {
        k: getattr(snapshot, k)
        for k in (
            "run_id",
            "candidate_id",
            "evaluation_round",
            "snapshot_id",
            "evidence_revision",
            "policy_version",
        )
    }
    identity.update(schema_version=schema_version, branch_id="business_deal")
    code = ErrorCode.LLM_OUTPUT_INVALID
    try:
        if snapshot.policy_version != policy.policy_version:
            raise ValueError("policy mismatch")
        if rubric.get("rubric_version") != verifiers.rubric_version:
            raise ValueError("rubric verifier version mismatch")
        dimensions = rubric.get("dimensions")
        for d in DOMAINS:
            expected = {cid for cid, _ in CATALOG if cid.startswith(d + ".")}
            policy_ids = {c.criterion_id for c in policy.criteria if c.dimension == d}
            if policy_ids != expected:
                raise ValueError("catalog mismatch")
            if (
                not isinstance(dimensions, Mapping)
                or set(dimensions[d]["criteria"]) != expected
            ):
                raise ValueError("rubric catalog mismatch")
        allowed = {
            eid: e
            for eid, e in snapshot.evidence.items()
            if e.scope == "company"
            and e.candidate_id == snapshot.candidate_id
            and e.source_id in snapshot.sources
            and any(cid.startswith(DOMAINS) for cid in e.criterion_ids)
        }
        scoped = snapshot.model_copy(
            update={"evidence": allowed, "evidence_ids": sorted(allowed)}, deep=True
        )
        raw = llm.generate(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(scoped, rubric, policy),
            output_schema=BusinessDealOutput,
        )
        output = BusinessDealOutput.model_validate(
            raw.model_dump() if isinstance(raw, BaseModel) else raw,
            context={"execution_mode": "fixture"},
        )
        evaluations = {}
        for d in DOMAINS:
            domain = getattr(output, d)
            neutral = []
            for c in domain.criteria:
                for eid in [*c.evidence_ids, *(c.applicability_evidence_ids or [])]:
                    e = allowed.get(eid)
                    if e is None or c.criterion_id not in e.criterion_ids:
                        raise ValueError("evidence outside criterion snapshot")
                    if e.conflicts_with or e.evidence_kind == "estimated":
                        raise ValueError("unverified evidence")
                    if not set(e.supporting_evidence_ids) <= set(snapshot.evidence):
                        raise ValueError("support outside snapshot")
                if c.status == "observed":
                    inputs = [allowed[eid] for eid in c.evidence_ids]
                    inputs += [
                        snapshot.evidence[sid]
                        for e in list(inputs)
                        for sid in e.supporting_evidence_ids
                    ]
                    money = [e for e in inputs if e.currency is not None]
                    if len({(e.unit, e.currency) for e in money}) > 1:
                        raise ValueError("mixed financial units/currencies")
                    if any(
                        e.source_id not in snapshot.sources
                        or e.candidate_id != snapshot.candidate_id
                        or e.conflicts_with
                        or e.evidence_kind == "estimated"
                        for e in inputs
                    ):
                        raise ValueError("unverified supporting inputs")
                    for eid in c.evidence_ids:
                        e = allowed[eid]
                        if e.currency is not None and isinstance(
                            to_amount(e), Unavailable
                        ):
                            raise ValueError("unknown monetary unit")
                    if (
                        verifiers.finance(c, scoped) is not True
                        or verifiers.rubric(c, scoped) is not True
                    ):
                        raise ValueError("unapproved financial rating")
                if c.status == "not_applicable" and rubric.get("status") == "approved":
                    approved_rules = {
                        "traction.rule_of_40": (
                            "finance-0.1.0:rule40-confirmed-pre-revenue"
                        ),
                        "traction.runway": (
                            "finance-0.1.0:runway-confirmed-nonnegative-ocf"
                        ),
                    }
                    if c.applicability_rule_id != approved_rules.get(c.criterion_id):
                        raise ValueError("applicability outside approved finance rules")
                if (
                    c.status == "not_applicable"
                    and verifiers.applicability(c, scoped) is not True
                ):
                    raise ValueError("unapproved applicability")
                neutral.append(
                    CriterionOutput(
                        criterion_id=c.criterion_id,
                        status="missing" if c.status == "not_applicable" else c.status,
                        rating=c.rating,
                        evidence_ids=c.evidence_ids,
                        rationale=c.rationale,
                        missing_reason=(
                            "not_applicable"
                            if c.status == "not_applicable"
                            else c.missing_reason
                        ),
                    )
                )
            common = DimensionAssessmentOutput(
                criteria=neutral,
                research_gaps=domain.research_gaps,
                caveats=domain.caveats,
            )
            if validate_output(
                common, dimension=d, snapshot=scoped, policy=policy, rubric=rubric
            ):
                raise ValueError("invalid domain output")
            assembled = assemble_evaluation(
                common,
                dimension=d,
                snapshot=scoped,
                policy=policy,
                rubric=rubric,
                schema_version=schema_version,
            )
            evaluations[d] = Evaluation(
                **{
                    **assembled.model_dump(),
                    "criteria": [
                        {**c.model_dump(), "schema_version": schema_version}
                        for c in domain.criteria
                    ],
                }
            )
        return EvaluationBranchResult(
            **identity, status="success", evaluations=evaluations, errors=[]
        )
    except LLMError as err:
        code = err.error_code
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    return EvaluationBranchResult(
        **identity,
        status="failure",
        evaluations=None,
        errors=[
            WorkflowError(
                schema_version=schema_version,
                error_id=f"err:{snapshot.snapshot_id}:business_deal:1",
                run_id=snapshot.run_id,
                candidate_id=snapshot.candidate_id,
                node="business_deal_evaluation",
                error_code=code.value,
                message_redacted=(
                    "BUSINESS_DEAL_OUTPUT_INVALID"
                    if code == ErrorCode.LLM_OUTPUT_INVALID
                    else "BUSINESS_DEAL_TECHNICAL_FAILURE"
                ),
                retryable=is_retryable(code),
                attempt=1,
                timestamp=clock.now(),
            )
        ],
    )
