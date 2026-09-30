"""Research-only presentation; synthetic evidence, no live calls."""

import html
import re

import pytest
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
        assert f">[{n}]</a></sup>" in rendered
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


def test_research_scoreboard_has_only_five_blank_scores_without_score_evidence():
    draft, data = research_report()
    eid = draft.cited_evidence_ids[0]
    data["evidence"][eid]["confidence"] = "medium"
    data["live_reviews"] = {
        "technology": {
            "observations": [{"text": "저자 보고", "evidence_ids": [eid, eid]}] * 2,
            "interpretations": [{"text": "해석", "evidence_ids": [eid]}],
            "missing": ["비용", "비용", "기간"],
        }
    }
    rendered = render_report_html(draft, rehash(canonical(data)))
    board = rendered.split('id="research-scoreboard"')[1].split("</section>")[0]
    assert (
        rendered.index('data-section="SUMMARY"')
        < rendered.index('id="research-scoreboard"')
        < rendered.index('data-section="COMPANY &amp; TEAM"')
    )
    assert board.count("data-role=") == 5
    row = board.split('data-role="technology"')[1].split("</tr>")[0]
    assert "<td></td>" in row
    assert board.count("<td></td>") == 5
    assert "<th>항목</th><th>점수 / 100</th>" in board
    assert "사업·투자조건" in board
    for removed in ("관측", "해석", "출처", "결측", "신뢰도", "미산정", "기록 수"):
        assert removed not in board
    assert not re.search(r"<td>\d", board)


def test_synthetic_saved_role_scores_render_exactly_without_recalculation():
    draft, data = research_report()
    eid = draft.cited_evidence_ids[0]
    values = dict(
        zip(
            ("founder", "market", "technology", "moat", "business_deal"),
            ("0", "100", "72.50", "12.125", "90"),
            strict=True,
        )
    )
    data["role_scores"] = {
        role: {"score": score, "evidence_ids": [eid]} for role, score in values.items()
    }
    ctx = rehash(canonical(data))
    before = ctx.payload
    rendered = render_report_html(draft, ctx)
    board = rendered.split('id="research-scoreboard"')[1].split("</section>")[0]
    for role, score in values.items():
        row = board.split(f'data-role="{role}"')[1].split("</tr>")[0]
        assert f"<td>{score} / 100" in row
    assert ctx.payload == before


@pytest.mark.parametrize(
    "score",
    [
        "NaN",
        "sNaN",
        "Infinity",
        "-Infinity",
        "-0.1",
        "100.01",
        "medium",
        "<script>",
        "",
        "1_0",
        " 70 ",
        "1e1",
        None,
        True,
        70,
        70.5,
    ],
)
def test_synthetic_invalid_scores_are_blank(score):
    draft, data = research_report()
    data["role_scores"] = {
        "technology": {
            "score": score,
            "evidence_ids": draft.cited_evidence_ids,
        }
    }
    rendered = render_report_html(draft, rehash(canonical(data)))
    row = rendered.split('data-role="technology"')[1].split("</tr>")[0]
    assert row.endswith("<td></td>")


@pytest.mark.parametrize(
    "ids", [[], None, "known", ["unknown"], ["known", "unknown"], [None], [{}], [""]]
)
def test_synthetic_scores_require_nonempty_all_valid_evidence(ids):
    draft, data = research_report()
    data["evidence"]["known"] = data["evidence"][draft.cited_evidence_ids[0]]
    data["role_scores"] = {"technology": {"score": "75", "evidence_ids": ids}}
    rendered = render_report_html(draft, rehash(canonical(data)))
    row = rendered.split('data-role="technology"')[1].split("</tr>")[0]
    assert row.endswith("<td></td>")


@pytest.mark.parametrize("record", [None, {}, {"source_id": "missing"}])
def test_synthetic_scores_require_resolvable_evidence_source(record):
    draft, data = research_report()
    data["evidence"]["invalid"] = record
    data["role_scores"] = {
        "technology": {
            "score": "75",
            "evidence_ids": ["invalid"],
        }
    }
    rendered = render_report_html(draft, rehash(canonical(data)))
    row = rendered.split('data-role="technology"')[1].split("</tr>")[0]
    assert row.endswith("<td></td>")


