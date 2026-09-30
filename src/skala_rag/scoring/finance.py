"""재무 Evidence 단위·기간·파생값 검증 — T21, scoring.md §4, contracts §3.

구조화 Evidence의 금액·기간을 해석해 파생값(월 번레이트, 런웨이, 성장률,
매출총이익률, 통화 환산)을 만든다. 조건이 맞지 않으면 값을 만들지 않고
``Unavailable``(missing 사유)을 돌려준다. 점수 구간·rating은 다루지 않는다
(D14 rubric, #11).

기간 표기 규약: ``Evidence.period``는 ``YYYY-MM-DD/YYYY-MM-DD``(양끝 포함,
월 1일 시작·월말 종료)만 해석한다. 그 밖의 표기는 기간 미상으로 본다.
"""

import calendar
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, localcontext
from typing import Literal

from skala_rag.contracts.evidence import Evidence

# 명시적 단위 배수만 허용한다. 모르는 단위를 추측하지 않는다.
UNIT_SCALE: dict[str, Decimal] = {
    "원": Decimal(1),
    "천원": Decimal(10) ** 3,
    "백만원": Decimal(10) ** 6,
    "억원": Decimal(10) ** 8,
    "조원": Decimal(10) ** 12,
    "one": Decimal(1),
    "thousand": Decimal(10) ** 3,
    "million": Decimal(10) ** 6,
    "billion": Decimal(10) ** 9,
}

# 경과 개월 환산: 평균 월 길이(365.25 / 12 일).
DAYS_PER_MONTH = Decimal("30.4375")

MissingCode = Literal[
    "not_disclosed",
    "period_mismatch",
    "currency_mismatch",
    "unknown_unit",
    "non_positive_base",
    "non_positive_burn",
    "estimated_input",
    "unresolved_conflict",
    "attribution_mismatch",
    "after_as_of",
]

_PERIOD = re.compile(r"^(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})$")


@dataclass(frozen=True)
class Period:
    """월 단위로 딱 떨어지는 회계 기간 (양끝 포함)."""

    start: date
    end: date

    @property
    def months(self) -> int:
        return (
            (self.end.year - self.start.year) * 12
            + (self.end.month - self.start.month)
            + 1
        )

    def label(self) -> str:
        return f"{self.start.isoformat()}/{self.end.isoformat()}"


@dataclass(frozen=True)
class Amount:
    """단위 배수를 적용한 기본 통화 금액."""

    value: Decimal
    currency: str
    as_of: date | None
    period: Period | None
    evidence_id: str


@dataclass(frozen=True)
class Derived:
    """derived Evidence로 옮길 값과 provenance."""

    value: Decimal
    unit: str
    currency: str | None
    period: str | None
    value_as_of: date | None
    supporting_evidence_ids: tuple[str, ...]
    derivation: str
    evidence_kind: Literal["derived"] = "derived"


@dataclass(frozen=True)
class Unavailable:
    """값을 만들지 않은 이유. 부정 평가가 아니라 missing 사유다."""

    reason: MissingCode
    detail: str


Result = Derived | Unavailable


class FinanceInputError(ValueError):
    """호출 측이 잘못된 조합을 넘긴 경우(데이터 부족과 구별)."""


def parse_period(text: str | None) -> Period | None:
    """``YYYY-MM-DD/YYYY-MM-DD``만 해석한다. 월 경계가 아니면 None."""
    if text is None:
        return None
    m = _PERIOD.match(text.strip())
    if not m:
        return None
    try:
        start, end = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
    except ValueError:
        return None
    last_day = calendar.monthrange(end.year, end.month)[1]
    if start.day != 1 or end.day != last_day or end < start:
        return None
    return Period(start, end)


def _check_inputs(evidence: Sequence[Evidence]) -> Unavailable | None:
    """공통 입력 검증: 같은 기업, 추정값·미해결 상충 제외."""
    candidates = {ev.candidate_id for ev in evidence}
    if len(candidates) != 1 or None in candidates:
        return Unavailable("attribution_mismatch", "입력 근거의 기업이 하나가 아니다")
    for ev in evidence:
        if ev.evidence_kind == "estimated":
            return Unavailable(
                "estimated_input", f"{ev.evidence_id}: 추정값은 입력 불가"
            )
        if ev.conflicts_with:
            return Unavailable("unresolved_conflict", f"{ev.evidence_id}: 상충 근거")
    return None


