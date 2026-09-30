"""Market 평가의 시장 맥락 경계 (#59).

rubric/catalog가 제안 상태(D14 OPEN)이므로 proposed rubric + draft policy 조합만
허용한다. 세부 시장 일치는 Evidence 텍스트에서 재추론하지 않고, 상위 조사 단계가
검증해 전달한 ``evidence_id → MarketLink``를 요구한다(#58 Founder 귀속과 같은 방식).

- 후보가 진입하는 세부 시장·지역과 맞지 않는 근거, 통화가 rubric 단위와 다른 규모
  수치는 프롬프트에서 제외하고 사유별 개수만 전달한다.
- 모델 출력은 #22 공통 검증 뒤에 시장 맥락 검증을 한 번 더 받는다. 서로 다른
  시장·지역·연도·통화 수치 혼합, 전망치를 현재 규모로 쓰기, rubric 구간과 맞지 않는
  rating은 위반이며 공통 wrapper가 구조 수정 1회 후 failure로 옮긴다.
- rating을 코드가 정하지 않는다. rubric 구간은 모델 출력 검증에만 쓴다.
"""

from collections import Counter
from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from skala_rag.agents.evaluation import DimensionAssessmentOutput, evaluate_dimension
from skala_rag.contracts.evaluation import EvaluationResult, EvaluationSnapshot
from skala_rag.contracts.evidence import Evidence
from skala_rag.contracts.interfaces import Clock, StructuredLLM
from skala_rag.prompts.market_evaluation import PROMPT_VERSION, SYSTEM_PROMPT
from skala_rag.scoring.catalog import ScoringPolicy

MarketMetric = Literal["tam", "sam", "cagr"]

_METRIC_CRITERION: dict[str, str] = {
    "tam": "market.size",
    "sam": "market.size",
    "cagr": "market.growth",
}
_PERCENT_UNIT = "%"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MarketTarget(_Frozen):
    """후보가 실제 진입하는 세부 시장. 상위 조사 단계가 정한다."""

    segment_id: str = Field(min_length=1)
    geographies: tuple[str, ...] = Field(min_length=1)


