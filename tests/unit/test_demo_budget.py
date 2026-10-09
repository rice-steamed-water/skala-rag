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


def test_requested_lower_limits_never_refill_or_extend_campaign(tmp_path):
    from skala_rag.demo_budget import Campaign
    from skala_rag.settings import load_runtime_document
    from skala_rag.tools.runtime import Allowance

    approve(tmp_path)
    document = load_runtime_document()
    profile = document.profiles.local_demo.model_copy(
        update={"llm_calls": 2, "cost_usd": Decimal("1"), "seconds": 600}
    )
    lowered = document.model_copy(
        update={
            "profiles": document.profiles.model_copy(update={"local_demo": profile})
        }
    )
    allowance = Allowance(
        schema_version="test",
        input_tokens=1,
        output_tokens=1,
        max_cost_usd=Decimal("0.4"),
    )
    with Campaign(tmp_path, runtime_document=lowered) as campaign:
        campaign.reserve(allowance)
        original = campaign.state.copy()
        assert campaign.runtime_document is lowered
    with Campaign(tmp_path, runtime_document=document) as campaign:
        assert campaign.state == original
        campaign.reserve(allowance)
        with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
            campaign.reserve(allowance)
        assert campaign.state["calls"] == 2
        assert campaign.state["deadline"] == original["deadline"]
        assert campaign.state["limits"] == original["limits"]


def test_lower_request_applies_to_existing_spend_without_reset(tmp_path):
    from skala_rag.demo_budget import Campaign
    from skala_rag.settings import load_runtime_document
    from skala_rag.tools.runtime import Allowance

    approve(tmp_path)
    document = load_runtime_document()
    allowance = Allowance(
        schema_version="test",
        input_tokens=1,
        output_tokens=1,
        max_cost_usd=Decimal("1"),
    )
    with Campaign(tmp_path, runtime_document=document) as campaign:
        campaign.reserve(allowance)
        original = campaign.state.copy()
    profile = document.profiles.local_demo.model_copy(update={"cost_usd": Decimal("1")})
    lowered = document.model_copy(
        update={
            "profiles": document.profiles.model_copy(update={"local_demo": profile})
        }
    )
    with Campaign(tmp_path, runtime_document=lowered) as campaign:
        with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
            campaign.reserve(allowance)
        assert campaign.state == original


@pytest.mark.parametrize(
    "field,value", [("llm_calls", 31), ("cost_usd", Decimal("4")), ("seconds", 1201)]
)
def test_injected_requested_limits_cannot_raise_approval(tmp_path, field, value):
    from pydantic import ValidationError

    from skala_rag.demo_budget import Campaign
    from skala_rag.settings import load_runtime_document

    approve(tmp_path)
    document = load_runtime_document()
    profile = document.profiles.local_demo.model_copy(update={field: value})
    forged = document.model_copy(
        update={
            "profiles": document.profiles.model_copy(update={"local_demo": profile})
        }
    )
    with pytest.raises(ValidationError):
        with Campaign(tmp_path, runtime_document=forged):
            pass
    assert not (tmp_path / "outputs").exists()
