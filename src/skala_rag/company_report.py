"""One admitted company, retained originals, and an honest persisted outcome.

Operator callbacks are Python inputs, never configuration or model-authored
approvals. Retained retrieval receipts keep their original run identities.
"""

from collections.abc import Mapping
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from sqlite3 import Error as SQLiteError
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx
from pydantic import TypeAdapter

from skala_rag.agents.company_report_research import (
    CompanyResearchError,
    ResearchAdmission,
    _Session,
    assess_freshness,
    research_target,
)
from skala_rag.agents.eligibility import check_eligibility
from skala_rag.contracts import (
    Candidate,
    CompanyProfile,
    EvaluationSnapshot,
    EvidenceProvenance,
    RetrievalRecord,
)
from skala_rag.contracts.company_report import (
    CompanyReportConfigError,
    CompanyReportReceipt,
    CompanyReportRequest,
    EffectiveCompanyReportConfig,
    ResearchLimits,
    load_company_report_config,
)
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.ids import snapshot_id
from skala_rag.contracts.interfaces import LLMError
from skala_rag.contracts.v3 import BRANCH_DIMENSIONS
from skala_rag.graph.actual_inputs_v3 import (
    ActualAuthorityV3,
    ActualInputError,
    CompanyReportAuthorityV3,
    CompanyReportReadmissionV3,
    IdentityResearchAdmissionV3,
    canonical,
    digest,
    verify_sources,
)
from skala_rag.graph.actual_reviews_v3 import _Reviews
from skala_rag.graph.actual_runner_v3 import Branch, evaluate_actual_branch_v3
from skala_rag.graph.candidate_workflow_v3 import run_candidate_workflow_v3
from skala_rag.graph.candidates_v3 import CandidateStagesV3
from skala_rag.prompt.company_report_freshness import GapQuery
from skala_rag.prompt.versions import ACTUAL_COMPOSITION_VERSION
from skala_rag.rag.company_store import (
    CompanyIdentity,
    CompanyStore,
    StoreSnapshot,
    ValidatedReport,
    _evidence_closure,
    evidence_fingerprint,
)
from skala_rag.reporting.company_context import (
    build_company_report_context,
    resolve_company_evidence,
)
from skala_rag.reporting.pdf import (
    PDFLayoutValidator,
    PdfProfile,
    PDFRenderer,
    load_pdf_profile,
)
from skala_rag.reporting.v3_pipeline import (
    ReportGeneratorV3,
    SemanticJudgeV3,
    run_report_v3,
)
from skala_rag.scoring.approved_consumers import ActualAdmissionV3
from skala_rag.scoring.catalog import ScoringPolicy, load_policy
from skala_rag.scoring.v3_policy import V3Policy, load_v3_policy
from skala_rag.settings import RuntimeDocument, resolve_environment_credential
from skala_rag.tools.company_research import (
    LiveResearchCompany,
    normalize_identifier,
    url_host,
)
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt, byte_bound_allowance
from skala_rag.tools.opendart import OpenDartCompany
from skala_rag.tools.runtime import Allowance, AttemptResponse, Usage
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM
from skala_rag.tools.source_fetch import (
    FetchError,
    FetchRejection,
    RawSnapshot,
    SafeFetcher,
    default_resolve,
)


def _matches(request: CompanyReportRequest, candidate: Candidate) -> bool:
    """Exact normalized names and all supplied identifiers; never fuzzy guessing."""
    name = " ".join(request.company_name.casefold().split())
    names = {
        " ".join(value.casefold().split())
        for value in (candidate.canonical_name, *candidate.aliases)
    }
    identifiers = {
        key.casefold(): normalize_identifier(value)
        for key, value in candidate.legal_identifiers.items()
    }
    return (
        name in names
        and (
            request.homepage_url is None
            or url_host(request.homepage_url) == url_host(candidate.homepage_url)
        )
        and all(
            identifiers.get(key.casefold()) == normalize_identifier(value)
            for key, value in request.legal_identifiers.items()
        )
    )


