"""Lossless offline terminal-envelope conversion; never evaluator admission."""

from collections.abc import Callable, Collection, Sequence
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel

from skala_rag.contracts.evaluation import EvaluationResult, EvaluationSnapshot
from skala_rag.contracts.v3 import BRANCH_DIMENSIONS, EvaluationBranchResult
from skala_rag.graph.evaluation_v3 import validate_branch_v3
from skala_rag.scoring.catalog import Criterion

BaselineBranchId = Literal["founder", "market", "technology"]
_BASELINE_BRANCHES = frozenset({"founder", "market", "technology"})
_INVALID = "Invalid baseline-to-v3 adapter input"


def _payload(value):
    """Detach every field before validation, including forged model_copy extras.

    model_dump can omit undeclared __dict__ keys and subclass-only fields. Build
    the validation payload explicitly instead of silently losing such data.
    """
    if isinstance(value, BaseModel):
        fields = type(value).model_fields
        if set(vars(value)) - set(fields) or value.model_extra:
            raise ValueError(_INVALID)
        return {name: _payload(getattr(value, name)) for name in fields}
    if isinstance(value, dict):
        return {key: _payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_payload(item) for item in value]
    return deepcopy(value)


def _schema_matches(payload, schema_version):
    if isinstance(payload, dict):
        if "schema_version" in payload and payload["schema_version"] != schema_version:
            raise ValueError(_INVALID)
        for value in payload.values():
            _schema_matches(value, schema_version)
    elif isinstance(payload, list):
        for value in payload:
            _schema_matches(value, schema_version)


def _catalog(criteria):
    catalog = tuple(Criterion.model_validate(_payload(c)) for c in criteria)
    dimensions = set().union(*BRANCH_DIMENSIONS.values())
    if (
        len(catalog) != 23
        or len({c.criterion_id for c in catalog}) != 23
        or {c.dimension for c in catalog} != dimensions
        or any(not c.criterion_id.startswith(c.dimension + ".") for c in catalog)
    ):
        raise ValueError(_INVALID)
    return catalog


def adapt_baseline_branch_result(
    result: EvaluationResult,
    *,
    branch_id: BaselineBranchId,
    snapshot: EvaluationSnapshot,
    criteria: Sequence[Criterion],
    industry_evidence_dimensions: Collection[str],
) -> EvaluationBranchResult:
    """Revalidate and convert one terminal dimension, preserving every field.

    The supplied frozen snapshot/catalog are caller-owned generation authority,
    not approval or live admission. Invalid input raises a redacted ValueError;
    it never becomes a fabricated Missing assessment or a rewritten generation.
    """
    try:
        if (
            branch_id not in _BASELINE_BRANCHES
            or not isinstance(result, EvaluationResult)
            or not isinstance(snapshot, EvaluationSnapshot)
        ):
            raise ValueError(_INVALID)
        snapshot = EvaluationSnapshot.model_validate(
            _payload(snapshot), context={"execution_mode": "fixture"}
        )
        catalog = _catalog(criteria)
        industry = frozenset(industry_evidence_dimensions)
        if not industry <= set().union(*BRANCH_DIMENSIONS.values()):
            raise ValueError(_INVALID)
        payload = _payload(result)
        _schema_matches(payload, snapshot.schema_version)
        result = EvaluationResult.model_validate(payload)
        if result.dimension != branch_id:
            raise ValueError(_INVALID)
        payload = result.model_dump(mode="python")
        evaluation = payload.pop("evaluation")
        payload.pop("dimension")
        payload["branch_id"] = branch_id
        payload["evaluations"] = (
            {branch_id: evaluation} if evaluation is not None else None
        )
        converted = EvaluationBranchResult.model_validate(payload)
        validate_branch_v3(
            converted,
            snapshot,
            criteria=catalog,
            industry_evidence_dimensions=industry,
            applicability_validator=None,
        )
        return converted
    except Exception:
        # Only adapter-owned detachment/validation runs here, never upstream.
        # Malformed DTO hooks may raise any Exception; do not catch BaseException.
        raise ValueError(_INVALID) from None


def bind_baseline_evaluator_v3(
    branch_id: BaselineBranchId,
    evaluate: Callable[[EvaluationSnapshot], EvaluationResult],
    *,
    criteria: Sequence[Criterion],
    industry_evidence_dimensions: Collection[str],
) -> Callable[[EvaluationSnapshot], EvaluationBranchResult]:
    """Bind one upstream invocation, with no new retries, repair or admission.

    Upstream receives a detached copy; validation uses the unmodified original
    frozen snapshot. Upstream exceptions propagate to the existing graph gate.
    Technology callers must explicitly extract .result and retain their receipt.
    """
    try:
        if branch_id not in _BASELINE_BRANCHES or not callable(evaluate):
            raise ValueError(_INVALID)
        catalog = _catalog(criteria)
        industry = frozenset(industry_evidence_dimensions)
        if not industry <= set().union(*BRANCH_DIMENSIONS.values()):
            raise ValueError(_INVALID)
    except Exception:
        raise ValueError(_INVALID) from None

    def call(snapshot: EvaluationSnapshot) -> EvaluationBranchResult:
        try:
            if not isinstance(snapshot, EvaluationSnapshot):
                raise ValueError(_INVALID)
            frozen = EvaluationSnapshot.model_validate(
                _payload(snapshot), context={"execution_mode": "fixture"}
            )
            upstream_snapshot = frozen.model_copy(deep=True)
        except Exception:
            raise ValueError(_INVALID) from None
        # Keep the real upstream call outside adapter-input redaction.
        result = evaluate(upstream_snapshot)
        return adapt_baseline_branch_result(
            result,
            branch_id=branch_id,
            snapshot=frozen,
            criteria=catalog,
            industry_evidence_dimensions=industry,
        )

    return call
