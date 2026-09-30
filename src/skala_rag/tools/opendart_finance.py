"""OpenDART 재무제표 → 재무 Evidence → T21 파생값 — #67 (#53 후속).

1. ``company.json``의 결산월(``acc_mt``)로 사업연도 기간을 정한다(월 경계).
2. ``fnlttSinglAcntAll.json``(단일회사 전체 재무제표, 사업보고서 11011)에서
   매출·매출원가·영업이익·영업활동현금흐름·현금및현금성자산의 당기/전기 값을
   ``reported`` Evidence로 만든다. 값·단위(원)·통화(KRW)·기간·기준일·Source·
   locator를 보존한다.
3. ``derive_financials``가 ``scoring.finance``(T21)로 성장률·매출총이익률·
   영업이익률·월 번레이트·잔여 런웨이를 계산한다. 조건이 안 맞으면 값을 만들지
   않고 missing 사유를 돌려준다.

key는 요청 URL에만 넣고 Source·locator·기록에는 key를 뺀 URL만 남긴다.
재무 rubric 점수 구간(#11, D14 OPEN)은 적용하지 않는다.
"""

import calendar
import dataclasses
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from skala_rag.contracts import Evidence, EvidenceProvenance, Source
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode
from skala_rag.contracts.ids import evidence_id, normalize_claim
from skala_rag.contracts.interfaces import Clock
from skala_rag.scoring.finance import (
    Derived,
    Unavailable,
    gross_margin_pct,
    monthly_net_burn,
    revenue_growth_pct,
    runway_months,
)
from skala_rag.tools.company_research import CallBudget, ProviderCall
from skala_rag.tools.opendart import (
    BASE,
    KEY_PARAM,
    STATUS_NO_DATA,
    _status_error,
    public_url,
)
from skala_rag.tools.source_fetch import FetchError, RawSnapshot, SafeFetcher, to_source

ANNUAL_REPORT = "11011"
SCOPE_LABEL = {"CFS": "연결", "OFS": "별도"}


@dataclass(frozen=True)
class Account:
    """재무 계정 매핑. account_id 우선, 없으면 계정명."""

    key: str
    statements: tuple[str, ...]
    account_ids: tuple[str, ...]
    names: tuple[str, ...]
    flow: bool
    criterion_ids: tuple[str, ...]


ACCOUNTS: tuple[Account, ...] = (
    Account(
        "revenue",
        ("IS", "CIS"),
        ("ifrs-full_Revenue",),
        ("매출액", "수익(매출액)", "영업수익", "매출"),
        True,
        ("traction.revenue_growth", "traction.gross_margin", "traction.rule_of_40"),
    ),
    Account(
        "cost_of_revenue",
        ("IS", "CIS"),
        ("ifrs-full_CostOfSales",),
        ("매출원가", "영업비용"),
        True,
        ("traction.gross_margin",),
    ),
    Account(
        "operating_income",
        ("IS", "CIS"),
        ("dart_OperatingIncomeLoss",),
        ("영업이익(손실)", "영업이익", "영업손실"),
        True,
        ("traction.rule_of_40",),
    ),
    Account(
        "operating_cash_flow",
        ("CF",),
        ("ifrs-full_CashFlowsFromUsedInOperatingActivities",),
        ("영업활동현금흐름", "영업활동으로 인한 현금흐름", "영업활동 현금흐름"),
        True,
        ("traction.burn", "traction.runway"),
    ),
    Account(
        "cash",
        ("BS",),
        ("ifrs-full_CashAndCashEquivalents",),
        ("현금및현금성자산",),
        False,
        ("traction.runway",),
    ),
)
TERMS = {"current": "thstrm_amount", "prior": "frmtrm_amount"}


class DartFinanceError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FinanceOutcome:
    status: str  # ok / empty / unavailable / failed
    sources: tuple[Source, ...] = ()
    evidence: Mapping[str, Evidence] = dataclasses.field(default_factory=dict)
    """``{account}.{current|prior}`` → Evidence."""
    calls: tuple[ProviderCall, ...] = ()
    error_code: ErrorCode | None = None
    message: str | None = None
    notes: tuple[str, ...] = ()