@pytest.mark.parametrize("scores", [None, [], {"technology": None}, {"technology": []}])
def test_synthetic_malformed_score_records_are_blank(scores):
    draft, data = research_report()
    data["role_scores"] = scores
    rendered = render_report_html(draft, rehash(canonical(data)))
    board = rendered.split('id="research-scoreboard"')[1].split("</section>")[0]
    assert board.count("<td></td>") == 5


def test_synthetic_score_only_evidence_has_usable_superscript_target():
    draft, data = research_report()
    original = data["evidence"][draft.cited_evidence_ids[0]]
    data["evidence"]["score-only"] = {**original, "excerpt": "Synthetic score support"}
    data["role_scores"] = {
        "technology": {
            "score": "72.5",
            "evidence_ids": ["score-only", "score-only"],
        }
    }
    rendered = render_report_html(draft, rehash(canonical(data)))
    row = rendered.split('data-role="technology"')[1].split("</tr>")[0]
    assert row.count('<sup class="citation">') == 1
    match = re.search(r'href="#([^"]+)"', row)
    assert match is not None
    target = match.group(1)
    assert f'id="{target}"' in rendered
    assert "Synthetic score support" in rendered


@pytest.mark.parametrize("scores", [{}, {"technology": {"score": None}}])
def test_role_score_contract_header_does_not_claim_no_evaluation(scores):
    draft, data = research_report()
    legacy = render_report_html(draft, rehash(canonical(data)))
    assert "자료 기반 / 투자 평가 미실시" in legacy
    data["role_scores"] = scores
    rendered = render_report_html(draft, rehash(canonical(data)))
    header = rendered.split('<header class="report-masthead">')[1].split("</header>")[0]
    assert "투자 적격성·추천 판정 미실시" in header
    assert "투자 평가 미실시" not in header


def test_synthetic_score_without_reference_section_has_no_dead_link():
    draft, data = research_report()
    data["role_scores"] = {
        "technology": {
            "score": "72.5",
            "evidence_ids": draft.cited_evidence_ids,
        }
    }
    draft = draft.model_copy(update={"markdown": "## SUMMARY\nSynthetic summary"})
    rendered = render_report_html(draft, rehash(canonical(data)))
    row = rendered.split('data-role="technology"')[1].split("</tr>")[0]
    assert "72.5 / 100" in row
    assert "href=" not in row and "<sup" not in row


def test_all_saved_review_items_are_preserved_with_role_and_evidence_links():
    draft, data = research_report()
    eid = draft.cited_evidence_ids[0]
    sid = data["evidence"][eid]["source_id"]
    extra = "additional-review-evidence"
    data["evidence"][extra] = {
        "source_id": sid,
        "excerpt": "Additional source excerpt.",
        "locator": "page 7",
        "confidence": "low",
    }
    item = {"text": "원문 역할의 기술 관측 <400시간>", "evidence_ids": [extra]}
    data["live_reviews"] = {
        role: {
            "observations": [item, item],
            "interpretations": [{"text": f"{role} 해석", "evidence_ids": [eid]}],
            "missing": [f"{role} 자료 부족", f"{role} 자료 부족"],
        }
        for role in ("founder", "market", "technology", "moat", "business_deal")
    }
    ctx = rehash(canonical(data))
    before = ctx.payload
    rendered = render_report_html(draft, ctx)
    for role in data["live_reviews"]:
        role_block = rendered.split(f'id="review-{role}"')[1].split("</article>")[0]
        observation_block = role_block
        if role in ("moat", "business_deal"):
            observation_block = rendered.split(f'id="observations-{role}"')[1].split(
                "</div>"
            )[0]
        assert observation_block.count("원문 역할의 기술 관측 &lt;400시간&gt;") == 1
        assert f"{role} 해석" in role_block and f"{role} 자료 부족" in role_block
        assert "원본 역할 출력" in role_block
    assert "역할명이 해당 영역의 실사를 보장하지 않습니다" in rendered
    assert "추가 역할 출력은 별도 의미 검증을 거치지 않았습니다" in rendered
    assert f"[@evidence:{extra}]" in rendered
    assert "Additional source excerpt." in rendered
    assert "인용 근거 2개" in rendered
    assert set(re.findall(r'href="#([^"]+)"', rendered)) <= set(
        re.findall(r'id="([^"]+)"', rendered)
    )
    assert ctx.payload == before
    assert extra not in draft.cited_evidence_ids


