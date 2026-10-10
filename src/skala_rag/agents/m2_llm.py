"""#62 shared LLM runtime. Construction/preflight never makes an API request."""

from datetime import datetime

import httpx

from skala_rag.contracts import ToolBudget
from skala_rag.contracts.interfaces import Clock
from skala_rag.prompt.eligibility_facts import PROMPT_VERSION as ELIGIBILITY_PROMPT
from skala_rag.prompt.evidence_extraction import PROMPT_VERSION as EVIDENCE_PROMPT
from skala_rag.prompt.technology_evaluation import PROMPT_VERSION as TECHNOLOGY_PROMPT
from skala_rag.settings import RuntimeDocument, load_runtime_document
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
        runtime_document: RuntimeDocument | None = None,
    ):
        if not api_key.strip():
            raise ValueError("api_key is required")
        self.runtime_document = (
            runtime_document
            if runtime_document is not None
            else load_runtime_document()
        )
        if runtime_document is not None:
            RuntimeDocument.model_validate_json(
                runtime_document.model_dump_json(), strict=True
            )
        profile = self.runtime_document.profiles.m2_shared
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
                    max_calls=profile.max_calls,
                    tool_max_calls={"m2-openai": profile.max_calls},
                    max_input_tokens=profile.max_input_tokens,
                    max_output_tokens=profile.max_output_tokens,
                    max_cost_usd=profile.max_cost_usd,
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
                    max_calls=profile.max_calls,
                    max_retries=profile.max_retries,
                    timeout_seconds=profile.timeout_seconds,
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
                    llm_settings=self.runtime_document.llm,
                ),
                allowance_for=lambda system, user, schema: self._allowance(
                    system,
                    user,
                    schema,
                    schema_version,
                    runtime_document=self.runtime_document,
                ),
            )

    @staticmethod
    def _allowance(
        system,
        user,
        schema,
        schema_version,
        *,
        runtime_document: RuntimeDocument | None = None,
    ):
        document = (
            runtime_document
            if runtime_document is not None
            else load_runtime_document()
        )
        profile = document.profiles.m2_shared
        allowance = byte_bound_allowance(
            system,
            user,
            schema,
            schema_version=schema_version,
            max_output_tokens=profile.request_output_tokens,
            usd_per_input_token=document.llm.usd_per_input_token,
            usd_per_output_token=document.llm.usd_per_output_token,
        )
        if allowance.input_tokens > profile.request_input_tokens:
            raise ValueError("M2 request exceeds per-request input limit")
        return allowance

    def observations(self):
        return {
            "ledger": self.runtime.ledger.snapshot(),
            "actual_cost_usd": None,
            "account_credit_verified": False,
            "pricing_reference": self.runtime_document.llm.pricing_reference,
            "pricing_checked_on": self.runtime_document.llm.pricing_checked_on,
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
