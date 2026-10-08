"""Market evaluation bounded by verified segment attribution (#59)."""

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Literal, Self, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from skala_rag.agents.evaluation import DimensionAssessmentOutput, evaluate_dimension
from skala_rag.contracts.evaluation import EvaluationResult, EvaluationSnapshot
from skala_rag.contracts.evidence import Evidence
from skala_rag.contracts.interfaces import Clock, StructuredLLM
from skala_rag.prompts.market_evaluation import SYSTEM_PROMPT
from skala_rag.scoring.catalog import ScoringPolicy

MarketMetric = Literal["tam", "sam", "cagr"]


class _MarketFigure(TypedDict):
    metric: MarketMetric | None
    basis: Literal["actual", "forecast"] | None
    reference_year: int | None
    end_year: int | None
    geography: str | None
    currency: str | None
    unit: str | None
    value: float


_METRIC_CRITERION: dict[str, str] = {
    "tam": "market.size",
    "sam": "market.size",
    "cagr": "market.growth",
}
_PERCENT_UNIT = "%"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MarketTarget(_Frozen):
    """Candidate's verified sub-market and geographies."""

    segment_id: str = Field(min_length=1)
    geographies: tuple[str, ...] = Field(min_length=1)


class MarketLink(_Frozen):
    """Verified attribution between one evidence item and market context."""

    segment_id: str = Field(min_length=1)
    metric: MarketMetric | None = None
    basis: Literal["actual", "forecast"] | None = None
    reference_year: int | None = None
    end_year: int | None = None

    @model_validator(mode="after")
    def require_figure_context(self) -> Self:
        if self.metric is None:
            if any(
                value is not None
                for value in (self.basis, self.reference_year, self.end_year)
            ):
                raise ValueError("figure context requires metric")
            return self
        if self.basis is None or self.reference_year is None:
            raise ValueError("market figure requires basis and reference_year")
        if self.metric == "cagr":
            if self.end_year is None or self.end_year <= self.reference_year:
                raise ValueError("CAGR requires end_year after reference_year")
        elif self.end_year is not None:
            raise ValueError("market size figure has no end_year")
        return self


class _MarketRules:
    """Market bands, cap, and unit read from the rubric."""

    def __init__(self, rubric: Mapping[str, object]) -> None:
        try:
            dimensions = rubric["dimensions"]
            if not isinstance(dimensions, Mapping):
                raise TypeError("rubric dimensions must be a mapping")
            market = dimensions["market"]
            if not isinstance(market, Mapping):
                raise TypeError("rubric market must be a mapping")
            criteria = market["criteria"]
            if not isinstance(criteria, Mapping):
                raise TypeError("rubric market criteria must be a mapping")
            size = criteria["market.size"]
            growth = criteria["market.growth"]
            self.size_unit: str = size["unit"]
            self.size_bands: list[Mapping] = list(size["bands"])
            self.tam_cap: int = next(
                cap["max_rating"]
                for cap in size["caps"]
                if cap["condition"] == "tam_only"
            )
            if growth["metric"] != "cagr_pct":
                raise ValueError("market.growth metric must be cagr_pct")
            self.growth_bands: list[Mapping] = list(growth["bands"])
            missing_reasons = rubric["missing_reasons"]
            if not isinstance(missing_reasons, Iterable):
                raise TypeError("rubric missing_reasons must be iterable")
            self.missing_reasons: frozenset[str] = frozenset(missing_reasons)
        except (KeyError, TypeError, StopIteration) as exc:
            raise ValueError("rubric lacks market bands/caps/unit") from exc


def _band(bands: list[Mapping], value: float) -> int:
    """Return the rating for the rubric's [min, max) band."""
    for band in bands:
        if ("min" not in band or value >= band["min"]) and (
            "max" not in band or value < band["max"]
        ):
            return int(band["rating"])
    raise ValueError("value outside rubric bands")


