"""Synthetic ToolResult/error-code contracts; no live adapter or retry policy."""

import pytest
from pydantic import ValidationError

import skala_rag.contracts as contracts
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode, is_retryable
from skala_rag.contracts.interfaces import LLMError

RetrievalResult = contracts.ToolResult[contracts.RetrievalBundle]
EMPTY_BUNDLE = {"schema_version": "synthetic-1", "sources": {}, "chunks": []}


def error(payloads, code, retryable):
    return {**payloads["WorkflowError"], "error_code": code, "retryable": retryable}


def result(status, data=None, errors=()):
    return {
        "schema_version": "synthetic-1",
        "status": status,
        "data": data,
        "retrieval_records": [],
        "errors": list(errors),
    }


def test_empty_auth_and_timeout_are_distinct_results(payloads):
    empty = RetrievalResult.model_validate(result("empty", EMPTY_BUNDLE))
    auth = RetrievalResult.model_validate(
        result("unavailable", errors=[error(payloads, "TOOL_AUTH_FAILED", False)])
    )
    timeout = RetrievalResult.model_validate(
        result("failed", errors=[error(payloads, "TOOL_TIMEOUT", True)])
    )

    assert empty.data == contracts.RetrievalBundle.model_validate(EMPTY_BUNDLE)
    assert empty.errors == []
    shapes = {
        (r.status, tuple((e.error_code, e.retryable) for e in r.errors))
        for r in (empty, auth, timeout)
    }
    assert shapes == {
        ("empty", ()),
        ("unavailable", (("TOOL_AUTH_FAILED", False),)),
        ("failed", (("TOOL_TIMEOUT", True),)),
    }


def test_parametrized_result_validates_data(payloads):
    ok = RetrievalResult.model_validate(result("ok", payloads["RetrievalBundle"]))
    assert isinstance(ok.data, contracts.RetrievalBundle)
    assert RetrievalResult.model_validate_json(ok.model_dump_json()) == ok
    with pytest.raises(ValidationError):
        RetrievalResult.model_validate(result("ok", {"schema_version": "synthetic-1"}))


@pytest.mark.parametrize(
    "status,with_data,code,retryable",
    [
        ("ok", False, None, None),
        ("empty", False, None, None),
        ("ok", True, "TOOL_FAILED", False),
        ("failed", True, "TOOL_FAILED", False),
        ("failed", False, None, None),
        ("failed", False, "SYNTHETIC_TIMEOUT", True),
        ("failed", False, "TOOL_AUTH_FAILED", False),
        ("unavailable", False, "TOOL_TIMEOUT", True),
        ("unavailable", False, "TOOL_AUTH_FAILED", True),
        ("failed", False, "SNAPSHOT_INVALID", False),
    ],
    ids=[
        "ok-no-data",
        "empty-no-data",
        "ok-with-error",
        "failed-with-data",
        "failed-no-error",
        "unknown-code",
        "auth-as-failed",
        "timeout-as-unavailable",
        "auth-retryable",
        "controller-code",
    ],
)
def test_tool_result_rejects_inconsistent_envelope(
    status, with_data, code, retryable, payloads
):
    errors = [] if code is None else [error(payloads, code, retryable)]
    data = EMPTY_BUNDLE if with_data else None
    with pytest.raises(ValidationError):
        RetrievalResult.model_validate(result(status, data, errors))


@pytest.mark.parametrize("name", ["EvidenceBundle", "CompanyResearchBundle"])
@pytest.mark.parametrize("fault", ["unresolved_source", "key_mismatch"])
def test_evidence_bundles_close_sources(name, fault, payloads):
    data = payloads[name]
    if fault == "unresolved_source":
        data["sources"] = {}
    else:
        data["evidence"] = {"ev-other": payloads["Evidence"]}
    with pytest.raises(ValidationError):
        getattr(contracts, name).model_validate(data)


@pytest.mark.parametrize(
    "changes",
    [
        {"artifact_path": None, "page_count": None},
        {"page_count": None},
        {"artifact_path": None},
    ],
    ids=["nothing", "artifact-without-pages", "pages-without-artifact"],
)
def test_render_result_requires_artifact_or_errors(changes, payloads):
    with pytest.raises(ValidationError):
        contracts.RenderResult.model_validate({**payloads["RenderResult"], **changes})


def test_failed_render_keeps_layout_errors(payloads):
    failed = contracts.RenderResult.model_validate(
        {
            **payloads["RenderResult"],
            "artifact_path": None,
            "page_count": None,
            "errors": [payloads["ValidationErrorDetail"]],
        }
    )
    assert failed.errors[0].code == "SYNTHETIC_ERROR"


def test_every_error_code_has_spec_and_documented_codes_exist():
    assert set(ERROR_SPECS) == set(ErrorCode)
    for code in ("SNAPSHOT_INVALID", "CONTEXT_INVALID", "UPSTREAM_INVALID"):
        assert ERROR_SPECS[ErrorCode(code)] == (False, None)
    assert is_retryable("TOOL_TIMEOUT") is True
    assert is_retryable("TOOL_AUTH_FAILED") is False
    with pytest.raises(ValueError):
        is_retryable("SYNTHETIC_UNKNOWN")


def test_llm_error_derives_retryable_from_code():
    assert LLMError(ErrorCode.LLM_OUTPUT_INVALID, "synthetic").retryable is True
    assert LLMError("LLM_FAILED", "synthetic").retryable is False
    with pytest.raises(ValueError):
        LLMError("SYNTHETIC_UNKNOWN", "synthetic")
