"""한글 정형 HTML 보고서 (#175). 사실·점수·판정을 바꾸지 않고 표시만 한다.

draft markdown을 보수적으로 파싱(`## ` 제목, 문단, `- ` 목록, 파이프 표)해 독립 HTML로
만든다. 모든 텍스트는 html.escape 하며 raw HTML/이미지/스크립트/외부 링크는 없다.
"""

import base64
import hashlib
import html
import re
from functools import lru_cache
from pathlib import Path

from skala_rag.contracts import ReportDraft
from skala_rag.reporting.research_presentation import CSS as RESEARCH_CSS
from skala_rag.reporting.research_presentation import (
    ROLES,
    review_evidence_ids,
    role_details,
    scoreboard,
)
from skala_rag.reporting.v3_context import ReportContextV3

FONT_ROOT = Path(__file__).with_name("fonts")
TITLES = {
    "SUMMARY": "요약",
    "COMPANY & TEAM": "기업·팀",
    "TECHNOLOGY & MARKET": "기술·시장",
    "INVESTMENT ASSESSMENT & RISKS": "투자 평가·위험",
    "REFERENCE": "참고문헌",
}
BANNER = "가상 데이터 — 실제 투자 판단 아님"
CSP = "default-src 'none'; style-src 'unsafe-inline'; font-src data:; img-src data:"
TOKEN = re.compile(r"\[@(evidence|source):([^\]\s]+)\]")
SEPARATOR = re.compile(r":?-{3,}:?")

CSS = """
@page { size: A4; margin: 18mm; }
* { box-sizing: border-box; }
html { font-family: 'NanumGothic', sans-serif; color: #1a202c; }
body { margin: 0; font-size: 10.5pt; line-height: 15pt; }
h1 { font-size: 17pt; line-height: 22pt; margin: 0 0 4pt; }
h2 { font-size: 14pt; line-height: 19pt; margin: 12pt 0 7pt; break-after: avoid;
     border-bottom: 1px solid #ccd4df; }
h3 { font-size: 11.5pt; line-height: 16pt; margin: 8pt 0 4pt; break-after: avoid; }
p { margin: 0 0 6pt; }
ul { margin: 0 0 6pt; padding-left: 16pt; }
p, li, td, th, h1, h2, h3 { word-break: break-word; overflow-wrap: anywhere; }
table { width: 100%; border-collapse: collapse; margin: 0 0 8pt; table-layout: fixed; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
th, td { border: 0.4pt solid #ccd4df; padding: 2pt 4pt; text-align: left;
         vertical-align: top; }
th { background: #edf2f7; }
a.cite { color: inherit; text-decoration: none; }
.meta { color: #4a5568; font-size: 9pt; line-height: 13pt; margin: 0 0 6pt; }
.banner { border: 1px solid #c53030; color: #9b2c2c; padding: 3pt 6pt;
          font-weight: 700; margin: 0 0 8pt; }
.card { border: 1px solid #ccd4df; background: #f7fafc; padding: 4pt 6pt;
        margin: 0 0 6pt; }
.card table { margin: 0; }
.machine-citation { color: #596579; font-size: 7pt; line-height: 10pt; }
.evidence-entry { margin: 0 0 10pt; break-inside: avoid; }
.evidence-excerpt { border-left: 2pt solid #ccd4df; padding-left: 8pt; }
"""


@lru_cache(maxsize=1)
def _font_css():
    faces = []
    for filename, weight in (
        ("NanumGothic-Regular.ttf", 400),
        ("NanumGothic-Bold.ttf", 700),
    ):
        data = base64.b64encode((FONT_ROOT / filename).read_bytes()).decode("ascii")
        faces.append(
            "@font-face { font-family: 'NanumGothic'; font-weight: "
            f"{weight}; src: url(data:font/ttf;base64,{data}) format('truetype'); }}"
        )
    return "\n".join(faces)


def _e(value):
    return html.escape(str(value), quote=True)


