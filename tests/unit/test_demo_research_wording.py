"""Research-mode presentation is not a failed investment assessment."""

from skala_rag.reporting.html_report import _card
from skala_rag.reporting.v3_pipeline import assessment_block


def test_unscored_research_omits_score_card():
    payload = {
        "policy_version": "unscored-research-only-1",
        "scores": {},
        "selection": {"selected_candidate_id": None},
    }
    assert _card(payload) == ""
    assert (
        assessment_block(payload)
        == "평가 미실시 — 자료 기반 연구이며 정량 점수·투자 판정을 수행하지 않았다."
    )


def test_regular_empty_assessment_keeps_existing_wording():
    payload = {"scores": {}, "selection": {"selected_candidate_id": None}}
    assert "성공 평가 후보 없음" in _card(payload)
    assert "성공 평가 후보 없음" in assessment_block(payload)
