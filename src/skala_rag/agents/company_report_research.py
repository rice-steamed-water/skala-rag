"""Explicit target research over the existing provider, runtime and store.

Freshness proposals and requested limits never grant authority. The narrative
factory binds an operator collection receipt to reviewed corpus originals and
the existing runtime. Legacy scored research retains its separate admission.
There is no discovery, competitor fetching, implicit approval, or collection retry.
"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from sqlite3 import Error as SQLiteError
from typing import Final, Literal, Protocol, TypeVar, assert_never

import httpx
from pydantic import BaseModel, TypeAdapter

from skala_rag.agents.eligibility_extraction import LLMEligibilityExtractor
from skala_rag.contracts import (
    Candidate,
    Chunk,
    Evidence,
    RetrievalRecord,
    Source,
    ToolBudget,
)
from skala_rag.contracts.common import ISODate
from skala_rag.contracts.company_report import (
    EffectiveCompanyReportConfig,
    InformationNeed,
    InformationResult,
    ResearchLimits,
    _reject_nonfinite,
    _unique_object,
)
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import Clock, StructuredLLM
from skala_rag.contracts.tools import CompanyResearchBundle
from skala_rag.prompt.company_report_freshness import (
    SYSTEM_PROMPT,
    FreshnessJudgment,
    FreshnessOutput,
    GapQuery,
    build_user_prompt,
)
from skala_rag.prompt.eligibility_facts import PROMPT_VERSION as FACTS_PROMPT_VERSION
from skala_rag.rag.company_store import (
    CompanyStore,
    RetainedSource,
    StoreSnapshot,
    evidence_fingerprint,
)
from skala_rag.rag.corpus import (
    CorpusGateError,
    CorpusManifest,
    check_corpus,
    require_indexable,
)
from skala_rag.settings import (
    FrozenSettings,
    LLMSettings,
    resolve_environment_credential,
)
from skala_rag.tools.company_research import (
    LiveResearchCompany,
    assemble_bundle,
    same_site,
    url_host,
)
from skala_rag.tools.official_homepage import OfficialHomepage
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt, byte_bound_allowance
from skala_rag.tools.provider_scope import EXCLUDED_PROVIDERS
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    AttemptResponse,
    CallContext,
    Readiness,
    TransportFailure,
    Usage,
)
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM
from skala_rag.tools.source_fetch import (
    FetchError,
    FetchPolicy,
    FetchRejection,
    RawSnapshot,
    Resolver,
    SafeFetcher,
    check_as_of,
    default_resolve,
    snapshot_source_id,
)

PROVIDER: Final = "official-homepage"
ModelT = TypeVar("ModelT", bound=BaseModel)


class CompanyResearchError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class FreshnessAssessment:
    as_of: date
    input_sha256: str
    judgments: tuple[FreshnessJudgment, ...]


def assess_freshness(
    evidence: Mapping[str, Evidence],
    sources: Mapping[str, Source],
    *,
    as_of: date,
    llm: StructuredLLM,
) -> FreshnessAssessment:
    """Assess detached retained data once; never fetch or create new Evidence."""
    frozen_evidence = {
        key: Evidence.model_validate_json(value.model_dump_json())
        for key, value in evidence.items()
    }
    frozen_sources = {
        key: Source.model_validate_json(value.model_dump_json())
        for key, value in sources.items()
    }
    if (
        any(key != e.evidence_id for key, e in frozen_evidence.items())
        or any(key != s.source_id for key, s in frozen_sources.items())
        or any(e.source_id not in frozen_sources for e in frozen_evidence.values())
    ):
        raise CompanyResearchError("FRESHNESS_INPUT_CLOSURE")
    prompt = build_user_prompt(frozen_evidence, frozen_sources, as_of=as_of)
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    if not frozen_evidence:
        return FreshnessAssessment(as_of, digest, ())
    output = llm.generate(
        system=SYSTEM_PROMPT, user=prompt, output_schema=FreshnessOutput
    )
    output = FreshnessOutput.model_validate_json(output.model_dump_json())
    ids = [j.evidence_id for j in output.judgments]
    if len(ids) != len(set(ids)) or set(ids) != set(frozen_evidence):
        raise CompanyResearchError("FRESHNESS_OUTPUT_CLOSURE")
    superseded = {e.supersedes for e in frozen_evidence.values() if e.supersedes}
    judgments = []
    for judgment in output.judgments:
        item = frozen_evidence[judgment.evidence_id]
        source = frozen_sources[item.source_id]
        if any(
            q.candidate_id != item.candidate_id or q.scope != item.scope
            for q in judgment.gap_queries
        ):
            raise CompanyResearchError("FRESHNESS_QUERY_SUBJECT_MISMATCH")
        if judgment.status == "current" and judgment.gap_queries:
            raise CompanyResearchError("CURRENT_EVIDENCE_HAS_GAPS")
        # Structural uncertainty cannot be erased by plausible model prose.
        reasons = []
        if source.published_at is None and item.event_date is None:
            reasons.append("UNDATED")
        if item.conflicts_with:
            reasons.append("CONFLICT")
        if item.evidence_id in superseded:
            reasons.append("SUPERSEDED")
        if (
            not check_as_of(source, as_of).admitted
            or (item.event_date is not None and item.event_date > as_of)
            or source.bibliographic_metadata.get("generated_report")
        ):
            reasons.append("NOT_CURRENT_ORIGINAL")
        if reasons:
            judgment = judgment.model_copy(
                update={
                    "status": "unknown",
                    "reason": "; ".join(reasons) + ": " + judgment.reason,
                }
            )
        judgments.append(judgment)
    return FreshnessAssessment(as_of, digest, tuple(judgments))


@dataclass(frozen=True, slots=True)
class ResearchTarget:
    query: GapQuery | InformationNeed
    url: str
    provider: str = PROVIDER


class ResearchRuntimeBinding(Protocol):
    """Read-only transport contract; neither eligibility nor scoring authority."""

    @property
    def runtime(self) -> AdapterRuntime: ...

    @property
    def budget(self) -> ToolBudget: ...

    @property
    def call(self) -> CallContext: ...

    @property
    def readiness(self) -> Readiness: ...

    @property
    def tool_name(self) -> str: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class NarrativeResearchAdmission:
    """Frozen operator opt-in, exact targets and the existing shared runtime."""

    runtime_binding: ResearchRuntimeBinding
    as_of: date
    execution_scope: Literal["actual", "controlled_response", "actual_replay"]
    candidate_json: str
    approved_limits: ResearchLimits
    targets: tuple[ResearchTarget, ...]
    authorize: Callable[[Candidate, tuple[ResearchTarget, ...], ResearchLimits], bool]
    prepare_source: Callable[
        [CompanyResearchBundle, RawSnapshot, tuple[RetrievalRecord, ...]],
        RetainedSource,
    ]
    fetch_policy: FetchPolicy
    readiness: Readiness
    llm: RuntimeStructuredLLM | None = None
    domain_definition: str | None = None
    max_input_chars: int | None = None
    http_transport: httpx.BaseTransport | None = field(default=None, repr=False)
    resolve: Resolver = field(default=default_resolve, repr=False)
    reviewed_corpus: CorpusManifest | None = None
    start_calls: int = field(init=False)
    start_cost_usd: str | None = field(init=False)
    started_at: datetime = field(init=False)
    deadline: datetime = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "targets",
            tuple(
                ResearchTarget(
                    type(target.query).model_validate_json(
                        target.query.model_dump_json()
                    ),
                    target.url,
                    target.provider,
                )
                for target in self.targets
            ),
        )
        usage = self.runtime_binding.runtime.ledger.snapshot()
        deadline = self.runtime_binding.budget.deadline
        if deadline is None:
            raise CompanyResearchError("RESEARCH_DEADLINE_REQUIRED")
        object.__setattr__(self, "start_calls", usage["calls"])
        object.__setattr__(self, "start_cost_usd", usage["cost_usd_accounted"])
        object.__setattr__(self, "started_at", self.runtime_binding.runtime.clock.now())
        object.__setattr__(
            self,
            "deadline",
            min(
                deadline,
                self.started_at
                + timedelta(seconds=self.approved_limits.deadline_seconds),
            ),
        )


@dataclass(frozen=True, slots=True)
class ResearchResult:
    status: Literal["not_requested", "blocked", "failed", "completed"]
    reason_codes: tuple[str, ...] = ()
    collection_records: tuple[RetrievalRecord, ...] = ()
    bundles: tuple[CompanyResearchBundle, ...] = ()
    materials: tuple[RetainedSource, ...] = ()
    snapshot: StoreSnapshot | None = None
    ingestion_status: Literal["not_attempted", "succeeded", "failed"] = "not_attempted"
    raw_snapshots: tuple[RawSnapshot, ...] = ()


class _Session:
    """Mutable per-invocation observations; never a replacement budget ledger."""

    def __init__(
        self,
        admission: NarrativeResearchAdmission,
        candidate: Candidate,
        limits: ResearchLimits,
        targets: tuple[ResearchTarget, ...],
    ) -> None:
        self.admission = admission
        self.candidate = candidate
        self.limits = limits
        self.targets = targets
        self.runtime = admission.runtime_binding.runtime
        self.start = self.runtime.ledger.snapshot()
        deadline = admission.runtime_binding.budget.deadline
        if deadline is None:
            raise CompanyResearchError("RESEARCH_DEADLINE_REQUIRED")
        self.deadline = min(
            deadline,
            self.runtime.clock.now() + timedelta(seconds=limits.deadline_seconds),
        )
        if isinstance(admission, NarrativeResearchAdmission):
            self.start["calls"] = admission.start_calls
            self.start["cost_usd_accounted"] = admission.start_cost_usd
            self.deadline = min(
                self.deadline,
                admission.deadline,
                admission.started_at + timedelta(seconds=limits.deadline_seconds),
            )
        self.records: list[RetrievalRecord] = []
        self.raw: list[RawSnapshot] = []
        self.reasons: list[str] = []

    def check(self, allowance: Allowance) -> None:
        if (
            self.admission.authorize(
                self.candidate.model_copy(deep=True), self.targets, self.limits
            )
            is not True
        ):
            raise CompanyResearchError("RESEARCH_ADMISSION_DENIED")
        used = self.runtime.ledger.snapshot()
        cost, initial = used["cost_usd_accounted"], self.start["cost_usd_accounted"]
        if (
            cost is None
            or initial is None
            or allowance.max_cost_usd is None
            or used["calls"] - self.start["calls"] >= self.limits.max_calls
            or self.runtime.clock.now() >= self.deadline
        ):
            raise CompanyResearchError("RESEARCH_CAP_EXHAUSTED")
        values = (Decimal(cost), Decimal(initial), allowance.max_cost_usd)
        exponents = tuple(v.as_tuple().exponent for v in values)
        if not all(isinstance(exponent, int) for exponent in exponents):
            raise CompanyResearchError("INVALID_COST_ACCOUNTING")
        with localcontext() as context:
            context.prec = max(
                28,
                max(v.adjusted() for v in values)
                - min(int(exponent) for exponent in exponents)
                + 3,
            )
            if values[0] - values[1] + values[2] > self.limits.max_cost_usd:
                raise CompanyResearchError("RESEARCH_CAP_EXHAUSTED")

    def budget(self) -> ToolBudget:
        original = self.admission.runtime_binding.budget
        return ToolBudget(
            schema_version=original.schema_version,
            max_calls=1,
            max_retries=0,
            timeout_seconds=min(
                original.timeout_seconds,
                self.admission.fetch_policy.timeout_seconds,
            ),
            deadline=self.deadline,
        )


class _RuntimeFetcher(SafeFetcher):
    """Adapt OfficialHomepage's one-fetch interface to one shared-runtime request."""

    def __init__(self, session: _Session):
        self.session = session

    def fetch(self, url: str) -> RawSnapshot:
        session = self.session
        admission = session.admission
        schema = session.candidate.schema_version
        allowance = Allowance(
            schema_version=schema,
            input_tokens=0,
            output_tokens=0,
            max_cost_usd=Decimal(0),
        )
        try:
            session.check(allowance)
            if url not in {target.url for target in session.targets}:
                raise CompanyResearchError("TARGET_URL_MISMATCH")
        except ValueError as exc:
            code = (
                exc.code
                if isinstance(exc, CompanyResearchError)
                else "RESEARCH_ADMISSION_INVALID"
            )
            session.reasons.append(code)
            raise FetchError(
                FetchRejection.URL_INVALID,
                ErrorCode.BUDGET_EXHAUSTED
                if code == "RESEARCH_CAP_EXHAUSTED"
                else ErrorCode.TOOL_NOT_CONFIGURED,
                code,
            ) from None

        class Attempt:
            retry_owner: Literal["runtime"] = "runtime"

            def __call__(
                self, *, timeout_seconds: float
            ) -> AttemptResponse[RawSnapshot]:
                fetcher = SafeFetcher(
                    replace(admission.fetch_policy, timeout_seconds=timeout_seconds),
                    clock=session.runtime.clock,
                    transport=admission.http_transport,
                    resolve=admission.resolve,
                )
                try:
                    raw = fetcher.fetch(url)
                except FetchError as exc:
                    raise TransportFailure(exc.error_code) from None
                session.raw.append(raw)
                if (
                    isinstance(admission, NarrativeResearchAdmission)
                    and admission.reviewed_corpus is not None
                    and not any(
                        doc.origin_url == raw.locator
                        and doc.content_hash == raw.content_hash
                        for doc in admission.reviewed_corpus.documents
                    )
                ):
                    raise TransportFailure(ErrorCode.TOOL_RESPONSE_INVALID)
                return AttemptResponse[RawSnapshot](
                    schema_version=schema,
                    status="ok",
                    data=raw,
                    source_ids=[],
                    chunk_ids=[],
                    evidence_ids=[],
                    usage=Usage(
                        schema_version=schema,
                        input_tokens=0,
                        output_tokens=0,
                        cost_usd=None,
                    ),
                )

        result = session.runtime.execute(
            CallContext(
                schema_version=schema,
                call_id=f"company-report-fetch-{len(session.records)}",
                run_id=admission.runtime_binding.call.run_id,
                candidate_id=session.candidate.candidate_id,
                tool_name=PROVIDER,
                node="company_report_research",
            ),
            budget=session.budget(),
            readiness=admission.readiness,
            allowance=allowance,
            transport=Attempt(),
        )
        session.records.extend(result.retrieval_records)
        if result.data is None:
            raise FetchError(
                FetchRejection.TRANSPORT_FAILED,
                ErrorCode(result.errors[-1].error_code),
                "admitted source request failed",
            )
        return result.data