def _v(value):
    """None만 미상이다. 0/빈 문자열/Decimal 문자열은 원형 그대로 표시한다."""
    if value is None:
        return "미상"
    if isinstance(value, list):
        return ", ".join(str(x) for x in value) or "없음"
    return str(value)


def _anchor(source_id):
    return "src-" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:12]


def _section_id(name):
    return "sec-" + re.sub(r"[^A-Za-z0-9]+", "-", name)


class _Builder:
    def __init__(self, draft, data):
        self.draft = draft
        self.evidence = data["evidence"]
        self.sources = data["sources"]
        self.research = data.get("policy_version") == "unscored-research-only-1"
        evidence_ids = list(draft.cited_evidence_ids)
        source_ids = list(draft.reference_source_ids)
        if self.research:
            for role in ROLES:
                for eid in review_evidence_ids(
                    data.get("live_reviews", {}).get(role, {})
                ):
                    if eid in self.evidence:
                        evidence_ids.append(eid)
                        sid = self.evidence[eid].get("source_id")
                        if sid in self.sources:
                            source_ids.append(sid)
        self.evidence_labels = {
            eid: n for n, eid in enumerate(dict.fromkeys(evidence_ids), 1)
        }
        self.source_labels = {
            sid: n for n, sid in enumerate(dict.fromkeys(source_ids), 1)
        }
        self.refs = set()

    def inline(self, text, link=True):
        text = text.replace("&#124;", "|")
        out, pos = [], 0
        for m in TOKEN.finditer(text):
            out.append(_e(text[pos : m.start()]))
            kind, ident = m.groups()
            source = (
                ident
                if kind == "source"
                else self.evidence.get(ident, {}).get("source_id")
            )
            label = _e(m.group(0))
            if self.research and kind == "evidence" and ident in self.evidence_labels:
                number = self.evidence_labels[ident]
                out.append(
                    f'<sup class="citation"><a class="cite" aria-label="근거 {number}" '
                    f'href="#evidence-{number}">[{number}]</a></sup>'
                )
            elif self.research and kind == "source" and ident in self.source_labels:
                number = self.source_labels[ident]
                readable = f"[자료 {number}]"
                if link and source in self.refs:
                    readable = (
                        f'<a class="cite" href="#{_anchor(source)}">{readable}</a>'
                    )
                out.append(f'{readable} <span class="machine-citation">{label}</span>')
            elif self.research and kind == "evidence":
                out.append(f'<span class="unresolved">미해소 근거: {_e(ident)}</span>')
            elif link and source in self.refs:
                out.append(f'<a class="cite" href="#{_e(_anchor(source))}">{label}</a>')
            else:
                out.append(label)
            pos = m.end()
        out.append(_e(text[pos:]))
        return "".join(out)

    def cited_evidence(self):
        """Several cited evidence records may share one bibliography source."""
        parts = [
            '<section id="cited-evidence"><h3>인용 근거 목록</h3>',
            f"<p>인용 근거 {len(self.evidence_labels)}개 · "
            f"고유 출처 {len(self.source_labels)}개</p>",
            '<p class="meta">같은 출처의 서로 다른 발췌는 별도 근거이며, '
            "독립된 출처를 뜻하지 않습니다.</p>",
            '<p class="meta">발췌는 공백 정리 후 앞 480자까지 표시합니다. '
            "전체 발췌·식별자는 report-context.json에서 확인할 수 있습니다.</p>",
        ]
        for eid, number in self.evidence_labels.items():
            evidence = self.evidence.get(eid, {})
            source = self.sources.get(evidence.get("source_id"), {})
            excerpt = " ".join(_v(evidence.get("excerpt")).split())
            excerpt = excerpt[:480] + (" … [이하 생략]" if len(excerpt) > 480 else "")
            trace = (
                " · ".join(
                    f"{p.get('method', '미상')}: {p.get('chunk_id') or '미상'} / "
                    f"{p.get('retrieval_id') or '미상'}"
                    for p in evidence.get("provenance", [])
                )
                or "미상"
            )
            parts.append(
                f'<div class="evidence-entry" id="evidence-{number}">'
                f"<h3>근거 {number}</h3>"
                f'<p class="meta">출처: {_e(_v(source.get("title")))}<br>'
                f"URL: {_e(_v(source.get('url')))}<br>"
                f"위치: {_e(_v(evidence.get('locator')))}</p>"
                f'<p class="evidence-excerpt">{_e(excerpt)}</p>'
                f'<p class="meta">근거 유형: {_e(_v(evidence.get("evidence_kind")))} · '
                f"저장 신뢰도: {_e(_v(evidence.get('confidence')))}</p>"
                f'<p class="machine-citation">추적: {_e(trace)}</p>'
                f'<p class="machine-citation">[@evidence:{_e(eid)}]</p></div>'
            )
        return "".join(parts) + "</section><h3>고유 출처 참고문헌</h3>"

    def table(self, rows):
        head, *body = rows
        cells = "".join(f"<th>{self.inline(c)}</th>" for c in head)
        lines = [f"<table><thead><tr>{cells}</tr></thead><tbody>"]
        for row in body:
            cells = "".join(f"<td>{self.inline(c)}</td>" for c in row)
            lines.append(f"<tr>{cells}</tr>")
        lines.append("</tbody></table>")
        return "".join(lines)

    def blocks(self, lines, reference):
        out, i = [], 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
            elif line.startswith("|"):
                rows, raw = [], []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    raw.append(lines[i].strip())
                    row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    if not all(SEPARATOR.fullmatch(c) for c in row):
                        rows.append(row)
                    i += 1
                if rows:
                    out.append(self.table(rows))
                else:  # 구분선뿐인 잘못된 표는 문단 텍스트로 둔다.
                    out.extend(f"<p>{self.inline(r)}</p>" for r in raw)
            elif line.startswith("- "):
                items = []
                while i < len(lines) and lines[i].strip().startswith("- "):
                    items.append(lines[i].strip()[2:])
                    i += 1
                out.append(
                    "<ul>" + "".join(self.item(t, reference) for t in items) + "</ul>"
                )
            else:
                out.append(f"<p>{self.inline(line)}</p>")
                i += 1
        return "".join(out)

    def item(self, text, reference):
        m = TOKEN.match(text)
        if reference and m and m.group(1) == "source":
            anchor, body = _e(_anchor(m.group(2))), self.inline(text, link=False)
            return f'<li id="{anchor}">{body}</li>'
        return f"<li>{self.inline(text)}</li>"


