"""Synthetic admission checks, no paid requests."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest


def approve(root):
    path = root / "data/local/demo180-approval.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"approval_reference": "synthetic-test-approval"}))


def test_campaign_requires_approval_and_persists_reservations(tmp_path):
    from skala_rag.demo_budget import Campaign
    from skala_rag.tools.runtime import Allowance

    with pytest.raises(ValueError, match="APPROVAL_REQUIRED"):
        with Campaign(tmp_path):
            pass
    approve(tmp_path)
    allowance = Allowance(
        schema_version="test",
        input_tokens=10,
        output_tokens=10,
        max_cost_usd=Decimal("2"),
    )
    with Campaign(tmp_path) as campaign:
        campaign.reserve(allowance)
        with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
            campaign.reserve(allowance)
    with Campaign(tmp_path) as campaign:
        assert campaign.state["calls"] == 1
        assert campaign.state["cost_usd_accounted"] == "2"
        with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
            campaign.reserve(allowance)


def test_campaign_expiry_and_lock_block_before_request(tmp_path):
    from skala_rag.demo_budget import Campaign

    approve(tmp_path)
    with Campaign(tmp_path) as campaign:
        with pytest.raises(ValueError, match="DEMO_BUSY"):
            with Campaign(tmp_path):
                pass
        campaign.state["deadline"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()
        campaign.save()
    with pytest.raises(ValueError, match="CAMPAIGN_EXPIRED"):
        with Campaign(tmp_path):
            pass


def test_failure_requires_explicit_reviewed_renewal_without_resetting_spend(tmp_path):
    from skala_rag.demo_budget import Campaign
    from skala_rag.tools.runtime import Allowance

    approve(tmp_path)
    with Campaign(tmp_path) as campaign:
        campaign.reserve(
            Allowance(
                schema_version="test",
                input_tokens=10,
                output_tokens=10,
                max_cost_usd=Decimal("1.25"),
            )
        )
        campaign.finish_run("failed-run", "failed")
    with pytest.raises(ValueError, match="REAPPROVAL_REQUIRED"):
        with Campaign(tmp_path):
            pass
    approval = tmp_path / "data/local/demo180-approval.json"
    approval.write_text(json.dumps({"approval_reference": "renewed"}))
    with pytest.raises(ValueError, match="REAPPROVAL_REQUIRED"):
        with Campaign(tmp_path):
            pass
    approval.write_text(
        json.dumps(
            {
                "approval_reference": "renewed",
                "reapproval": {
                    "previous_approval_reference": "synthetic-test-approval",
                    "failed_run_id": "failed-run",
                    "review_reference": "reviewed failure offline",
                },
            }
        )
    )
    with Campaign(tmp_path) as campaign:
        assert campaign.state["calls"] == 1
        assert campaign.state["cost_usd_accounted"] == "1.25"
        assert campaign.state["limits"] == {
            "llm_calls": 30,
            "cost_usd": "3",
            "seconds": 1200,
        }
        assert campaign.state["reapproval_required"] is False
        assert campaign.state["approval_reference"] == "renewed"


@pytest.mark.parametrize(
    "missing", ["previous_approval_reference", "failed_run_id", "review_reference"]
)
def test_reapproval_cannot_omit_review_or_failure_binding(tmp_path, missing):
    from skala_rag.demo_budget import Campaign

    approve(tmp_path)
    with Campaign(tmp_path) as campaign:
        campaign.finish_run("interrupted", "running")
    renewal = {
        "previous_approval_reference": "synthetic-test-approval",
        "failed_run_id": "interrupted",
        "review_reference": "review",
    }
    del renewal[missing]
    (tmp_path / "data/local/demo180-approval.json").write_text(
        json.dumps(
            {
                "approval_reference": "new-approval",
                "reapproval": renewal,
            }
        )
    )
    with pytest.raises(ValueError, match="REAPPROVAL_REQUIRED"):
        with Campaign(tmp_path):
            pass