def _validate_admission(
    admission: NarrativeResearchAdmission,
    candidate: Candidate,
    as_of: date,
    limits: ResearchLimits,
    queries: Sequence[GapQuery | InformationNeed],
) -> tuple[ResearchTarget, ...]:
    if type(admission) is not NarrativeResearchAdmission:
        raise CompanyResearchError("TRUSTED_RESEARCH_ADMISSION_REQUIRED")
    binding = admission.runtime_binding
    if (
        Candidate.model_validate_json(admission.candidate_json) != candidate
        or as_of != admission.as_of
        or binding.call.candidate_id not in (None, candidate.candidate_id)
    ):
        raise CompanyResearchError("RESEARCH_IDENTITY_MISMATCH")
    approved = ResearchLimits.model_validate_json(
        admission.approved_limits.model_dump_json()
    )
    ledger_limits = binding.runtime.ledger.limits
    if PROVIDER not in ledger_limits.tool_max_calls:
        raise CompanyResearchError("RESEARCH_ADMISSION_DENIED")
    if (
        limits.max_calls > approved.max_calls
        or limits.max_cost_usd > approved.max_cost_usd
        or limits.deadline_seconds > approved.deadline_seconds
        or limits.max_calls > ledger_limits.max_calls
        or ledger_limits.max_cost_usd is None
        or limits.max_cost_usd > ledger_limits.max_cost_usd
    ):
        raise CompanyResearchError("RESEARCH_CAPS_EXCEED_AUTHORITY")
    if (
        admission.fetch_policy.max_redirects != 0
        or binding.runtime.policy.retry_delays_seconds
    ):
        raise CompanyResearchError("RESEARCH_REQUIRES_SINGLE_ATTEMPTS")
    match admission.execution_scope:
        case "actual":
            if admission.http_transport is not None or admission.resolve is not (
                default_resolve
            ):
                raise CompanyResearchError("ACTUAL_TRANSPORT_MISMATCH")
        case "controlled_response":
            if type(admission.http_transport) is not httpx.MockTransport:
                raise CompanyResearchError("CONTROLLED_TRANSPORT_REQUIRED")
        case "actual_replay":
            raise CompanyResearchError("REPLAY_CANNOT_COLLECT")
        case unreachable:
            assert_never(unreachable)
    for query in queries:
        match query:
            case GapQuery():
                if (
                    query.scope != "company"
                    or query.candidate_id != candidate.candidate_id
                ):
                    raise CompanyResearchError("COMPETITOR_QUERY_DENIED")
            case InformationNeed():
                if not isinstance(admission, NarrativeResearchAdmission):
                    raise CompanyResearchError("NARRATIVE_ADMISSION_REQUIRED")
            case unreachable:
                assert_never(unreachable)
    targets = tuple(t for t in admission.targets if t.query in queries)
    if (
        isinstance(admission, NarrativeResearchAdmission)
        and admission.reviewed_corpus is not None
    ):
        # Needs select information, never destinations or permission. Only the
        # factory's frozen receipt contributes these exact URLs.
        targets = tuple(
            ResearchTarget(query, url)
            for query in queries
            for doc in admission.reviewed_corpus.documents
            if (url := doc.origin_url) is not None
        )
    if any(not any(t.query == query for t in targets) for query in queries):
        raise CompanyResearchError("GAP_NOT_ADMITTED")
    for target in targets:
        if target.provider in EXCLUDED_PROVIDERS or target.provider != PROVIDER:
            raise CompanyResearchError("PROVIDER_NOT_APPROVED")
        if not same_site(target.url, candidate.homepage_url):
            raise CompanyResearchError("COMPETITOR_URL_DENIED")
    if (
        admission.authorize(candidate.model_copy(deep=True), targets, limits)
        is not True
    ):
        raise CompanyResearchError("RESEARCH_ADMISSION_DENIED")
    return targets


