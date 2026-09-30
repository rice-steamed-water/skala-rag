"""Deterministic display of saved research outputs; never a scoring policy."""

from collections import Counter
from html import escape

CSS = """
.research-report { color: #182c40; font-size: 10.5pt; line-height: 15pt; }
.research-report .report-masthead { border-top: 4pt solid #182c40; padding-top: 10pt;
  margin-bottom: 10pt; }
.research-report .edition { color: #17675f; font-size: 8pt; letter-spacing: 1.6pt;
  font-weight: 700; margin: 0 0 6pt; }
.research-report h1 { font-size: 21pt; line-height: 27pt; max-width: 95%; }
.research-report h2 { font-size: 14pt; line-height: 19pt; margin: 14pt 0 7pt;
  padding-bottom: 4pt; border-bottom: 1pt solid #182c40; color: #182c40; }
.research-report h3 { color: #17675f; margin: 9pt 0 5pt; }
.research-report .meta { color: #4b5b69; }
.research-report .citation { font-size: 7pt; line-height: 0; vertical-align: super; }
.research-report a.cite { color: #17675f; text-decoration: none; }
.research-report a.cite:focus-visible { outline: 1pt solid #17675f; }
.research-report #research-scoreboard { margin: 9pt 0; break-inside: avoid; }
.research-report #research-scoreboard h3 { margin: 0 0 4pt; }
.research-report #research-scoreboard table { font-size: 9pt; line-height: 12pt; }
.research-report #research-scoreboard th:first-child { width: 20%; }
.research-report #research-scoreboard th:nth-child(7) { width: 18%; }
.research-report #research-scoreboard th:nth-child(8) { width: 14%; }
.research-report th, .research-report td { border: 0; border-bottom: .5pt solid #d4dddF;
  padding: 4pt 3pt; font-variant-numeric: tabular-nums; }
.research-report thead th { background: #eaf0f1; color: #182c40; }
.research-report tbody th { background: transparent; font-weight: 700; }
.research-report .review-notice { font-size: 9pt; line-height: 13pt; color: #4b5b69;
  padding: 6pt 0; border-bottom: .5pt solid #d4dddf; }
.research-report .role-origin { font-size: 8pt; color: #4b5b69; font-weight: 400; }
.research-report .item-kind { font-size: 8pt; color: #17675f; font-weight: 700; }
.research-report .review-item, .research-report .review-missing { margin-bottom: 5pt; }
.research-report .review-missing { color: #4b5b69; }
.research-report #sec-REFERENCE { break-before: page; }
.research-report .evidence-entry { margin-bottom: 9pt; padding-top: 5pt;
  border-top: .5pt solid #d4dddf; }
.research-report .evidence-entry h3 { margin: 0 0 4pt; }
.research-report .evidence-excerpt { border: 0; padding: 0; }
.research-report .machine-citation { font-size: 7pt; line-height: 10pt;
  margin-bottom: 3pt; }
.research-report .unresolved { color: #8b3826; }
@media screen {
  .research-report { max-width: 800px; margin: 32px auto; padding: 36px; }
}
"""

ROLES = {
    "founder": "창업자·팀",
    "market": "시장",
    "technology": "기술",
    "moat": "경쟁 우위",
    "business_deal": "사업·투자조건",
}


def review_evidence_ids(review):
    return list(
        dict.fromkeys(
            eid
            for kind in ("observations", "interpretations")
            for item in review.get(kind, [])
            for eid in item.get("evidence_ids", [])
        )
    )


ROLE_SECTIONS = {
    "COMPANY & TEAM": ("founder",),
    "TECHNOLOGY & MARKET": ("technology", "market"),
    "INVESTMENT ASSESSMENT & RISKS": ("moat", "business_deal"),
}