def to_amount(ev: Evidence) -> Amount | Unavailable:
    """Evidence 금액을 기본 통화 단위로 환산한다."""
    if ev.value is None or ev.currency is None or ev.unit is None:
        return Unavailable("not_disclosed", f"{ev.evidence_id}: 금액·통화·단위 필요")
    scale = UNIT_SCALE.get(ev.unit.strip())
    if scale is None:
        return Unavailable("unknown_unit", f"{ev.evidence_id}: 단위 '{ev.unit}'")
    return Amount(
        value=Decimal(str(ev.value)) * scale,
        currency=ev.currency.strip().upper(),
        as_of=ev.value_as_of,
        period=parse_period(ev.period),
        evidence_id=ev.evidence_id,
    )


def _div(a: Decimal, b: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 28
        return a / b


def monthly_net_burn(operating_cash_flow: Evidence) -> Result:
    """영업활동현금흐름(음수=유출)을 기간 개월 수로 나눈 월 순현금소모.

    기간이 1개월이 아니면 평균 환산값이며 derivation에 그 사실을 남긴다.
    유출이 없으면(흐름 >= 0) 값을 만들지 않는다.
    """
    bad = _check_inputs([operating_cash_flow])
    if bad:
        return bad
    amt = to_amount(operating_cash_flow)
    if isinstance(amt, Unavailable):
        return amt
    if amt.period is None:
        return Unavailable("period_mismatch", "현금흐름 기간 미상 — 월/연 구분 불가")
    if amt.value >= 0:
        return Unavailable(
            "non_positive_burn", "영업활동현금흐름 >= 0, 순현금소모 없음"
        )
    months = amt.period.months
    kind = "monthly_direct" if months == 1 else "period_to_monthly_average"
    value = _div(-amt.value, Decimal(months))
    return Derived(
        value=value,
        unit="currency_per_month",
        currency=amt.currency,
        period=amt.period.label(),
        value_as_of=amt.period.end,
        supporting_evidence_ids=(amt.evidence_id,),
        derivation=(
            f"{kind}: |operating_cash_flow {amt.value} {amt.currency}| / {months}개월"
            f" ({amt.period.label()})"
        ),
    )


def months_between(start: date, end: date) -> Decimal:
    """경과 개월 수 = 일수 / 30.4375 (평균 월 길이)."""
    return _div(Decimal((end - start).days), DAYS_PER_MONTH)


def runway_months(
    cash: Evidence,
    operating_cash_flow: Evidence,
    run_as_of: date,
    post_date_funding: Sequence[Evidence] = (),
) -> Result:
    """잔여 런웨이(개월) = (현금 + 기준일 이후 확인된 조달) / 월 번레이트 − 경과 개월.

    현금 기준일은 현금흐름 기간 종료일과 같아야 한다. 결과 단위는 항상 개월이다.
    """
    bad = _check_inputs([cash, operating_cash_flow, *post_date_funding])
    if bad:
        return bad
    burn = monthly_net_burn(operating_cash_flow)
    if isinstance(burn, Unavailable):
        return burn
    cash_amt = to_amount(cash)
    if isinstance(cash_amt, Unavailable):
        return cash_amt
    if cash_amt.as_of is None:
        return Unavailable("period_mismatch", "현금 기준일 미상")
    if cash_amt.as_of != burn.value_as_of:
        return Unavailable(
            "period_mismatch",
            f"현금 기준일 {cash_amt.as_of} ≠ 현금흐름 종료일 {burn.value_as_of}",
        )
    if cash_amt.currency != burn.currency:
        return Unavailable("currency_mismatch", "현금과 현금흐름 통화가 다르다")
    if cash_amt.as_of > run_as_of:
        return Unavailable("after_as_of", "현금 기준일이 실행 as_of 이후")

    total = cash_amt.value
    funding_ids: list[str] = []
    for f in post_date_funding:
        amt = to_amount(f)
        if isinstance(amt, Unavailable):
            return amt
        if amt.currency != cash_amt.currency:
            return Unavailable(
                "currency_mismatch", f"{f.evidence_id}: 조달 통화 불일치"
            )
        event = f.event_date
        if event is None or not (cash_amt.as_of < event <= run_as_of):
            raise FinanceInputError(
                f"{f.evidence_id}: 조달 사건일은 현금 기준일 이후·as_of 이전이어야 한다"
            )
        total += amt.value
        funding_ids.append(f.evidence_id)

    elapsed = months_between(cash_amt.as_of, run_as_of)
    runway = _div(total, burn.value) - elapsed
    funding_note = f" + 기준일 이후 조달 {funding_ids}" if funding_ids else ""
    return Derived(
        value=runway,
        unit="months",
        currency=None,
        period=None,
        value_as_of=run_as_of,
        supporting_evidence_ids=(
            cash_amt.evidence_id,
            *burn.supporting_evidence_ids,
            *funding_ids,
        ),
        derivation=(
            f"(cash {cash_amt.value}{funding_note}) / monthly_net_burn {burn.value}"
            f" − elapsed {elapsed}개월 ({cash_amt.as_of}→{run_as_of});"
            f" burn: {burn.derivation}"
        ),
    )


def _same_length_consecutive(prior: Period, current: Period) -> bool:
    return (
        prior.months == current.months
        and prior.end + timedelta(days=1) == current.start
    )


def revenue_growth_pct(current: Evidence, prior: Evidence) -> Result:
    """(당기 − 전기) / 전기 × 100. 같은 길이의 연속 기간·같은 통화만."""
    bad = _check_inputs([current, prior])
    if bad:
        return bad
    cur, pri = to_amount(current), to_amount(prior)
    for amt in (cur, pri):
        if isinstance(amt, Unavailable):
            return amt
    if (
        cur.period is None
        or pri.period is None
        or not _same_length_consecutive(pri.period, cur.period)
    ):
        return Unavailable("period_mismatch", "같은 길이의 연속 두 기간이 아니다")
    if cur.currency != pri.currency:
        return Unavailable("currency_mismatch", "두 기간 통화가 다르다")
    if pri.value <= 0:
        return Unavailable("non_positive_base", "전기 매출 <= 0")
    value = _div((cur.value - pri.value) * 100, pri.value)
    return Derived(
        value=value,
        unit="percent",
        currency=None,
        period=cur.period.label(),
        value_as_of=cur.period.end,
        supporting_evidence_ids=(cur.evidence_id, pri.evidence_id),
        derivation=(
            f"({cur.value} − {pri.value}) / {pri.value} × 100"
            f" [{pri.period.label()} → {cur.period.label()}]"
        ),
    )


def gross_margin_pct(revenue: Evidence, cost_of_revenue: Evidence) -> Result:
    """(매출 − 매출원가) / 매출 × 100. 같은 기간·통화만."""
    bad = _check_inputs([revenue, cost_of_revenue])
    if bad:
        return bad
    rev, cogs = to_amount(revenue), to_amount(cost_of_revenue)
    for amt in (rev, cogs):
        if isinstance(amt, Unavailable):
            return amt
    if rev.period is None or rev.period != cogs.period:
        return Unavailable("period_mismatch", "매출과 매출원가 기간이 다르거나 미상")
    if rev.currency != cogs.currency:
        return Unavailable("currency_mismatch", "매출과 매출원가 통화가 다르다")
    if rev.value <= 0:
        return Unavailable("non_positive_base", "매출 <= 0")
    value = _div((rev.value - cogs.value) * 100, rev.value)
    return Derived(
        value=value,
        unit="percent",
        currency=None,
        period=rev.period.label(),
        value_as_of=rev.period.end,
        supporting_evidence_ids=(rev.evidence_id, cogs.evidence_id),
        derivation=f"({rev.value} − {cogs.value}) / {rev.value} × 100",
    )


def convert_currency(amount: Evidence, rate: Evidence, target_currency: str) -> Result:
    """환율 Evidence로 통화를 환산한다. 환율 기본값은 없다.

    rate Evidence: value = 1 source 통화당 target 통화, unit =
    ``"{SOURCE}/{TARGET}"``, value_as_of = 환율 기준일(금액 기준일과 같아야 함).
    """
    bad = _check_inputs([amount])
    if bad:
        return bad
    if (rate.scope == "company" and rate.candidate_id != amount.candidate_id) or (
        rate.scope == "industry" and rate.candidate_id is not None
    ):
        return Unavailable("attribution_mismatch", "환율 근거의 기업 귀속이 다르다")
    if rate.evidence_kind == "estimated" or rate.conflicts_with:
        return Unavailable("estimated_input", f"{rate.evidence_id}: 환율 근거 부적합")
    amt = to_amount(amount)
    if isinstance(amt, Unavailable):
        return amt
    target = target_currency.strip().upper()
    expected_unit = f"{amt.currency}/{target}"
    if (
        rate.currency is None
        or rate.currency.strip().upper() != target
        or not target
        or rate.value is None
        or rate.unit is None
        or rate.unit.strip().upper() != expected_unit
    ):
        return Unavailable("currency_mismatch", f"환율 단위 {expected_unit} 필요")
    if rate.value <= 0:
        return Unavailable("non_positive_base", "환율 <= 0")
    basis = amt.as_of or (amt.period.end if amt.period else None)
    if basis is None or rate.value_as_of != basis:
        return Unavailable("period_mismatch", "환율 기준일이 금액 기준일과 다르다")
    value = amt.value * Decimal(str(rate.value))
    return Derived(
        value=value,
        unit="one",
        currency=target,
        period=amt.period.label() if amt.period else None,
        value_as_of=basis,
        supporting_evidence_ids=(amt.evidence_id, rate.evidence_id),
        derivation=(
            f"{amt.value} {amt.currency} × {rate.value} ({expected_unit}, {basis})"
        ),
    )