def _extractor(session: _Session, as_of: date) -> LLMEligibilityExtractor | None:
    admission = session.admission
    llm = admission.llm
    if llm is None:
        return None
    binding = admission.runtime_binding
    if (
        type(llm) is not RuntimeStructuredLLM
        or llm.runtime is not session.runtime
        or llm.call.run_id != binding.call.run_id
        or llm.call.candidate_id != session.candidate.candidate_id
        or llm.call.tool_name != binding.tool_name
        or llm.budget != binding.budget
        or llm.readiness != binding.readiness
        or type(llm.transport) is not OpenAIResponsesAttempt
        or llm.transport._clock is not session.runtime.clock
        or not admission.domain_definition
        or admission.max_input_chars is None
    ):
        raise CompanyResearchError("EXTRACTOR_RUNTIME_MISMATCH")
    LLMSettings.model_validate_json(llm.transport.llm_settings.model_dump_json())
    match admission.execution_scope:
        case "actual":
            valid = llm.transport._http_transport is None
        case "controlled_response":
            valid = type(llm.transport._http_transport) is httpx.MockTransport
        case "actual_replay":
            valid = False
        case unreachable:
            assert_never(unreachable)
    if not valid:
        raise CompanyResearchError("EXTRACTOR_TRANSPORT_MISMATCH")

    def allowance_for(system: str, user: str, schema: type[BaseModel]) -> Allowance:
        allowance = llm.allowance_for(system, user, schema)
        session.check(allowance)
        return allowance

    capped = RuntimeStructuredLLM(
        runtime=llm.runtime,
        call=llm.call,
        budget=session.budget(),
        readiness=llm.readiness,
        transport=llm.transport,
        allowance_for=allowance_for,
    )

    class RecordedLLM:
        def generate(
            self, *, system: str, user: str, output_schema: type[ModelT]
        ) -> ModelT:
            before = len(capped.retrieval_records)
            try:
                return capped.generate(
                    system=system, user=user, output_schema=output_schema
                )
            finally:
                session.records.extend(capped.retrieval_records[before:])

    return LLMEligibilityExtractor(
        RecordedLLM(),
        as_of=as_of,
        domain_definition=admission.domain_definition,
        max_input_chars=admission.max_input_chars,
    )


