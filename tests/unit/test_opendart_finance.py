"""#67: OpenDART 재무제표 → 재무 Evidence → T21 파생값 (가상 응답만, 네트워크 없음)."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.fakes import FakeClock
from skala_rag.scoring.finance import Derived, Unavailable
from skala_rag.tools.company_research import CallBudget
from skala_rag.tools.opendart_finance import (
    OpenDartFinancials,
    derive_financials,
    fiscal_period,
    parse_amount,
    select_rows,
)
from skala_rag.tools.source_fetch import FetchPolicy, SafeFetcher

KEY = "test-key-not-real"
CORP = "00999999"


def row(sj, aid, name, cur, prev, currency="KRW"):
    return {
        "rcept_no": "20250311000001",
        "reprt_code": "11011",
        "bsns_year": "2024",
        "corp_code": CORP,
        "sj_div": sj,
        "sj_nm": {"BS": "재무상태표", "IS": "손익계산서", "CF": "현금흐름표"}.get(
            sj, sj
        ),
        "account_id": aid,
        "account_nm": name,
        "thstrm_nm": "제 10 기",
        "thstrm_amount": cur,
        "frmtrm_nm": "제 9 기",
        "frmtrm_amount": prev,
        "currency": currency,
    }


ROWS = [
    row(
        "BS",
        "ifrs-full_CashAndCashEquivalents",
        "현금및현금성자산",
        "9,000,000,000",
        "5,000,000,000",
    ),
    row("IS", "ifrs-full_Revenue", "매출액", "7,500,000,000", "5,000,000,000"),
    row("IS", "ifrs-full_CostOfSales", "매출원가", "5,250,000,000", "3,600,000,000"),
    row(
        "IS",
        "dart_OperatingIncomeLoss",
        "영업이익(손실)",
        "-1,500,000,000",
        "-2,000,000,000",
    ),
    row(
        "CF",
        "ifrs-full_CashFlowsFromUsedInOperatingActivities",
        "영업활동현금흐름",
        "-6,000,000,000",
        "-4,000,000,000",
    ),
]


class Site:
    def __init__(self, company=None, cfs=None, ofs=None):
        self.company = company or {
            "status": "000",
            "corp_name": "가상로봇",
            "acc_mt": "12",
        }
        self.cfs = cfs if cfs is not None else {"status": "000", "list": ROWS}
        self.ofs = ofs if ofs is not None else {"status": "013", "message": "없음"}
        self.requested: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requested.append(str(request.url))
        path = request.url.path
        if path.endswith("company.json"):
            body = self.company
        elif path.endswith("fnlttSinglAcntAll.json"):
            body = self.cfs if request.url.params["fs_div"] == "CFS" else self.ofs
        else:
            return httpx.Response(404)
        return httpx.Response(
            200,
            content=json.dumps(body, ensure_ascii=False).encode(),
            headers={"content-type": "application/json"},
        )


def provider(site, key=KEY):
    clock = FakeClock(datetime(2026, 9, 30, tzinfo=UTC))
    fetcher = SafeFetcher(
        FetchPolicy(
            allowed_schemes=frozenset({"https"}),
            allowed_hosts=frozenset({"opendart.fss.or.kr"}),
            max_bytes=1_000_000,
            timeout_seconds=30.0,
            max_redirects=2,
        ),
        clock=clock,
        transport=httpx.MockTransport(site),
        resolve=lambda host: ["8.8.8.8"],
    )
    return OpenDartFinancials(
        fetcher, api_key=key, schema_version="synthetic-1", clock=clock
    ), clock


def fetch(site, **kw):
    p, clock = provider(site, kw.pop("key", KEY))
    return p(
        candidate_id="cand-1",
        corp_code=CORP,
        bsns_year=2024,
        retrieval_id="ret-1",
        calls=CallBudget(kw.pop("max_calls", 5), clock=clock, deadline=None),
        **kw,
    )


def test_fiscal_period_and_amounts():
    assert fiscal_period(2024, 12) == (date(2024, 1, 1), date(2024, 12, 31))
    assert fiscal_period(2024, 3) == (date(2023, 4, 1), date(2024, 3, 31))
    assert parse_amount("(1,234)") == -1234
    assert parse_amount("-") is None and parse_amount("") is None


def test_evidence_preserves_value_unit_period_source():
    out = fetch(Site())
    assert out.status == "ok"
    rev = out.evidence["revenue.current"]
    assert rev.value == 7_500_000_000 and rev.unit == "원" and rev.currency == "KRW"
    assert rev.period == "2024-01-01/2024-12-31"
    assert rev.value_as_of == date(2024, 12, 31)
    assert out.evidence["revenue.prior"].period == "2023-01-01/2023-12-31"
    cash = out.evidence["cash.current"]
    assert cash.period is None and cash.value_as_of == date(2024, 12, 31)
    assert rev.source_id == out.sources[0].source_id
    assert rev.provenance[0].retrieval_id == "ret-1"
    assert rev.provenance[0].method == "api"
    assert "traction.revenue_growth" in rev.criterion_ids


def test_key_never_stored():
    site = Site()
    out = fetch(site)
    assert any(KEY in u for u in site.requested)  # 요청에는 있어야 함
    dumped = json.dumps([s.model_dump(mode="json") for s in out.sources]) + json.dumps(
        [e.model_dump(mode="json") for e in out.evidence.values()]
    )
    assert KEY not in dumped
    assert all(KEY not in json.dumps(c.arguments) for c in out.calls)


def test_derived_values_through_t21():
    out = fetch(Site())
    d = derive_financials(out.evidence, run_as_of=date(2024, 12, 31))
    assert d["revenue_growth_pct"].value == Decimal(50)
    assert d["gross_margin_pct"].value == Decimal(30)
    assert d["operating_margin_pct"].value == Decimal(-20)
    assert d["monthly_net_burn"].value == Decimal(500_000_000)
    assert d["runway_months"].value == Decimal(18)
    assert d["rule_of_40"].value == Decimal(30)
    for v in d.values():
        assert isinstance(v, Derived) and v.evidence_kind == "derived"
        assert v.supporting_evidence_ids and v.derivation


def test_positive_cash_flow_has_no_runway():
    rows = [dict(r) for r in ROWS]
    rows[-1]["thstrm_amount"] = "1,000,000"
    out = fetch(Site(cfs={"status": "000", "list": rows}))
    d = derive_financials(out.evidence, run_as_of=date(2025, 1, 1))
    assert isinstance(d["runway_months"], Unavailable)
    assert d["runway_months"].reason == "non_positive_burn"


def test_missing_account_is_not_disclosed_not_zero():
    rows = [r for r in ROWS if r["account_id"] != "ifrs-full_CostOfSales"]
    out = fetch(Site(cfs={"status": "000", "list": rows}))
    d = derive_financials(out.evidence, run_as_of=date(2025, 1, 1))
    assert d["gross_margin_pct"].reason == "not_disclosed"
    assert "cost_of_revenue.current" not in out.evidence


def test_non_december_fiscal_year():
    out = fetch(Site(company={"status": "000", "corp_name": "가상", "acc_mt": "3"}))
    assert out.evidence["revenue.current"].period == "2023-04-01/2024-03-31"


def test_falls_back_to_separate_statements():
    out = fetch(Site(cfs={"status": "013"}, ofs={"status": "000", "list": ROWS}))
    assert out.status == "ok"
    assert "별도" in out.evidence["revenue.current"].claim
    assert "NO_DATA:CFS" in out.notes


def test_no_key_makes_no_request():
    site = Site()
    out = fetch(site, key=None)
    assert (
        out.status == "unavailable" and out.error_code == ErrorCode.TOOL_NOT_CONFIGURED
    )
    assert site.requested == []


@pytest.mark.parametrize(
    "status,code",
    [("010", ErrorCode.TOOL_AUTH_FAILED), ("020", ErrorCode.TOOL_RATE_LIMITED)],
)
def test_dart_status_errors(status, code):
    out = fetch(Site(company={"status": status, "message": "오류"}))
    assert out.error_code == code and not out.evidence


def test_budget_exhausted():
    out = fetch(Site(), max_calls=1)
    assert out.error_code == ErrorCode.BUDGET_EXHAUSTED


def test_account_id_preferred_over_name():
    rows = [
        row("IS", "custom_x", "매출액", "1", "1"),
        row("IS", "ifrs-full_Revenue", "수익(매출액)", "2", "1"),
    ]
    assert select_rows(rows)["revenue"]["thstrm_amount"] == "2"