def _card(data):
    if data.get("policy_version") == "unscored-research-only-1" and not data["scores"]:
        return ""
    head = [
        "후보",
        "관측 점수",
        "정규화 점수",
        "coverage %",
        "결측 비중",
        "판정",
        "등급",
    ]
    rows = []
    for cid in sorted(data["scores"]):
        s, d = data["scores"][cid], data["decisions"][cid]
        values = [
            cid,
            s["observed_score"],
            s["normalized_score"],
            s["coverage_pct"],
            s["missing_weight"],
            d["label"],
            d["report_grade"],
        ]
        rows.append("<tr>" + "".join(f"<td>{_e(_v(x))}</td>" for x in values) + "</tr>")
    selection = data["selection"]
    note = f"선택 결과: {_v(selection['selected_candidate_id'])}"
    if not rows:
        return (
            '<section class="card" id="score-overview"><h3>점수 개요</h3>'
            "<p>성공 평가 후보 없음 — 점수·판정을 만들지 않았다.</p></section>"
        )
    cells = "".join(f"<th>{h}</th>" for h in head)
    return (
        '<section class="card" id="score-overview"><h3>점수 개요</h3>'
        f"<table><thead><tr>{cells}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        f"<p>{_e(note)}</p></section>"
    )


def _extras(data):
    """후보 비교와 위험. 모두 context 값의 표시이며 새 판단을 만들지 않는다."""
    parts = []
    if data["outcomes"]:
        rows = "".join(
            f"<tr><td>{_e(cid)}</td><td>{_e(o['status'])}</td>"
            f"<td>{_e(_v(o['summary_reason']))}</td></tr>"
            for cid, o in sorted(data["outcomes"].items())
        )
        parts.append(
            "<h3>후보 비교</h3><table><thead><tr><th>후보</th><th>상태</th>"
            f"<th>사유</th></tr></thead><tbody>{rows}</tbody></table>"
        )
    risks = [
        f"{cid}: {r}"
        for cid, d in sorted(data["decisions"].items())
        for r in d["risks"]
    ]
    if risks:
        items = "".join(f"<li>{_e(r)}</li>" for r in risks)
        parts.append(f"<h3>위험</h3><ul>{items}</ul>")
    return "".join(parts)


