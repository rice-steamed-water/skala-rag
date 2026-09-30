"""Tool boundary results (contracts §7); no adapter, retry, or budget policy."""

from typing import Annotated, Generic, Literal, Self, TypeVar

from pydantic import Field, model_validator

from ._validation import validate_map_ids
from .bundles import SourceBundle
from .candidates import CompanyProfile
from .common import Contract, Count, JSONMap, Number, Text, Timestamp
from .error_codes import ERROR_SPECS, ErrorCode
from .errors import WorkflowError
from .evidence import Evidence
from .reports import ValidationErrorDetail
from .retrieval import RetrievalRecord

DataT = TypeVar("DataT")


class ToolBudget(Contract):
    """호출자가 넘기는 한도. 값은 architecture §5 설정에서 오며 기본값이 없다."""

    max_calls: Count
    max_retries: Count
    timeout_seconds: Annotated[Number, Field(gt=0)]
    deadline: Timestamp | None = None


class ToolResult(Contract, Generic[DataT]):
    """도구 한 번의 terminal 결과.

    - ok: data 필수, errors 없음
    - empty: 조회는 성공했지만 0건. 빈 bundle을 data로 주고 errors 없음
    - unavailable/failed: data 없음, errors ≥1. error_code는 ErrorCode의 도구
      코드여야 하며 status·retryable이 ERROR_SPECS와 일치해야 한다.
    """

    status: Literal["ok", "empty", "unavailable", "failed"]
    data: DataT | None = None
    retrieval_records: list[RetrievalRecord]
    errors: list[WorkflowError]

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status in ("ok", "empty"):
            if self.data is None or self.errors:
                raise ValueError(f"{self.status} requires data and no errors")
            return self
        if self.data is not None or not self.errors:
            raise ValueError(f"{self.status} requires data=None and errors")
        for error in self.errors:
            try:
                spec = ERROR_SPECS[ErrorCode(error.error_code)]
            except ValueError:
                raise ValueError(f"unknown error_code {error.error_code}") from None
            if spec.tool_status != self.status:
                raise ValueError(f"{error.error_code} does not match {self.status}")
            if spec.retryable != error.retryable:
                raise ValueError(f"{error.error_code} retryable mismatch")
        return self


class EvidenceBundle(SourceBundle):
    evidence: dict[Text, Evidence]

    @model_validator(mode="after")
    def close_evidence_sources(self) -> Self:
        validate_map_ids(self.evidence, "evidence_id")
        if any(item.source_id not in self.sources for item in self.evidence.values()):
            raise ValueError("unresolved Evidence Source payload")
        return self


class CompanyResearchBundle(EvidenceBundle):
    profile: CompanyProfile


class DecisionPolicyResult(Contract):
    """결정적 판단 정책 결과. LLM 설명은 이 값을 바꾸지 않는다."""

    label: Literal["RECOMMEND", "WATCHLIST", "PASS"]
    report_grade: Text
    reason_codes: list[Text]


class RenderResult(Contract):
    """PDF 렌더링 결과. layout 오류는 보고서 수정 loop의 입력이다."""

    artifact_path: Text | None = None
    page_count: Count | None = None
    layout_measurements: JSONMap
    errors: list[ValidationErrorDetail]

    @model_validator(mode="after")
    def require_artifact_or_errors(self) -> Self:
        if self.artifact_path is None and not self.errors:
            raise ValueError("render without artifact requires errors")
        if (self.artifact_path is None) != (self.page_count is None):
            raise ValueError("artifact_path and page_count go together")
        return self
