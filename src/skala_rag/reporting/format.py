"""보고서 형식 규약 — reporting.md §1–§4 (D09 승인), #27 구현 규약.

Generator(#28)와 Structural Validator(#27)가 같은 함수를 써서 목차·표·인용·
REFERENCE 문자열을 만든다. 표 형식은 reporting.md의 "평가 결과 표"·"후보 결과
표"를 기계 검사할 수 있게 고정한 구현 규약이다.
"""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from skala_rag.contracts import (
    CandidateOutcome,
    InvestmentDecision,
    ScoreSummary,
    Source,
)

Mode = Literal["single_candidate", "no_recommendation"]

HEADINGS: dict[Mode, tuple[str, ...]] = {
    "single_candidate": (
        "SUMMARY",
        "1. 기업과 제품 / 핵심 컨셉",
        "2. 시장성과 성장 가능성",
        "3. 창업자·팀, 기술력과 경쟁 우위",
        "4. 실적·투자조건과 평가 결과",
        "5. 주요 리스크, 한계와 추가 확인 사항",
        "REFERENCE",
    ),
    "no_recommendation": (
        "SUMMARY",
        "1. 조사 주제와 후보 탐색 범위",
        "2. 후보별 결과와 제외·보류 사유",
        "3. 시장·기술 관찰과 근거 범위",
        "4. 평가 결과와 정보 공백",
        "5. 공통 리스크, 한계와 추가 확인 사항",
        "REFERENCE",
    ),
}
SCORE_SECTION = 4
OUTCOME_SECTION = 2  # no_recommendation만
DIMENSIONS = ("founder", "market", "technology", "moat", "traction", "deal_terms")
EMPTY_REFERENCE_PREFIX = "인용 자료 없음:"
FIXTURE_MARK = "가상 데이터"


def evidence_token(evidence_id: str) -> str:
    return f"[@evidence:{evidence_id}]"


def fmt_number(value: Decimal) -> str:
    """scoring 표시 규칙: 소수 둘째 자리 반올림(비교는 반올림 전 값)."""
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def score_rows(summary: ScoreSummary, decision: InvestmentDecision) -> dict[str, str]:
    """평가 결과 표의 항목 → 값."""
    rows = {
        "후보": summary.candidate_id,
        "관측 근거 기반 점수": f"{fmt_number(summary.observed_score)}/100",
        "결측 비중": f"{fmt_number(summary.missing_weight)}%",
        "coverage": f"{fmt_number(summary.coverage_pct)}%",
    }
    for dim in DIMENSIONS:
        rating = summary.dimension_ratings.get(dim)
        rows[f"rating.{dim}"] = "미상" if rating is None else fmt_number(rating)
    rows["label"] = decision.label
    rows["report_grade"] = decision.report_grade
    rows["hold_reasons"] = ", ".join(summary.hold_reasons) or "없음"
    return rows


def render_score_table(summary: ScoreSummary, decision: InvestmentDecision) -> str:
    lines = ["| 항목 | 값 |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in score_rows(summary, decision).items()]
    return "\n".join(lines)


def render_outcome_table(outcomes: list[CandidateOutcome]) -> str:
    lines = ["| 후보 | 상태 | 사유 |", "| --- | --- | --- |"]
    lines += [
        f"| {o.candidate_id} | {o.status} | {o.summary_reason} |" for o in outcomes
    ]
    return "\n".join(lines)


def _year(value: date | datetime | None) -> str | None:
    return None if value is None else f"{value.year:04d}"


def _day(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return (
        value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    )


def _location(source: Source) -> str:
    if source.url:
        return source.url
    if source.local_path and not source.local_path.startswith(("/", "~")):
        return source.local_path  # 재배포 가능한 상대 locator만
    return "온라인 주소 없음"


def bibliography(source: Source) -> str:
    """Source metadata만으로 서지 문자열을 만든다. 미상 필드는 승인 표기."""
    meta = source.bibliographic_metadata
    loc = _location(source)
    if source.source_kind == "paper":
        author = source.author or "저자 미상"
        year = _year(source.published_at) or "발행연도 미상"
        journal = meta.get("journal") or "학술지 미상"
        volume = meta.get("volume_issue") or "권호 미상"
        pages = meta.get("pages") or "페이지 미상"
        return f"{author}({year}). {source.title}. {journal}, {volume}, {pages}."
    if source.source_kind in ("report", "filing"):
        org = source.publisher or "발행기관 미상"
        year = _year(source.published_at) or "발행연도 미상"
        return f"{org}({year}). {source.title}. {loc}"
    who = source.publisher or source.author or "발행기관 미상"
    day = _day(source.published_at) or "발행일 미상"
    site = meta.get("site_name") or "사이트명 미상"
    return f"{who}({day}). {source.title}. {site}, {loc}"


def reference_line(source: Source) -> str:
    return f"- [@source:{source.source_id}] {bibliography(source)}"
