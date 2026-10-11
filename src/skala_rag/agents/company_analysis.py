"""One grounded analysis batch and contextual single-attempt configured roles."""

import json
from typing import ClassVar, Final, Generic, Literal, Self, TypeVar

import httpx
from pydantic import BaseModel, Field, TypeAdapter, create_model

from skala_rag.contracts.common import JSONMap
from skala_rag.contracts.company_report import (
    QUESTION_IDS,
    AnalysisReply,
    CompanyReportConfigError,
    NarrativeQuestion,
    WriterContext,
)
from skala_rag.contracts.interfaces import Clock
from skala_rag.contracts.tools import ToolResult
from skala_rag.graph.company_report import NarrativeReportLifecycle
from skala_rag.prompt._resources import read_prompt, read_questions
from skala_rag.reporting.company_context import CompanyWriterContext
from skala_rag.settings import LLMSettings
from skala_rag.tools.openai_attempt import (
    OpenAIResponsesAttempt,
    byte_bound_allowance,
)
from skala_rag.tools.runtime import Allowance, AttemptResponse

PROMPT_VERSION: Final = "company-analysis-1"
QUESTION_VERSION: Final = "company-analysis-questions-1"
SYSTEM_PROMPT: Final = read_prompt("company_analysis.v1.json")
ModelT = TypeVar("ModelT", bound=BaseModel)


def analysis_questions() -> list[NarrativeQuestion]:
    """Load fresh DTOs so callers cannot mutate the shared approved questions."""
    questions = TypeAdapter(list[NarrativeQuestion]).validate_json(read_questions())
    if tuple(question.question_id for question in questions) != QUESTION_IDS:
        raise CompanyReportConfigError("EXACT_ORDERED_QUESTIONS_REQUIRED")
    return questions


def _fixed_answers(schema: JSONMap) -> None:
    """OpenAI strict objects require explicit keys, not arbitrary answer maps."""
    item = schema.pop("additionalProperties")
    schema.pop("propertyNames", None)
    schema["properties"] = {qid: item for qid in QUESTION_IDS}
    schema["required"] = list(QUESTION_IDS)


class _ContextualReply(BaseModel):
    """Inject controller context into the existing provider parser, not the wire."""

    writer_context: ClassVar[WriterContext]

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        context: dict[str, BaseModel] | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        return super().model_validate_json(
            json_data,
            strict=strict,
            extra=extra,
            context={"writer_context": cls.writer_context},
            by_alias=by_alias,
            by_name=by_name,
        )


class ContextualRoleAttempt(Generic[ModelT]):
    """Configured OpenAI request usable by the captured lifecycle runtime.

    Construct before the lifecycle and pass ``attempt.allowance`` as its captured
    allowance, or supply the same captured allowance for later roles/generations.
    Dispatch only through ``lifecycle.execute_model(transport=attempt)``.
    Composition/Judge can use this same contextual parser with their own DTOs.
    No runtime, ledger, deadline, repair counter or retry policy is created here.
    """

    retry_owner: Literal["runtime"] = "runtime"

    def __init__(
        self,
        *,
        writer_context: WriterContext,
        output_schema: type[ModelT],
        system: str,
        user: str,
        prompt_version: str,
        llm_settings: LLMSettings,
        clock: Clock,
        max_output_tokens: int,
        allowance: Allowance | None = None,
        api_key: str | None = None,
        http_transport: httpx.BaseTransport | None = None,
    ) -> None:
        writer = WriterContext.model_validate(writer_context).model_copy(deep=True)
        fields = {}
        if output_schema is AnalysisReply:
            fields["answers"] = (
                AnalysisReply.model_fields["answers"].annotation,
                Field(json_schema_extra=_fixed_answers),
            )
        schema = create_model(
            output_schema.__name__,
            __base__=(_ContextualReply, output_schema),
            **fields,
        )
        schema.writer_context = writer
        required = byte_bound_allowance(
            system,
            user,
            schema,
            schema_version="narrative-report-1",
            max_output_tokens=max_output_tokens,
            usd_per_input_token=llm_settings.usd_per_input_token,
            usd_per_output_token=llm_settings.usd_per_output_token,
        )
        assert required.max_cost_usd is not None
        selected = (
            required if allowance is None else Allowance.model_validate(allowance)
        )
        if (
            selected.input_tokens < required.input_tokens
            or selected.output_tokens < required.output_tokens
            or selected.max_cost_usd is None
            or selected.max_cost_usd < required.max_cost_usd
        ):
            raise CompanyReportConfigError("ROLE_ALLOWANCE_INSUFFICIENT")
        self.allowance = selected.model_copy(deep=True)
        self._max_output_tokens = max_output_tokens
        self._schema = schema
        self._output_schema = output_schema
        self._writer = writer
        self._system, self._user = system, user
        self.provider = OpenAIResponsesAttempt(
            api_key=api_key,
            prompt_version=prompt_version,
            schema_version="narrative-report-1",
            clock=clock,
            llm_settings=llm_settings,
            http_transport=http_transport,
        )

    def __call__(self, *, timeout_seconds: float) -> AttemptResponse[ModelT]:
        response = self.provider.generate_once(
            system=self._system,
            user=self._user,
            output_schema=self._schema,
            timeout_seconds=timeout_seconds,
            input_token_limit=self.allowance.input_tokens,
            output_token_limit=self._max_output_tokens,
        )
        parsed = self._output_schema.model_validate_json(
            response.data.model_dump_json(),
            context={"writer_context": self._writer},
        )
        return response.model_copy(update={"data": parsed})


class CompanyAnalysisAttempt(ContextualRoleAttempt[AnalysisReply]):
    """Analyze exactly the approved question set, exposing only writer projection."""

    def __init__(
        self,
        context: CompanyWriterContext,
        *,
        llm_settings: LLMSettings,
        clock: Clock,
        max_output_tokens: int,
        allowance: Allowance | None = None,
        api_key: str | None = None,
        http_transport: httpx.BaseTransport | None = None,
    ) -> None:
        context.verify_binding()
        writer = context.writer_context
        if writer.questions != analysis_questions():
            raise CompanyReportConfigError("APPROVED_ANALYSIS_QUESTIONS_REQUIRED")
        super().__init__(
            writer_context=writer,
            output_schema=AnalysisReply,
            system=SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "prompt_version": PROMPT_VERSION,
                    "question_version": QUESTION_VERSION,
                    "writer_context": writer.model_dump(mode="json"),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            prompt_version=PROMPT_VERSION,
            llm_settings=llm_settings,
            clock=clock,
            max_output_tokens=max_output_tokens,
            allowance=allowance,
            api_key=api_key,
            http_transport=http_transport,
        )


def analyze_company(
    lifecycle: NarrativeReportLifecycle, *, transport: CompanyAnalysisAttempt
) -> ToolResult[AnalysisReply]:
    """Execute one analysis under the captured ledger/allowance/absolute deadline."""
    if lifecycle.context.writer_context != transport._writer:
        raise CompanyReportConfigError("STALE_ANALYSIS_CONTEXT")
    if lifecycle._allowance != transport.allowance:
        raise CompanyReportConfigError("ANALYSIS_ALLOWANCE_BINDING_MISMATCH")
    return lifecycle.execute_model(transport=transport)
