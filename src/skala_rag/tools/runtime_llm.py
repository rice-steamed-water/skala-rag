"""StructuredLLM bridge: transport retry here, structural repairs in #22/#47."""

from collections.abc import Callable
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import LLMError
from skala_rag.contracts.tools import ToolBudget
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    AttemptResponse,
    CallContext,
    Readiness,
    TransportFailure,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class StructuredAttempt(Protocol):
    retry_owner: str

    def generate_once(
        self,
        *,
        system: str,
        user: str,
        output_schema: type[ModelT],
        timeout_seconds: float,
        input_token_limit: int,
        output_token_limit: int,
    ) -> AttemptResponse[ModelT]: ...


class RuntimeStructuredLLM:
    """Satisfies the existing StructuredLLM Protocol with a shared request ledger.

    allowance_for must count/bound the request's tokens and estimate its maximum
    cost using verified pricing. No tokenizer, price, model, or budget defaults.
    Prompts and exception bodies are never copied into runtime observations.
    """

    def __init__(
        self,
        *,
        runtime: AdapterRuntime,
        call: CallContext,
        budget: ToolBudget,
        readiness: Readiness,
        transport: StructuredAttempt,
        allowance_for: Callable[[str, str, type[BaseModel]], Allowance],
    ) -> None:
        self.runtime = runtime
        self.call = call.model_copy(deep=True)
        self.budget = budget.model_copy(deep=True)
        self.readiness = readiness.model_copy(deep=True)
        self.transport = transport
        self.allowance_for = allowance_for
        self.retrieval_records = []

    def generate(
        self, *, system: str, user: str, output_schema: type[ModelT]
    ) -> ModelT:
        try:
            allowance = self.allowance_for(system, user, output_schema)
            allowance = Allowance.model_validate(allowance)
        except Exception:
            raise LLMError(ErrorCode.LLM_FAILED, "LLM allowance unavailable") from None
        outer = self

        class Attempt:
            retry_owner = getattr(outer.transport, "retry_owner", None)

            def __call__(self, *, timeout_seconds: float) -> AttemptResponse[ModelT]:
                response = outer.transport.generate_once(
                    system=system,
                    user=user,
                    output_schema=output_schema,
                    timeout_seconds=timeout_seconds,
                    input_token_limit=allowance.input_tokens,
                    output_token_limit=allowance.output_tokens,
                )
                if not isinstance(response, AttemptResponse) or response.status != "ok":
                    raise TransportFailure(ErrorCode.TOOL_RESPONSE_INVALID)
                try:
                    payload = (
                        response.data.model_dump()
                        if isinstance(response.data, BaseModel)
                        else response.data
                    )
                    parsed = output_schema.model_validate(payload)
                except ValidationError:
                    raise TransportFailure(
                        ErrorCode.TOOL_RESPONSE_INVALID, usage=response.usage
                    ) from None
                return response.model_copy(update={"data": parsed})

        result = self.runtime.execute(
            self.call,
            budget=self.budget,
            readiness=self.readiness,
            allowance=allowance,
            transport=Attempt(),
        )
        self.retrieval_records.extend(result.retrieval_records)
        if result.status == "ok":
            return result.data
        terminal_code = result.errors[-1].error_code
        code = {
            ErrorCode.TOOL_RESPONSE_INVALID: ErrorCode.LLM_OUTPUT_INVALID,
            ErrorCode.TOOL_TIMEOUT: ErrorCode.LLM_TIMEOUT,
        }.get(terminal_code, ErrorCode.LLM_FAILED)
        # #22 repairs only LLM_OUTPUT_INVALID, never an exhausted transport error.
        # Historical TOOL_TIMEOUT/429/etc remain in runtime.error_history.
        raise LLMError(code, f"LLM runtime rejected: {result.errors[-1].error_code}")
