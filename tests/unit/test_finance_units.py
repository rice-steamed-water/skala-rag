"""#53 / T21: 재무 Evidence 단위·기간·파생값 검증 (가상 데이터만)."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from skala_rag.contracts.evidence import Evidence
from skala_rag.scoring.finance import (
    Derived,
    FinanceInputError,
    Unavailable,
    convert_currency,
    gross_margin_pct,
    monthly_net_burn,
    parse_period,
    revenue_growth_pct,
    runway_months,
)

ROOT = Path(__file__).resolve().parents[2]
BASE = json.loads((ROOT / "tests/fixtures/contracts.json").read_text())["Evidence"]
FY24 = "2024-01-01/2024-12-31"
FY25 = "2025-01-01/2025-12-31"


def ev(eid, value, unit="억원", currency="KRW", period=None, as_of=None, **kw):
    payload = {
        **BASE,
        "evidence_id": eid,
        "value": value,
        "unit": unit,
        "currency": currency,
        "period": period,
        "value_as_of": as_of,
        **kw,
    }
    return Evidence.model_validate(payload)


def ocf(value, period=FY25, as_of="2025-12-31", **kw):
    return ev("ev-ocf", value, period=period, as_of=as_of, **kw)


def cash(value, as_of="2025-12-31", **kw):
    return ev("ev-cash", value, as_of=as_of, **kw)


# --- 기간 해석 --------------------------------------------------------------


@pytest.mark.parametrize(
    "text,months",
    [(FY25, 12), ("2025-03-01/2025-03-31", 1), ("2024-07-01/2025-06-30", 12)],
)
def test_parse_period(text, months):
    assert parse_period(text).months == months


@pytest.mark.parametrize(
    "text", [None, "FY2025", "2025", "2025-01-02/2025-12-31", "2025-01-01/2025-12-30"]
)
def test_unparseable_period_is_unknown(text):
    assert parse_period(text) is None


# --- 월/연 번레이트 (T21) ----------------------------------------------------


def test_annual_cash_flow_becomes_monthly_average_with_provenance():
    r = monthly_net_burn(ocf(-60))
    assert isinstance(r, Derived)
    assert r.value == Decimal(5) * 10**8
    assert r.unit == "currency_per_month"
    assert r.evidence_kind == "derived"
    assert r.supporting_evidence_ids == ("ev-ocf",)
    assert "period_to_monthly_average" in r.derivation and "12개월" in r.derivation


def test_monthly_cash_flow_is_direct():
    r = monthly_net_burn(ocf(-5, period="2025-12-01/2025-12-31"))
    assert r.value == Decimal(5) * 10**8
    assert "monthly_direct" in r.derivation


def test_unknown_period_does_not_guess_month_or_year():
    r = monthly_net_burn(ocf(-60, period="FY2025"))
    assert r == Unavailable("period_mismatch", r.detail)


@pytest.mark.parametrize("value", [0, 3])
def test_non_negative_cash_flow_has_no_burn(value):
    assert monthly_net_burn(ocf(value)).reason == "non_positive_burn"


# --- 런웨이 (T21) ------------------------------------------------------------


def test_runway_in_months_with_elapsed_adjustment():
    # 현금 90억, 연간 −60억 → 월 5억 → 18개월; as_of = 기준일 당일
    r = runway_months(cash(90), ocf(-60), run_as_of=date(2025, 12, 31))
    assert r.unit == "months" and r.value == Decimal(18)
    assert set(r.supporting_evidence_ids) == {"ev-cash", "ev-ocf"}


def test_runway_elapsed_months_reduce_remaining():
    r = runway_months(cash(90), ocf(-60), run_as_of=date(2026, 6, 30))
    # 181일 / 30.4375 ≈ 5.9466개월 경과
    assert Decimal("12.05") < r.value < Decimal("12.06")
    assert "elapsed" in r.derivation


def test_runway_zero_or_positive_denominator_is_missing():
    r = runway_months(cash(90), ocf(0), run_as_of=date(2025, 12, 31))
    assert r.reason == "non_positive_burn"


def test_runway_cash_date_must_match_cash_flow_end():
    r = runway_months(
        cash(90, as_of="2025-06-30"), ocf(-60), run_as_of=date(2026, 1, 1)
    )
    assert r.reason == "period_mismatch"


def test_runway_currency_mismatch():
    r = runway_months(
        cash(9, currency="USD", unit="million"), ocf(-60), run_as_of=date(2026, 1, 1)
    )
    assert r.reason == "currency_mismatch"


def test_runway_adds_only_confirmed_post_date_funding():
    funding = ev("ev-round", 30, as_of="2026-03-15", event_date="2026-03-15")
    r = runway_months(
        cash(90), ocf(-60), date(2026, 3, 31), post_date_funding=[funding]
    )
    assert "ev-round" in r.supporting_evidence_ids
    assert "기준일 이후 조달" in r.derivation


def test_runway_rejects_funding_before_cash_date():
    funding = ev("ev-round", 30, as_of="2025-06-01", event_date="2025-06-01")
    with pytest.raises(FinanceInputError):
        runway_months(
            cash(90), ocf(-60), date(2026, 3, 31), post_date_funding=[funding]
        )


# --- 성장률·매출총이익률 ------------------------------------------------------


def test_revenue_growth_consecutive_same_length():
    r = revenue_growth_pct(
        ev("ev-rev25", 75, period=FY25, as_of="2025-12-31"),
        ev("ev-rev24", 50, period=FY24, as_of="2024-12-31"),
    )
    assert r.value == Decimal(50)


def test_revenue_growth_non_positive_base_is_missing():
    r = revenue_growth_pct(
        ev("ev-rev25", 10, period=FY25, as_of="2025-12-31"),
        ev("ev-rev24", 0, period=FY24, as_of="2024-12-31"),
    )
    assert r.reason == "non_positive_base"


@pytest.mark.parametrize(
    "prior_period", ["2024-07-01/2024-12-31", "2023-01-01/2023-12-31", None]
)
def test_revenue_growth_period_mismatch(prior_period):
    r = revenue_growth_pct(
        ev("ev-rev25", 75, period=FY25, as_of="2025-12-31"),
        ev("ev-rev24", 50, period=prior_period, as_of="2024-12-31"),
    )
    assert r.reason == "period_mismatch"


def test_unit_scale_applied_before_comparison():
    # 7,500백만원 vs 50억원 = 75억 vs 50억 → 50%
    r = revenue_growth_pct(
        ev("ev-rev25", 7500, unit="백만원", period=FY25, as_of="2025-12-31"),
        ev("ev-rev24", 50, unit="억원", period=FY24, as_of="2024-12-31"),
    )
    assert r.value == Decimal(50)


def test_unknown_unit_is_not_guessed():
    r = revenue_growth_pct(
        ev("ev-rev25", 75, unit="억", period=FY25, as_of="2025-12-31"),
        ev("ev-rev24", 50, period=FY24, as_of="2024-12-31"),
    )
    assert r.reason == "unknown_unit"


def test_gross_margin_same_period():
    r = gross_margin_pct(
        ev("ev-rev", 100, period=FY25, as_of="2025-12-31"),
        ev("ev-cogs", 70, period=FY25, as_of="2025-12-31"),
    )
    assert r.value == Decimal(30)


def test_gross_margin_period_mismatch():
    r = gross_margin_pct(
        ev("ev-rev", 100, period=FY25, as_of="2025-12-31"),
        ev("ev-cogs", 70, period=FY24, as_of="2024-12-31"),
    )
    assert r.reason == "period_mismatch"


# --- 입력 근거 규칙 -----------------------------------------------------------


def test_estimated_input_never_becomes_fact():
    r = monthly_net_burn(ocf(-60, evidence_kind="estimated"))
    assert r.reason == "estimated_input"


def test_conflicting_input_is_missing():
    r = monthly_net_burn(ocf(-60, conflicts_with=["ev-other"]))
    assert r.reason == "unresolved_conflict"


def test_other_company_input_rejected():
    r = revenue_growth_pct(
        ev("ev-rev25", 75, period=FY25, as_of="2025-12-31"),
        ev("ev-rev24", 50, period=FY24, as_of="2024-12-31", candidate_id="co-other"),
    )
    assert r.reason == "attribution_mismatch"


def test_missing_value_is_not_disclosed():
    payload = {**BASE, "evidence_id": "ev-x", "period": FY25}
    r = monthly_net_burn(Evidence.model_validate(payload))
    assert r.reason == "not_disclosed"


# --- 통화 환산 provenance -----------------------------------------------------


def _rate(value=1400, unit="USD/KRW", as_of="2025-12-31", currency="KRW", **kw):
    return ev("ev-fx", value, unit=unit, currency=currency, as_of=as_of, **kw)


def test_currency_conversion_records_rate_provenance():
    amount = ev("ev-usd", 2, unit="million", currency="USD", as_of="2025-12-31")
    r = convert_currency(amount, _rate(), "KRW")
    assert r.value == Decimal(2800) * 10**6
    assert r.currency == "KRW"
    assert r.supporting_evidence_ids == ("ev-usd", "ev-fx")


def test_currency_conversion_rate_date_must_match():
    amount = ev("ev-usd", 2, unit="million", currency="USD", as_of="2025-12-31")
    assert convert_currency(amount, _rate(as_of="2025-06-30"), "KRW").reason == (
        "period_mismatch"
    )


def test_currency_conversion_wrong_pair():
    amount = ev("ev-usd", 2, unit="million", currency="USD", as_of="2025-12-31")
    assert convert_currency(amount, _rate(unit="EUR/KRW"), "KRW").reason == (
        "currency_mismatch"
    )


def test_currency_conversion_rate_currency_matches_target():
    amount = ev("ev-usd", 2, unit="million", currency="USD", as_of="2025-12-31")
    result = convert_currency(amount, _rate(currency="USD"), "KRW")
    assert isinstance(result, Unavailable)
    assert result.reason == "currency_mismatch"


def test_currency_conversion_rejects_other_company_rate():
    amount = ev("ev-usd", 2, unit="million", currency="USD", as_of="2025-12-31")
    result = convert_currency(amount, _rate(candidate_id="co-other"), "KRW")
    assert isinstance(result, Unavailable)
    assert result.reason == "attribution_mismatch"


def test_currency_conversion_accepts_industry_rate():
    amount = ev("ev-usd", 2, unit="million", currency="USD", as_of="2025-12-31")
    result = convert_currency(amount, _rate(scope="industry", candidate_id=None), "KRW")
    assert isinstance(result, Derived)
    assert result.value == Decimal(2800) * 10**6
