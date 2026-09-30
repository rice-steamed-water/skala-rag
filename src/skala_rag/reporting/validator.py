"""Structural Validator — #27, reporting.md §5 SV01–SV09, T14·T23.

같은 고정 ReportContext와 ReportDraft만 보고 구조·인용·점수 일치를 검사한다.
주장의 사실성·SUMMARY 품질은 Semantic Judge(T15) 범위다.

결과의 ``checks["action"]``: ``pass`` / ``revise``(draft 오류, 같은 context로
재작성) / ``fail``(context·upstream 오류, 재작성 대신 workflow failed).
"""

import hashlib
import json
import re
from dataclasses import dataclass, field

from skala_rag.contracts import ReportContext, ReportDraft, ValidationResult
from skala_rag.reporting.format import (
    EMPTY_REFERENCE_PREFIX,
    FIXTURE_MARK,
    HEADINGS,
    OUTCOME_SECTION,
    SCORE_SECTION,
    reference_line,
    score_rows,
)

TOKEN = re.compile(r"\[@evidence:([^\]\s]+)\]")
LOOSE_TOKEN = re.compile(r"\[@evidence:[^\]]*\]?")
REF_LINE = re.compile(r"^- \[@source:([^\]\s]+)\] (.+)$")
CHECK_IDS = [f"SV0{i}" for i in range(1, 10)]


