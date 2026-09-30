"""Deterministic display of saved research outputs; never a scoring policy."""

import re
from decimal import Decimal
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
.research-report #research-scoreboard th:first-child { width: 55%; }
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


def usable_role_score(data, role):
    """Validate the display contract, not the rubric or evidence semantics."""
    scores = data.get("role_scores")
    record = scores.get(role) if isinstance(scores, dict) else None
    if not isinstance(record, dict):
        return None
    score, ids = record.get("score"), record.get("evidence_ids")
    if not isinstance(score, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", score):
        return None
    number = Decimal(score)
    if not number.is_finite() or not 0 <= number <= 100:
        return None
    if not isinstance(ids, list) or not ids:
        return None
    for eid in ids:
        if not isinstance(eid, str) or not eid:
            return None
        evidence = data["evidence"].get(eid)
        if not isinstance(evidence, dict):
            return None
        sid = evidence.get("source_id")
        if not isinstance(sid, str) or not isinstance(data["sources"].get(sid), dict):
            return None
    return record


def scoreboard(data, inline):
    """Display saved role scores only; absent scores remain blank."""
    parts = [
        '<section id="research-scoreboard"><h3>항목별 스코어보드</h3>',
        "<table><thead><tr><th>항목</th><th>점수 / 100</th></tr></thead><tbody>",
    ]
    for role, label in ROLES.items():
        record = usable_role_score(data, role)
        value = ""
        if record is not None:
            citations = " ".join(
                f"[@evidence:{eid}]" for eid in dict.fromkeys(record["evidence_ids"])
            )
            value = f"{escape(record['score'])} / 100"
            if inline is not None:
                value += " " + inline(citations)
        parts.append(
            f'<tr data-role="{role}"><th scope="row">{label}</th><td>{value}</td></tr>'
        )
    parts.append("</tbody></table></section>")
    return "".join(parts)
