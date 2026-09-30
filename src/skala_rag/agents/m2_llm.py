"""#62 shared LLM runtime. Construction/preflight never makes an API request."""

from datetime import datetime
from decimal import Decimal

import httpx

from skala_rag.contracts import ToolBudget
from skala_rag.contracts.interfaces import Clock
from skala_rag.prompts.eligibility_facts import PROMPT_VERSION as ELIGIBILITY_PROMPT
from skala_rag.prompts.evidence_extraction import PROMPT_VERSION as EVIDENCE_PROMPT
from skala_rag.prompts.technology_evaluation import PROMPT_VERSION as TECHNOLOGY_PROMPT
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt, byte_bound_allowance
from skala_rag.tools.runtime import (
    AdapterRuntime,
    BudgetLedger,
    CallContext,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
)
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM

# Dated public list prices, not observed account billing/credit or actual costs.
# Official model page fetched 2026-09-30. Keep the previously approved model.
PRICING_REFERENCE = "https://developers.openai.com/api/docs/models/gpt-4.1-mini"
PRICING_CHECKED_ON = "2026-09-30"
INPUT_PRICE = Decimal("0.40") / 1_000_000
OUTPUT_PRICE = Decimal("1.60") / 1_000_000


class M2LLMs:
    """Eligibility, Evidence and Technology share eight requests and one deadline."""

    def __init__(
        self,
        *,
        api_key: str,
        run_id: str,
        candidate_id: str,
        schema_version: str,
        execution_mode: str,
        clock: Clock,
        deadline: datetime,
        http_transport: httpx.BaseTransport | None = None,
    ):
        self.runtime = AdapterRuntime(
            policy=RuntimePolicy(
                schema_version=schema_version,
                execution_mode=execution_mode,
                retry_delays_seconds=(),
                live_approval_reference="#43 B / #62 deferred-key continuation",
                timing_approval_reference="#62 user approval: timeout 30s/retries 0",
            ),
            ledger=BudgetLedger(
                RuntimeLimits(
                    schema_version=schema_version,
                    max_calls=8,
                    tool_max_calls={"m2-openai": 8},
                    max_input_tokens=64000,
                    max_output_tokens=16000,
                    max_cost_usd="1.00",
                )
            ),
            clock=clock,
            sleep=lambda _: None,
        )
        self.stages = {}
        for stage, prompt in (
            ("eligibility", ELIGIBILITY_PROMPT),
            ("evidence", EVIDENCE_PROMPT),
            ("technology", TECHNOLOGY_PROMPT),
        ):
            self.stages[stage] = RuntimeStructuredLLM(
                runtime=self.runtime,
                call=CallContext(
                    schema_version=schema_version,
                    call_id=f"{run_id}-{stage}",
                    run_id=run_id,
                    candidate_id=candidate_id,
                    tool_name="m2-openai",
                    node=stage,
                ),
                budget=ToolBudget(
                    schema_version=schema_version,
                    max_calls=8,
                    max_retries=0,
                    timeout_seconds=30,
                    deadline=deadline,
                ),
                readiness=Readiness(
                    schema_version=schema_version,
                    required=True,
                    configured=True,
                    credential_required=True,
                    credential_present=bool(api_key.strip()),
                    model_required=False,
                    model_available=False,
                    index_required=False,
                    index_available=False,
                ),
                transport=OpenAIResponsesAttempt(
                    api_key=api_key,
                    prompt_version=prompt,
                    schema_version=schema_version,
                    clock=clock,
                    http_transport=http_transport,
                ),
                allowance_for=lambda system, user, schema: self._allowance(
                    system, user, schema, schema_version
                ),
            )

    @staticmethod
    def _allowance(system, user, schema, schema_version):
        allowance = byte_bound_allowance(
            system,
            user,
            schema,
            schema_version=schema_version,
            max_output_tokens=2000,
            usd_per_input_token=INPUT_PRICE,
            usd_per_output_token=OUTPUT_PRICE,
        )
        if allowance.input_tokens > 8000:
            raise ValueError("M2 request exceeds per-request input limit")
        return allowance

    def observations(self):
        return {
            "ledger": self.runtime.ledger.snapshot(),
            "actual_cost_usd": None,
            "account_credit_verified": False,
            "pricing_reference": PRICING_REFERENCE,
            "pricing_checked_on": PRICING_CHECKED_ON,
            "runtime_error_codes": [
                e.error_code for e in self.runtime.error_history.values()
            ],
            "llm_calls": {
                stage: [
                    dict(
                        model=c.model,
                        prompt_version=c.prompt_version,
                        schema_hash=c.schema_hash,
                        status=c.status,
                        input_tokens=c.input_tokens,
                        output_tokens=c.output_tokens,
                        error_code=c.error_code,
                    )
                    for c in llm.transport.llm_calls
                ]
                for stage, llm in self.stages.items()
            },
        }
