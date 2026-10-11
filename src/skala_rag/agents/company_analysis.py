"""근거 기반 분석 1회분과, 문맥을 주입받는 단일 시도(single-attempt) 역할 모음.

이 모듈은 Company 보고서 파이프라인의 첫 모델 호출인 "분석(analysis)"을 맡는다.
입력은 이미 검증된 ``CompanyWriterContext``(수집된 근거와 승인된 18개 질문)이고,
출력은 질문별 답변(``AnalysisReply``)이다.

역할 분담을 먼저 알아 두면 코드가 쉽다.

- 이 모듈: 요청(프롬프트 + JSON 스키마)을 만들고 응답을 파싱/검증하는
  "시도 1회"만 담당한다.
- 라이프사이클(``NarrativeReportLifecycle``): 실행 예산(ledger), 마감 시각(deadline),
  보고서 수정 횟수를 관리한다. 여기서는 그 어떤 것도 새로 만들지 않는다.
- 작성(Composition)/심사(Judge) 역할은 ``narrative.py``에 있고,
  ``ContextualRoleAttempt``를 재사용해 같은 파서 규칙을 공유한다.
"""

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

# 프롬프트/질문 버전은 모델에 보내는 payload에 그대로 들어간다.
# 프롬프트나 질문 문구를 바꾸면 이 값도 함께 올려야 결과 추적이 가능하다.
PROMPT_VERSION: Final = "company-analysis-1"
QUESTION_VERSION: Final = "company-analysis-questions-1"
SYSTEM_PROMPT: Final = read_prompt("company_analysis.v1.json")
ModelT = TypeVar("ModelT", bound=BaseModel)


def analysis_questions() -> list[NarrativeQuestion]:
    """승인된 18개 질문을 호출마다 새 DTO로 읽어 온다.

    매번 새로 파싱하므로 호출자가 반환 리스트를 수정해도 공유 원본(승인된 질문)이
    오염되지 않는다. 질문 ID와 순서가 ``QUESTION_IDS``와 정확히 같아야 하며,
    다르면 설정 오류로 즉시 중단한다.
    """
    questions = TypeAdapter(list[NarrativeQuestion]).validate_json(read_questions())
    if tuple(question.question_id for question in questions) != QUESTION_IDS:
        raise CompanyReportConfigError("EXACT_ORDERED_QUESTIONS_REQUIRED")
    return questions


def _fixed_answers(schema: JSONMap) -> None:
    """``answers``를 18개 고정 키 객체로 바꾸는 JSON 스키마 후처리 훅.

    OpenAI strict 모드는 "임의의 키를 가진 map"을 허용하지 않고 모든 키를 명시해야
    한다. 그래서 Pydantic이 만든 ``additionalProperties`` 항목 스키마를 꺼내,
    질문 ID마다 같은 스키마를 붙이고 18개 모두를 required로 지정한다.
    """
    item = schema.pop("additionalProperties")
    schema.pop("propertyNames", None)
    schema["properties"] = {qid: item for qid in QUESTION_IDS}
    schema["required"] = list(QUESTION_IDS)


class _ContextualReply(BaseModel):
    """컨트롤러 문맥(writer_context)을 파서에만 주입하는 믹스인.

    이 ClassVar는 응답 필드가 아니므로 모델의 응답 스키마에 들어가지 않는다.
    응답 JSON을 파싱할 때 Pydantic 검증기가 ``ValidationInfo.context``로 읽는다.
    모델에 보내는 작성용 문맥은 각 역할이 별도로 사용자 메시지에 넣는다.
    덕분에 "모델이 인용한 evidence ID가 실제 수집된 근거인가" 같은 교차 검증을
    기존 provider 파서를 고치지 않고 수행할 수 있다.
    """

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
        # 호출자가 어떤 context를 넘기든 클래스에 묶인 writer_context로 덮어쓴다.
        return super().model_validate_json(
            json_data,
            strict=strict,
            extra=extra,
            context={"writer_context": cls.writer_context},
            by_alias=by_alias,
            by_name=by_name,
        )