def research_target(
    candidate: Candidate,
    evidence: Mapping[str, Evidence],
    sources: Mapping[str, Source],
    *,
    as_of: date,
    research: bool,
    limits: ResearchLimits | None,
    freshness: FreshnessAssessment,
    gaps: Sequence[GapQuery | InformationNeed] = (),
    admission: NarrativeResearchAdmission | None,
    store: CompanyStore,
) -> ResearchResult:
    """Collect admitted target URLs once and batch one reviewed store ingestion.

    A failed ingestion returns the collected materials for a storage-only retry.
    It never returns a successful empty search or restarts completed fetches.
    """
    TypeAdapter(bool).validate_python(research, strict=True)
    if not research:
        return ResearchResult("not_requested", ("RESEARCH_NOT_REQUESTED",))
    if limits is None or admission is None:
        return ResearchResult("blocked", ("RESEARCH_ADMISSION_OR_CAPS_MISSING",))
    try:
        limits = ResearchLimits.model_validate_json(limits.model_dump_json())
        candidate = Candidate.model_validate_json(candidate.model_dump_json())
        prompt = build_user_prompt(evidence, sources, as_of=as_of)
        if (
            freshness.as_of != as_of
            or freshness.input_sha256 != hashlib.sha256(prompt.encode()).hexdigest()
        ):
            raise CompanyResearchError("STALE_FRESHNESS_ASSESSMENT")
        queries: list[GapQuery | InformationNeed] = []
        for query in [*gaps, *(q for j in freshness.judgments for q in j.gap_queries)]:
            if query not in queries:
                queries.append(query)
        targets = _validate_admission(admission, candidate, as_of, limits, queries)
        session = _Session(admission, candidate, limits, targets)
        extractor = _extractor(session, as_of)
    except CompanyResearchError as exc:
        return ResearchResult("blocked", (exc.code,))
    except ValueError:
        return ResearchResult("blocked", ("INVALID_ADMISSION",))
    bundles: list[CompanyResearchBundle] = []
    materials: list[RetainedSource] = []
    reasons: list[str] = []
    seen: set[str] = set()
    # One adapter instance preserves unique provider retrieval IDs across URLs.
    provider = OfficialHomepage(
        _RuntimeFetcher(session),
        schema_version=candidate.schema_version,
        clock=session.runtime.clock,
        extractor=extractor,
    )
    tool = LiveResearchCompany(
        [provider],
        run_id=admission.runtime_binding.call.run_id,
        schema_version=candidate.schema_version,
        as_of=as_of,
        clock=session.runtime.clock,
    )
    for target in targets:
        if target.url in seen:
            continue
        seen.add(target.url)
        selected = candidate.model_copy(update={"homepage_url": target.url})
        result = tool(selected, session.budget())
        session.records.extend(result.retrieval_records)
        if result.data is None:
            reasons.extend(e.error_code for e in result.errors)
            # The existing provider preserves fetched Sources on extractor failure.
            # Keep those originals, with no invented observations or successful status.
            retained = result.retrieval_records[-1].arguments_without_secrets.get(
                "retained_sources", {}
            )
            if not isinstance(retained, dict) or not retained:
                break
            bundle, _, _ = assemble_bundle(
                candidate,
                tuple(Source.model_validate(value) for value in retained.values()),
                (),
                as_of=as_of,
                schema_version=candidate.schema_version,
            )
        else:
            bundle = result.data
        bundles.append(bundle)
        for source in bundle.sources.values():
            raw = next(
                r
                for r in session.raw
                if r.content_hash == source.content_hash and r.locator == source.url
            )
            lineage = tuple(
                r.model_copy(
                    update={
                        "evidence_ids": sorted(
                            {
                                e.evidence_id
                                for e in bundle.evidence.values()
                                if any(
                                    p.retrieval_id == r.retrieval_id
                                    for p in e.provenance
                                )
                            }
                            | set(r.evidence_ids)
                        )
                    }
                )
                for r in result.retrieval_records
                if source.source_id in r.source_ids
            )
            try:
                material = admission.prepare_source(
                    bundle.model_copy(deep=True), raw, lineage
                )
                expected = source.model_copy(
                    update={"local_path": material.source.local_path}
                )
                if (
                    material.source != expected
                    or material.content != raw.content
                    or material.document.scope != "company"
                    or material.document.candidate_ids != (candidate.candidate_id,)
                    or material.retrieval_records != lineage
                    or {e.evidence_id: e for e in material.evidence}
                    != {
                        eid: e
                        for eid, e in bundle.evidence.items()
                        if e.source_id == source.source_id
                    }
                ):
                    raise CompanyResearchError("RETENTION_REVIEW_MISMATCH")
                materials.append(material)
            except (ValueError, OSError):
                reasons.append("SOURCE_RETENTION_REJECTED")
                break
        if reasons:
            break
    snapshot = None
    ingestion: Literal["not_attempted", "succeeded", "failed"] = "not_attempted"
    if materials:
        try:
            snapshot = store.ingest_sources(materials)
            ingestion = "succeeded"
        except (OSError, ValueError, SQLiteError):
            reasons.append("SOURCE_INGESTION_FAILED")
            ingestion = "failed"
    return ResearchResult(
        "failed" if reasons else "completed",
        tuple([*session.reasons, *reasons]),
        tuple(session.records),
        tuple(bundles),
        tuple(materials),
        snapshot,
        ingestion,
        tuple(session.raw),
    )


