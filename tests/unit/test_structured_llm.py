"""HTTP MockTransport만 사용하는 structured-output provider 경계 검증."""

import json
from datetime import datetime, timezone

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from skala_rag.agents.evaluation import DimensionAssessmentOutput
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import LLMError, StructuredLLM
from skala_rag.fakes import FakeClock
from skala_rag.tools.structured_llm import (
    APPROVED_MODEL,
    OpenAIStructuredLLM,
    strict_schema,
)


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rating: int | None
    reason: str


def response(text='{"rating":null,"reason":"가상 결측"}', **updates):
    payload = dict(
        model=APPROVED_MODEL,
        status="completed",
        output=[
            {"type": "message", "content": [{"type": "output_text", "text": text}]}
        ],
        usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    payload.update(updates)
    return payload


def adapter(body=None, *, status=200, exception=None):
    received = []

    def handler(request):
        received.append(json.loads(request.content))
        if exception:
            raise exception
        return httpx.Response(status, json=body if body is not None else response())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    llm = OpenAIStructuredLLM(
        transport=lambda payload: client.post(
            "https://api.openai.com/v1/responses", json=payload
        ),
        model=APPROVED_MODEL,
        prompt_version="fixture-prompt-v1",
        schema_version="fixture-schema-v1",
        max_output_tokens=100,
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=timezone.utc)),
    )
    return llm, received, client


def test_valid_missing_and_redacted_metadata():
    llm, received, client = adapter()
    with client:
        assert isinstance(llm, StructuredLLM)
        output = llm.generate(
            system="가상 system", user="가상 user", output_schema=Output
        )
    assert output.rating is None
    payload = received[0]
    assert payload["model"] == APPROVED_MODEL and payload["store"] is False
    assert payload["text"]["format"]["strict"] is True
    assert payload["text"]["format"]["schema"]["additionalProperties"] is False
    assert llm.calls[0].input_tokens == 10 and llm.calls[0].output_tokens == 5
    assert "가상 user" not in repr(llm.calls)
    assert len(received) == 1


@pytest.mark.parametrize(
    "text",
    [
        '{"rating":"3","reason":"가상"}',
        '{"rating":3}',
        '{"rating":3,"reason":"가상","decision_id":"bad"}',
        "not-json",
    ],
)
def test_invalid_content_not_missing(text):
    llm, received, client = adapter(response(text))
    with client, pytest.raises(LLMError) as error:
        llm.generate(system="s", user="u", output_schema=Output)
    assert error.value.error_code == ErrorCode.LLM_OUTPUT_INVALID
    assert llm.calls[0].status == "failure"
    assert len(received) == 1


@pytest.mark.parametrize(
    "status,code",
    [
        (401, ErrorCode.TOOL_AUTH_FAILED),
        (403, ErrorCode.TOOL_AUTH_FAILED),
        (429, ErrorCode.TOOL_RATE_LIMITED),
        (503, ErrorCode.TOOL_UNAVAILABLE),
        (400, ErrorCode.LLM_FAILED),
    ],
)
def test_http_errors_no_adapter_retry(status, code):
    llm, received, client = adapter(status=status)
    with client, pytest.raises(LLMError) as error:
        llm.generate(system="s", user="u", output_schema=Output)
    assert error.value.error_code == code
    assert len(received) == 1
    assert llm.calls[0].input_tokens is None


@pytest.mark.parametrize(
    "body",
    [
        response(status="incomplete"),
        response(
            output=[
                {
                    "type": "message",
                    "content": [{"type": "refusal", "refusal": "private text"}],
                }
            ]
        ),
    ],
)
def test_refusal_and_incomplete_are_failures(body):
    llm, _, client = adapter(body)
    with client, pytest.raises(LLMError) as error:
        llm.generate(system="s", user="u", output_schema=Output)
    assert error.value.error_code == ErrorCode.LLM_FAILED
    assert "private text" not in str(error.value)
    assert llm.calls[0].input_tokens == 10


def test_timeout_distinct_and_no_retry():
    llm, received, client = adapter(exception=httpx.ReadTimeout("secret error"))
    with client, pytest.raises(LLMError) as error:
        llm.generate(system="s", user="u", output_schema=Output)
    assert error.value.error_code == ErrorCode.LLM_TIMEOUT
    assert len(received) == 1
    assert "secret error" not in str(error.value)


def test_assessment_schema_has_no_envelope_and_required_nullable_fields():
    schema = strict_schema(DimensionAssessmentOutput)
    assert set(schema["properties"]) == {"criteria", "research_gaps", "caveats"}
    criterion = schema["$defs"]["CriterionOutput"]
    assert set(criterion["required"]) == set(criterion["properties"])
    assert criterion["additionalProperties"] is False
    assert "default" not in criterion["properties"]["rating"]
    assert {"type": "null"} in criterion["properties"]["rating"]["anyOf"]


def test_dynamic_map_rejected_before_request():
    class MapOutput(BaseModel):
        values: dict[str, str]

    llm, received, client = adapter()
    with client, pytest.raises(LLMError):
        llm.generate(system="s", user="u", output_schema=MapOutput)
    assert received == []


def test_wrong_model_and_missing_usage_not_fabricated():
    llm, _, client = adapter(response(model="unapproved-model"))
    with client, pytest.raises(LLMError):
        llm.generate(system="s", user="u", output_schema=Output)
    llm, _, client = adapter(response(usage=None))
    with client:
        llm.generate(system="s", user="u", output_schema=Output)
    assert llm.calls[0].input_tokens is None


def test_optional_default_omission_rejected_by_strict_shape():
    class DefaultOutput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        optional: str | None = None

    llm, received, client = adapter(response("{}"))
    with client, pytest.raises(LLMError) as error:
        llm.generate(system="s", user="u", output_schema=DefaultOutput)
    assert error.value.error_code == ErrorCode.LLM_OUTPUT_INVALID
    assert len(received) == 1


def test_unexpected_transport_error_not_logged_as_success():
    llm, received, client = adapter(exception=RuntimeError("private failure"))
    with client, pytest.raises(LLMError) as error:
        llm.generate(system="s", user="u", output_schema=Output)
    assert error.value.error_code == ErrorCode.LLM_FAILED
    assert llm.calls[0].status == "failure"
    assert "private failure" not in str(error.value)


@pytest.mark.parametrize(
    "model,max_output_tokens",
    [("unapproved-model", 100), (APPROVED_MODEL, 2001)],
)
def test_direct_constructor_retains_model_and_output_authority(
    model, max_output_tokens
):
    received = []
    with pytest.raises(ValueError):
        OpenAIStructuredLLM(
            transport=lambda payload: received.append(payload) or httpx.Response(200),
            model=model,
            max_output_tokens=max_output_tokens,
            prompt_version="synthetic",
            schema_version="test",
            clock=FakeClock(datetime(2026, 9, 30, tzinfo=timezone.utc)),
        )
    assert received == []
