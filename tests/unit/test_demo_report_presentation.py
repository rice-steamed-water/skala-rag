"""Research-only presentation; synthetic evidence, no live calls."""

import html
import re

from tests.unit.test_html_report import rehash
from tests.unit.test_v3_report_pipeline import Stub, context

from skala_rag.reporting.html_report import render_report_html
from skala_rag.reporting.v3_context import canonical
from skala_rag.reporting.v3_pipeline import ReportGeneratorV3


def research_report(subject="Example Robotics"):
    ctx = context()
    draft = ReportGeneratorV3(Stub())(ctx, [])
    data = ctx.snapshot()
    data.update(
        policy_version="unscored-research-only-1",
        research_subject=subject,
        scores={},
        decisions={},
        outcomes={},
    )
    return draft, data


def test_research_company_is_prominent_and_escaped():
    draft, data = research_report("Example <Robotics> & Co")
    rendered = render_report_html(draft, rehash(canonical(data)))
    title = "Example &lt;Robotics&gt; &amp; Co — 자료 기반 연구 보고서"
    assert f"<title>{title}</title>" in rendered
    assert f"<h1>{title}</h1>" in rendered
    assert "score-overview" not in rendered


def test_cited_evidence_is_distinct_from_unique_sources():
    draft, data = research_report()
    sid = draft.reference_source_ids[0]
    ids = [f"long-evidence-id-{n}" for n in range(4)]
    data["evidence"] = {
        eid: {
            "source_id": sid,
            "excerpt": f"Exact excerpt {n} <not markup>.",
            "locator": f"https://example.test/paper#page={n + 1}",
        }
        for n, eid in enumerate(ids)
    }
    data["sources"][sid].update(title="One paper", url="https://example.test/paper")
    # Uncited stored evidence must not inflate the list or count.
    data["evidence"]["uncited"] = {"source_id": sid, "excerpt": "DO NOT INCLUDE"}
    marked = draft.markdown.split("## REFERENCE")[0]
    marked = re.sub(r"\[@evidence:[^\]]+\]", "", marked)
    marked += " ".join(f"[@evidence:{eid}]" for eid in ids + ids[:1])
    marked += f"\n## REFERENCE\n- [@source:{sid}] One paper\n"
    draft = draft.model_copy(
        update={
            "markdown": marked,
            "cited_evidence_ids": ids,
            "reference_source_ids": [sid],
        }
    )
    rendered = render_report_html(draft, rehash(canonical(data)))
    assert "인용 근거 4개 · 고유 출처 1개" in rendered
    evidence = rendered.split('id="cited-evidence"')[1].split("</section>")[0]
    assert "인용 근거 목록" in evidence
    assert "고유 출처 참고문헌" in rendered
    for n, eid in enumerate(ids, 1):
        assert f">[근거 {n}]</a>" in rendered
        assert f'class="machine-citation">[@evidence:{eid}]</p>' in evidence
        assert html.escape(data["evidence"][eid]["excerpt"]) in evidence
        assert data["evidence"][eid]["locator"] in evidence
    assert evidence.count("One paper") == 4
    assert "DO NOT INCLUDE" not in rendered
    assert set(re.findall(r'href="#([^"]+)"', rendered)) <= set(
        re.findall(r'id="([^"]+)"', rendered)
    )
    assert not re.search(r'(?:href|src)="https?://', rendered)


def test_long_excerpt_is_bounded_and_explicitly_marked_without_mutation():
    draft, data = research_report()
    eid = draft.cited_evidence_ids[0]
    original = "Verbatim   text & symbols.\n" * 1000
    data["evidence"][eid]["excerpt"] = original
    ctx = rehash(canonical(data))
    rendered = render_report_html(draft, ctx)
    excerpts = re.findall(r'<p class="evidence-excerpt">(.*?)</p>', rendered, re.S)
    assert html.escape(" ".join(original.split())[:480]) in excerpts[0]
    assert "[이하 생략]" in excerpts[0]
    assert len(html.unescape(excerpts[0])) < 520
    assert "공백 정리" in rendered
    assert ctx.snapshot()["evidence"][eid]["excerpt"] == original


def test_missing_research_details_are_unknown_not_invented():
    draft, data = research_report(None)
    eid = draft.cited_evidence_ids[0]
    data["evidence"][eid] = {}
    rendered = render_report_html(draft, rehash(canonical(data)))
    assert "조사 대상 미상 — 자료 기반 연구 보고서" in rendered
    assert "출처: 미상" in rendered and "위치: 미상" in rendered
