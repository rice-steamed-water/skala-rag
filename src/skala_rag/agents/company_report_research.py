"""Explicit target research over the existing provider, runtime and store.

Freshness proposals and requested limits never grant authority. The operator
supplies a non-serializable ResearchAdmission, binding exact URLs and the resolved
candidate to the existing ActualAdmissionV3. There is no discovery, free-text
search, competitor fetching, implicit approval, or collection retry here.
"""

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal, localcontext
from sqlite3 import Error as SQLiteError
from typing import Final, Literal, TypeVar, assert_never

import httpx
from pydantic import BaseModel, TypeAdapter

from skala_rag.agents.eligibility_extraction import LLMEligibilityExtractor
from skala_rag.contracts import (
    Candidate,
    Evidence,
    RetrievalRecord,
    Source,
    ToolBudget,
)
from skala_rag.contracts.company_report import ResearchLimits
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import StructuredLLM
from skala_rag.contracts.tools import CompanyResearchBundle
from skala_rag.prompt.company_report_freshness import (
    SYSTEM_PROMPT,
    FreshnessJudgment,
    FreshnessOutput,
    GapQuery,
    build_user_prompt,
)
from skala_rag.rag.company_store import (
    CompanyStore,
    RetainedSource,
    StoreSnapshot,
)
from skala_rag.scoring.approved_consumers import ActualAdmissionV3
from skala_rag.settings import LLMSettings
from skala_rag.tools.company_research import (
    LiveResearchCompany,
    assemble_bundle,
    same_site,
)
from skala_rag.tools.official_homepage import OfficialHomepage
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt
from skala_rag.tools.provider_scope import EXCLUDED_PROVIDERS
from skala_rag.tools.runtime import (
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
    query: GapQuery
    url: str
    provider: str = PROVIDER


@dataclass(frozen=True, slots=True, kw_only=True)
class ResearchAdmission:
    """Operator-owned Python inputs, never constructed from model/config JSON.

    authorize resolves collection permission for this exact candidate, target set
    and effective caps. It is checked again immediately before each dispatch.
    prepare_source supplies reviewed retention descriptors, not new observations.
    The existing actual admission owns the unchanged runtime and shared ledger.
    """

    actual: ActualAdmissionV3
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
        admission: ResearchAdmission,
        candidate: Candidate,
        limits: ResearchLimits,
        targets: tuple[ResearchTarget, ...],
    ) -> None:
        self.admission = admission
        self.candidate = candidate
        self.limits = limits
        self.targets = targets
        self.runtime = admission.actual.runtime_binding.runtime
        self.start = self.runtime.ledger.snapshot()
        deadline = admission.actual.runtime_binding.budget.deadline
        if deadline is None:
            raise CompanyResearchError("RESEARCH_DEADLINE_REQUIRED")
        self.deadline = min(
            deadline,
            self.runtime.clock.now() + timedelta(seconds=limits.deadline_seconds),
        )
        self.records: list[RetrievalRecord] = []
        self.raw: list[RawSnapshot] = []
        self.reasons: list[str] = []

    def check(self, allowance: Allowance) -> None:
        self.admission.actual.load_policy()
        source = self.admission.actual.source
        if source.live_gate_verifier is None or source.live_gates is None:
            raise CompanyResearchError("RESEARCH_ADMISSION_DENIED")
        for gate in ("runtime_readiness", "call_budget", "cost_budget"):
            if source.live_gate_verifier(gate, source.live_gates) is not True:
                raise CompanyResearchError("RESEARCH_ADMISSION_DENIED")
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
        original = self.admission.actual.runtime_binding.budget
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
        schema = admission.actual.run_input.schema_version
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
                run_id=admission.actual.runtime_binding.gates.run_id,
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
    admission: ResearchAdmission,
    candidate: Candidate,
    as_of: date,
    limits: ResearchLimits,
    queries: Sequence[GapQuery],
) -> tuple[ResearchTarget, ...]:
    if type(admission) is not ResearchAdmission or type(admission.actual) is not (
        ActualAdmissionV3
    ):
        raise CompanyResearchError("TRUSTED_RESEARCH_ADMISSION_REQUIRED")
    actual = admission.actual
    actual.load_policy()
    if (
        Candidate.model_validate_json(admission.candidate_json) != candidate
        or as_of != actual.run_input.as_of
        or actual.runtime_binding.call.candidate_id
        not in (None, candidate.candidate_id)
    ):
        raise CompanyResearchError("RESEARCH_IDENTITY_MISMATCH")
    approved = ResearchLimits.model_validate_json(
        admission.approved_limits.model_dump_json()
    )
    ledger_limits = actual.runtime_binding.runtime.ledger.limits
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
        or actual.runtime_binding.runtime.policy.retry_delays_seconds
    ):
        raise CompanyResearchError("RESEARCH_REQUIRES_SINGLE_ATTEMPTS")
    match actual.execution_scope:
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
    if any(
        q.scope != "company" or q.candidate_id != candidate.candidate_id
        for q in queries
    ):
        raise CompanyResearchError("COMPETITOR_QUERY_DENIED")
    targets = tuple(t for t in admission.targets if t.query in queries)
    if set(queries) != {t.query for t in targets}:
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
    actual = admission.actual
    binding = actual.runtime_binding
    if (
        type(llm) is not RuntimeStructuredLLM
        or llm.runtime is not session.runtime
        or llm.call.run_id != binding.gates.run_id
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
    match actual.execution_scope:
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
    gaps: Sequence[GapQuery] = (),
    admission: ResearchAdmission | None,
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
        queries = tuple(
            dict.fromkeys(
                [*gaps, *(q for j in freshness.judgments for q in j.gap_queries)]
            )
        )
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
        run_id=admission.actual.runtime_binding.gates.run_id,
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