class MarketLink(_Frozen):
    """근거 하나의 검증된 시장 귀속. 수치 근거면 지표·기준연도를 함께 둔다.

    ``reference_year``는 규모의 기준연도 또는 CAGR 시작연도, ``end_year``는 CAGR
    종료연도다. ``basis=forecast``는 기준연도 값이 전망치라는 뜻이다.
    """

    segment_id: str = Field(min_length=1)
    metric: MarketMetric | None = None
    basis: Literal["actual", "forecast"] | None = None
    reference_year: int | None = None
    end_year: int | None = None

    @model_validator(mode="after")
    def require_figure_context(self) -> Self:
        if self.metric is None:
            if any(
                v is not None for v in (self.basis, self.reference_year, self.end_year)
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
    """rubric의 market 구간·상한·단위. 코드 기본값 없이 rubric에서만 읽는다."""

    def __init__(self, rubric: Mapping[str, object]) -> None:
        try:
            criteria = rubric["dimensions"]["market"]["criteria"]  # type: ignore[index]
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
        except (KeyError, TypeError, StopIteration) as exc:
            raise ValueError("rubric lacks market bands/caps/unit") from exc


def _band(bands: list[Mapping], value: float) -> int:
    """[min, max) 구간의 rating."""
    for band in bands:
        if ("min" not in band or value >= band["min"]) and (
            "max" not in band or value < band["max"]
        ):
            return int(band["rating"])
    raise ValueError("value outside rubric bands")


def _figure(evidence: Evidence, link: MarketLink) -> dict[str, object]:
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


def _exclusion(
    evidence: Evidence,
    link: MarketLink | None,
    *,
    snapshot: EvaluationSnapshot,
    target: MarketTarget,
    rules: _MarketRules,
) -> str | None:
    """제외 사유(rubric missing_reasons 표기). 통과하면 None.

    상위 조사 결과와 Evidence가 서로 모순되면 ValueError로 거절한다.
    """
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
    return None


def _context_key(criterion_id: str, figure: Mapping[str, object]) -> tuple:
    if criterion_id == "market.size":
        fields = ("metric", "geography", "currency", "reference_year")
    else:
        fields = ("metric", "geography", "reference_year", "end_year")
    return tuple(figure[f] for f in fields)


def market_output_violations(
    output: DimensionAssessmentOutput,
    *,
    figures: Mapping[str, Mapping[str, object]],
    rules: _MarketRules,
) -> list[str]:
    """시장 수치 인용의 맥락·구간 일치 위반. #22 공통 검증을 통과한 출력에 쓴다."""
    violations: list[str] = []
    for c in output.criteria:
        if c.status != "observed" or c.criterion_id not in (
            "market.size",
            "market.growth",
        ):
            continue
        cid = c.criterion_id
        cited = [figures[eid] for eid in c.evidence_ids if eid in figures]
        if not cited:
            violations.append(f"MARKET_FIGURE_REQUIRED: {cid}")
            continue
        if any(_METRIC_CRITERION[str(f["metric"])] != cid for f in cited):
            violations.append(f"MARKET_METRIC_MISMATCH: {cid}")
            continue
        if cid == "market.size" and any(f["basis"] == "forecast" for f in cited):
            violations.append(f"MARKET_FORECAST_AS_ACTUAL: {cid}")
            continue
        if len({_context_key(cid, f) for f in cited}) > 1:
            violations.append(f"MARKET_CONTEXT_MIXED: {cid}")
            continue
        bands_def = rules.size_bands if cid == "market.size" else rules.growth_bands
        bands = {_band(bands_def, float(f["value"])) for f in cited}  # type: ignore[arg-type]
        if cid == "market.size":
            if len(bands) > 1:
                violations.append(f"MARKET_CONFLICT_UNRESOLVED: {cid}")
                continue
            expected = bands.pop()
            if cited[0]["metric"] == "tam":
                expected = min(expected, rules.tam_cap)
        else:
            # rubric conflict_rule: 1구간 차이는 낮은 쪽, 2구간 이상은 missing.
            if max(bands) - min(bands) > 1:
                violations.append(f"MARKET_CONFLICT_UNRESOLVED: {cid}")
                continue
            expected = min(bands)
        if c.rating != expected:
            violations.append(f"MARKET_RATING_BAND_MISMATCH: {cid}")
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
    """검증된 세부 시장 근거만 #22 공통 평가 wrapper에 전달한다.

    ``market_links``는 상위 조사 경계에서 시장 정의·지역·기준연도를 확인한
    결과여야 한다. 링크 밖 근거는 프롬프트와 출력 검증 양쪽에서 제외된다.
    """
    if rubric.get("status") != "proposed" or policy.status != "draft":
        raise ValueError("Market evaluation requires proposed rubric and draft policy")
    if not set(market_links) <= set(snapshot.evidence):
        raise ValueError("Market link references evidence outside snapshot")
    rules = _MarketRules(rubric)

    allowed: dict[str, Evidence] = {}
    figures: dict[str, dict[str, object]] = {}
    excluded: Counter[str] = Counter()
    for eid, evidence in snapshot.evidence.items():
        if not any(cid.startswith("market.") for cid in evidence.criterion_ids):
            continue
        link = market_links.get(eid)
        reason = _exclusion(
            evidence, link, snapshot=snapshot, target=target_market, rules=rules
        )
        if reason is not None:
            excluded[reason] += 1
            continue
        allowed[eid] = evidence
        if link is not None and link.metric is not None:
            figures[eid] = _figure(evidence, link)

    scoped = snapshot.model_copy(
        update={"evidence_ids": list(allowed), "evidence": allowed}, deep=True
    )
    context = {
        "prompt_version": PROMPT_VERSION,
        "target_market": target_market.model_dump(mode="json"),
        "market_figures": figures,
        "excluded_evidence_reasons": dict(sorted(excluded.items())),
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