class ContextualRoleAttempt(Generic[ModelT]):
    """라이프사이클 런타임이 호출할 수 있는, 설정이 끝난 OpenAI 요청 1회분.

    사용 순서:

    1. 역할의 요청과 응답 DTO를 지정하고 라이프사이클의 요청 예산(``allowance``)을
       넘긴다. 분석·작성·심사가 같은 예산 범위를 사용한다.
    2. 실행은 반드시 ``lifecycle.execute_model(transport=attempt)``로만 한다.

    작성(Composition)/심사(Judge) 역할도 각자의 DTO로 이 클래스를 재사용한다.
    runtime, ledger, deadline, repair 카운터, 재시도 정책은 여기서 만들지 않는다.
    (``retry_owner = "runtime"``이 그 책임이 runtime에 있다는 표시다.)
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
        # 입력 DTO를 한 번 더 검증하고 깊은 복사한다. 호출자가 나중에 원본을 바꿔도
        # 이 시도가 쓰는 문맥은 변하지 않는다.
        writer = WriterContext.model_validate(writer_context).model_copy(deep=True)
        fields = {}
        # 분석 응답만 answers를 18개 고정 키 스키마로 바꾼다(_fixed_answers 참고).
        if output_schema is AnalysisReply:
            fields["answers"] = (
                AnalysisReply.model_fields["answers"].annotation,
                Field(json_schema_extra=_fixed_answers),
            )
        # 원래 응답 DTO에 _ContextualReply를 섞은 동적 서브클래스를 만들어,
        # 파싱 시 writer_context가 검증기에 전달되게 한다.
        schema = create_model(
            output_schema.__name__,
            __base__=(_ContextualReply, output_schema),
            **fields,
        )
        schema.writer_context = writer
        # 프롬프트·스키마의 바이트 수로 보수적으로 예약할 토큰·비용을 계산한다.
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
        # 라이프사이클이 캡처해 둔 allowance가 있으면 그것을 쓰되, 이 요청이 필요로 하는
        # 토큰/비용보다 작으면 호출 전에 막는다. 없으면 계산한 필요량을 그대로 쓴다.
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
        # runtime이 부르는 진입점. 모델 호출은 정확히 1번만 하고(generate_once),
        # 재시도는 runtime이 결정한다. timeout은 runtime이 남은 시간으로 계산해 넘긴다.
        response = self.provider.generate_once(
            system=self._system,
            user=self._user,
            output_schema=self._schema,
            timeout_seconds=timeout_seconds,
            input_token_limit=self.allowance.input_tokens,
            output_token_limit=self._max_output_tokens,
        )
        # 동적 서브클래스로 한 번 파싱한 결과를 JSON으로 되돌린 뒤, 원래 DTO로
        # 다시 검증해서 호출자에게는 원래 타입만 노출한다.
        parsed = self._output_schema.model_validate_json(
            response.data.model_dump_json(),
            context={"writer_context": self._writer},
        )
        return response.model_copy(update={"data": parsed})


class CompanyAnalysisAttempt(ContextualRoleAttempt[AnalysisReply]):
    """승인된 질문 18개만 분석하는 시도. 모델에는 writer 투영(projection)만 보인다.

    ``CompanyWriterContext``는 수집 내부 정보까지 담은 컨트롤러용 문맥이고,
    모델에 보내는 것은 ``context.writer_context``(출처 식별자가 중립화된 투영)뿐이다.
    """

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
        # 문맥이 변조되거나 다른 실행의 것이 아닌지 먼저 확인한다.
        context.verify_binding()
        writer = context.writer_context
        # 질문 목록이 승인본(18개, 정해진 순서)과 다르면 분석을 시작하지 않는다.
        if writer.questions != analysis_questions():
            raise CompanyReportConfigError("APPROVED_ANALYSIS_QUESTIONS_REQUIRED")
        super().__init__(
            writer_context=writer,
            output_schema=AnalysisReply,
            system=SYSTEM_PROMPT,
            # 사용자 메시지는 버전 + writer 투영만 담은 source-neutral payload다.
            # sort_keys/separators를 고정해 같은 입력이면 항상 같은 바이트가 나온다.
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
    """라이프사이클이 캡처한 ledger/allowance/절대 마감 안에서 분석을 1회 실행한다."""
    # 시도가 만들어진 뒤 라이프사이클 문맥이 바뀌었다면 낡은 시도이므로 거부한다.
    if lifecycle.context.writer_context != transport._writer:
        raise CompanyReportConfigError("STALE_ANALYSIS_CONTEXT")
    # 두 객체가 같은 allowance(예산)를 가리켜야 비용 집계가 어긋나지 않는다.
    if lifecycle._allowance != transport.allowance:
        raise CompanyReportConfigError("ANALYSIS_ALLOWANCE_BINDING_MISMATCH")
    return lifecycle.execute_model(transport=transport)