def role_details(data, section, inline):
    """Preserve full saved text. Dedup only identical text + reference sets."""
    parts = []
    for role in ROLE_SECTIONS.get(section, ()):
        review = data.get("live_reviews", {}).get(role)
        if review is None:
            continue
        parts.append(
            f'<article class="role-review" id="review-{role}">'
            f'<h3>{ROLES[role]} <span class="role-origin">'
            f"원본 역할 출력 · {role}</span></h3>"
        )
        for kind, label in (
            ("observations", "관측 · 저자 보고"),
            ("interpretations", "분석·해석"),
        ):
            seen = set()
            for item in review.get(kind, []):
                ids = tuple(dict.fromkeys(item.get("evidence_ids", [])))
                key = (item["text"], frozenset(ids))
                if key in seen:
                    continue
                seen.add(key)
                citations = " ".join(f"[@evidence:{eid}]" for eid in ids)
                parts.append(
                    f'<p class="review-item"><span class="item-kind">{label}</span> '
                    f"{escape(item['text'])} {inline(citations)}</p>"
                )
        missing = list(dict.fromkeys(review.get("missing", [])))
        if missing:
            parts.append(
                '<p class="review-missing"><span class="item-kind">'
                "판단 불가 · 원본 결측</span> "
                + " / ".join(escape(text) for text in missing)
                + "</p>"
            )
        parts.append("</article>")
    if parts and section == "COMPANY & TEAM":
        parts.insert(
            0,
            '<p class="review-notice">아래는 저장된 분석 역할의 원본 출력입니다. '
            "역할명이 해당 영역의 실사를 보장하지 않습니다. 기술 내용이 다른 역할에도 "
            "포함될 수 있습니다. 추가 역할 출력은 별도 의미 검증을 거치지 않았습니다. "
            "동일 역할·분류 내 문장과 참조가 같은 항목만 중복 제거하며 "
            "나머지는 전부 표시합니다.</p>",
        )
    return "".join(parts)


def scoreboard(data):
    """Counts are inventory, not quality, coverage or calibrated confidence."""
    reviews, evidence, sources = (
        data.get("live_reviews", {}),
        data["evidence"],
        data["sources"],
    )
    parts = [
        '<section id="research-scoreboard"><h3>항목별 스코어보드 · 기록 수</h3>',
        '<p class="meta">품질·coverage·투자 점수가 아닙니다. '
        "고유 출처 수는 독립 검증·교차 확인 횟수가 아닙니다. "
        "신뢰도는 참조 근거의 저장 범주별 건수이며 수치 신뢰도는 미산정입니다.</p>",
        "<table><thead><tr><th>분석 역할</th><th>관측</th><th>해석</th>"
        "<th>근거</th><th>출처</th><th>결측</th><th>근거 신뢰도</th>"
        "<th>수치 신뢰도</th></tr></thead><tbody>",
    ]
    for role, label in ROLES.items():
        review = reviews.get(role)
        if review is None:
            cells = '<td colspan="7">출력 없음 · 집계 불가 · 수치 신뢰도 미산정</td>'
        else:
            ids = review_evidence_ids(review)
            known = [eid for eid in ids if eid in evidence]
            source_ids = {
                evidence[eid].get("source_id")
                for eid in known
                if evidence[eid].get("source_id") in sources
            }
            confidence = Counter(
                evidence[eid].get("confidence") or "unknown" for eid in known
            )
            distribution = (
                " · ".join(
                    f"{escape(str(k))} {v}" for k, v in sorted(confidence.items())
                )
                or "없음"
            )
            unresolved = len(ids) - len(known)
            if unresolved:
                distribution += f" · 미해소 {unresolved}"
            counts = [
                len(review.get("observations", [])),
                len(review.get("interpretations", [])),
                len(known),
                len(source_ids),
                len(set(review.get("missing", []))),
            ]
            cells = "".join(f"<td>{n}</td>" for n in counts)
            cells += f"<td>{distribution}</td><td>미산정</td>"
        parts.append(f'<tr data-role="{role}"><th scope="row">{label}</th>{cells}</tr>')
    parts.append(
        '</tbody></table><p class="meta">관측·해석: 원본 항목 수 / '
        "근거·출처: 역할 내 고유 ID 수 / 결측: 동일 문자열 중복 제거. "
        "역할 간 합산은 중복을 포함합니다.</p></section>"
    )
    return "".join(parts)
