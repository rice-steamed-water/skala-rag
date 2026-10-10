"""Crash-conservative, cross-process campaign budget for the approved demo.

Reservations persist BEFORE network I/O; no retry, restart or new run resets them.
Actual billed USD is unknown. Reserved public-rate upper bounds are not billing.
"""

import fcntl
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import NotRequired, TypedDict

from skala_rag.settings import LocalDemoSettings, RuntimeDocument, load_runtime_document


class CampaignLimits(TypedDict):
    llm_calls: int
    cost_usd: str
    seconds: int


class CampaignState(TypedDict):
    approval_reference: str
    deadline: str
    calls: int
    cost_usd_accounted: str
    actual_billing_usd: None
    limits: CampaignLimits
    last_run_id: NotRequired[str]
    last_run_status: NotRequired[str]
    reapproval_required: NotRequired[bool]
    approval_history: NotRequired[list[dict]]


class Campaign:
    def __init__(self, root: Path, *, runtime_document: RuntimeDocument | None = None):
        self.root = root
        self.lock = None
        self.runtime_document = runtime_document
        self.state: CampaignState

    def __enter__(self):
        approval = self.root / "data/local/demo180-approval.json"
        if not approval.is_file():
            raise ValueError("APPROVAL_REQUIRED")
        self.approval = json.loads(approval.read_text())
        if not str(self.approval.get("approval_reference", "")).strip():
            raise ValueError("APPROVAL_REQUIRED")
        if self.runtime_document is None:
            self.runtime_document = load_runtime_document()
        self.settings = self.runtime_document.profiles.local_demo
        # A constructed/copy-injected snapshot cannot grant additional authority.
        LocalDemoSettings.model_validate_json(
            self.settings.model_dump_json(), strict=True
        )
        directory = self.root / "outputs"
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "demo180-campaign.json"
        self.lock = (directory / "demo180-campaign.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise ValueError("DEMO_BUSY") from None
        try:
            if self.path.exists():
                self.state = json.loads(self.path.read_text())
                if self.state.get("reapproval_required"):
                    renewal = self.approval.get("reapproval", {})
                    if not (
                        isinstance(renewal, dict)
                        and self.approval["approval_reference"]
                        != self.state["approval_reference"]
                        and renewal.get("previous_approval_reference")
                        == self.state["approval_reference"]
                        and renewal.get("failed_run_id")
                        == self.state.get("last_run_id")
                        and isinstance(renewal.get("review_reference"), str)
                        and renewal["review_reference"].strip()
                    ):
                        raise ValueError("REAPPROVAL_REQUIRED")
                    self.state.setdefault("approval_history", []).append(
                        {
                            "approval_reference": self.state["approval_reference"],
                            "reapproval": renewal,
                        }
                    )
                    self.state.update(
                        approval_reference=self.approval["approval_reference"],
                        reapproval_required=False,
                        deadline=(
                            datetime.now(UTC)
                            + timedelta(seconds=self.state["limits"]["seconds"])
                        ).isoformat(),
                    )
                    self.save()
                if (
                    self.state["approval_reference"]
                    != self.approval["approval_reference"]
                ):
                    raise ValueError("APPROVAL_MISMATCH")
            else:
                self.state = {
                    "approval_reference": self.approval["approval_reference"],
                    "deadline": (
                        datetime.now(UTC) + timedelta(seconds=self.settings.seconds)
                    ).isoformat(),
                    "calls": 0,
                    "cost_usd_accounted": "0",
                    "actual_billing_usd": None,
                    "limits": {
                        "llm_calls": self.settings.llm_calls,
                        "cost_usd": str(self.settings.cost_usd),
                        "seconds": self.settings.seconds,
                    },
                }
                self.save()
            self.check_time()
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def check_time(self):
        if datetime.now(UTC) >= datetime.fromisoformat(self.state["deadline"]):
            raise ValueError("CAMPAIGN_EXPIRED")

    def finish_run(self, run_id, status):
        self.state.update(
            last_run_id=run_id,
            last_run_status=status,
            reapproval_required=status != "completed",
        )
        self.save()

    def reserve(self, allowance):
        self.check_time()
        cost = allowance.max_cost_usd
        if cost is None or self.state["calls"] >= min(
            self.settings.llm_calls, self.state["limits"]["llm_calls"], 30
        ):
            raise ValueError("BUDGET_EXHAUSTED")
        total = Decimal(self.state["cost_usd_accounted"]) + cost
        if total > min(
            self.settings.cost_usd,
            Decimal(self.state["limits"]["cost_usd"]),
            Decimal("3"),
        ):
            raise ValueError("BUDGET_EXHAUSTED")
        self.state["calls"] += 1
        self.state["cost_usd_accounted"] = str(total)
        self.save()

    def save(self):
        pending = self.path.with_suffix(".pending")
        with pending.open("w", encoding="utf-8") as stream:
            json.dump(self.state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(self.path)

    def __exit__(self, *_):
        if self.lock is not None:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            self.lock.close()
