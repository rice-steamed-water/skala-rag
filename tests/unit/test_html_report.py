"""#175: 한글 HTML 보고서 (브라우저 없음). fixture이며 실측 아님."""

import hashlib
import re
from dataclasses import replace

import pytest
from tests.unit.test_v3_report_pipeline import Stub, context

from skala_rag.reporting.html_report import render_report_html
from skala_rag.reporting.v3_context import ReportContextV3, canonical
from skala_rag.reporting.v3_pipeline import ReportGeneratorV3


@pytest.fixture(scope="module")
def ctx():
    return context()


@pytest.fixture(scope="module")
def draft(ctx):
    return ReportGeneratorV3(Stub())(ctx, [])


def rehash(payload):
    return ReportContextV3(
        "sha256:" + hashlib.sha256(payload.encode()).hexdigest(), payload
    )


def test_korean_structure_and_csp(ctx, draft):
    html = render_report_html(draft, ctx)
    assert html.startswith("<!DOCTYPE html>") and '<html lang="ko">' in html
    assert '<meta charset="utf-8">' in html
    assert "default-src 'none'; style-src 'unsafe-inline'; font-src data:" in html
    for ko, en in [
        ("요약", "SUMMARY"),
        ("기업·팀", "COMPANY &amp; TEAM"),
        ("기술·시장", "TECHNOLOGY &amp; MARKET"),
        ("투자 평가·위험", "INVESTMENT ASSESSMENT &amp; RISKS"),
        ("참고문헌", "REFERENCE"),
    ]:
        assert f">{ko}</h2>" in html
        assert f'data-section="{en}"' in html
    assert "data:font/ttf;base64," in html
    assert "<script" not in html and "<img" not in html


def test_tokens_preserved_as_internal_anchors(ctx, draft):
    html = render_report_html(draft, ctx)
    for eid in draft.cited_evidence_ids:
        assert f"[@evidence:{eid}]" in html
    for sid in draft.reference_source_ids:
        assert f"[@source:{sid}]" in html
    assert not re.search(r'(?:href|src)="(?:https?:|file:|//)', html)
    hrefs = re.findall(r'href="#([^"]+)"', html)
    ids = set(re.findall(r'id="([^"]+)"', html))
    assert hrefs and set(hrefs) <= ids


def test_injection_is_escaped(ctx, draft):
    evil = "<script>alert(1)</script><img src=x onerror=1> [x](http://evil.test)"
    marked = draft.markdown.replace("가상 관측", evil, 1)
    html = render_report_html(draft.model_copy(update={"markdown": marked}), ctx)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=1&gt;" in html
    assert "<script" not in html and "<img" not in html
    assert not re.search(r'(?:href|src)="http', html)


def test_none_is_unknown_and_decimal_zero_preserved(ctx, draft):
    data = ctx.snapshot()
    cid = next(iter(data["scores"]))
    data["scores"][cid]["observed_score"] = None
    data["scores"][cid]["coverage_pct"] = "0"
    data["scores"][cid]["missing_weight"] = "12.500"
    html = render_report_html(draft, rehash(canonical(data)))
    card = html.split('id="score-overview"')[1].split("</section>")[0]
    assert "미상" in card and ">0<" in card and "12.500" in card


def test_fixture_banner(ctx, draft):
    assert "가상 데이터 — 실제 투자 판단 아님" in render_report_html(draft, ctx)


def test_deterministic(ctx, draft):
    assert render_report_html(draft, ctx) == render_report_html(draft, ctx)


def test_tampered_context_rejected(ctx, draft):
    with pytest.raises(ValueError):
        render_report_html(draft, replace(ctx, payload="{}"))


def test_malformed_table_is_escaped_paragraph(ctx, draft):
    marked = draft.markdown.replace("가상 관측", "|---|\n|<b>x</b>| 가상 관측", 1)
    html = render_report_html(draft.model_copy(update={"markdown": marked}), ctx)
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>" not in html


def test_separator_only_table_does_not_raise(ctx, draft):
    marked = draft.markdown.replace("가상 관측", "|---|\n\n가상 관측", 1)
    render_report_html(draft.model_copy(update={"markdown": marked}), ctx)


def test_anchor_ids_collision_free_and_dangling_citation_unlinked(ctx, draft):
    a, b, ghost = "s.a", "s_a", "ghost"
    marked = draft.markdown + f"\n- [@source:{a}] A\n- [@source:{b}] B\n"
    marked = marked.replace(
        "가상 관측", f"가상 관측 [@source:{ghost}] [@source:{a}]", 1
    )
    ids = [*draft.reference_source_ids, a, b, ghost]
    d = draft.model_copy(update={"markdown": marked, "reference_source_ids": ids})
    html = render_report_html(d, ctx)
    found = re.findall(r'<li id="([^"]+)">', html)
    assert len(found) == len(set(found))
    hrefs = re.findall(r'href="#([^"]+)"', html)
    assert set(hrefs) <= set(found)
    assert f">[@source:{ghost}]</a>" not in html