def artifact_hash(draft: ReportDraft) -> str:
    """검증 대상 draft의 hash. 문장·metadata가 바뀌면 달라진다."""
    payload = json.dumps(
        {
            "context_id": draft.context_id,
            "revision": draft.revision,
            "markdown": draft.markdown,
            "cited_evidence_ids": draft.cited_evidence_ids,
            "reference_source_ids": draft.reference_source_ids,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


@dataclass
class _Findings:
    errors: list[tuple[str, str, str, str]] = field(default_factory=list)

    def add(self, check: str, severity: str, location: str, message: str) -> None:
        self.errors.append((check, severity, location, message))

    def failed(self, check: str) -> bool:
        return any(e[0] == check for e in self.errors)


def _strip_code(markdown: str) -> list[str]:
    """code block 안 줄을 빈 줄로 바꿔 줄 번호를 유지한다."""
    out, fenced = [], False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
            out.append("")
            continue
        out.append("" if fenced else line)
    return out


def _sections(lines: list[str]) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    for line in lines:
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections:
            sections[-1][1].append(line)
    return sections


def _tables(body: list[str]) -> list[list[list[str]]]:
    """본문의 Markdown 표 → 행(셀 목록) 목록. 구분선 제외."""
    tables, current = [], []
    for line in body + [""]:
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if not all(set(c) <= {"-", ":", " "} and c for c in cells):
                current.append(cells)
        elif current:
            tables.append(current)
            current = []
    return tables


def validate_report(draft: ReportDraft, context: ReportContext) -> ValidationResult:
    f = _Findings()
    ri = context.input
    mode = ri.mode

    # SV01 — context 일치
    if draft.context_id != context.context_id:
        f.add("SV01", "fail", "draft.context_id", "고정 context와 다른 context_id")
    for summary in context.score_summaries.values():
        if summary.policy_version != ri.policy_version or summary.run_id != ri.run_id:
            f.add("SV01", "fail", summary.score_summary_id, "다른 실행·정책의 점수")

    # SV02 — mode·선택 후보
    decided = {d.candidate_id: d for d in context.decisions.values()}
    if mode == "single_candidate":
        if ri.selected_candidate_id not in decided:
            f.add("SV02", "fail", "input", "선택 후보의 판정이 context에 없다")
        elif decided[ri.selected_candidate_id].label != "RECOMMEND":
            f.add("SV02", "fail", "input", "단일 보고서 선택 후보가 RECOMMEND가 아니다")

    lines = _strip_code(draft.markdown)
    sections = _sections(lines)
    titles = [t for t, _ in sections]
    expected = list(HEADINGS[mode])

    # SV03 — 목차
    if titles != expected:
        f.add("SV03", "revise", "headings", f"기대 {expected}, 실제 {titles}")
    for title, body in sections:
        if not any(line.strip() for line in body):
            f.add("SV03", "revise", title, "빈 섹션")
    by_title = dict(sections) if len(set(titles)) == len(titles) else {}

    # SV04 — 평가 결과 표
    score_title = expected[SCORE_SECTION]
    tables = _tables(by_title.get(score_title, []))
    found: dict[str, dict[str, str]] = {}
    for table in tables:
        rows = {r[0]: r[1] for r in table if len(r) == 2}
        if "후보" in rows:
            found[rows["후보"]] = rows
    summaries = {s.candidate_id: s for s in context.score_summaries.values()}
    for cid, summary in summaries.items():
        want = score_rows(summary, decided[cid])
        got = found.get(cid)
        if got is None:
            f.add("SV04", "revise", score_title, f"{cid} 평가 결과 표 없음")
            continue
        for key, value in want.items():
            if got.get(key) != value:
                f.add(
                    "SV04",
                    "revise",
                    f"{score_title}/{cid}/{key}",
                    f"context {value!r} ≠ draft {got.get(key)!r}",
                )

    # SV05 — 인용 token
    body_lines = [
        line
        for title, body in sections
        if title != "REFERENCE"
        for line in body + [title]
    ]
    text = "\n".join(body_lines)
    cited = TOKEN.findall(text)
    if len(LOOSE_TOKEN.findall(text)) != len(cited):
        f.add("SV05", "revise", "body", "잘못된 인용 token 문법")
    c = set(cited)
    permitted = set(ri.permitted_evidence_ids) & set(context.evidence)
    for eid in sorted(c - permitted):
        f.add("SV05", "revise", "body", f"허용 밖 Evidence {eid}")
    if set(draft.cited_evidence_ids) != c:
        f.add(
            "SV05", "revise", "cited_evidence_ids", "본문 인용 집합과 metadata 불일치"
        )
    if mode == "single_candidate" and not c:
        f.add("SV05", "revise", "body", "단일 기업 보고서에 평가 근거 인용이 없다")

    # SV06·SV07 — Source와 REFERENCE
    sources_needed: set[str] = set()
    for eid in c & set(context.evidence):
        sid = context.evidence[eid].source_id
        if sid not in context.sources:
            f.add("SV06", "fail", eid, f"Source 누락 {sid}")
        sources_needed.add(sid)
    ref_body = [line for line in by_title.get("REFERENCE", []) if line.strip()]
    ref_ids: list[str] = []
    for line in ref_body:
        m = REF_LINE.match(line.strip())
        if m:
            ref_ids.append(m.group(1))
            sid = m.group(1)
            if sid in context.sources:
                want = reference_line(context.sources[sid])
                if line.strip() != want:
                    f.add("SV07", "revise", f"REFERENCE/{sid}", f"서지 불일치: {want}")
        elif not line.strip().startswith(EMPTY_REFERENCE_PREFIX):
            f.add("SV07", "revise", "REFERENCE", f"형식 밖 줄: {line.strip()[:60]}")
    if len(ref_ids) != len(set(ref_ids)):
        f.add("SV06", "revise", "REFERENCE", "중복 Source 항목")
    if ref_ids != sorted(ref_ids):
        f.add("SV07", "revise", "REFERENCE", "source_id 오름차순이 아니다")
    if set(ref_ids) != sources_needed:
        f.add("SV06", "revise", "REFERENCE", "REFERENCE와 인용 Source 집합 S 불일치")
    if set(draft.reference_source_ids) != sources_needed:
        f.add("SV06", "revise", "reference_source_ids", "metadata와 집합 S 불일치")
    if not sources_needed:
        if not any(
            line.strip().startswith(EMPTY_REFERENCE_PREFIX)
            and len(line.strip()) > len(EMPTY_REFERENCE_PREFIX)
            for line in ref_body
        ):
            f.add("SV07", "revise", "REFERENCE", "빈 참고문헌 사유가 없다")

    # SV08 — fixture 표시·미평가 후보 점수 금지·outcome 표
    is_fixture = any(
        (s.url or "").startswith("fixture://") for s in context.sources.values()
    ) or any(e.locator.startswith("fixture://") for e in context.evidence.values())
    summary_text = "\n".join(by_title.get("SUMMARY", []))
    if is_fixture and FIXTURE_MARK not in summary_text:
        f.add("SV08", "revise", "SUMMARY", "fixture 출력에 가상 데이터 표시가 없다")
    if mode == "single_candidate" and "최우수" in summary_text:
        f.add("SV08", "revise", "SUMMARY", "첫 추천을 전체 최우수로 표현(D03 금지)")
    for cid in sorted(set(found) - set(summaries)):
        f.add("SV08", "revise", score_title, f"평가 안 된 후보 {cid}의 점수 표")
    if mode == "no_recommendation":
        otitle = expected[OUTCOME_SECTION]
        rows = {
            r[0]: r[1]
            for t in _tables(by_title.get(otitle, []))
            for r in t
            if len(r) == 3 and r[0] != "후보"
        }
        for o in ri.candidate_outcomes:
            if rows.get(o.candidate_id) != o.status:
                f.add("SV08", "revise", otitle, f"{o.candidate_id} 상태 표 불일치")

    h = artifact_hash(draft)
    checks: dict = {cid: not f.failed(cid) for cid in CHECK_IDS}
    checks["SV09"] = True  # 이 결과는 위 artifact_hash에만 유효
    action = (
        "fail"
        if any(e[1] == "fail" for e in f.errors)
        else ("revise" if f.errors else "pass")
    )
    checks["action"] = action
    return ValidationResult(
        schema_version=draft.schema_version,
        valid=not f.errors,
        context_id=context.context_id,
        checks=checks,
        errors=[
            {
                "schema_version": draft.schema_version,
                "code": check,
                "location": loc,
                "message": msg,
            }
            for check, _sev, loc, msg in f.errors
        ],
        artifact_hash=h,
    )


def is_current(
    result: ValidationResult, draft: ReportDraft, context: ReportContext
) -> bool:
    """SV09: 이 결과가 바로 이 draft·context를 검증한 것인지."""
    return (
        result.context_id == context.context_id == draft.context_id
        and result.artifact_hash == artifact_hash(draft)
    )
