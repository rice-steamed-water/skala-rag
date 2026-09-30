"""Approved Finance policy regression; offline, not live measurements."""

from pathlib import Path

import yaml


def test_approved_finance_scope():
    root = Path(__file__).resolve().parents[2]
    rubric = yaml.safe_load((root / "configs/rubrics/finance.yaml").read_text())
    assert rubric["status"] == "approved"
    assert rubric["rubric_version"] == "finance-0.1.0"
    assert rubric["approval_reference"] == "#61 comment5906253348"
    criteria = {
        cid: c
        for d in rubric["dimensions"].values()
        for cid, c in d["criteria"].items()
    }
    assert len(criteria) == 9
    rules = {
        cid: c["applicability_rule_id"]
        for cid, c in criteria.items()
        if "applicability_rule_id" in c
    }
    assert rules == {
        "traction.rule_of_40": "finance-0.1.0:rule40-confirmed-pre-revenue",
        "traction.runway": "finance-0.1.0:runway-confirmed-nonnegative-ocf",
    }
    assert criteria["deal_terms.valuation"]["comparison_thresholds"] == {
        "previous_round_multiple": 3,
        "peer_median_multiple": 2,
        "minimum_peer_evidence": 2,
    }
    assert "pre_post_unknown" in criteria["deal_terms.valuation"]["missing_when"]
    assert "pre_post_unknown" in criteria["deal_terms.ownership"]["missing_when"]
    assert "CAPEX 미포함" in criteria["traction.burn"]["definition"]
    assert (
        "새 기준액·rating cap 없음"
        in criteria["traction.revenue_growth"]["limitations_required"]
    )