def _figure(evidence: Evidence, link: MarketLink) -> _MarketFigure:
    assert evidence.value is not None
    return {
        "metric": link.metric,
        "basis": link.basis,
        "reference_year": link.reference_year,
        "end_year": link.end_year,
        "geography": evidence.geography,
        "currency": evidence.currency,
        "unit": evidence.unit,
        "value": evidence.value,
    }


_PROMPT_FIGURE_FIELDS = ("metric", "basis", "reference_year", "end_year", "geography")


def _prompt_figure(figure: _MarketFigure, rules: _MarketRules) -> dict:
    """Attach the rubric band and deterministic TAM cap to a figure."""
    out = {key: figure[key] for key in _PROMPT_FIGURE_FIELDS}
    bands = (
        rules.size_bands
        if figure["metric"] in ("tam", "sam")
        else rules.growth_bands
    )
    out["rubric_band"] = _band(bands, figure["value"])
    if figure["metric"] == "tam":
        out["rubric_max_rating"] = rules.tam_cap
    return out


def _exclusion(
    evidence: Evidence,
    link: MarketLink | None,
    *,
    snapshot: EvaluationSnapshot,
    target: MarketTarget,
    rules: _MarketRules,
) -> str | None:
    """Return an exclusion reason, or reject inconsistent verified input."""
    own = evidence.scope == "company" and evidence.candidate_id == snapshot.candidate_id
    industry = evidence.scope == "industry" and evidence.candidate_id is None
    if link is None or not (own or industry):
        return "attribution_unverified"
    if link.segment_id != target.segment_id:
        return "segment_mismatch"
    numeric_tags = {"market.size", "market.growth"} & set(evidence.criterion_ids)
    if link.metric is None:
        if evidence.value is not None and numeric_tags:
            raise ValueError("numeric market evidence requires a figure link")
        return None
    if _METRIC_CRITERION[link.metric] not in evidence.criterion_ids:
        raise ValueError("figure metric does not match evidence criterion")
    if evidence.value is None or evidence.geography is None:
        raise ValueError("market figure requires value and geography")
    if evidence.geography not in target.geographies:
        return "segment_mismatch"
    if link.metric == "cagr":
        if evidence.unit != _PERCENT_UNIT or evidence.currency is not None:
            raise ValueError("CAGR figure must be a percent without currency")
        return None
    if evidence.currency != rules.size_unit:
        return "currency_mismatch"
    if evidence.unit != rules.size_unit:
        raise ValueError("market size figure unit must match rubric unit")
    if evidence.value_as_of is None:
        raise ValueError("market size figure requires value_as_of")
    if evidence.value_as_of.year != link.reference_year:
        raise ValueError("market size value_as_of year must match reference_year")
    return None


def _context_key(criterion_id: str, figure: Mapping[str, object]) -> tuple:
    if criterion_id == "market.size":
        fields = ("metric", "geography", "currency", "reference_year")
    else:
        fields = ("metric", "geography", "reference_year", "end_year")
    return tuple(figure[field] for field in fields)


def _code(code: str, criterion_id: str) -> str:
    return f"{code}_{criterion_id.removeprefix('market.').upper()}: {criterion_id}"


