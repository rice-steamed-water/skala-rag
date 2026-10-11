"""Synthetic observations with real runtime objects, never scoring authority."""

import builtins
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from skala_rag.contracts.company_report import QUESTION_IDS, NarrativeQuestion
from skala_rag.contracts.tools import ToolBudget
from skala_rag.fakes import FakeClock
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    BudgetLedger,
    CallContext,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
)


@dataclass(frozen=True, slots=True)
class NarrativeRuntimeBinding:
    runtime: AdapterRuntime
    call: CallContext
    budget: ToolBudget
    readiness: Readiness
    allowance: Allowance
    tool_name: str


def gates_payload():
    # Synthetic admission observations, not current run authorization.
    return {
        "run_id": "synthetic-run",
        "policy_version": "v3-operational-1.0.0",
        "provider": "synthetic-provider",
        "open_decisions": [],
        "readiness": {
            "schema_version": "test",
            "required": True,
            "configured": True,
            "credential_required": True,
            "credential_present": True,
            "model_required": True,
            "model_available": True,
            "index_required": True,
            "index_available": True,
        },
        "limits": {
            "schema_version": "test",
            "max_calls": 2,
            "tool_max_calls": {"synthetic-provider": 2},
            "max_input_tokens": 100,
            "max_output_tokens": 100,
            "max_cost_usd": "0.20",
        },
        "allowance": {
            "schema_version": "test",
            "input_tokens": 10,
            "output_tokens": 10,
            "max_cost_usd": "0.10",
        },
        "runtime_readiness_reference": "synthetic:readiness",
        "call_budget_reference": "synthetic:calls",
        "cost_budget_reference": "synthetic:cost",
    }


def narrative_questions() -> list[NarrativeQuestion]:
    return [
        NarrativeQuestion.model_validate(
            {
                "schema_version": "narrative-report-1",
                "question_id": qid,
                "domain": qid.rsplit(".", 1)[0],
                "question": f"Controlled question {number}",
            }
        )
        for number, qid in enumerate(QUESTION_IDS)
    ]


class SyntheticLocalModel:
    """Synthetic vectors at the real LocalEncoder and SQLite seams."""

    max_seq_length = 4096

    def tokenizer(self, text, *, truncation):
        assert truncation is False
        return {"input_ids": list(range(len(text.split())))}

    def encode(self, texts, **kwargs):
        return [(1.0,) + (0.0,) * 1023 for _ in texts]


def runtime_binding(*, run_id="synthetic-run", schema_version="test", payload=None):
    payload = gates_payload() if payload is None else payload
    for name in ("readiness", "limits", "allowance"):
        payload[name]["schema_version"] = schema_version
    clock = FakeClock(datetime(2026, 9, 30, tzinfo=UTC))

    def forbidden_sleep(seconds):
        pytest.fail("read-only preflight slept")

    runtime = AdapterRuntime(
        policy=RuntimePolicy(
            schema_version=schema_version,
            execution_mode="live",
            retry_delays_seconds=(),
            live_approval_reference="synthetic:scope",
            timing_approval_reference="synthetic:timing",
        ),
        ledger=BudgetLedger(RuntimeLimits.model_validate(payload["limits"])),
        clock=clock,
        sleep=forbidden_sleep,
    )
    return NarrativeRuntimeBinding(
        runtime=runtime,
        call=CallContext(
            schema_version=schema_version,
            call_id="synthetic-call",
            run_id=run_id,
            candidate_id=None,
            tool_name=payload["provider"],
            node="synthetic-preflight",
        ),
        budget=ToolBudget(
            schema_version=schema_version,
            max_calls=2,
            max_retries=0,
            timeout_seconds=2,
            deadline=clock.now() + timedelta(seconds=30),
        ),
        readiness=Readiness.model_validate(payload["readiness"]),
        allowance=Allowance.model_validate(payload["allowance"]),
        tool_name=payload["provider"],
    )


def deny_scored_import(monkeypatch):
    """Subscribe before the action to reject any retired scored runtime import."""
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if (
            name.startswith("skala_rag.scoring.")
            and name != "skala_rag.scoring.finance"
        ):
            pytest.fail(f"Narrative execution imported scored runtime: {name}")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