@dataclass(frozen=True, slots=True)
class NarrativeResearchResult:
    results: tuple[InformationResult, ...]
    evidence: Mapping[str, Evidence]
    sources: Mapping[str, Source]
    collection_records: tuple[RetrievalRecord, ...]
    snapshot: StoreSnapshot | None


class ReviewedAcquisitionReceipt(FrozenSettings):
    """Private operator collection opt-in plus existing exact-byte corpus review.

    Not a model output or general host grant. A changed original needs a new
    reviewed corpus document; the factory never approves observed bytes itself.
    """

    schema_version: Literal["company-acquisition-1"]
    candidate: Candidate
    as_of: ISODate
    collection_approved: bool
    corpus: CorpusManifest


def build_narrative_research_service(
    *,
    config: EffectiveCompanyReportConfig,
    store: CompanyStore,
    candidate: Candidate,
    as_of: date,
    binding: ResearchRuntimeBinding,
    admit: Callable[
        [StoreSnapshot, tuple[str, ...], tuple[RetrievalRecord, ...]],
        tuple[str, ...],
    ],
    execution_scope: Literal[
        "actual", "controlled_response", "actual_replay"
    ] = "actual",
    http_transport: httpx.BaseTransport | None = None,
    resolve: Resolver = default_resolve,
) -> "NarrativeResearchService":
    """Freeze configured acquisition before execution; missing permission denies.

    The existing provenance admit seam remains private. Transport injection is
    for controlled responses only; actual runs use the provider's credential
    resolver. Construction performs no fetch, model call or download.
    """
    config = EffectiveCompanyReportConfig.model_validate(config.model_dump())
    candidate = Candidate.model_validate_json(candidate.model_dump_json())
    if binding.budget.deadline is None:
        raise CompanyResearchError("RESEARCH_DEADLINE_REQUIRED")
    admission = None
    path = config.acquisition_receipt_path
    limits = config.research_limits
    if config.research_enabled and limits is not None and path is not None:
        try:
            content = path.read_bytes()
            # Reuse the strict config reader's duplicate/nonfinite JSON checks.
            json.loads(
                content,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_nonfinite,
            )
            receipt = ReviewedAcquisitionReceipt.model_validate_json(content)
            require_indexable(check_corpus(receipt.corpus))
            documents = receipt.corpus.documents
            if (
                not receipt.collection_approved
                or receipt.candidate != candidate
                or receipt.as_of != as_of
                or not documents
                or execution_scope == "actual_replay"
                or PROVIDER not in binding.runtime.ledger.limits.tool_max_calls
                or any(
                    doc.scope != "company"
                    or doc.candidate_ids != (candidate.candidate_id,)
                    or not doc.origin_url
                    or not same_site(doc.origin_url, candidate.homepage_url)
                    or doc.source_id
                    != snapshot_source_id(doc.origin_url, doc.content_hash)
                    or Path(doc.local_path).suffix.lower() == ".pdf"
                    for doc in documents
                )
                or len({doc.origin_url for doc in documents}) != len(documents)
            ):
                raise CompanyResearchError("RESEARCH_RECEIPT_MISMATCH")
        except (OSError, ValueError, CorpusGateError):
            receipt = None
        if receipt is not None:
            urls = tuple(doc.origin_url for doc in documents if doc.origin_url)
            profile = config.runtime_document.profiles.m2_research
            fetch = profile.fetch
            fetch_policy = FetchPolicy(
                allowed_schemes=frozenset(fetch.allowed_schemes),
                allowed_hosts=frozenset(
                    host for url in urls if (host := url_host(url)) is not None
                ),
                max_bytes=fetch.max_bytes,
                timeout_seconds=min(
                    fetch.timeout_seconds, binding.budget.timeout_seconds
                ),
                max_redirects=fetch.max_redirects,
            )
            llm_settings = config.runtime_document.llm

            def allowance_for(
                system: str, user: str, schema: type[BaseModel]
            ) -> Allowance:
                return byte_bound_allowance(
                    system,
                    user,
                    schema,
                    schema_version=binding.call.schema_version,
                    max_output_tokens=config.runtime_document.profiles.actual_v3.request_output_tokens,
                    usd_per_input_token=llm_settings.usd_per_input_token,
                    usd_per_output_token=llm_settings.usd_per_output_token,
                )

            def authorize(
                selected: Candidate,
                targets: tuple[ResearchTarget, ...],
                requested: ResearchLimits,
            ) -> bool:
                return (
                    selected == candidate
                    and requested.max_calls <= limits.max_calls
                    and requested.max_cost_usd <= limits.max_cost_usd
                    and requested.deadline_seconds <= limits.deadline_seconds
                    and all(
                        target.provider == PROVIDER and target.url in urls
                        for target in targets
                    )
                )

            def retain(
                bundle: CompanyResearchBundle,
                raw: RawSnapshot,
                records: tuple[RetrievalRecord, ...],
            ) -> RetainedSource:
                doc = next(doc for doc in documents if doc.origin_url == raw.locator)
                if raw.content_hash != doc.content_hash:
                    raise CompanyResearchError("REVIEWED_CONTENT_HASH_MISMATCH")
                source = bundle.sources[doc.source_id].model_copy(
                    update={"local_path": doc.local_path}
                )
                # The store independently verifies byte hash, exact text/excerpts,
                # reviewer approval, identity and record closure before publishing.
                chunk = Chunk(
                    schema_version=source.schema_version,
                    chunk_id=f"chunk-{source.source_id}",
                    source_id=source.source_id,
                    corpus_version=receipt.corpus.corpus_version,
                    text=raw.content.decode("utf-8"),
                    locator=raw.locator,
                    candidate_ids=[candidate.candidate_id],
                    scope="company",
                    language=source.language,
                    embedding_model=store.settings.model_id,
                    embedding_revision=store.settings.model_revision,
                )
                return RetainedSource(
                    doc,
                    source,
                    raw.content,
                    (chunk,),
                    tuple(bundle.evidence.values()),
                    records,
                )

            llm = None
            if not binding.readiness.missing:
                llm = RuntimeStructuredLLM(
                    runtime=binding.runtime,
                    call=binding.call.model_copy(
                        update={
                            "candidate_id": candidate.candidate_id,
                            "node": "company_report_research",
                        }
                    ),
                    budget=binding.budget,
                    readiness=binding.readiness,
                    transport=OpenAIResponsesAttempt(
                        api_key=(
                            resolve_environment_credential("OPENAI_API_KEY")
                            if execution_scope == "actual"
                            else None
                        ),
                        prompt_version=FACTS_PROMPT_VERSION,
                        schema_version=binding.call.schema_version,
                        clock=binding.runtime.clock,
                        http_transport=http_transport,
                        llm_settings=llm_settings,
                    ),
                    allowance_for=allowance_for,
                )
            admission = NarrativeResearchAdmission(
                runtime_binding=binding,
                as_of=as_of,
                execution_scope=execution_scope,
                candidate_json=candidate.model_dump_json(),
                approved_limits=limits,
                targets=(),
                authorize=authorize,
                prepare_source=retain,
                fetch_policy=fetch_policy,
                # Preserve existing readiness failures, including absent credentials.
                readiness=binding.readiness,
                llm=llm,
                domain_definition="Physical AI / Robotics",
                max_input_chars=profile.eligibility_max_input_chars,
                http_transport=http_transport,
                resolve=resolve,
                reviewed_corpus=receipt.corpus,
            )
    return NarrativeResearchService(
        store=store,
        candidate=candidate,
        as_of=as_of,
        run_id=binding.call.run_id,
        clock=binding.runtime.clock,
        deadline=binding.budget.deadline,
        research=config.research_enabled,
        limits=limits,
        admission=admission,
        admit=admit,
        top_k=config.runtime_document.profiles.actual_v3.retrieval_top_k,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class NarrativeResearchService:
    """Local factual retrieval with a read-only task-3 provenance admission seam.

    admit receives the retained snapshot, proposed original IDs and real search/
    acquisition records. It returns only IDs accepted by the private projection
    registry. Its shape stays private; no scored policy or registry is serialized.
    """

    store: CompanyStore
    candidate: Candidate
    as_of: date
    run_id: str
    clock: Clock
    deadline: datetime
    research: bool
    limits: ResearchLimits | None
    admission: NarrativeResearchAdmission | None
    admit: Callable[
        [StoreSnapshot, tuple[str, ...], tuple[RetrievalRecord, ...]],
        tuple[str, ...],
    ]
    top_k: int = 8

    def __post_init__(self) -> None:
        TypeAdapter(bool).validate_python(self.research, strict=True)
        if self.admission is not None and (
            self.clock is not self.admission.runtime_binding.runtime.clock
            or self.run_id != self.admission.runtime_binding.call.run_id
        ):
            raise CompanyResearchError("NARRATIVE_RUNTIME_MISMATCH")

    def resolve(
        self,
        needs: Sequence[InformationNeed],
        *,
        evidence: Mapping[str, Evidence],
        sources: Mapping[str, Source],
        snapshot: StoreSnapshot | None = None,
    ) -> NarrativeResearchResult:
        """Settle factual needs; optional failures never replace valid originals."""
        TypeAdapter(bool).validate_python(self.research, strict=True)
        needs = tuple(
            InformationNeed.model_validate_json(need.model_dump_json())
            for need in needs
        )
        if (
            len(needs) > 6
            or len({need.question_id for need in needs}) != len(needs)
            or any(
                not set(need.related_evidence_ids) <= evidence.keys() for need in needs
            )
        ):
            raise CompanyResearchError("INVALID_INFORMATION_NEEDS")
        selected = snapshot if snapshot is not None else self.store.open()
        original_evidence = {
            key: Evidence.model_validate_json(item.model_dump_json())
            for key, item in evidence.items()
        }
        original_sources = {
            key: Source.model_validate_json(item.model_dump_json())
            for key, item in sources.items()
        }
        if (
            any(key != item.evidence_id for key, item in original_evidence.items())
            or any(key != item.source_id for key, item in original_sources.items())
            or any(item.source_id not in original_sources for item in evidence.values())
            or (selected is None and (original_evidence or original_sources))
        ):
            raise CompanyResearchError("NARRATIVE_INPUT_CLOSURE")
        records = list(selected.manifest.retrieval_records.values()) if selected else []
        if selected is not None:
            manifest = selected.manifest
            if any(
                manifest.evidence.get(key) != item
                for key, item in original_evidence.items()
            ) or any(
                manifest.sources.get(key) != item
                for key, item in original_sources.items()
            ):
                raise CompanyResearchError("NARRATIVE_ORIGINALS_MISMATCH")
        results = []
        for need in needs:
            reason: Literal[
                "not_found",
                "research_disabled",
                "not_authorized",
                "budget_exhausted",
                "deadline_exceeded",
                "acquisition_failed",
                "evidence_rejected",
            ] = "not_found"
            admitted: tuple[str, ...] = ()
            started = self.clock.now()
            effective_deadline = self.deadline
            if self.admission is not None:
                effective_deadline = min(effective_deadline, self.admission.deadline)
                if self.limits is not None:
                    effective_deadline = min(
                        effective_deadline,
                        self.admission.started_at
                        + timedelta(seconds=self.limits.deadline_seconds),
                    )
            if started >= effective_deadline:
                reason = "deadline_exceeded"
            else:
                try:
                    hits = self.store.search_local(
                        need.information_needed,
                        top_k=self.top_k,
                        timeout_seconds=(effective_deadline - started).total_seconds(),
                        snapshot=selected,
                    )
                    ids = tuple(
                        sorted(
                            {
                                item.evidence_id
                                for hit in hits
                                for item in hit.original_evidence
                            }
                        )
                    )
                    if selected is not None:
                        manifest = selected.manifest
                        record = RetrievalRecord(
                            schema_version=self.candidate.schema_version,
                            retrieval_id=f"{self.run_id}-local-{len(records)}",
                            run_id=self.run_id,
                            candidate_id=self.candidate.candidate_id,
                            tool_name="company-store-retrieval",
                            query=need.information_needed,
                            arguments_without_secrets={
                                "corpus_version": manifest.version,
                                "index_version": manifest.index_metadata.index_version,
                                "as_of": self.as_of.isoformat(),
                                "original_evidence_hashes": {
                                    eid: evidence_fingerprint(
                                        manifest.evidence[eid],
                                        manifest.sources[
                                            manifest.evidence[eid].source_id
                                        ],
                                    )
                                    for eid in ids
                                },
                            },
                            started_at=started,
                            finished_at=self.clock.now(),
                            status="ok" if ids else "empty",
                            source_ids=sorted(
                                {manifest.evidence[eid].source_id for eid in ids}
                                | {hit.source.source_id for hit in hits}
                            ),
                            chunk_ids=sorted({hit.chunk.chunk_id for hit in hits}),
                            evidence_ids=list(ids),
                            cache_hit=True,
                        )
                        records.append(record)
                        try:
                            admitted = self._admit(selected, ids, tuple(records))
                        except ValueError:
                            reason = "evidence_rejected"
                    if ids and not admitted:
                        reason = "evidence_rejected"
                except (OSError, ValueError, SQLiteError):
                    reason = "acquisition_failed"
                    records.append(
                        RetrievalRecord(
                            schema_version=self.candidate.schema_version,
                            retrieval_id=f"{self.run_id}-local-{len(records)}",
                            run_id=self.run_id,
                            candidate_id=self.candidate.candidate_id,
                            tool_name="company-store-retrieval",
                            query=need.information_needed,
                            arguments_without_secrets={"as_of": self.as_of.isoformat()},
                            started_at=started,
                            finished_at=self.clock.now(),
                            status="failed",
                            source_ids=[],
                            chunk_ids=[],
                            evidence_ids=[],
                            cache_hit=False,
                        )
                    )
                if self.clock.now() >= effective_deadline:
                    admitted = ()
                    reason = "deadline_exceeded"
                if not admitted and reason != "deadline_exceeded":
                    if not self.research:
                        if reason == "not_found":
                            reason = "research_disabled"
                    elif self.admission is None or self.limits is None:
                        reason = "not_authorized"
                    else:
                        prompt = build_user_prompt(
                            original_evidence, original_sources, as_of=self.as_of
                        )
                        acquired = research_target(
                            self.candidate,
                            original_evidence,
                            original_sources,
                            as_of=self.as_of,
                            research=True,
                            limits=ResearchLimits(
                                max_calls=self.limits.max_calls,
                                max_cost_usd=self.limits.max_cost_usd,
                                deadline_seconds=min(
                                    self.limits.deadline_seconds,
                                    (
                                        self.deadline - self.admission.started_at
                                    ).total_seconds(),
                                ),
                            ),
                            freshness=FreshnessAssessment(
                                self.as_of,
                                hashlib.sha256(prompt.encode()).hexdigest(),
                                (),
                            ),
                            gaps=(need,),
                            admission=self.admission,
                            store=self.store,
                        )
                        records.extend(acquired.collection_records)
                        match acquired.status:
                            case "blocked":
                                reason = "not_authorized"
                            case "failed":
                                reason = "acquisition_failed"
                                if {
                                    "RESEARCH_CAP_EXHAUSTED",
                                    "BUDGET_EXHAUSTED",
                                }.intersection(acquired.reason_codes):
                                    reason = "budget_exhausted"
                                if self.clock.now() >= effective_deadline:
                                    reason = "deadline_exceeded"
                            case "completed":
                                reason = "not_found"
                            case "not_requested":
                                reason = "research_disabled"
                            case unreachable:
                                assert_never(unreachable)
                        if self.clock.now() >= effective_deadline:
                            reason = "deadline_exceeded"
                        elif acquired.snapshot is not None:
                            proposed = tuple(
                                sorted(
                                    {
                                        item.evidence_id
                                        for material in acquired.materials
                                        for item in material.evidence
                                    }
                                )
                            )
                            try:
                                admitted = self._admit(
                                    acquired.snapshot, proposed, tuple(records)
                                )
                            except (ValueError, OSError, SQLiteError):
                                admitted = ()
                            if admitted:
                                selected = acquired.snapshot
                            elif proposed:
                                reason = "evidence_rejected"
            if admitted and selected is not None:
                manifest = selected.manifest
                for eid in admitted:
                    item = manifest.evidence[eid]
                    original_evidence[eid] = item
                    original_sources[item.source_id] = manifest.sources[item.source_id]
            results.append(
                InformationResult(
                    schema_version="narrative-report-1",
                    question_id=need.question_id,
                    status="provided" if admitted else "unavailable",
                    evidence_ids=list(admitted),
                    reason=None if admitted else reason,
                    detail="Admitted factual evidence."
                    if admitted
                    else "No admitted factual evidence.",
                )
            )
        return NarrativeResearchResult(
            tuple(results),
            original_evidence,
            original_sources,
            tuple(records),
            selected,
        )

    def _admit(
        self,
        snapshot: StoreSnapshot,
        ids: tuple[str, ...],
        records: tuple[RetrievalRecord, ...],
    ) -> tuple[str, ...]:
        manifest = snapshot.manifest
        if not set(ids) <= manifest.evidence.keys():
            raise CompanyResearchError("NARRATIVE_UNKNOWN_ORIGINAL")
        eligible = []
        for eid in ids:
            item = manifest.evidence[eid]
            if (
                (
                    (
                        item.scope == "company"
                        and item.candidate_id == self.candidate.candidate_id
                    )
                    or (item.scope == "industry" and item.candidate_id is None)
                )
                and check_as_of(manifest.sources[item.source_id], self.as_of).admitted
                and (item.event_date is None or item.event_date <= self.as_of)
            ):
                eligible.append(eid)
        accepted = self.admit(snapshot, tuple(eligible), records) if eligible else ()
        if len(set(accepted)) != len(accepted) or not set(accepted) <= set(eligible):
            raise CompanyResearchError("NARRATIVE_ADMISSION_IDS_MISMATCH")
        if any(
            not set(manifest.evidence[eid].supporting_evidence_ids) <= set(accepted)
            for eid in accepted
        ):
            raise CompanyResearchError("NARRATIVE_SUPPORT_CLOSURE")
        return accepted
