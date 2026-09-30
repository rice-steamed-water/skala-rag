"""Fixture Report Generator — #28, reporting.md §1–§4, T16.

``GenerateReport`` 경계를 만족하는 결정적 템플릿 생성기다. 고정 ReportContext의
payload만 읽고, 인용 token·평가 결과 표·후보 결과 표·REFERENCE는 Structural
Validator와 같은 ``reporting.format`` 함수로 만든다. 실제 Generator LLM(M3)이
아니며, 통과해도 실모델 보고서 품질의 증거가 아니다. ``feedback``은 기록만 하고
문장을 고치지 않는다 — 같은 context면 같은 draft가 나온다.
"""

import hashlib
from collections.abc import Sequence

from skala_rag.contracts import Evaluation, ReportContext, ReportDraft
from skala_rag.reporting.format import (
    EMPTY_REFERENCE_PREFIX,
    FIXTURE_MARK,
    HEADINGS,
    evidence_token,
    reference_line,
    render_outcome_table,
    render_score_table,
)
from skala_rag.reporting.validator import TOKEN

SECTION_DIMENSIONS = {
    2: ("market",),
    3: ("founder", "technology", "moat"),
    4: ("traction", "deal_terms"),
}


def report_id_for(context: ReportContext) -> str:
    digest = hashlib.sha256(context.context_id.encode("utf-8")).hexdigest()[:16]
    return f"report-fixture-{digest}"


def _final_evaluations(context: ReportContext, candidate_id: str) -> list[Evaluation]:
    summary = next(
        (s for s in context.score_summaries.values() if s.candidate_id == candidate_id),
        None,
    )
    if summary is None:
        return []
    return [
        e
        for e in context.evaluations.values()
        if e.candidate_id == candidate_id
        and e.evaluation_round == summary.evaluation_round
    ]


def _evidence_ids(context: ReportContext, evaluations, dimensions) -> list[str]:
    ids = {
        eid
        for e in evaluations
        if e.dimension in dimensions
        for criterion in e.criteria
        for eid in criterion.evidence_ids
    }
    return sorted(ids & set(context.evidence))


def _claims(context: ReportContext, evidence_ids: list[str]) -> list[str]:
    return [
        f"- {context.evidence[eid].claim} {evidence_token(eid)}" for eid in evidence_ids
    ]


def _missing(evaluations) -> list[str]:
    return [
        f"- {c.criterion_id}: 결측 — {c.missing_reason or '사유 미기재'}"
        for e in sorted(evaluations, key=lambda e: e.dimension)
        for c in e.criteria
        if c.status != "observed"
    ]


def _score_tables(context: ReportContext) -> list[str]:
    by_summary = {d.score_summary_id: d for d in context.decisions.values()}
    return [
        render_score_table(s, by_summary[s.score_summary_id])
        for s in sorted(context.score_summaries.values(), key=lambda s: s.candidate_id)
        if s.score_summary_id in by_summary
    ]


def _single(context: ReportContext) -> tuple[dict[int, str], list[str]]:
    ri = context.input
    cid = ri.selected_candidate_id
    decision = next(d for d in context.decisions.values() if d.candidate_id == cid)
    summary = next(s for s in context.score_summaries.values() if s.candidate_id == cid)
    evaluations = _final_evaluations(context, cid)
    eligibility = [
        eid
        for r in context.eligibility_results.values()
        if r.candidate_id == cid
        for eid in r.evidence_ids
        if eid in context.evidence
    ]
    sec = {
        i: _evidence_ids(context, evaluations, d) for i, d in SECTION_DIMENSIONS.items()
    }
    key = (eligibility or sec[2] or sec[3] or sec[4])[:1]
    limits = list(summary.hold_reasons) or ["보류 사유 없음"]
    body = {
        0: "\n".join(
            [
                f"이 보고서는 {FIXTURE_MARK}로 만든 fixture 출력이다.",
                f"선택 기업 {cid}는 D03 기준 첫 RECOMMEND 후보이며, "
                "전체 후보 비교 결과가 아니다.",
                f"판정 {decision.label}, 등급 {decision.report_grade}. "
                f"핵심 근거 {''.join(evidence_token(e) for e in key)}",
                f"주요 한계: {', '.join(limits)}.",
            ]
        ),
        1: "\n".join(
            [f"기업 식별: {cid}", *_claims(context, sorted(set(eligibility)))]
        ),
        2: "\n".join(_claims(context, sec[2])) or "시장 영역의 인용 가능한 근거 없음.",
        3: "\n".join(_claims(context, sec[3])) or "팀·기술·경쟁 우위 근거 없음.",
        4: "\n\n".join(
            [
                "\n".join(_claims(context, sec[4])) or "실적·투자조건 근거 없음.",
                "평가 결과",
                *_score_tables(context),
            ]
        ),
        5: "\n".join(
            [
                *[f"- 위험: {r}" for r in decision.risks],
                *[f"- 한계: {r}" for r in decision.limitations],
                *_missing(evaluations),
                f"- 보류 사유: {', '.join(limits)}",
            ]
        ),
    }
    return body, limits