def _snapshot(
    retained: StoreSnapshot,
    candidate: Candidate,
    chunk_ids: tuple[str, ...],
    actual: ActualAdmissionV3,
) -> EvaluationSnapshot:
    """Record a current local reread; keep historic claims/receipts in the store."""
    run = actual.run_input
    manifest = retained.manifest
    resolved = resolve_company_evidence(
        retained,
        chunk_ids,
        candidate_id=candidate.candidate_id,
        as_of=run.as_of,
        current_report_id=f"company-report-{actual.runtime_binding.gates.run_id}",
    )
    evidence = {e.evidence_id: e for e in resolved.evidence}
    superseded = {e.supersedes for e in evidence.values() if e.supersedes}
    # Exclude descendants of corrected facts rather than retaining stale support.
    excluded = set(superseded)
    while True:
        more = {
            eid
            for eid, e in evidence.items()
            if any(
                s in excluded or s not in evidence for s in e.supporting_evidence_ids
            )
        }
        if more <= excluded:
            break
        excluded.update(more)
    evidence = {eid: e for eid, e in evidence.items() if eid not in excluded}
    sources = {e.source_id: manifest.sources[e.source_id] for e in evidence.values()}
    chunks = {}
    retrieval_id = f"{actual.runtime_binding.gates.run_id}:retained"
    for eid, item in evidence.items():
        pieces = [
            c
            for c in manifest.chunks.values()
            if c.source_id == item.source_id
            and c.locator == item.locator
            and item.excerpt in c.text
            and c.scope == item.scope
        ]
        if not pieces:
            raise ActualInputError("ORIGINAL_CHUNK_MISSING")
        piece = sorted(pieces, key=lambda c: c.chunk_id)[0]
        chunks[piece.chunk_id] = piece
        evidence[eid] = item.model_copy(
            update={
                "provenance": [
                    EvidenceProvenance(
                        schema_version=run.schema_version,
                        retrieval_id=retrieval_id,
                        method="rag",
                        chunk_id=piece.chunk_id,
                    )
                ]
            }
        )
    now = actual.runtime_binding.runtime.clock.now()
    record = RetrievalRecord(
        schema_version=run.schema_version,
        retrieval_id=retrieval_id,
        run_id=actual.runtime_binding.gates.run_id,
        candidate_id=candidate.candidate_id,
        tool_name="company-store-retrieval",
        query=candidate.canonical_name,
        arguments_without_secrets={
            "corpus_version": manifest.version,
            "index_version": manifest.index_metadata.index_version,
            "as_of": run.as_of.isoformat(),
            "original_evidence_hashes": {
                eid: evidence_fingerprint(
                    manifest.evidence[eid], sources[item.source_id]
                )
                for eid, item in evidence.items()
            },
        },
        started_at=now,
        finished_at=now,
        status="ok",
        source_ids=sorted(sources),
        chunk_ids=sorted(chunks),
        evidence_ids=sorted(evidence),
        error_id=None,
        cost=None,
        cache_hit=True,
    )
    return EvaluationSnapshot(
        schema_version=run.schema_version,
        run_id=actual.runtime_binding.gates.run_id,
        candidate_id=candidate.candidate_id,
        snapshot_id=snapshot_id(
            actual.runtime_binding.gates.run_id,
            candidate.candidate_id,
            1,
            0,
            run.policy_version,
        ),
        evaluation_round=1,
        evidence_revision=0,
        policy_version=run.policy_version,
        corpus_version=manifest.version,
        index_version=manifest.index_metadata.index_version,
        as_of=run.as_of,
        evidence_ids=sorted(evidence),
        evidence=evidence,
        sources=sources,
        chunks=chunks,
        retrieval_records={retrieval_id: record},
    )


def run_company_report(
    company_name: str,
    *,
    config_path: Path,
    output_dir: Path,
    research: bool | None = None,
    homepage_url: str | None = None,
    legal_identifiers: Mapping[str, str] | None = None,
    as_of: date | None = None,
    research_limits: Mapping[str, object] | None = None,
    runtime_document: RuntimeDocument | None = None,
    actual_admission: ActualAdmissionV3 | None = None,
    authority: ActualAuthorityV3 | None = None,
) -> Path:
    """Return a directory with run-result.json, including expected refusals.

    Invalid configuration/types raise before output writes. Missing authority,
    readiness, provider/review failure and validation rejection are receipts.
    Neither completion nor automatic local retention grants publication approval.
    """
    request = CompanyReportRequest(
        schema_version="company-report-request-1",
        company_name=company_name,
        homepage_url=homepage_url,
        legal_identifiers={} if legal_identifiers is None else legal_identifiers,
        as_of=as_of,
    )
    out = TypeAdapter(Path).validate_python(output_dir, strict=True).resolve()
    if out.exists():
        raise FileExistsError(out)
    if actual_admission is not None and type(actual_admission) is not ActualAdmissionV3:
        raise CompanyReportConfigError("ACTUAL_ADMISSION_TYPE")
    if authority is not None and type(authority) is not ActualAuthorityV3:
        raise CompanyReportConfigError("ACTUAL_AUTHORITY_TYPE")
    if authority is not None and not all(
        callable(callback)
        for callback in (
            authority.live_gate_verifier,
            authority.reviews_for,
            authority.evaluation_inputs_for,
            authority.support_check,
            authority.applicability_assessments,
            authority.applicability_check,
        )
    ):
        raise CompanyReportConfigError("AUTHORITY_CALLBACK_REQUIRED")
    missing_caps = False
    try:
        config = load_company_report_config(
            config_path,
            research=research,
            research_limits=research_limits,
            runtime_document=runtime_document,
        )
    except CompanyReportConfigError as exc:
        if exc.code != "RESEARCH_CAPS_REQUIRED":
            raise
        missing_caps = True
        config = load_company_report_config(
            config_path, research=False, runtime_document=runtime_document
        )
    catalog = load_policy(config.catalog_path, execution_mode="fixture")
    pdf_profile = load_pdf_profile(
        Path(__file__).resolve().parents[2] / "configs/pdf.layout.v1.json"
    )
    # Validate the policy file even for an eventual identity/readiness refusal.
    policy = load_v3_policy(config.policy_path, execution_mode="fixture")
    if {c.criterion_id: (c.dimension, c.weight) for c in catalog.criteria} != {
        c.criterion_id: (c.dimension, c.weight) for c in policy.criteria
    }:
        raise CompanyReportConfigError("CATALOG_POLICY_MISMATCH")
    if authority is not None and authority.company_report is not None:
        if type(
            authority.company_report
        ) is not CompanyReportAuthorityV3 or not callable(
            authority.company_report.profile_for
        ):
            raise CompanyReportConfigError("COMPANY_AUTHORITY_TYPE")
        if any(
            callback is not None and not callable(callback)
            for callback in (
                authority.company_report.research_for,
                authority.company_report.identity_research,
                authority.company_report.identity_review,
                authority.company_report.readmit_after_research,
            )
        ):
            raise CompanyReportConfigError("AUTHORITY_CALLBACK_REQUIRED")
    return _run_prepared_company_report(
        request,
        config,
        out,
        missing_caps=missing_caps,
        policy=policy,
        catalog=catalog,
        pdf_profile=pdf_profile,
        actual_admission=actual_admission,
        authority=authority,
    )