def render_report_html(draft: ReportDraft, context: ReportContextV3) -> str:
    data = context.snapshot()
    title = "투자 검토 보고서"
    if data.get("policy_version") == "unscored-research-only-1":
        subject = data.get("research_subject") or "조사 대상 미상"
        title = f"{subject} — 자료 기반 연구 보고서"
    builder = _Builder(draft, data)
    sections, current = [], None
    for line in draft.markdown.splitlines():
        heading = re.match(r"^## (.+)$", line)
        if heading and heading.group(1) in TITLES:
            current = (heading.group(1), [])
            sections.append(current)
        elif current is not None:
            current[1].append(line)
    for name, lines in sections:
        if name == "REFERENCE":
            for line in lines:
                m = re.match("- " + TOKEN.pattern, line.strip())
                if m and m.group(1) == "source":
                    builder.refs.add(m.group(2))
    if builder.research:
        for name, lines in sections:
            if name == "REFERENCE":
                for sid in builder.source_labels:
                    if sid not in builder.refs:
                        source = builder.sources.get(sid, {})
                        lines.append(
                            f"- [@source:{sid}] 역할 출력 추가 참조 · "
                            f"{_v(source.get('author'))} "
                            f"({_v(source.get('published_at'))}). "
                            f"{_v(source.get('title'))}. {_v(source.get('url'))}"
                        )
                        builder.refs.add(sid)
    body = []
    for name, lines in sections:
        extra = _extras(data) if name == "INVESTMENT ASSESSMENT & RISKS" else ""
        evidence_list = (
            builder.cited_evidence() if name == "REFERENCE" and builder.research else ""
        )
        if builder.research:
            extra += role_details(data, name, builder.inline)
        body.append(
            f'<section id="{_section_id(name)}" data-section="{_e(name)}">'
            f"<h2>{TITLES[name]}</h2>"
            f"{evidence_list}{builder.blocks(lines, name == 'REFERENCE')}"
            f"{extra}</section>"
        )
        if name == "SUMMARY" and builder.research:
            body.append(scoreboard(data))
    banner = (
        f'<div class="banner">{BANNER}</div>'
        if data["execution_mode"] == "fixture"
        else ""
    )
    meta = (
        f"보고서 {_e(draft.report_id)} · 수정 {draft.revision} · "
        f"기준일 {_e(data['as_of'])} · 코퍼스 {_e(data['corpus_version'])}"
    )
    header = f'<h1>{_e(title)}</h1><p class="meta">{meta}</p>{banner}'
    body_tag, styles = "<body>", CSS
    if builder.research:
        header = (
            '<header class="report-masthead"><p class="edition">RESEARCH BRIEF · '
            "자료 기반 / 투자 평가 미실시</p>" + header + "</header>"
        )
        body_tag, styles = '<body class="research-report">', CSS + RESEARCH_CSS
    return (
        '<!DOCTYPE html>\n<html lang="ko"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
        f"<title>{_e(title)}</title>"
        f"<style>{_font_css()}{styles}</style></head>{body_tag}"
        f"{header}{_card(data)}{''.join(body)}</body></html>\n"
    )