def _summary(context: ReportContext) -> tuple[dict[int, str], list[str]]:
    ri = context.input
    outcomes = ri.candidate_outcomes
    counts: dict[str, int] = {}
    for o in outcomes:
        counts[o.status] = counts.get(o.status, 0) + 1
    count_text = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    evaluated = [s.candidate_id for s in context.score_summaries.values()]
    evaluations = [e for cid in evaluated for e in _final_evaluations(context, cid)]
    observed = _evidence_ids(context, evaluations, ("market", "technology"))
    reason = (
        "평가 후보 없음"
        if not outcomes
        else "정보 부족 후보 포함"
        if "eligibility_unknown" in counts or "failed" in counts
        else "추천 후보 없음"
    )
    limits = [reason]
    tables = _score_tables(context)
    body = {
        0: "\n".join(
            [
                f"이 보고서는 {FIXTURE_MARK}로 만든 fixture 출력이다.",
                f"결과: {reason}. 처리 후보 {len(outcomes)}건 "
                f"({count_text or '없음'}).",
                "단일 기업 추천 보고서가 아니다.",
            ]
        ),
        1: "\n".join(
            [
                f"기준일 {ri.as_of.isoformat()}, corpus {ri.corpus_version}, "
                f"policy {ri.policy_version}.",
                f"처리 후보 수: {len(outcomes)}.",
            ]
        ),
        2: render_outcome_table(outcomes) if outcomes else "평가 후보 없음.",
        3: "\n".join(_claims(context, observed))
        or "관찰 불가: 성공 평가 후보가 없어 인용 가능한 시장·기술 근거가 없다.",
        4: "\n\n".join(["평가 결과", *tables])
        if tables
        else "성공 평가 후보 없음 — 점수·판정을 만들지 않는다.",
        5: "\n".join(
            [
                f"- 공통 한계: {reason}",
                *_missing(evaluations),
                "- 후속 조사: 결측 기준과 적격성 미확정 항목을 추가 확인한다.",
            ]
        ),
    }
    return body, limits


def generate_fixture_report(
    context: ReportContext, feedback: Sequence[str]
) -> ReportDraft:
    """고정 context → ReportDraft. revision은 보고서 controller가 정한다."""
    del feedback  # 결정적 템플릿: feedback으로 문장을 바꾸지 않는다
    mode = context.input.mode
    headings = HEADINGS[mode]
    body, limits = _single(context) if mode == "single_candidate" else _summary(context)
    cited = sorted(set(TOKEN.findall("\n".join(body.values()))))
    sources = sorted({context.evidence[e].source_id for e in cited})
    reference = "\n".join(reference_line(context.sources[s]) for s in sources) or (
        f"{EMPTY_REFERENCE_PREFIX} 본문에서 인용한 Evidence가 없다"
    )
    parts = [f"## {headings[i]}\n\n{body[i]}" for i in range(6)]
    parts.append(f"## {headings[6]}\n\n{reference}")
    return ReportDraft(
        schema_version=context.schema_version,
        report_id=report_id_for(context),
        context_id=context.context_id,
        revision=0,
        markdown="\n\n".join(parts),
        cited_evidence_ids=cited,
        reference_source_ids=sources,
        limitations=limits,
    )