def test_investment_details_follow_interpretations_without_losing_numbers():
    draft, data = research_report()
    eid = draft.cited_evidence_ids[0]
    data["live_reviews"] = {
        role: {
            "observations": [
                {"text": "π0.5: 10–15분, $2.5M <원문>", "evidence_ids": [eid]},
                {"text": "숫자 없는 관측도 보존", "evidence_ids": [eid]},
            ],
            "interpretations": [{"text": "2개 환경의 한계", "evidence_ids": [eid]}],
            "missing": ["비용·운영 위험 후속 확인"],
        }
        for role in ("moat", "business_deal")
    }
    ctx = rehash(canonical(data))
    before = ctx.payload
    rendered = render_report_html(draft, ctx)
    assessment = rendered.split('data-section="INVESTMENT ASSESSMENT &amp; RISKS"')[1]
    narrative, details = assessment.split('id="investment-evidence-details"', 1)
    details = details.split('id="sec-REFERENCE"')[0]
    assert "π0.5: 10–15분" not in narrative
    for role in data["live_reviews"]:
        assert f'id="review-{role}"' in narrative
        assert f'id="observations-{role}"' in details
    assert narrative.count("2개 환경의 한계") == 2
    assert narrative.count("비용·운영 위험 후속 확인") == 2
    assert details.count("π0.5: 10–15분, $2.5M &lt;원문&gt;") == 2
    assert details.count("숫자 없는 관측도 보존") == 2
    assert details.count('<sup class="citation">') == 4
    assert "#investment-evidence-details h4 { margin: 4pt 0;" in rendered
    assert ctx.payload == before
    assert set(re.findall(r'href="#([^\"]+)"', rendered)) <= set(
        re.findall(r'id="([^\"]+)"', rendered)
    )


def test_review_only_source_is_in_appendix_with_traceable_provenance():
    draft, data = research_report()
    data["sources"]["review-source"] = {
        "title": "추가 원문 <검증 아님>",
        "url": "https://example.test/review",
        "author": None,
        "published_at": "2025-04-16",
    }
    data["evidence"]["review-evidence"] = {
        "source_id": "review-source",
        "excerpt": "원문 추가 발췌",
        "locator": "page 8",
        "confidence": "low",
        "evidence_kind": "reported",
        "provenance": [
            {
                "method": "rag",
                "chunk_id": "chunk-extra",
                "retrieval_id": "retrieval-extra",
            }
        ],
    }
    data["live_reviews"] = {
        "technology": {
            "observations": [
                {
                    "text": "추가 원문 관측",
                    "evidence_ids": ["review-evidence", "unresolved"],
                }
            ],
            "interpretations": [],
            "missing": [],
        }
    }
    rendered = render_report_html(draft, rehash(canonical(data)))
    assert "[@source:review-source]" in rendered
    assert "추가 원문 &lt;검증 아님&gt;" in rendered
    assert "chunk-extra" in rendered and "retrieval-extra" in rendered
    assert "reported" in rendered and "low" in rendered
    assert "미해소 근거: unresolved" in rendered
    assert "인용 근거 2개 · 고유 출처 2개" in rendered
    assert set(re.findall(r'href="#([^"]+)"', rendered)) <= set(
        re.findall(r'id="([^"]+)"', rendered)
    )


def test_editorial_research_style_is_scoped_and_preserves_readable_body():
    draft, data = research_report()
    rendered = render_report_html(draft, rehash(canonical(data)))
    assert '<body class="research-report">' in rendered
    assert '<header class="report-masthead">' in rendered
    assert "RESEARCH BRIEF" in rendered
    assert ".research-report {" in rendered
    assert "font-size: 10.5pt; line-height: 15pt" in rendered
    assert ".research-report .citation {" in rendered
    assert "vertical-align: super" in rendered
    assert "#research-scoreboard th:first-child { width: 55%" in rendered
    assert "#research-scoreboard th:nth-child(7)" not in rendered
    assert "#research-scoreboard th:nth-child(8)" not in rendered
    assert ".research-report #sec-REFERENCE { break-before: page; }" in rendered
    assert "overflow: hidden" not in rendered
    normal = render_report_html(draft, context())
    assert "research-report" not in normal and "RESEARCH BRIEF" not in normal


def test_missing_research_details_are_unknown_not_invented():
    draft, data = research_report(None)
    eid = draft.cited_evidence_ids[0]
    data["evidence"][eid] = {}
    rendered = render_report_html(draft, rehash(canonical(data)))
    assert "조사 대상 미상 — 자료 기반 연구 보고서" in rendered
    assert "출처: 미상" in rendered and "위치: 미상" in rendered
