"""Explicit, immutable runtime requests; approval remains with each consumer."""

import json
import os
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal, TypeAlias, assert_never

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

ProfileName: TypeAlias = Literal[
    "m2_shared",
    "m2_evidence_validation",
    "actual_v3",
    "local_demo",
    "m2_source",
    "m2_research",
    "m2_local_rag",
    "recommended_run",
]
PositiveCount: TypeAlias = Annotated[int, Field(gt=0)]
NonnegativeCount: TypeAlias = Annotated[int, Field(ge=0)]
PositiveSeconds: TypeAlias = Annotated[float, Field(gt=0)]
JSONValue: TypeAlias = (
    None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
)


class FrozenSettings(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        hide_input_in_errors=True,
    )


class LLMSettings(FrozenSettings):
    model: Literal["gpt-4.1-mini-2025-04-14"]
    endpoint: Literal["https://api.openai.com/v1/responses"]
    # Rates and destination retain authorization; the file cannot redirect
    # credentials or shrink the amount reserved against the cost ceiling.
    usd_per_input_token: Annotated[
        Decimal, Field(ge=Decimal("0.0000004"), le=Decimal("0.0000004"))
    ]
    usd_per_output_token: Annotated[
        Decimal, Field(ge=Decimal("0.0000016"), le=Decimal("0.0000016"))
    ]
    pricing_reference: Annotated[str, Field(pattern=r"^https://[^\s]+$")]
    pricing_checked_on: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class M2SharedSettings(FrozenSettings):
    max_calls: Annotated[int, Field(gt=0, le=8)]
    max_input_tokens: Annotated[int, Field(gt=0, le=64000)]
    max_output_tokens: Annotated[int, Field(gt=0, le=16000)]
    max_cost_usd: Annotated[Decimal, Field(gt=0, le=1)]
    request_input_tokens: Annotated[int, Field(gt=0, le=8000)]
    request_output_tokens: Annotated[int, Field(gt=0, le=2000)]
    timeout_seconds: Annotated[float, Field(gt=0, le=30)]
    max_retries: Annotated[int, Field(ge=0, le=0)]


class M2EvidenceValidationSettings(FrozenSettings):
    max_calls: Annotated[int, Field(gt=0, le=8)]
    max_input_tokens: Annotated[int, Field(gt=0, le=64000)]
    max_output_tokens: Annotated[int, Field(gt=0, le=16000)]
    max_cost_usd: Annotated[Decimal, Field(gt=0, le=1)]
    request_input_tokens: Annotated[int, Field(gt=0, le=8000)]
    request_output_tokens: Annotated[int, Field(gt=0, le=2000)]
    timeout_seconds: Annotated[float, Field(gt=0, le=30)]
    max_retries: Annotated[int, Field(ge=0, le=0)]
    deadline_seconds: Annotated[float, Field(gt=0, le=600)]
    attempt_max_calls: Annotated[int, Field(gt=0, le=1)]
    retrieval_max_calls: Annotated[int, Field(gt=0, le=4)]
    retrieval_attempt_max_calls: Annotated[int, Field(gt=0, le=1)]
    retrieval_max_retries: Annotated[int, Field(ge=0, le=0)]


class FetchSettings(FrozenSettings):
    allowed_schemes: tuple[Literal["https"], ...] = Field(min_length=1, max_length=1)
    max_bytes: PositiveCount
    timeout_seconds: Annotated[float, Field(gt=0, le=30)]
    max_redirects: NonnegativeCount


class M2SourceSettings(FrozenSettings):
    fetch: FetchSettings
    max_name_matches: PositiveCount
    max_index_bytes: PositiveCount
    max_calls: Annotated[int, Field(gt=0, le=3)]
    max_retries: Annotated[int, Field(ge=0, le=0)]
    timeout_seconds: Annotated[float, Field(gt=0, le=30)]
    deadline_seconds: Annotated[float, Field(gt=0, le=600)]


class M2ResearchSettings(FrozenSettings):
    fetch: FetchSettings
    max_name_matches: PositiveCount
    max_index_bytes: PositiveCount
    deadline_seconds: Annotated[float, Field(gt=0, le=600)]
    eligibility_max_input_chars: PositiveCount
    retrieval_max_calls: Annotated[int, Field(gt=0, le=3)]
    retrieval_max_retries: Annotated[int, Field(ge=0, le=0)]
    retrieval_timeout_seconds: Annotated[float, Field(gt=0, le=30)]


class M2LocalRAGSettings(FrozenSettings):
    max_calls: Annotated[int, Field(gt=0, le=1)]
    max_input_tokens: Annotated[int, Field(ge=0, le=0)]
    max_output_tokens: Annotated[int, Field(ge=0, le=0)]
    max_cost_usd: Annotated[Decimal, Field(ge=0, le=0)]
    max_retries: Annotated[int, Field(ge=0, le=0)]
    timeout_seconds: Annotated[float, Field(gt=0, le=30)]
    top_k: PositiveCount


class ActualV3Settings(FrozenSettings):
    max_calls: Annotated[int, Field(gt=0, le=40)]
    openai_max_calls: Annotated[int, Field(gt=0, le=40)]
    retrieval_max_calls: Annotated[int, Field(gt=0, le=40)]
    max_input_tokens: Annotated[int, Field(gt=0, le=2000000)]
    max_output_tokens: Annotated[int, Field(gt=0, le=120000)]
    max_cost_usd: Annotated[Decimal, Field(gt=0, le=1)]
    request_input_tokens: Annotated[int, Field(gt=0, le=1)]
    request_output_tokens: Annotated[int, Field(gt=0, le=2000)]
    timeout_seconds: Annotated[float, Field(gt=0, le=60)]
    deadline_seconds: Annotated[float, Field(gt=0, le=3600)]
    max_retries: Annotated[int, Field(ge=0, le=0)]
    retrieval_top_k: PositiveCount


