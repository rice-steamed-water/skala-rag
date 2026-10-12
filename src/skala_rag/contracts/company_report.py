"""Company-report requests and receipts; serializable values never grant authority."""

import hashlib
import json
import re
from collections.abc import Mapping
from decimal import Decimal
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Final, Literal, Self, assert_never, get_args

from pydantic import (
    AfterValidator,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)

from skala_rag.contracts.common import Count, ISODate, JSONMap, Number, Text
from skala_rag.contracts.retrieval import RetrievalRecord
from skala_rag.settings import (
    FrozenSettings,
    JSONValue,
    RuntimeDocument,
    load_runtime_document,
)


def _cost_number(value: Decimal | int | float | str) -> Decimal | int | float | str:
    """Retain exact decimal cost inputs without importing scoring contracts."""
    if type(value) not in (Decimal, int, float, str) or (
        isinstance(value, str)
        and not re.fullmatch(
            r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?",
            value,
        )
    ):
        raise CompanyReportConfigError("INVALID_COST_NUMBER")
    return value


Cost = Annotated[
    Decimal,
    BeforeValidator(_cost_number),
    Field(ge=0, strict=False, allow_inf_nan=False),
]
Seconds = Annotated[float, Field(gt=0)]
SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CompanyReportConfigError(ValueError):
    """A non-secret machine code for configuration failures before execution."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _nonblank_path(value: str | Path) -> str | Path:
    if not str(value).strip() or "\x00" in str(value):
        raise CompanyReportConfigError("INVALID_PATH")
    return value


ConfigPath = Annotated[Path, BeforeValidator(_nonblank_path)]


class _CompanyContract(FrozenSettings):
    model_config = ConfigDict(revalidate_instances="always", validate_default=True)


class CompanyReportRequest(_CompanyContract):
    """Legacy request-1, retained until the public runner migrates in task 10."""

    schema_version: Literal["company-report-request-1"]
    company_name: Text
    homepage_url: Text | None = None
    legal_identifiers: Mapping[Text, Text] = Field(default_factory=dict)
    as_of: ISODate | None = None

    @field_validator("legal_identifiers")
    @classmethod
    def freeze_identifiers(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return MappingProxyType(dict(value))

    @field_serializer("legal_identifiers")
    def serialize_identifiers(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)


class _ResearchOverrides(_CompanyContract):
    max_calls: Count | None = None
    max_cost_usd: Cost | None = None
    deadline_seconds: Seconds | None = None


class ResearchLimits(_ResearchOverrides):
    """Explicit requested caps, not permission or a separate runtime ledger."""

    max_calls: Count
    max_cost_usd: Cost
    deadline_seconds: Seconds


class OperatorFolderConfig(_CompanyContract):
    """수동 폴더의 현재 가져오기 세대를 지정하며 검토 권한을 부여하지 않는다."""

    imports_path: ConfigPath
    generation_id: Text
    store_version: SHA256


class ImportTarget(_CompanyContract):
    """운영자가 지정한 폴더 대상이며 법적 기업 식별을 뜻하지 않는다."""

    subject_id: Text
    name: Text
    raw_dir: ConfigPath


class ImportFile(_CompanyContract):
    """발견한 PDF별 결과를 보존하며 읽기 실패의 해시는 null로 남긴다."""

    relative_path: Text
    pdf_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")] | None
    status: Literal["ok", "empty", "failed"]
    source_id: Text | None
    error_code: Text | None


class ImportSnapshot(_CompanyContract):
    """ready 세대가 명시적으로 다시 열어야 하는 불변 저장소 버전이다."""

    store_dir: ConfigPath
    store_version: SHA256


class ImportState(_CompanyContract):
    """현재 상태 영수증: ready만 완전한 해시와 불변 snapshot을 가진다."""

    schema_version: Literal["manual-import-1"]
    generation_id: Text
    status: Literal["importing", "ready", "empty", "failed"]
    target: ImportTarget
    input_fingerprint: SHA256 | None
    recipe_hash: SHA256 | None
    files: tuple[ImportFile, ...]
    snapshot: ImportSnapshot | None

    @model_validator(mode="after")
    def check_ready_snapshot(self) -> Self:
        """미완료 상태를 snapshot 존재만으로 ready로 오인하지 못하게 한다."""
        ready = self.status == "ready"
        if ready != (self.snapshot is not None):
            raise CompanyReportConfigError("IMPORT_SNAPSHOT_STATUS_MISMATCH")
        if ready and (
            self.input_fingerprint is None
            or self.recipe_hash is None
            or any(item.pdf_hash is None for item in self.files)
        ):
            raise CompanyReportConfigError("READY_IMPORT_HASHES_REQUIRED")
        return self


class OperatorFolderBinding(_CompanyContract):
    """보고서의 비공개 경계에서 대상·입력·세대·저장소를 함께 고정한다."""

    subject_id: Text
    name: Text
    generation_id: Text
    input_fingerprint: SHA256
    store_version: SHA256


class CompanyReportConfig(_CompanyContract):
    schema_version: Literal["company-report-2"]
    runtime_path: ConfigPath
    store_dir: ConfigPath
    model_path: ConfigPath
    model_receipt_path: ConfigPath
    issuer_receipt_path: ConfigPath | None = None
    acquisition_receipt_path: ConfigPath | None = None
    operator_folder: OperatorFolderConfig | None = None
    research_enabled: bool = False
    research_limits: ResearchLimits | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_config(cls, value: JSONValue) -> JSONValue:
        if (
            isinstance(value, dict)
            and value.get("schema_version") == "company-report-1"
        ):
            raise CompanyReportConfigError("COMPANY_REPORT_V1_MIGRATION_REQUIRED")
        return value

    @model_validator(mode="after")
    def separate_operator_authority(self) -> Self:
        """폴더 입력을 reviewed issuer/acquisition 승인과 혼합하지 않는다."""
        if self.operator_folder is not None and (
            self.issuer_receipt_path is not None
            or self.acquisition_receipt_path is not None
        ):
            raise CompanyReportConfigError(
                "OPERATOR_FOLDER_REVIEWED_AUTHORITY_CONFLICT"
            )
        return self


class EffectiveCompanyReportConfig(CompanyReportConfig):
    """Resolved paths and a frozen snapshot from the single settings owner."""

    runtime_document: RuntimeDocument

    @model_validator(mode="after")
    def check_research_caps(self) -> Self:
        limits = self.research_limits
        if self.research_enabled and limits is None:
            raise CompanyReportConfigError("RESEARCH_CAPS_REQUIRED")
        if limits is not None:
            ceiling = self.runtime_document.profiles.actual_v3
            if (
                limits.max_calls > ceiling.max_calls
                or limits.max_cost_usd > ceiling.max_cost_usd
                or limits.deadline_seconds > ceiling.deadline_seconds
            ):
                raise CompanyReportConfigError("RESEARCH_CAPS_EXCEED_RUNTIME")
        return self

    @property
    def config_hash(self) -> str:
        """Hash effective non-secret values, including the selected runtime."""
        content = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(content).hexdigest()


class ReviewedIssuerReceipt(_CompanyContract):
    """Private pre-execution review input, not model output or legal identity."""

    schema_version: Literal["issuer-association-1"]
    subject_id: Text
    name: Text
    evidence_hashes: Annotated[
        dict[Text, Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]],
        Field(min_length=1),
    ]


def _unique_object(pairs: list[tuple[str, JSONValue]]) -> dict[str, JSONValue]:
    result: dict[str, JSONValue] = {}
    for key, value in pairs:
        if key in result:
            raise CompanyReportConfigError("DUPLICATE_CONFIG_KEY")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise CompanyReportConfigError("NONFINITE_CONFIG_NUMBER")


def load_company_report_config(
    config_path: Path,
    *,
    research: bool | None = None,
    research_limits: Mapping[str, object] | None = None,
    runtime_document: RuntimeDocument | None = None,
) -> EffectiveCompanyReportConfig:
    """Resolve explicit overrides without credentials, providers, or output writes.

    Invalid requested caps fail even in stored-only mode. Missing enabled caps
    fail before runtime loading; the report controller can emit research_blocked.
    Runtime ceilings are necessary, not sufficient: collection must additionally
    be admitted against the trusted authority and the shared remaining ledger.
    """
    selected_research = TypeAdapter(bool | None).validate_python(research, strict=True)
    overrides = _ResearchOverrides.model_validate(
        {} if research_limits is None else research_limits
    )
    path = TypeAdapter(ConfigPath).validate_python(config_path, strict=True).resolve()
    content = path.read_bytes()
    json.loads(
        content, object_pairs_hook=_unique_object, parse_constant=_reject_nonfinite
    )
    configured = CompanyReportConfig.model_validate_json(content)
    enabled = (
        configured.research_enabled if selected_research is None else selected_research
    )
    limits = (
        configured.research_limits.model_dump()
        if configured.research_limits is not None
        else {}
    )
    limits.update(overrides.model_dump(exclude_none=True))
    effective_limits = ResearchLimits.model_validate(limits) if limits else None
    if enabled and effective_limits is None:
        raise CompanyReportConfigError("RESEARCH_CAPS_REQUIRED")
    values = configured.model_dump()
    for name in (
        "runtime_path",
        "store_dir",
        "model_path",
        "model_receipt_path",
        "issuer_receipt_path",
        "acquisition_receipt_path",
    ):
        if values[name] is not None:
            values[name] = (path.parent / values[name]).resolve()
    if configured.operator_folder is not None:
        # 기존 config 디렉터리 기준 경로 해석을 중첩 폴더 binding에도 적용한다.
        values["operator_folder"]["imports_path"] = (
            path.parent / configured.operator_folder.imports_path
        ).resolve()
    document = (
        load_runtime_document(path=values["runtime_path"])
        if runtime_document is None
        else RuntimeDocument.model_validate(runtime_document.model_dump(), strict=True)
    )
    return EffectiveCompanyReportConfig.model_validate(
        values
        | {
            "research_enabled": enabled,
            "research_limits": effective_limits,
            "runtime_document": document,
        }
    )


class CompanyReportReceipt(_CompanyContract):
    """Legacy result-1 with genuine scored/ingestion semantics, pending task 10."""

    schema_version: Literal["company-report-result-1"]
    run_id: Text
    company_name: Text
    candidate_id: Text | None
    matching_candidate_ids: tuple[Text, ...]
    as_of: ISODate
    effective_config_hash: SHA256
    research_requested: bool
    collection_records: tuple[RetrievalRecord, ...]
    collection_cost_usd: Cost | None
    selected_index_versions: tuple[Text, ...]
    eligibility_status: Literal["not_checked", "eligible", "ineligible", "unknown"]
    outcome: Literal[
        "identity_unknown",
        "identity_ambiguous",
        "ineligible",
        "eligibility_unknown",
        "research_blocked",
        "failed",
        "warning",
        "completed",
    ]
    report_path: ConfigPath | None
    report_validation: Literal["not_run", "passed", "failed"]
    validation_receipt_hashes: tuple[SHA256, ...]
    publication_allowed: bool
    ingestion_status: Literal["not_attempted", "succeeded", "failed"]
    reason_codes: tuple[Text, ...]

    @model_validator(mode="after")
    def check_observed_result(self) -> Self:
        report_passed = (
            self.report_path is not None
            and self.report_validation == "passed"
            and bool(self.validation_receipt_hashes)
            and self.eligibility_status == "eligible"
            and self.candidate_id is not None
        )
        if self.report_validation == "passed" and not report_passed:
            raise CompanyReportConfigError("REPORT_VALIDATION_INCOMPLETE")
        if self.publication_allowed and not report_passed:
            raise CompanyReportConfigError("PUBLICATION_REQUIRES_VALIDATED_REPORT")
        if self.ingestion_status != "not_attempted" and not report_passed:
            raise CompanyReportConfigError("INGESTION_REQUIRES_VALIDATED_REPORT")
        if self.outcome == "completed" and (
            not report_passed or self.ingestion_status != "succeeded"
        ):
            raise CompanyReportConfigError("COMPLETION_REQUIRES_REPORT_AND_INGESTION")
        if self.ingestion_status == "failed" and self.outcome != "warning":
            raise CompanyReportConfigError("INGESTION_FAILURE_REQUIRES_WARNING")
        if self.outcome in (
            "identity_unknown",
            "identity_ambiguous",
            "ineligible",
            "eligibility_unknown",
            "research_blocked",
        ) and (self.report_path is not None or self.report_validation != "not_run"):
            raise CompanyReportConfigError("NO_REPORT_OUTCOME_HAS_REPORT")
        if self.outcome == "ineligible" and self.eligibility_status != "ineligible":
            raise CompanyReportConfigError("ELIGIBILITY_STATUS_MISMATCH")
        if (
            self.outcome == "eligibility_unknown"
            and self.eligibility_status != "unknown"
        ):
            raise CompanyReportConfigError("ELIGIBILITY_STATUS_MISMATCH")
        if self.outcome != "completed" and not self.reason_codes:
            raise CompanyReportConfigError("DIAGNOSTIC_REASON_REQUIRED")
        if not self.research_requested and self.collection_records:
            raise CompanyReportConfigError("UNREQUESTED_COLLECTION")
        if any(record.run_id != self.run_id for record in self.collection_records):
            raise CompanyReportConfigError("COLLECTION_RUN_MISMATCH")
        return self


# These are narrative IDs, not the legacy scoring dimensions or criterion catalog.
NarrativeDomain = Literal[
    "technology_product",
    "market_customer",
    "business_model",
    "competition_moat",
    "team_execution",
    "finance_funding_deal",
]
QuestionID = Literal[
    "technology_product.1",
    "technology_product.2",
    "technology_product.3",
    "market_customer.1",
    "market_customer.2",
    "market_customer.3",
    "business_model.1",
    "business_model.2",
    "business_model.3",
    "competition_moat.1",
    "competition_moat.2",
    "competition_moat.3",
    "team_execution.1",
    "team_execution.2",
    "team_execution.3",
    "finance_funding_deal.1",
    "finance_funding_deal.2",
    "finance_funding_deal.3",
]
QUESTION_IDS: Final[tuple[QuestionID, ...]] = get_args(QuestionID)


def _unique_ids(value: list[str]) -> list[str]:
    if len(value) != len(set(value)):
        raise CompanyReportConfigError("DUPLICATE_NARRATIVE_ID")
    return value


EvidenceIDs = Annotated[list[Text], AfterValidator(_unique_ids)]
SupportIDs = Annotated[EvidenceIDs, Field(min_length=1)]


class _NarrativeContract(_CompanyContract):
    """JSON-shaped boundary; nested models keep the same explicit version."""

    schema_version: Literal["narrative-report-1"]

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        context: dict[str, _CompanyContract] | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """Reject duplicate IDs before a JSON parser can silently overwrite them."""
        json.loads(
            json_data,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonfinite,
        )
        return super().model_validate_json(
            json_data,
            strict=strict,
            extra=extra,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )


class NarrativeTarget(_NarrativeContract):
    """Admitted issuer subject, not a claimed authenticated legal entity."""

    subject_id: Text
    name: Text


class NarrativeQuestion(_NarrativeContract):
    question_id: QuestionID
    domain: NarrativeDomain
    question: Text

    @model_validator(mode="after")
    def check_domain(self) -> Self:
        if self.question_id.rsplit(".", 1)[0] != self.domain:
            raise CompanyReportConfigError("QUESTION_DOMAIN_MISMATCH")
        return self


def _json_number(value: int | float) -> int | float:
    """StrictFloat otherwise coerces non-JSON Decimal/Fraction-like inputs."""
    if type(value) not in (int, float):
        raise CompanyReportConfigError("JSON_NUMBER_REQUIRED")
    return value


class EvidenceView(_NarrativeContract):
    """Allowlisted factual projection; provenance remains in a private registry."""

    scope: Literal["company", "industry"]
    claim: Text
    excerpt: Text
    evidence_kind: Literal["reported", "derived", "estimated"]
    value: Annotated[Number, BeforeValidator(_json_number)] | None = None
    unit: Text | None = None
    currency: Text | None = None
    value_as_of: ISODate | None = None
    period: Text | None = None
    geography: Text | None = None
    event_date: ISODate | None = None
    limitations: list[Text]
    supporting_evidence_ids: EvidenceIDs
    derivation: Text | None = None
    conflicts_with: EvidenceIDs
    supersedes: Text | None = None

    @model_validator(mode="after")
    def check_observation(self) -> Self:
        if self.currency is not None and (
            self.value is None or self.unit is None or self.value_as_of is None
        ):
            raise CompanyReportConfigError("MONETARY_CONTEXT_REQUIRED")
        if self.value_as_of is not None and self.currency is None:
            raise CompanyReportConfigError("MONETARY_CURRENCY_REQUIRED")
        if self.evidence_kind in ("derived", "estimated") and (
            not self.supporting_evidence_ids or self.derivation is None
        ):
            raise CompanyReportConfigError("DERIVED_SUPPORT_REQUIRED")
        return self


class InformationResult(_NarrativeContract):
    """Source-neutral feedback; acquisition failure is never an empty success."""

    question_id: QuestionID
    status: Literal["provided", "unavailable"]
    evidence_ids: EvidenceIDs
    reason: (
        Literal[
            "not_found",
            "research_disabled",
            "not_authorized",
            "budget_exhausted",
            "deadline_exceeded",
            "acquisition_failed",
            "evidence_rejected",
        ]
        | None
    )
    detail: Text

    @model_validator(mode="after")
    def check_status(self) -> Self:
        match self.status:
            case "provided":
                if not self.evidence_ids or self.reason is not None:
                    raise CompanyReportConfigError("PROVIDED_REQUIRES_EVIDENCE")
            case "unavailable":
                if self.evidence_ids or self.reason is None:
                    raise CompanyReportConfigError("UNAVAILABLE_REQUIRES_REASON")
            case unreachable:
                assert_never(unreachable)
        return self


class WriterContext(_NarrativeContract):
    context_id: Text
    target: NarrativeTarget
    as_of: ISODate
    questions: list[NarrativeQuestion]
    evidence: dict[Text, EvidenceView]
    may_request_information: bool
    information_results: Annotated[list[InformationResult], Field(max_length=6)]

    @model_validator(mode="after")
    def check_closed_projection(self) -> Self:
        if tuple(question.question_id for question in self.questions) != QUESTION_IDS:
            raise CompanyReportConfigError("EXACT_ORDERED_QUESTIONS_REQUIRED")
        for evidence_id, evidence in self.evidence.items():
            references = (
                evidence.supporting_evidence_ids
                + evidence.conflicts_with
                + ([evidence.supersedes] if evidence.supersedes is not None else [])
            )
            if evidence_id in references or not set(references) <= self.evidence.keys():
                raise CompanyReportConfigError("INVALID_EVIDENCE_REFERENCE")
        try:
            tuple(
                TopologicalSorter(
                    {
                        key: evidence.supporting_evidence_ids
                        for key, evidence in self.evidence.items()
                    }
                ).static_order()
            )
        except CycleError as exc:
            raise CompanyReportConfigError("CIRCULAR_EVIDENCE_SUPPORT") from exc
        _unique_ids([result.question_id for result in self.information_results])
        for result in self.information_results:
            if not set(result.evidence_ids) <= self.evidence.keys():
                raise CompanyReportConfigError("INVALID_INFORMATION_EVIDENCE")
        if self.information_results and self.may_request_information:
            raise CompanyReportConfigError("INFORMATION_ROUND_ALREADY_SETTLED")
        return self


def _writer_context(info: ValidationInfo) -> WriterContext:
    """Replies require trusted context, never self-authorizing evidence IDs.

    Pass context={"writer_context": admitted_context} to model_validate or
    model_validate_json. The projection is revalidated to reject mutated inputs;
    provenance admission and the context hash remain controller responsibilities.
    """
    if not isinstance(info.context, dict) or not isinstance(
        info.context.get("writer_context"), WriterContext
    ):
        raise CompanyReportConfigError("WRITER_CONTEXT_REQUIRED")
    return WriterContext.model_validate(info.context["writer_context"])


class Finding(_NarrativeContract):
    kind: Literal["fact", "interpretation", "strength", "risk"]
    text: Text
    evidence_ids: SupportIDs


class Answer(_NarrativeContract):
    status: Literal["answered", "partial", "unknown", "not_applicable"]
    findings: list[Finding]
    limitation: Text | None = None
    verification_task: Text | None = None

    @model_validator(mode="after")
    def check_answer_support(self) -> Self:
        if self.status != "unknown" and not self.findings:
            raise CompanyReportConfigError("ANSWER_SUPPORT_REQUIRED")
        if self.status in ("partial", "unknown") and (
            self.limitation is None or self.verification_task is None
        ):
            raise CompanyReportConfigError("INCOMPLETE_ANSWER_REQUIRES_TASK")
        if self.status == "not_applicable" and not any(
            finding.kind == "interpretation" for finding in self.findings
        ):
            raise CompanyReportConfigError("APPLICABILITY_RATIONALE_REQUIRED")
        return self


class InformationNeed(_NarrativeContract):
    question_id: QuestionID
    information_needed: Text
    related_evidence_ids: EvidenceIDs


InformationNeeds = Annotated[list[InformationNeed], Field(max_length=6)]


def _check_needs(needs: list[InformationNeed], context: WriterContext) -> None:
    _unique_ids([need.question_id for need in needs])
    if needs and not context.may_request_information:
        raise CompanyReportConfigError("INFORMATION_REQUESTS_DISABLED")
    for need in needs:
        if not set(need.related_evidence_ids) <= context.evidence.keys():
            raise CompanyReportConfigError("INVALID_NEED_EVIDENCE")


class AnalysisReply(_NarrativeContract):
    context_id: Text
    answers: dict[QuestionID, Answer]
    needs: InformationNeeds

    @model_validator(mode="after")
    def check_context(self, info: ValidationInfo) -> Self:
        context = _writer_context(info)
        if self.context_id != context.context_id:
            raise CompanyReportConfigError("STALE_ANALYSIS_CONTEXT")
        if set(self.answers) != set(QUESTION_IDS):
            raise CompanyReportConfigError("EXACT_ANSWER_QUESTIONS_REQUIRED")
        for answer in self.answers.values():
            for finding in answer.findings:
                if not set(finding.evidence_ids) <= context.evidence.keys():
                    raise CompanyReportConfigError("INVALID_FINDING_EVIDENCE")
        _check_needs(self.needs, context)
        return self


class InformationRequest(_NarrativeContract):
    """Controller-owned envelope, not a model-generated request identifier."""

    request_id: Text
    base_context_id: Text
    needs: Annotated[InformationNeeds, Field(min_length=1)]

    @model_validator(mode="after")
    def check_context(self, info: ValidationInfo) -> Self:
        context = _writer_context(info)
        if self.base_context_id != context.context_id:
            raise CompanyReportConfigError("STALE_INFORMATION_REQUEST")
        _check_needs(self.needs, context)
        return self


class InformationResponse(_NarrativeContract):
    """Validate against the controller request and the settled writer context."""

    request_id: Text
    base_context_id: Text
    result_context_id: Text
    results: Annotated[list[InformationResult], Field(min_length=1, max_length=6)]

    @model_validator(mode="after")
    def check_context(self, info: ValidationInfo) -> Self:
        context = _writer_context(info)
        request = (
            info.context.get("information_request")
            if isinstance(info.context, dict)
            else None
        )
        if not isinstance(request, InformationRequest):
            raise CompanyReportConfigError("INFORMATION_REQUEST_REQUIRED")
        if (
            self.request_id != request.request_id
            or self.base_context_id != request.base_context_id
            or self.result_context_id != context.context_id
            or self.base_context_id == self.result_context_id
            or context.may_request_information
        ):
            raise CompanyReportConfigError("INFORMATION_RESPONSE_BINDING_MISMATCH")
        _unique_ids([result.question_id for result in self.results])
        if {result.question_id for result in self.results} != {
            need.question_id for need in request.needs
        } or self.results != context.information_results:
            raise CompanyReportConfigError("INFORMATION_RESULTS_MISMATCH")
        return self


class SupportedStatement(_NarrativeContract):
    text: Text
    evidence_ids: SupportIDs


class VerificationTask(_NarrativeContract):
    question_id: QuestionID
    task: Text


class CompositionReply(_NarrativeContract):
    context_id: Text
    markdown_without_reference: Text
    cited_evidence_ids: EvidenceIDs
    strengths: list[SupportedStatement]
    weaknesses: list[SupportedStatement]
    verification_tasks: list[VerificationTask]

    @model_validator(mode="after")
    def check_context(self, info: ValidationInfo) -> Self:
        context = _writer_context(info)
        if self.context_id != context.context_id:
            raise CompanyReportConfigError("STALE_COMPOSITION_CONTEXT")
        if not set(self.cited_evidence_ids) <= context.evidence.keys():
            raise CompanyReportConfigError("INVALID_CITATION_EVIDENCE")
        for statement in self.strengths + self.weaknesses:
            if not set(statement.evidence_ids) <= set(self.cited_evidence_ids):
                raise CompanyReportConfigError("UNCITED_STATEMENT_EVIDENCE")
        _unique_ids([task.question_id for task in self.verification_tasks])
        return self


class NarrativeRunResult(_CompanyContract):
    """Observed v2 result; real provenance/semantic/PDF checks belong to the runner."""

    schema_version: Literal["company-report-2"]
    run_id: Text
    company_name: Text
    subject_id: Text | None
    as_of: ISODate
    outcome: Literal["completed", "partial", "empty", "ambiguous", "failed"]
    completeness: Literal["full", "partial", "none"]
    reason_codes: EvidenceIDs
    report_md_path: Text | None
    pdf_path: Text | None
    validation: Literal["not_run", "passed", "failed"]
    context_id: Text | None
    validation_receipt_hashes: list[SHA256]
    research_requested: bool
    collection_records: list[RetrievalRecord]
    model_usage: JSONMap

    @model_validator(mode="after")
    def check_observed_result(self) -> Self:
        _unique_ids(self.validation_receipt_hashes)
        match self.outcome:
            case "completed" | "partial":
                expected = "full" if self.outcome == "completed" else "partial"
                if (
                    self.completeness != expected
                    or self.validation != "passed"
                    or self.subject_id is None
                    or self.context_id is None
                    or self.report_md_path is None
                    or self.pdf_path is None
                    or not self.validation_receipt_hashes
                ):
                    raise CompanyReportConfigError("VALIDATED_REPORT_REQUIRED")
            case "empty" | "ambiguous" | "failed":
                if (
                    self.completeness != "none"
                    or self.report_md_path is not None
                    or self.pdf_path is not None
                    or self.validation == "passed"
                ):
                    raise CompanyReportConfigError("NO_REPORT_OUTCOME_HAS_REPORT")
                if self.outcome != "failed" and self.validation != "not_run":
                    raise CompanyReportConfigError("NO_REPORT_VALIDATION_NOT_RUN")
                if self.outcome == "ambiguous" and self.subject_id is not None:
                    raise CompanyReportConfigError("AMBIGUOUS_SUBJECT_HAS_ID")
            case unreachable:
                assert_never(unreachable)
        if self.outcome != "completed" and not self.reason_codes:
            raise CompanyReportConfigError("DIAGNOSTIC_REASON_REQUIRED")
        if not self.research_requested and self.collection_records:
            raise CompanyReportConfigError("UNREQUESTED_COLLECTION")
        if any(record.run_id != self.run_id for record in self.collection_records):
            raise CompanyReportConfigError("COLLECTION_RUN_MISMATCH")
        return self
