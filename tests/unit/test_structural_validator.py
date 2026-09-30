"""#27 / T14·T23: Structural Validator (reporting.md §5 SV01–SV09)."""

import pytest
from tests.unit.test_report_context import POLICY, SV, _input, world  # noqa: F401

from skala_rag.contracts import ReportDraft
from skala_rag.reporting.context import build_report_context
from skala_rag.reporting.format import (
    FIXTURE_MARK,
    HEADINGS,
    evidence_token,
    reference_line,
    render_outcome_table,
    render_score_table,
    score_rows,
)
from skala_rag.reporting.validator import artifact_hash, is_current, validate_report

SCORE_KEY = "관측 근거 기반 점수"


def _draft(ctx, *, cite=2, overrides=None, **kw):
    ri = ctx.input
    headings = HEADINGS[ri.mode]
    evidence = sorted(ctx.evidence)[:cite]
    tokens = "".join(evidence_token(e) for e in evidence)
    sources = sorted({ctx.evidence[e].source_id for e in evidence})
    body = {h: f"{h} 내용입니다. {tokens}" for h in headings}
    body["SUMMARY"] = f"{FIXTURE_MARK}로 만든 보고서. 핵심 이유 {tokens}"
    tables = [
        render_score_table(
            s,
            next(
                d
                for d in ctx.decisions.values()
                if d.score_summary_id == s.score_summary_id
            ),
        )
        for s in ctx.score_summaries.values()
    ]
    body[headings[4]] = "평가 결과\n\n" + "\n\n".join(tables)
    if ri.mode == "no_recommendation":
        body[headings[2]] = render_outcome_table(ri.candidate_outcomes)
    body["REFERENCE"] = (
        "\n".join(reference_line(ctx.sources[s]) for s in sources)
        or "인용 자료 없음: 가상 사유"
    )
    body.update(overrides or {})
    markdown = "\n\n".join(f"## {h}\n\n{body[h]}" for h in headings)
    payload = {
        "schema_version": SV,
        "report_id": "report-1",
        "context_id": ctx.context_id,
        "revision": 0,
        "markdown": markdown,
        "cited_evidence_ids": evidence,
        "reference_source_ids": sources,
        "limitations": [],
    }
    payload.update(kw)
    return ReportDraft(**payload)


@pytest.fixture
def ctx(world):  # noqa: F811
    return build_report_context(_input(world), world["state"], policy=POLICY)


def _codes(result):
    return {e.code for e in result.errors}


def test_valid_draft_passes(ctx):
    draft = _draft(ctx)
    result = validate_report(draft, ctx)
    assert result.valid, result.errors
    assert result.checks["action"] == "pass"
    assert result.context_id == ctx.context_id
    assert result.artifact_hash == artifact_hash(draft)


def test_same_source_two_evidence(ctx):
    draft = _draft(ctx, cite=2)
    assert len(draft.cited_evidence_ids) == 2
    assert len(draft.reference_source_ids) == 1  # C=2, S=1
    assert validate_report(draft, ctx).valid


@pytest.mark.parametrize("problem", ["missing", "duplicate", "order"])
def test_heading_errors_revise(ctx, problem):
    good = _draft(ctx).markdown
    first, second = HEADINGS[ctx.input.mode][1:3]
    if problem == "missing":
        md = good.replace(f"## {first}\n", "")
    elif problem == "duplicate":
        md = good + f"\n\n## {first}\n\n중복"
    else:
        md = (
            good.replace(f"## {first}", "## @@")
            .replace(f"## {second}", f"## {first}")
            .replace("## @@", f"## {second}")
        )
    result = validate_report(_draft(ctx).model_copy(update={"markdown": md}), ctx)
    assert "SV03" in _codes(result) and result.checks["action"] == "revise"


def test_changed_score_or_label_revise(ctx):
    good = _draft(ctx)
    summary = next(iter(ctx.score_summaries.values()))
    decision = next(iter(ctx.decisions.values()))
    for old, new in [
        (f"| label | {decision.label} |", "| label | PASS_FAKE |"),
        (
            f"| 관측 근거 기반 점수 | {score_rows(summary, decision)[SCORE_KEY]} |",
            f"| {SCORE_KEY} | 99.99/100 |",
        ),
    ]:
        assert old in good.markdown
        bad = good.model_copy(update={"markdown": good.markdown.replace(old, new)})
        assert "SV04" in _codes(validate_report(bad, ctx))


def test_disallowed_evidence_and_metadata_mismatch(ctx):
    good = _draft(ctx)
    md = good.markdown.replace("## SUMMARY\n\n", "## SUMMARY\n\n[@evidence:ev-nope] ")
    bad = good.model_copy(update={"markdown": md})
    assert "SV05" in _codes(validate_report(bad, ctx))
    meta = good.model_copy(update={"cited_evidence_ids": good.cited_evidence_ids[:1]})
    assert "SV05" in _codes(validate_report(meta, ctx))


def test_code_block_token_not_counted(ctx):
    good = _draft(ctx)
    md = good.markdown.replace(
        "## SUMMARY\n\n", "## SUMMARY\n\n```\n[@evidence:ev-example]\n```\n"
    )
    assert validate_report(good.model_copy(update={"markdown": md}), ctx).valid


def test_extra_reference_and_fabricated_bibliography(ctx):
    good = _draft(ctx)
    extra_sid = next(s for s in ctx.sources if s not in good.reference_source_ids)
    extra = good.markdown + "\n" + reference_line(ctx.sources[extra_sid])
    r1 = validate_report(good.model_copy(update={"markdown": extra}), ctx)
    assert "SV06" in _codes(r1)
    sid = good.reference_source_ids[0]
    line = reference_line(ctx.sources[sid])
    faked = good.markdown.replace(line, line.replace("(2026)", "(2019)"))
    r2 = validate_report(good.model_copy(update={"markdown": faked}), ctx)
    assert "SV07" in _codes(r2)


def test_fixture_mark_required(ctx):
    good = _draft(ctx)
    md = good.markdown.replace(FIXTURE_MARK, "실제")
    assert "SV08" in _codes(
        validate_report(good.model_copy(update={"markdown": md}), ctx)
    )


def test_context_mismatch_is_fail(ctx):
    bad = _draft(ctx, context_id="context-v1-other")
    result = validate_report(bad, ctx)
    assert "SV01" in _codes(result) and result.checks["action"] == "fail"


def test_previous_result_not_reused_after_edit(ctx):
    draft = _draft(ctx)
    result = validate_report(draft, ctx)
    edited = draft.model_copy(update={"markdown": draft.markdown + "\n추가 문장"})
    assert is_current(result, draft, ctx)
    assert not is_current(result, edited, ctx)


def test_empty_reference_needs_reason(ctx):
    if ctx.input.mode == "single_candidate":
        pytest.skip("single_candidate는 인용 필수")
    draft = _draft(ctx, cite=0, overrides={"REFERENCE": "인용 자료 없음:"})
    assert "SV07" in _codes(validate_report(draft, ctx))