class LocalDemoSettings(FrozenSettings):
    llm_calls: Annotated[int, Field(gt=0, le=30)]
    cost_usd: Annotated[Decimal, Field(gt=0, le=3)]
    seconds: Annotated[int, Field(gt=0, le=1200)]
    request_output_tokens: Annotated[int, Field(gt=0, le=2000)]
    request_timeout_seconds: Annotated[float, Field(gt=0, le=120)]
    minimum_timeout_seconds: PositiveSeconds


class RecommendedRunSettings(FrozenSettings):
    max_candidates: PositiveCount
    seed: int
    initial_company_research: NonnegativeCount
    unknown_additional_retries: NonnegativeCount
    evaluate_unknown: bool
    refill: bool
    paid_call_allowance: Annotated[int, Field(ge=0, le=0)]
    paid_cost_usd: Annotated[Decimal, Field(ge=0, le=0)]
    past_paid_ledger: Literal["not_supplied_unverified"]
    enforcement: Literal["controller_handoff_only"]
    criterion_support: Literal["actual_fact_verifier_approved_minimum_evidence"]
    coverage_target: Literal["missing_weight*100 < 30*applicable_weight"]
    profile_version: Literal["recommended-run-profile-v1"]


class RuntimeProfiles(FrozenSettings):
    m2_shared: M2SharedSettings
    m2_evidence_validation: M2EvidenceValidationSettings
    actual_v3: ActualV3Settings
    local_demo: LocalDemoSettings
    m2_source: M2SourceSettings
    m2_research: M2ResearchSettings
    m2_local_rag: M2LocalRAGSettings
    recommended_run: RecommendedRunSettings


class RuntimeDocument(FrozenSettings):
    schema_version: Literal["runtime-1"]
    llm: LLMSettings
    profiles: RuntimeProfiles


RuntimeProfile: TypeAlias = (
    M2SharedSettings
    | M2EvidenceValidationSettings
    | ActualV3Settings
    | LocalDemoSettings
    | M2SourceSettings
    | M2ResearchSettings
    | M2LocalRAGSettings
    | RecommendedRunSettings
)


class RuntimeSettings(FrozenSettings):
    schema_version: Literal["runtime-1"]
    llm: LLMSettings
    profile_name: ProfileName
    profile: RuntimeProfile


def resolve_explicit_credential(value: str | None) -> str | None:
    """Return a supplied credential unchanged; never consult the environment."""
    return value


def resolve_environment_credential(name: str) -> str:
    """Return one environment credential with surrounding whitespace removed."""
    return os.environ.get(name, "").strip()


def resolve_demo_credential(root: str | Path) -> str | None:
    """Resolve the demo's environment-first, then root .env credential."""
    environment_value = os.environ.get("OPENAI_API_KEY")
    if environment_value:
        return environment_value
    return dotenv_values(Path(root) / ".env").get("OPENAI_API_KEY")


def _unique_json_object(pairs: list[tuple[str, JSONValue]]) -> dict[str, JSONValue]:
    result: dict[str, JSONValue] = {}
    for name, value in pairs:
        if name in result:
            message = "duplicate runtime settings key"
            raise ValueError(message)
        result[name] = value
    return result


def _reject_nonfinite_json(_value: str) -> None:
    message = "nonfinite runtime settings number"
    raise ValueError(message)


def load_runtime_document(*, path: str | Path | None = None) -> RuntimeDocument:
    """Load the complete immutable document once for a multi-profile composition."""
    if path is None:
        module_path = Path(__file__).resolve()
        if module_path.parts[-3:] == ("src", "skala_rag", "settings.py"):
            content = (module_path.parents[2] / "configs/runtime.json").read_bytes()
        else:
            content = files("skala_rag").joinpath("_config/runtime.json").read_bytes()
    else:
        content = Path(path).read_bytes()
    json.loads(
        content,
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_nonfinite_json,
    )
    # JSON validation accepts decimal strings and immutable tuples without
    # relaxing strict integer/bool checks on the file boundary.
    return RuntimeDocument.model_validate_json(content, strict=True)


def load_runtime_settings(
    profile: ProfileName, *, path: str | Path | None = None
) -> RuntimeSettings:
    """Select one profile, rejecting unknown names before accessing its file."""
    profile_name = TypeAdapter[ProfileName](ProfileName).validate_python(
        profile, strict=True
    )
    document = load_runtime_document(path=path)
    match profile_name:
        case "m2_shared":
            selected = document.profiles.m2_shared
        case "m2_evidence_validation":
            selected = document.profiles.m2_evidence_validation
        case "actual_v3":
            selected = document.profiles.actual_v3
        case "local_demo":
            selected = document.profiles.local_demo
        case "m2_source":
            selected = document.profiles.m2_source
        case "m2_research":
            selected = document.profiles.m2_research
        case "m2_local_rag":
            selected = document.profiles.m2_local_rag
        case "recommended_run":
            selected = document.profiles.recommended_run
        case unreachable:
            assert_never(unreachable)
    return RuntimeSettings(
        schema_version=document.schema_version,
        llm=document.llm,
        profile_name=profile_name,
        profile=selected,
    )