def _run_prepared_company_report(
    request: CompanyReportRequest,
    config: EffectiveCompanyReportConfig,
    out: Path,
    *,
    missing_caps: bool,
    policy: V3Policy,
    catalog: ScoringPolicy,
    pdf_profile: PdfProfile,
    actual_admission: ActualAdmissionV3 | None,
    authority: ActualAuthorityV3 | None,
) -> Path:
    """Compose admitted input/research with existing candidate/report stages.

    This private orchestration owns receipts and local intake only; the existing
    candidate workflow owns evaluation promotion/scoring/selection, and the
    report pipeline owns validation, bounded repair and PDF acceptance.
    """
    document = config.runtime_document
    effective = config.model_dump(mode="json")
    if missing_caps:
        effective["research_enabled"] = True
    enabled = config.research_enabled or missing_caps
    effective_hash = digest(canonical(effective))
    actual = actual_admission
    run_id = actual.runtime_binding.gates.run_id if actual else f"company-{uuid4().hex}"
    report_id = f"company-report-{run_id}"
    cutoff = request.as_of or (actual.run_input.as_of if actual else date.today())
    receipt = dict(
        schema_version="company-report-result-1",
        run_id=run_id,
        company_name=request.company_name,
        candidate_id=None,
        matching_candidate_ids=(),
        as_of=cutoff,
        effective_config_hash=effective_hash,
        research_requested=enabled,
        collection_records=(),
        collection_cost_usd=Decimal(0),
        selected_index_versions=(),
        eligibility_status="not_checked",
        outcome="failed",
        report_path=None,
        report_validation="not_run",
        validation_receipt_hashes=(),
        publication_allowed=False,
        ingestion_status="not_attempted",
        reason_codes=(),
    )
    out.mkdir(parents=True, exist_ok=False)

    def write(name, value) -> None:
        (out / name).write_bytes(canonical(value))

    write("effective-config.json", effective)
    write("request.json", request.model_dump(mode="json"))
    write(
        "execution-scope.json",
        {
            "execution_scope": actual.execution_scope if actual else None,
            "synthetic": actual is not None
            and actual.execution_scope == "controlled_response",
            "actual_qa_established": False,
            "publication_allowed": False,
        },
    )
    models: list[RuntimeStructuredLLM] = []
    collection_records: list[RetrievalRecord] = []
    versions: list[str] = []

    def finish(outcome, reasons=()) -> Path:
        receipt.update(outcome=outcome, reason_codes=tuple(reasons))
        costs = [r.cost for r in collection_records]
        known_costs = [c for c in costs if c is not None and c.currency == "USD"]
        receipt["collection_cost_usd"] = (
            sum((Decimal(str(c.value)) for c in known_costs), Decimal(0))
            if len(known_costs) == len(costs)
            else None
        )
        receipt["collection_records"] = tuple(collection_records)
        receipt["selected_index_versions"] = tuple(dict.fromkeys(versions))
        parsed = CompanyReportReceipt.model_validate(receipt)
        write("run-result.json", parsed.model_dump(mode="json"))
        if actual is not None:
            write("ledger.json", actual.runtime_binding.runtime.ledger.snapshot())
        write(
            "analysis-records.json",
            [
                record.model_dump(mode="json")
                for model in models
                for record in model.retrieval_records
            ],
        )
        return out

    if missing_caps:
        return finish("research_blocked", ("RESEARCH_CAPS_REQUIRED",))
    try:
        store = CompanyStore.from_local(
            config.store_dir,
            model_path=config.model_path,
            receipt_path=config.model_receipt_path,
        )
        before = retained = store.open()
    except (OSError, ValueError, KeyError, SQLiteError):
        return finish("failed", ("LOCAL_STORE_NOT_READY",))
    if before is not None:
        versions.append(before.manifest.version)
    matches = (
        [
            item.candidate
            for item in before.manifest.companies.values()
            if _matches(request, item.candidate)
        ]
        if before is not None
        else []
    )
    receipt["matching_candidate_ids"] = tuple(sorted(c.candidate_id for c in matches))
    if len(matches) > 1:
        return finish("identity_ambiguous", ("MULTIPLE_IDENTITIES",))
    if not matches and not enabled:
        return finish("identity_unknown", ("NO_STORED_IDENTITY",))
    if matches:
        receipt["candidate_id"] = matches[0].candidate_id
    if actual is None or authority is None:
        return finish("research_blocked", ("ACTUAL_AUTHORITY_REQUIRED",))
    options = authority.company_report
    if options is None:
        return finish("research_blocked", ("COMPANY_INPUTS_REQUIRED",))
    binding = actual.runtime_binding
    runtime = binding.runtime
    ledger = runtime.ledger
    initial_ledger = runtime.ledger.snapshot()
    started = runtime.clock.now()
    try:
        approved = actual.load_policy(require_capacity=True)
        if (
            Path(actual.source.path).resolve() != config.policy_path
            or approved.operational != policy
            or cutoff != actual.run_input.as_of
            or authority.live_gate_verifier is not actual.source.live_gate_verifier
        ):
            raise ActualInputError("RUN_AUTHORITY_IDENTITY_MISMATCH")
        for gate in ("runtime_readiness", "call_budget", "cost_budget"):
            if authority.live_gate_verifier(gate, binding.gates) is not True:
                raise ActualInputError("RUNTIME_AUTHORITY_DENIED")
    except ValueError:
        return finish("research_blocked", ("RUN_AUTHORITY_IDENTITY_MISMATCH",))
    if enabled and config.research_limits is not None:
        limits = config.research_limits
        ceilings = runtime.ledger.limits
        if (
            limits.max_calls > ceilings.max_calls
            or ceilings.max_cost_usd is None
            or limits.max_cost_usd > ceilings.max_cost_usd
            or binding.budget.deadline is None
            or limits.deadline_seconds
            > (binding.budget.deadline - started).total_seconds()
        ):
            return finish("research_blocked", ("RESEARCH_CAPS_EXCEED_AUTHORITY",))

    research_rejection: str | None = None

    def model(role: str, cid: str) -> RuntimeStructuredLLM:
        if enabled and role == "freshness":
            remaining_limits()
        actual.load_policy(require_capacity=True)
        key = (
            resolve_environment_credential("OPENAI_API_KEY")
            if actual.execution_scope == "actual"
            else None
        )
        if actual.execution_scope == "actual" and not key:
            raise ActualInputError("OPENAI_CREDENTIAL_REQUIRED")
        attempt = OpenAIResponsesAttempt(
            api_key=key,
            prompt_version=ACTUAL_COMPOSITION_VERSION,
            schema_version=actual.run_input.schema_version,
            clock=runtime.clock,
            llm_settings=document.llm,
        )
        # Controlled tests inject only the wire, never a fake evaluator/model.
        if (
            actual.execution_scope == "actual" and attempt._http_transport is not None
        ) or (
            actual.execution_scope != "actual"
            and type(attempt._http_transport) is not httpx.MockTransport
        ):
            raise ActualInputError("TRANSPORT_SCOPE_MISMATCH")

        def allowance_for(system, user, schema):
            nonlocal research_rejection
            allowance = byte_bound_allowance(
                system,
                user,
                schema,
                schema_version=actual.run_input.schema_version,
                max_output_tokens=document.profiles.actual_v3.request_output_tokens,
                usd_per_input_token=document.llm.usd_per_input_token,
                usd_per_output_token=document.llm.usd_per_output_token,
            )
            if enabled and role == "freshness":
                try:
                    remaining = remaining_limits()
                    if (
                        allowance.max_cost_usd is None
                        or allowance.max_cost_usd > remaining.max_cost_usd
                    ):
                        raise ActualInputError("RESEARCH_CAP_EXHAUSTED")
                except ActualInputError as exc:
                    # The existing LLM bridge intentionally redacts allowance
                    # exceptions. Preserve this controller's own bounded reason.
                    research_rejection = exc.code
                    raise
            return allowance

        budget = binding.budget
        if enabled and role == "freshness" and config.research_limits is not None:
            budget = budget.model_copy(
                update={
                    "deadline": started
                    + timedelta(seconds=config.research_limits.deadline_seconds)
                }
            )
        wrapped = RuntimeStructuredLLM(
            runtime=runtime,
            call=binding.call.model_copy(
                update={
                    "call_id": f"{run_id}:{cid}:{role}",
                    "candidate_id": cid,
                    "node": f"{role}_evaluation" if role in BRANCH_DIMENSIONS else role,
                }
            ),
            budget=budget,
            readiness=binding.readiness,
            transport=attempt,
            allowance_for=allowance_for,
        )
        models.append(wrapped)
        return wrapped

    def remaining_limits() -> ResearchLimits:
        limits = config.research_limits
        if limits is None:
            raise ActualInputError("RESEARCH_CAPS_REQUIRED")
        used = runtime.ledger.snapshot()
        if (
            used["cost_usd_accounted"] is None
            or initial_ledger["cost_usd_accounted"] is None
        ):
            raise ActualInputError("RESEARCH_COST_UNKNOWN")
        calls = limits.max_calls - (used["calls"] - initial_ledger["calls"])
        cost = limits.max_cost_usd - (
            Decimal(used["cost_usd_accounted"])
            - Decimal(initial_ledger["cost_usd_accounted"])
        )
        seconds = (
            limits.deadline_seconds - (runtime.clock.now() - started).total_seconds()
        )
        if calls <= 0 or cost < 0 or seconds <= 0:
            raise ActualInputError("RESEARCH_CAP_EXHAUSTED")
        return ResearchLimits(
            max_calls=calls, max_cost_usd=cost, deadline_seconds=seconds
        )

    def collect_identity(candidate, supplied):
        nonlocal retained
        admission = supplied.collection
        if type(admission) is not ResearchAdmission or admission.actual is not actual:
            raise ActualInputError("RESEARCH_ADMISSION_REQUIRED")
        limits = remaining_limits()
        approved_limits = admission.approved_limits
        if (
            Candidate.model_validate_json(admission.candidate_json) != candidate
            or limits.max_calls > approved_limits.max_calls
            or limits.max_cost_usd > approved_limits.max_cost_usd
            or limits.deadline_seconds > approved_limits.deadline_seconds
            or candidate.country != "KR"
            or not candidate.legal_identifiers
            or not supplied.api_key
            or not admission.targets
            or admission.fetch_policy.max_redirects != 0
            or runtime.policy.retry_delays_seconds
            or any(
                t.provider != "opendart-company"
                or t.query.field != "identity"
                or t.query.candidate_id != candidate.candidate_id
                or t.query.scope != "company"
                or url_host(t.url) != "opendart.fss.or.kr"
                for t in admission.targets
            )
        ):
            raise ActualInputError("IDENTITY_RESEARCH_SCOPE_MISMATCH")
        if (
            (
                actual.execution_scope == "actual"
                and (
                    admission.http_transport is not None
                    or admission.resolve is not default_resolve
                )
            )
            or (
                actual.execution_scope == "controlled_response"
                and type(admission.http_transport) is not httpx.MockTransport
            )
            or actual.execution_scope == "actual_replay"
        ):
            raise ActualInputError("IDENTITY_TRANSPORT_MISMATCH")
        session = _Session(admission, candidate, limits, admission.targets)

        class IdentityFetcher(SafeFetcher):
            def fetch(self, url: str) -> RawSnapshot:
                parts = urlsplit(url)
                shown = urlunsplit(
                    parts._replace(
                        query=urlencode(
                            sorted(
                                (k, v)
                                for k, v in parse_qsl(parts.query)
                                if k != "crtfc_key"
                            )
                        )
                    )
                )
                allowance = Allowance(
                    schema_version=candidate.schema_version,
                    input_tokens=0,
                    output_tokens=0,
                    max_cost_usd=Decimal(0),
                )
                try:
                    session.check(allowance)
                except CompanyResearchError as exc:
                    raise ActualInputError(exc.code) from None
                if shown not in {t.url for t in admission.targets}:
                    raise ActualInputError("IDENTITY_URL_NOT_ADMITTED")

                class Attempt:
                    retry_owner: Literal["runtime"] = "runtime"

                    def __call__(self, *, timeout_seconds):
                        raw = SafeFetcher(
                            replace(
                                admission.fetch_policy, timeout_seconds=timeout_seconds
                            ),
                            clock=runtime.clock,
                            transport=admission.http_transport,
                            resolve=admission.resolve,
                        ).fetch(url)
                        raw = replace(raw, requested=shown, locator=shown, redirects=())
                        session.raw.append(raw)
                        return AttemptResponse[RawSnapshot](
                            schema_version=candidate.schema_version,
                            status="ok",
                            data=raw,
                            source_ids=[],
                            chunk_ids=[],
                            evidence_ids=[],
                            usage=Usage(
                                schema_version=candidate.schema_version,
                                input_tokens=0,
                                output_tokens=0,
                                cost_usd=None,
                            ),
                        )

                response = runtime.execute(
                    binding.call.model_copy(
                        update={
                            "call_id": f"{run_id}:identity:{len(session.records)}",
                            "candidate_id": candidate.candidate_id,
                            "tool_name": "opendart-company",
                            "node": "company_identity",
                        }
                    ),
                    budget=session.budget(),
                    readiness=admission.readiness,
                    allowance=allowance,
                    transport=Attempt(),
                )
                session.records.extend(response.retrieval_records)
                if response.data is None:
                    raise FetchError(
                        FetchRejection.TRANSPORT_FAILED,
                        ErrorCode.TOOL_FAILED,
                        "Identity request failed",
                    )
                return response.data

        class RetainedDart(OpenDartCompany):
            required = True

            def __call__(self, candidate, calls):
                outcome = super().__call__(candidate, calls)
                # OpenDART's display excerpts are key=value summaries. Retain
                # the actual bounded JSON bytes instead, not invented raw text.
                texts = {
                    s.source_id: next(
                        raw.content.decode("utf-8")
                        for raw in session.raw
                        if raw.content_hash == s.content_hash
                    )
                    for s in outcome.sources
                }
                return replace(
                    outcome,
                    observations=tuple(
                        replace(o, excerpt=texts[o.source_id])
                        for o in outcome.observations
                    ),
                )

        settings = document.profiles.m2_source
        provider = RetainedDart(
            IdentityFetcher(admission.fetch_policy, clock=runtime.clock),
            api_key=supplied.api_key,
            schema_version=candidate.schema_version,
            clock=runtime.clock,
            max_name_matches=settings.max_name_matches,
            max_index_bytes=settings.max_index_bytes,
        )
        tool = LiveResearchCompany(
            [provider],
            run_id=run_id,
            schema_version=candidate.schema_version,
            as_of=cutoff,
            clock=runtime.clock,
            retrieval_namespace="identity",
        )
        try:
            result = tool(candidate, session.budget())
        finally:
            collection_records.extend(session.records)
        collection_records.extend(result.retrieval_records)
        write(
            "identity-result.json",
            result.model_dump(mode="json"),
        )
        if result.status not in ("ok", "empty") or result.data is None:
            raise ActualInputError("IDENTITY_PROVIDER_FAILED")
        bundle = result.data
        if not bundle.profile.field_evidence_ids.get("identity"):
            return None
        review_identity = options.identity_review
        if review_identity is None:
            raise ActualInputError("IDENTITY_REVIEW_REQUIRED")
        identity = review_identity(candidate, (bundle,))
        if type(identity) is not CompanyIdentity or identity.candidate != candidate:
            return None
        ids = set(bundle.evidence)
        if not ids or any(
            not set(values) <= ids for values in identity.field_evidence_ids.values()
        ):
            raise ActualInputError("IDENTITY_ORIGINALS_MISSING")
        materials = []
        for source in bundle.sources.values():
            raw = next(r for r in session.raw if r.content_hash == source.content_hash)
            records = tuple(
                r.model_copy(
                    update={
                        "evidence_ids": sorted(
                            e.evidence_id
                            for e in bundle.evidence.values()
                            if any(
                                p.retrieval_id == r.retrieval_id for p in e.provenance
                            )
                        )
                    }
                )
                for r in result.retrieval_records
                if source.source_id in r.source_ids
            )
            material = admission.prepare_source(bundle, raw, records)
            if (
                material.source
                != source.model_copy(update={"local_path": material.source.local_path})
                or material.content != raw.content
                or material.retrieval_records != records
                or {e.evidence_id: e for e in material.evidence}
                != {
                    eid: e
                    for eid, e in bundle.evidence.items()
                    if e.source_id == source.source_id
                }
            ):
                raise ActualInputError("IDENTITY_RETENTION_MISMATCH")
            materials.append(material)
        retained = store.ingest_sources(materials, companies=(identity,))
        versions.append(retained.manifest.version)
        return identity

    try:
        if not matches:
            if options.identity_research is None or options.identity_review is None:
                return finish(
                    "research_blocked", ("IDENTITY_RESEARCH_AUTHORITY_REQUIRED",)
                )
            admissions = options.identity_research(request, actual)
            if any(
                type(item) is not IdentityResearchAdmissionV3 for item in admissions
            ):
                raise ActualInputError("IDENTITY_RESEARCH_SCOPE_MISMATCH")
            candidates = [
                Candidate.model_validate_json(item.collection.candidate_json)
                for item in admissions
            ]
            if any(item.collection.actual is not actual for item in admissions) or any(
                not _matches(request, c) for c in candidates
            ):
                raise ActualInputError("IDENTITY_RESEARCH_SCOPE_MISMATCH")
            receipt["matching_candidate_ids"] = tuple(
                sorted({c.candidate_id for c in candidates})
            )
            if len(candidates) > 1:
                return finish("identity_ambiguous", ("MULTIPLE_ADMITTED_IDENTITIES",))
            if not candidates:
                return finish("identity_unknown", ("NO_ADMITTED_IDENTITY",))
            candidate = candidates[0]
            if (
                binding.call.candidate_id != candidate.candidate_id
                or candidate.country not in actual.run_input.countries
                or candidate.schema_version != actual.run_input.schema_version
            ):
                raise ActualInputError("COMPANY_ADMISSION_MISMATCH")
            identity_admission = admissions[0]
            identity = collect_identity(candidate, identity_admission)
            if identity is None:
                return finish("identity_unknown", ("IDENTITY_REVIEW_MISSING",))
            matches = [candidate]
            receipt["candidate_id"] = candidate.candidate_id
        candidate = matches[0]
        if retained is None:
            raise ActualInputError("RETAINED_IDENTITY_REQUIRED")
        if (
            binding.call.candidate_id != candidate.candidate_id
            or candidate.country not in actual.run_input.countries
            or candidate.schema_version != actual.run_input.schema_version
        ):
            raise ActualInputError("COMPANY_ADMISSION_MISMATCH")
        if not enabled and (
            retained.manifest.version != actual.run_input.corpus_version
            or retained.manifest.index_metadata.index_version != actual.index_version
        ):
            raise ActualInputError("CORPUS_ADMISSION_MISMATCH")
        profile = CompanyProfile.model_validate(
            options.profile_for(candidate, retained, cutoff)
        )
        if (
            profile.candidate_id != candidate.candidate_id
            or profile.as_of != cutoff
            or profile.schema_version != candidate.schema_version
        ):
            raise ActualInputError("PROFILE_IDENTITY_MISMATCH")
        eligibility = check_eligibility(
            profile,
            retained.manifest.evidence,
            {"policy_version": policy.policy_version},
            run_id=run_id,
            evidence_revision=0,
        )
        receipt["eligibility_status"] = eligibility.status
        write("eligibility.json", eligibility.model_dump(mode="json"))
        if eligibility.status == "ineligible":
            return finish("ineligible", eligibility.reason_codes)
        if eligibility.status == "unknown" and not enabled:
            return finish("eligibility_unknown", eligibility.reason_codes)
        current = retained.manifest
        target_evidence = {
            eid: e
            for eid, e in current.evidence.items()
            if e.candidate_id == candidate.candidate_id
        }
        target_sources = {
            e.source_id: current.sources[e.source_id] for e in target_evidence.values()
        }
        freshness = assess_freshness(
            target_evidence,
            target_sources,
            as_of=cutoff,
            llm=model("freshness", candidate.candidate_id),
        )
        write(
            "freshness.json",
            TypeAdapter(type(freshness)).dump_python(freshness, mode="json"),
        )
        if enabled:
            if options.research_for is None:
                return finish("research_blocked", ("RESEARCH_ADMISSION_REQUIRED",))
            research_admission = options.research_for(candidate, actual)
            gaps = tuple(
                GapQuery(
                    candidate_id=candidate.candidate_id, scope="company", field=field
                )
                for field, check in (
                    ("domain_match", "domain"),
                    ("is_listed", "listing"),
                    ("stage", "stage"),
                    ("exit_completed", "exit"),
                    ("identity", "evaluability"),
                    ("business", "evaluability"),
                )
                if eligibility.checks[check]["status"] == "unknown"
            )
            # Freshness input must be the exact target-only subset supplied above.
            if (
                type(research_admission) is not ResearchAdmission
                or research_admission.actual is not actual
            ):
                raise ActualInputError("RESEARCH_ADMISSION_REQUIRED")
            result = research_target(
                candidate,
                target_evidence,
                target_sources,
                as_of=cutoff,
                research=True,
                limits=remaining_limits(),
                freshness=freshness,
                gaps=gaps,
                admission=research_admission,
                store=store,
            )
            collection_records.extend(result.collection_records)
            if result.snapshot is not None:
                retained = result.snapshot
                versions.append(retained.manifest.version)
            write(
                "research-result.json",
                TypeAdapter(type(result)).dump_python(result, mode="json"),
            )
            if result.status != "completed":
                return finish(
                    "research_blocked" if result.status == "blocked" else "failed",
                    result.reason_codes,
                )
            profile = CompanyProfile.model_validate(
                options.profile_for(candidate, retained, cutoff)
            )
        changed_corpus = (
            retained.manifest.version != actual.run_input.corpus_version
            or retained.manifest.index_metadata.index_version != actual.index_version
        )
        if changed_corpus and (not enabled or options.readmit_after_research is None):
            raise ActualInputError("CORPUS_ADMISSION_MISMATCH")
        hits = store.search_local(
            candidate.canonical_name,
            top_k=document.profiles.actual_v3.retrieval_top_k,
            timeout_seconds=binding.budget.timeout_seconds,
            snapshot=retained,
            current_report_id=report_id,
        )
        chunk_ids = tuple(hit.chunk.chunk_id for hit in hits)
        frozen = _snapshot(retained, candidate, chunk_ids, actual)
        eligibility = check_eligibility(
            profile,
            frozen.evidence,
            {"policy_version": policy.policy_version},
            run_id=run_id,
            evidence_revision=frozen.evidence_revision,
        )
        receipt["eligibility_status"] = eligibility.status
        write("eligibility.json", eligibility.model_dump(mode="json"))
        if eligibility.status != "eligible":
            return finish(
                "ineligible"
                if eligibility.status == "ineligible"
                else "eligibility_unknown",
                eligibility.reason_codes,
            )
        write("snapshot.json", frozen.model_dump(mode="json"))
        if changed_corpus:
            # The operator may replace review inputs, never the execution budget.
            # Retained payload owns serialized bytes; the callback receives a
            # detached evaluation view so it cannot edit the evaluated snapshot.
            consumed = runtime.ledger.snapshot()
            commitment = digest(canonical(frozen.model_dump(mode="json")))
            readmit = options.readmit_after_research
            assert readmit is not None
            supplied = readmit(retained, frozen.model_copy(deep=True))
            if type(supplied) is not CompanyReportReadmissionV3:
                raise ActualInputError("READMISSION_DENIED")
            if (
                supplied.retained_sha256 != digest(retained.payload)
                or supplied.snapshot_sha256 != commitment
            ):
                raise ActualInputError("READMISSION_SNAPSHOT_MISMATCH")
            if not callable(supplied.reviews_for) or not callable(
                supplied.evaluation_inputs_for
            ):
                raise ActualInputError("READMISSION_REVIEWS_REQUIRED")
            authority = replace(
                authority,
                sources=supplied.sources,
                reviews_for=supplied.reviews_for,
                evaluation_inputs_for=supplied.evaluation_inputs_for,
            )
        verify_sources(frozen.sources, authority, frozen.corpus_version)
        reviews = _Reviews(authority, actual.registry, out)
        reviews.for_snapshot(frozen)
        if changed_corpus:
            try:
                actual.load_policy(require_capacity=True)
                if runtime.ledger.snapshot() != consumed:
                    raise ValueError("readmission changed cumulative usage")
            except ValueError:
                raise ActualInputError("READMISSION_RUNTIME_CHANGED") from None
            original = actual
            actual = replace(
                original,
                run_input=original.run_input.model_copy(
                    update={"corpus_version": frozen.corpus_version}
                ),
                index_version=frozen.index_version,
                review_resolvers={},
                review_resolver_for=reviews,
            )
            write(
                "readmission.json",
                {
                    "retained_sha256": digest(retained.payload),
                    "snapshot_sha256": commitment,
                    "original_corpus_version": original.run_input.corpus_version,
                    "original_index_version": original.index_version,
                    "corpus_version": actual.run_input.corpus_version,
                    "index_version": actual.index_version,
                    "run_id": run_id,
                    "candidate_id": candidate.candidate_id,
                    "as_of": cutoff.isoformat(),
                    "policy_version": actual.run_input.policy_version,
                    "shared_runtime_binding": actual.runtime_binding is binding,
                    "shared_ledger": actual.runtime_binding.runtime.ledger is ledger,
                    "ledger_before_review": consumed,
                    "ledger_after_review": runtime.ledger.snapshot(),
                    "budget": binding.budget.model_dump(mode="json"),
                    "limits": runtime.ledger.limits.model_dump(mode="json"),
                },
            )
        # Both authorities must authenticate the identical payload, before fanout.
        for version in ("core-0.1.0", "finance-0.1.0"):
            actual.verify_snapshot(frozen, actual.registry.rubric(version))

        def evaluate(role: Branch, snapshot):
            if snapshot != frozen:
                raise ActualInputError("EVALUATION_SNAPSHOT_MISMATCH")
            return evaluate_actual_branch_v3(
                role,
                snapshot,
                reviews=reviews,
                admission=actual,
                llm=model,
                policy=policy,
                industry_evidence_dimensions=("market",),
            )

        stages = CandidateStagesV3(
            discover=lambda: [candidate],
            normalize=lambda candidates: candidates,
            research=lambda _: profile,
            eligibility=lambda *_: eligibility,
            collect=lambda *_: tuple(frozen.evidence.values()),
            freeze=lambda *_: frozen,
            retained_store=retained,
        )
        result = run_candidate_workflow_v3(
            stages,
            {
                role: lambda s, role=role: evaluate(role, s)
                for role in BRANCH_DIMENSIONS
            },
            policy=policy,
            catalog=catalog,
            catalog_policy_version=catalog.policy_version,
            run_id=run_id,
            schema_version=actual.run_input.schema_version,
            support_check=authority.support_check,
            applicability_assessments=authority.applicability_assessments,
            applicability_check=authority.applicability_check,
            applicability_verifier=authority.applicability_verifier,
            industry_evidence_dimensions=("market",),
            clock=runtime.clock.now,
            approved_policy_source=actual.source,
            actual_admission=actual,
        )
        write(
            "candidate-result.json",
            TypeAdapter(type(result)).dump_python(result, mode="json"),
        )
        if result.errors or candidate.candidate_id not in result.scores:
            return finish(
                "failed",
                tuple(e.error_code for e in result.errors) or ("EVALUATION_FAILED",),
            )
        competitor_ids = tuple(before.manifest.chunks) if before is not None else ()
        context = build_company_report_context(
            result,
            frozen,
            profile=profile,
            eligibility=eligibility,
            retained=retained,
            pre_research=before,
            current_report_id=report_id,
            target_chunk_ids=chunk_ids,
            competitor_chunk_ids=competitor_ids,
            actual_admission=actual,
        )
        write("context.json", context.snapshot())

        def check_pdf(draft, context, structural, judged):
            rendered = PDFRenderer(
                profile=pdf_profile,
                output_dir=out,
                proof=lambda _: (structural, judged),
                execution_mode="live",
            )(draft, pdf_profile.version)
            write("render.json", rendered.model_dump(mode="json"))
            return PDFLayoutValidator()(draft, context, rendered)

        generator = ReportGeneratorV3(model("report_generator", candidate.candidate_id))

        def generate(context, feedback):
            return generator(context, feedback).model_copy(
                update={"report_id": report_id}
            )

        report = run_report_v3(
            context,
            generate=generate,
            judge=SemanticJudgeV3(model("report_judge", candidate.candidate_id)),
            check_pdf=check_pdf,
        )
        write(
            "report-result.json",
            TypeAdapter(type(report)).dump_python(report, mode="json"),
        )
        if report.draft is not None:
            (out / "report.md").write_text(report.draft.markdown, encoding="utf-8")
            receipt["report_path"] = out / "report.md"
        if (
            report.status != "completed"
            or report.warning
            or report.error_code
            or report.validation is None
            or not report.validation.valid
            or report.judgement is None
            or report.judgement.verdict != "pass"
            or report.judgement.findings
            or report.judgement.revision_instructions
            or report.pdf_validation is None
            or not report.pdf_validation.valid
            or report.draft is None
        ):
            receipt["report_validation"] = "failed"
            return finish(
                "warning" if report.warning else "failed",
                (report.error_code or "REPORT_NOT_ACCEPTED",),
            )
        receipt["report_validation"] = "passed"
        receipt["validation_receipt_hashes"] = tuple(
            digest(canonical(proof.model_dump(mode="json")))
            for proof in (report.validation, report.judgement, report.pdf_validation)
        )
        originals = _evidence_closure(
            set(report.draft.cited_evidence_ids),
            retained.manifest.evidence,
            retained.manifest.sources,
        )
        descriptor = ValidatedReport(
            run_id=run_id,
            candidate_id=candidate.candidate_id,
            as_of=cutoff,
            retrieved_at=runtime.clock.now(),
            generation_model=document.llm.model,
            generation_revision=document.llm.model,
            reviewer="company-report-validated-pipeline",
            permission_note="Local derived retrieval only; no publication approval.",
            result=report,
            evidence_hashes={
                eid: evidence_fingerprint(
                    retained.manifest.evidence[eid],
                    retained.manifest.sources[
                        retained.manifest.evidence[eid].source_id
                    ],
                )
                for eid in originals
            },
            parent_report_ids=tuple(
                sorted(
                    {
                        item["report_id"]
                        for item in context.snapshot()["company_context"][
                            "prior_interpretations"
                        ]
                    }
                )
            ),
        )
        try:
            published = store.ingest_report(descriptor)
        except (OSError, ValueError, SQLiteError):
            receipt["ingestion_status"] = "failed"
            return finish("warning", ("REPORT_INGESTION_FAILED",))
        receipt["ingestion_status"] = "succeeded"
        versions.append(published.manifest.version)
        return finish("completed")
    except ActualInputError as exc:
        return finish("research_blocked", (exc.code,))
    except LLMError as exc:
        if research_rejection is not None:
            return finish("research_blocked", (research_rejection,))
        return finish("failed", (exc.error_code.value,))
    except (OSError, ValueError, SQLiteError):
        return finish("failed", ("COMPANY_WORKFLOW_FAILED",))