def fiscal_period(bsns_year: int, acc_mt: int) -> tuple[date, date]:
    """결산월 기준 12개월 사업연도(양끝 포함, 월 경계)."""
    if not 1 <= acc_mt <= 12:
        raise ValueError("acc_mt must be 1..12")
    end = date(bsns_year, acc_mt, calendar.monthrange(bsns_year, acc_mt)[1])
    start_month = acc_mt % 12 + 1
    start_year = bsns_year if acc_mt == 12 else bsns_year - 1
    return date(start_year, start_month, 1), end


def parse_amount(text: object) -> int | None:
    """'1,234' / '-567' / '(890)' → 정수. 빈 값·'-'는 None."""
    if text is None:
        return None
    s = str(text).strip().replace(",", "")
    if s in ("", "-"):
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        value = int(Decimal(s))
    except Exception:
        return None
    return -value if negative else value


def select_rows(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    """계정별 한 행. account_id 일치를 계정명보다, 앞 재무제표를 뒤보다 우선한다."""
    chosen: dict[str, Mapping[str, object]] = {}
    for acc in ACCOUNTS:
        best, best_rank = None, None
        for row in rows:
            sj = str(row.get("sj_div") or "")
            if sj not in acc.statements:
                continue
            aid = str(row.get("account_id") or "")
            name = str(row.get("account_nm") or "").replace(" ", "")
            if aid in acc.account_ids:
                rank = (0, acc.statements.index(sj))
            elif name in {n.replace(" ", "") for n in acc.names}:
                rank = (1, acc.statements.index(sj))
            else:
                continue
            if best_rank is None or rank < best_rank:
                best, best_rank = row, rank
        if best is not None:
            chosen[acc.key] = best
    return chosen


class OpenDartFinancials:
    name = "opendart-finance"

    def __init__(
        self,
        fetcher: SafeFetcher,
        *,
        api_key: str | None,
        schema_version: str,
        clock: Clock,
    ) -> None:
        self._fetcher = fetcher
        self._key = api_key.strip() if api_key and api_key.strip() else None
        self._schema_version = schema_version
        self._clock = clock

    def __call__(
        self,
        *,
        candidate_id: str,
        corp_code: str,
        bsns_year: int,
        retrieval_id: str,
        calls: CallBudget,
        fs_div: str = "CFS",
    ) -> FinanceOutcome:
        """연결(CFS) 재무제표가 없으면(013) 별도(OFS)로 한 번 더 조회한다."""
        if self._key is None:
            return FinanceOutcome(
                status="unavailable",
                error_code=ErrorCode.TOOL_NOT_CONFIGURED,
                message="OpenDART API key not configured",
            )
        made: list[ProviderCall] = []
        notes: list[str] = []
        try:
            company, _ = self._json(
                "company.json", {"corp_code": corp_code}, calls, made
            )
            if company is None:
                return FinanceOutcome(
                    status="empty", calls=tuple(made), notes=("NO_COMPANY",)
                )
            acc_mt = int(str(company.get("acc_mt") or "0") or 0)
            if not 1 <= acc_mt <= 12:
                notes.append("ACC_MT_UNKNOWN")
                return FinanceOutcome(
                    status="empty", calls=tuple(made), notes=tuple(notes)
                )
            corp_name = str(company.get("corp_name") or corp_code)
            for div in (fs_div, "OFS") if fs_div == "CFS" else (fs_div,):
                params = {
                    "corp_code": corp_code,
                    "bsns_year": str(bsns_year),
                    "reprt_code": ANNUAL_REPORT,
                    "fs_div": div,
                }
                body, raw = self._json("fnlttSinglAcntAll.json", params, calls, made)
                if body is None:
                    notes.append(f"NO_DATA:{div}")
                    continue
                rows = body.get("list")
                if not isinstance(rows, list):
                    raise DartFinanceError(
                        ErrorCode.TOOL_RESPONSE_INVALID, "financial list missing"
                    )
                source = to_source(
                    raw,
                    schema_version=self._schema_version,
                    title=f"OpenDART {corp_name} {bsns_year} 사업보고서 재무제표 {div}",
                    publisher="금융감독원 OpenDART",
                    source_kind="filing",
                    language="ko",
                    access_notes="OpenDART 단일회사 전체 재무제표 API 응답 snapshot",
                    bibliographic_metadata={
                        "corp_code": corp_code,
                        "bsns_year": bsns_year,
                        "reprt_code": ANNUAL_REPORT,
                        "fs_div": div,
                    },
                )
                made[-1] = dataclasses.replace(made[-1], source_ids=(source.source_id,))
                evidence = self._evidence(
                    rows,
                    source=source,
                    candidate_id=candidate_id,
                    bsns_year=bsns_year,
                    acc_mt=acc_mt,
                    fs_div=div,
                    retrieval_id=retrieval_id,
                )
                return FinanceOutcome(
                    status="ok" if evidence else "empty",
                    sources=(source,),
                    evidence=evidence,
                    calls=tuple(made),
                    notes=tuple(notes),
                )
        except DartFinanceError as exc:
            return FinanceOutcome(
                status=ERROR_SPECS[exc.code].tool_status or "failed",
                calls=tuple(made),
                error_code=exc.code,
                message=exc.message,
                notes=tuple(notes),
            )
        return FinanceOutcome(status="empty", calls=tuple(made), notes=tuple(notes))

    # ------------------------------------------------------------ requests

    def _json(
        self,
        endpoint: str,
        params: dict[str, str],
        calls: CallBudget,
        made: list[ProviderCall],
    ) -> tuple[dict | None, RawSnapshot]:
        if not calls.take():
            raise DartFinanceError(ErrorCode.BUDGET_EXHAUSTED, "no request budget")
        url = f"{BASE}/{endpoint}?" + urlencode({KEY_PARAM: self._key, **params})
        shown = public_url(endpoint, **params)
        started = self._clock.now()
        try:
            raw = self._fetcher.fetch(url)
        except FetchError as exc:
            made.append(
                ProviderCall(
                    status=ERROR_SPECS[exc.error_code].tool_status,
                    method="api",
                    query=None,
                    arguments={"endpoint": endpoint, **params},
                    started_at=started,
                    finished_at=self._clock.now(),
                    error_code=exc.error_code,
                )
            )
            raise DartFinanceError(exc.error_code, exc.message_redacted) from exc
        raw = dataclasses.replace(raw, requested=shown, locator=shown, redirects=())
        call = ProviderCall(
            status="ok",
            method="api",
            query=None,
            arguments={"endpoint": endpoint, **params},
            started_at=started,
            finished_at=self._clock.now(),
        )
        try:
            body = json.loads(raw.content)
            if not isinstance(body, dict):
                raise ValueError
        except ValueError as exc:
            made.append(dataclasses.replace(call, status="failed"))
            raise DartFinanceError(
                ErrorCode.TOOL_RESPONSE_INVALID, f"{endpoint} is not a JSON object"
            ) from exc
        status = str(body.get("status", ""))
        if status == STATUS_NO_DATA:
            made.append(dataclasses.replace(call, status="empty"))
            return None, raw
        if status != "000":
            error = _status_error(status)
            made.append(
                dataclasses.replace(
                    call,
                    status=ERROR_SPECS[error.code].tool_status,
                    error_code=error.code,
                )
            )
            raise DartFinanceError(error.code, error.message)
        made.append(call)
        return body, raw

    # ------------------------------------------------------------ evidence

    def _evidence(
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        source: Source,
        candidate_id: str,
        bsns_year: int,
        acc_mt: int,
        fs_div: str,
        retrieval_id: str,
    ) -> dict[str, Evidence]:
        periods = {
            "current": fiscal_period(bsns_year, acc_mt),
            "prior": fiscal_period(bsns_year - 1, acc_mt),
        }
        chosen = select_rows(rows)
        out: dict[str, Evidence] = {}
        for acc in ACCOUNTS:
            row = chosen.get(acc.key)
            if row is None:
                continue
            currency = str(row.get("currency") or "KRW").strip().upper()
            for term, field in TERMS.items():
                value = parse_amount(row.get(field))
                if value is None:
                    continue
                start, end = periods[term]
                period = f"{start.isoformat()}/{end.isoformat()}" if acc.flow else None
                name = str(row.get("account_nm") or acc.key)
                label = (
                    f"{start.year}.{start.month:02d}~{end.year}.{end.month:02d}"
                    if acc.flow
                    else f"{end.isoformat()} 현재"
                )
                claim = normalize_claim(
                    f"{name}({SCOPE_LABEL[fs_div]}) {label}: {value:,}원"
                )
                anchor = f"{row.get('sj_div')}:{row.get('account_id') or name}:{field}"
                locator = f"{source.url}#{anchor}"
                core = dict(
                    source_id=source.source_id,
                    locator=locator,
                    claim=claim,
                    candidate_id=candidate_id,
                    scope="company",
                    value=value,
                    unit="원",
                    currency=currency,
                    value_as_of=end,
                    period=period,
                    geography="KR",
                    event_date=None,
                    evidence_kind="reported",
                    supporting_evidence_ids=[],
                    derivation=None,
                    supersedes=None,
                )
                out[f"{acc.key}.{term}"] = Evidence(
                    schema_version=self._schema_version,
                    evidence_id=evidence_id(**core),
                    criterion_ids=list(acc.criterion_ids),
                    excerpt=(
                        f"{row.get('sj_nm') or row.get('sj_div')} / {name} / "
                        f"{field}={row.get(field)}"
                    ),
                    provenance=[
                        EvidenceProvenance(
                            schema_version=self._schema_version,
                            retrieval_id=retrieval_id,
                            method="api",
                        )
                    ],
                    confidence="high",
                    limitations=[
                        f"결산월 {acc_mt}월 기준 12개월 사업연도로 기간을 정함",
                        "공시 재무제표 수치이며 감사 의견·정정 공시는 확인하지 않음",
                    ],
                    conflicts_with=[],
                    **core,
                )
        return out


# ------------------------------------------------------------ T21 연결


def operating_margin_pct(
    revenue: Evidence, operating_income: Evidence
) -> Derived | Unavailable:
    """영업이익 / 매출 × 100. 같은 기간·통화, 매출 > 0일 때만."""
    if revenue.value is None or operating_income.value is None:
        return Unavailable("not_disclosed", "매출 또는 영업이익 값 없음")
    if revenue.period is None or revenue.period != operating_income.period:
        return Unavailable("period_mismatch", "매출과 영업이익 기간이 다르거나 미상")
    if revenue.currency != operating_income.currency:
        return Unavailable("currency_mismatch", "매출과 영업이익 통화가 다르다")
    rev = Decimal(str(revenue.value))
    if rev <= 0:
        return Unavailable("non_positive_base", "매출 <= 0")
    op = Decimal(str(operating_income.value))
    return Derived(
        value=op * 100 / rev,
        unit="percent",
        currency=None,
        period=revenue.period,
        value_as_of=revenue.value_as_of,
        supporting_evidence_ids=(revenue.evidence_id, operating_income.evidence_id),
        derivation=f"operating_income {op} / revenue {rev} × 100",
    )


def derive_financials(
    evidence: Mapping[str, Evidence], *, run_as_of: date
) -> dict[str, Derived | Unavailable]:
    """재무 Evidence로 T21 파생값을 만든다. 입력 없으면 not_disclosed."""

    def need(*keys: str) -> Unavailable | None:
        lacking = [k for k in keys if k not in evidence]
        return (
            Unavailable("not_disclosed", f"입력 없음: {lacking}") if lacking else None
        )

    e = evidence
    out: dict[str, Derived | Unavailable] = {}
    out["revenue_growth_pct"] = need("revenue.current", "revenue.prior") or (
        revenue_growth_pct(e["revenue.current"], e["revenue.prior"])
    )
    out["gross_margin_pct"] = need("revenue.current", "cost_of_revenue.current") or (
        gross_margin_pct(e["revenue.current"], e["cost_of_revenue.current"])
    )
    out["operating_margin_pct"] = need(
        "revenue.current", "operating_income.current"
    ) or (operating_margin_pct(e["revenue.current"], e["operating_income.current"]))
    out["monthly_net_burn"] = need("operating_cash_flow.current") or (
        monthly_net_burn(e["operating_cash_flow.current"])
    )
    out["runway_months"] = need("cash.current", "operating_cash_flow.current") or (
        runway_months(e["cash.current"], e["operating_cash_flow.current"], run_as_of)
    )
    growth, margin = out["revenue_growth_pct"], out["operating_margin_pct"]
    if isinstance(growth, Derived) and isinstance(margin, Derived):
        out["rule_of_40"] = Derived(
            value=growth.value + margin.value,
            unit="percent_points",
            currency=None,
            period=growth.period,
            value_as_of=growth.value_as_of,
            supporting_evidence_ids=tuple(
                dict.fromkeys(
                    growth.supporting_evidence_ids + margin.supporting_evidence_ids
                )
            ),
            derivation=(
                f"revenue_growth {growth.value} + operating_margin {margin.value}"
            ),
        )
    else:
        bad = growth if isinstance(growth, Unavailable) else margin
        out["rule_of_40"] = Unavailable(
            "not_disclosed", f"Rule of 40 입력 없음: {bad.detail}"
        )
    return out
