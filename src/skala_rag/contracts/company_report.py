"""Company-report requests and receipts; serializable values never grant authority."""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal, Self

from pydantic import (
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    field_serializer,
    field_validator,
    model_validator,
)

from skala_rag.contracts.common import Count, ISODate, Text
from skala_rag.contracts.decisions import ScoreNumber
from skala_rag.contracts.retrieval import RetrievalRecord
from skala_rag.settings import (
    FrozenSettings,
    JSONValue,
    RuntimeDocument,
    load_runtime_document,
)

Cost = Annotated[ScoreNumber, Field(ge=0, strict=False)]
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
    """User identity hints only; a homepage is not permission to fetch it."""

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


class CompanyReportConfig(_CompanyContract):
    schema_version: Literal["company-report-1"]
    runtime_path: ConfigPath
    store_dir: ConfigPath
    model_path: ConfigPath
    model_receipt_path: ConfigPath
    policy_path: ConfigPath
    catalog_path: ConfigPath
    research_enabled: bool = False
    research_limits: ResearchLimits | None = None


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
        "policy_path",
        "catalog_path",
    ):
        values[name] = (path.parent / values[name]).resolve()
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
    """Observed result structure, never an admission or publication approval."""

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
