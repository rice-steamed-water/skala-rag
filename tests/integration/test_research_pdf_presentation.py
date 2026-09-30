"""Synthetic research outputs through real Chromium; no model/network calls."""

import re
from pathlib import Path

import pytest
from pypdf import PdfReader
from tests.unit.test_demo_report_presentation import research_report
from tests.unit.test_html_report import rehash

from skala_rag.reporting.html_pdf import _chromium_pdf, verify_pdf
from skala_rag.reporting.html_report import render_report_html
from skala_rag.reporting.research_presentation import ROLES
from skala_rag.reporting.v3_context import canonical

pytestmark = pytest.mark.browser


def render_research(tmp_path, *, overflow=False):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        if not Path(p.chromium.executable_path).exists():
            pytest.skip("Playwright Chromium missing")
    draft, data = research_report()
    eid = draft.cited_evidence_ids[0]
    data["evidence"][eid]["confidence"] = "medium"
    # Explicitly synthetic saved scores; never written to live artifacts.
    data["role_scores"] = {
        role: {
            "score": "72.50" if role == "technology" else None,
            "evidence_ids": [eid] if role == "technology" else [],
        }
        for role in ROLES
    }
    data["live_reviews"] = {
        role: {
            "observations": [
                {
                    "text": f"{role} 원본 관측 {n}: "
                    "합성 자료의 저자 보고이며 독립 검증 아님.",
                    "evidence_ids": [eid],
                }
                for n in range(80 if overflow else 2)
            ],
            "interpretations": [{"text": f"{role} 원본 해석", "evidence_ids": [eid]}],
            "missing": [f"{role} 미확인 정보"],
        }
        for role in ROLES
    }
    text = render_report_html(draft, rehash(canonical(data)))
    path = tmp_path / "synthetic-research.pdf"
    _chromium_pdf(text, path)
    return draft, data, text, path


def test_research_pdf_has_clickable_superscripts_full_text_and_valid_layout(tmp_path):
    draft, data, rendered, path = render_research(tmp_path)
    checks, pages, fraction = verify_pdf(path, draft)
    assert all(checks.values()), checks
    assert pages <= 5 and fraction is not None and fraction <= 0.5
    reader = PdfReader(path)

    def squash(text):
        return re.sub(r"\s+", "", text)

    text = squash("".join(page.extract_text() for page in reader.pages))
    for review in data["live_reviews"].values():
        for kind in ("observations", "interpretations"):
            for item in review[kind]:
                assert squash(item["text"]) in text
        for missing in review["missing"]:
            assert squash(missing) in text
    assert "72.50/100" in text
    board = rendered.split('id="research-scoreboard"')[1].split("</section>")[0]
    assert board.count("<td></td>") == 4
    assert "미산정" not in board and "기록 수" not in board
    assert "투자적격성·추천판정미실시" in text
    assert '<sup class="citation">' in rendered
    links = [a.get_object() for page in reader.pages for a in page.get("/Annots", [])]
    assert links and all(a.get("/Dest") for a in links)
    reference = next(p for p in reader.pages if "참고문헌" in p.extract_text())
    assert reference.extract_text().startswith("참고문헌")


def test_research_overflow_is_not_hidden_or_shrunk(tmp_path):
    draft, _, rendered, path = render_research(tmp_path, overflow=True)
    checks, pages, _ = verify_pdf(path, draft)
    assert pages > 5 and not checks["page_count"]
    assert "business_deal 원본 관측 79" in rendered
    assert "overflow: hidden" not in rendered
    assert "font-size: 10.5pt; line-height: 15pt" in rendered