def market_output_violations(
    output: DimensionAssessmentOutput,
    *,
    figures: Mapping[str, _MarketFigure],
    rules: _MarketRules,
) -> list[str]:
    """Check market figure context and rubric-band consistency."""
    violations: list[str] = []
    for criterion in output.criteria:
        if (
            criterion.status == "missing"
            and criterion.missing_reason not in rules.missing_reasons
        ):
            violations.append(
                _code("MARKET_MISSING_REASON_INVALID", criterion.criterion_id)
            )
        if criterion.status != "observed" or criterion.criterion_id not in (
            "market.size",
            "market.growth",
        ):
            continue
        criterion_id = criterion.criterion_id
        cited = [
            figures[evidence_id]
            for evidence_id in criterion.evidence_ids
            if evidence_id in figures
        ]
        if not cited:
            violations.append(_code("MARKET_FIGURE_REQUIRED", criterion_id))
            continue
        if any(
            _METRIC_CRITERION[str(figure["metric"])] != criterion_id
            for figure in cited
        ):
            violations.append(_code("MARKET_METRIC_MISMATCH", criterion_id))
            continue
        if criterion_id == "market.size" and any(
            figure["basis"] == "forecast" for figure in cited
        ):
            violations.append(_code("MARKET_FORECAST_AS_ACTUAL", criterion_id))
            continue
        if len({_context_key(criterion_id, figure) for figure in cited}) > 1:
            violations.append(_code("MARKET_CONTEXT_MIXED", criterion_id))
            continue
        bands_definition = (
            rules.size_bands if criterion_id == "market.size" else rules.growth_bands
        )
        bands = {_band(bands_definition, figure["value"]) for figure in cited}
        if criterion_id == "market.size":
            if len(bands) > 1:
                violations.append(_code("MARKET_CONFLICT_UNRESOLVED", criterion_id))
                continue
            expected = bands.pop()
            if cited[0]["metric"] == "tam" and expected > rules.tam_cap:
                if criterion.rating is not None and criterion.rating > rules.tam_cap:
                    violations.append(_code("MARKET_TAM_CAP_EXCEEDED", criterion_id))
                    continue
                expected = rules.tam_cap
        else:
            if max(bands) - min(bands) > 1:
                violations.append(_code("MARKET_CONFLICT_UNRESOLVED", criterion_id))
                continue
            expected = min(bands)
        if criterion.rating != expected:
            violations.append(_code("MARKET_RATING_BAND_MISMATCH", criterion_id))
    return violations


def evaluate_market(
    snapshot: EvaluationSnapshot,
    *,
    target_market: MarketTarget,
    market_links: Mapping[str, MarketLink],
    rubric: Mapping[str, object],
    llm: StructuredLLM,
    policy: ScoringPolicy,
    clock: Clock,
    schema_version: str,
) -> EvaluationResult:
    """Evaluate only evidence attributed to the candidate's verified sub-market."""
    if getattr(policy, "status", None) != "draft":
        raise ValueError(
            "Market core requires draft policy; actual admission unavailable"
        )
    if rubric.get("rubric_version") != "core-0.1.0":
        raise ValueError("Market evaluation requires approved core-0.1.0")
    if rubric.get("status") != "approved":
        raise ValueError("Market evaluation requires approved rubric (D14 core)")
    if not set(market_links) <= set(snapshot.evidence):
        raise ValueError("Market link references evidence outside snapshot")
    rules = _MarketRules(rubric)

    allowed: dict[str, Evidence] = {}
    figures: dict[str, _MarketFigure] = {}
    excluded: Counter[str] = Counter()
    for evidence_id, evidence in snapshot.evidence.items():
        if not any(cid.startswith("market.") for cid in evidence.criterion_ids):
            continue
        link = market_links.get(evidence_id)
        reason = _exclusion(
            evidence,
            link,
            snapshot=snapshot,
            target=target_market,
            rules=rules,
        )
        if reason is not None:
            excluded[reason] += 1
            continue
        allowed[evidence_id] = evidence
        if link is not None and link.metric is not None:
            figures[evidence_id] = _figure(evidence, link)

    scoped = snapshot.model_copy(
        update={"evidence_ids": list(allowed), "evidence": allowed}, deep=True
    )
    context = {
        "target_market": target_market.model_dump(mode="json"),
        "market_figures": {
            evidence_id: _prompt_figure(figure, rules)
            for evidence_id, figure in figures.items()
        },
        "excluded_evidence_reasons": dict(sorted(excluded.items())),
        "missing_reasons": sorted(rules.missing_reasons),
    }
    return evaluate_dimension(
        "market",
        scoped,
        rubric,
        llm=llm,
        policy=policy,
        clock=clock,
        schema_version=schema_version,
        system_prompt=SYSTEM_PROMPT,
        prompt_context=context,
        extra_validator=lambda output: market_output_violations(
            output, figures=figures, rules=rules
        ),
    )
