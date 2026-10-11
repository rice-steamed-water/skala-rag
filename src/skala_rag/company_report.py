"""Python-direct narrative reporting; the returned directory owns the receipt."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from sqlite3 import Error as SQLiteError
from typing import Literal
from uuid import uuid4

from pydantic import TypeAdapter

from skala_rag.agents.company_analysis import analysis_questions
from skala_rag.agents.company_report_research import (
    PROVIDER,
    build_narrative_research_service,
)
from skala_rag.contracts import Candidate, RetrievalRecord
from skala_rag.contracts.common import JSONMap
from skala_rag.contracts.company_report import (
    AnalysisReply,
    CompanyReportConfigError,
    CompanyReportRequest,
    NarrativeRunResult,
    load_company_report_config,
)
from skala_rag.contracts.tools import ToolBudget
from skala_rag.graph.company_report import (
    NarrativeReportLifecycle,
    run_narrative_graph,
)
from skala_rag.rag.company_store import CompanyStore, evidence_fingerprint
from skala_rag.reporting.company_context import (
    build_writer_context,
    load_issuer_association,
)
from skala_rag.reporting.narrative import NarrativeModelSettings
from skala_rag.reporting.pdf import load_pdf_profile
from skala_rag.settings import resolve_environment_credential
from skala_rag.tools.company_research import url_host
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    BudgetLedger,
    CallContext,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
)


class _Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class _Binding:
    runtime: AdapterRuntime
    budget: ToolBudget
    call: CallContext
    readiness: Readiness
    tool_name: str


def _no_retry(seconds: float) -> None:
    raise CompanyReportConfigError("UNEXPECTED_TRANSPORT_RETRY")


def run_company_report(
    company_name: str,
    *,
    config_path: Path,
    output_dir: Path,
    research: bool | None = None,
    homepage_url: str | None = None,
    as_of: date | None = None,
    research_limits: Mapping[str, int | float | str | Decimal] | None = None,
) -> Path:
    """Return receipts for validated reports or explicit non-report outcomes.

    Invalid configuration raises before writes. Credentials are resolved by the
    runtime and never serialized. Homepage is a subject hint, not fetch permission.
    No legal identity, eligibility or scored-policy approval is manufactured.
    """
    request = CompanyReportRequest(
        schema_version="company-report-request-1",
        company_name=company_name,
        homepage_url=homepage_url,
        legal_identifiers={},
        as_of=as_of,
    )
    config = load_company_report_config(
        config_path, research=research, research_limits=research_limits
    )
    out = TypeAdapter(Path).validate_python(output_dir, strict=True).resolve()
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    clock = _Clock()
    run_id = f"company-{uuid4().hex}"
    cutoff = request.as_of or clock.now().date()
    document = config.runtime_document
    limits = document.profiles.actual_v3
    runtime = AdapterRuntime(
        policy=RuntimePolicy(
            schema_version="runtime-1",
            execution_mode="live",
            retry_delays_seconds=(),
            live_approval_reference=config.config_hash,
            timing_approval_reference=config.config_hash,
        ),
        ledger=BudgetLedger(
            RuntimeLimits(
                schema_version="runtime-1",
                max_calls=limits.max_calls,
                tool_max_calls={
                    "openai-responses": limits.openai_max_calls,
                    **(
                        {PROVIDER: config.research_limits.max_calls}
                        if config.research_limits is not None
                        else {}
                    ),
                },
                max_input_tokens=limits.max_input_tokens,
                max_output_tokens=limits.max_output_tokens,
                max_cost_usd=limits.max_cost_usd,
            )
        ),
        clock=clock,
        sleep=_no_retry,
    )
    key = resolve_environment_credential("OPENAI_API_KEY")
    deadline = clock.now() + timedelta(seconds=limits.deadline_seconds)
    binding = _Binding(
        runtime=runtime,
        budget=ToolBudget(
            schema_version="runtime-1",
            max_calls=1,
            max_retries=0,
            timeout_seconds=limits.timeout_seconds,
            deadline=deadline,
        ),
        call=CallContext(
            schema_version="runtime-1",
            call_id=run_id + ":narrative",
            run_id=run_id,
            candidate_id=None,
            tool_name="openai-responses",
            node="company-report",
        ),
        readiness=Readiness(
            schema_version="runtime-1",
            required=True,
            configured=True,
            credential_required=True,
            credential_present=bool(key),
            model_required=False,
            model_available=True,
            index_required=False,
            index_available=True,
        ),
        tool_name="openai-responses",
    )
    # Divide the configured run token ceiling across its call capacity. The legacy
    # request_input_tokens=1 sentinel is not a narrative request bound. Every role
    # verifies its actual UTF-8 request/schema fits this captured allowance.
    request_input_tokens = limits.max_input_tokens // limits.max_calls
    allowance = Allowance(
        schema_version="runtime-1",
        input_tokens=request_input_tokens,
        output_tokens=limits.request_output_tokens,
        max_cost_usd=(
            Decimal(request_input_tokens) * document.llm.usd_per_input_token
            + Decimal(limits.request_output_tokens) * document.llm.usd_per_output_token
        ),
    )
    lifecycle: NarrativeReportLifecycle | None = None
    local_records: list[RetrievalRecord] = []
    subject_id: str | None = None
    validation: Literal["not_run", "passed", "failed"] = "not_run"
    hashes: list[str] = []
    md_path: str | None = None
    pdf_path: str | None = None

    def write(name: str, payload: JSONMap) -> None:
        (out / name).write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    write("request.json", request.model_dump(mode="json"))
    write("effective-config.json", config.model_dump(mode="json"))

    def provider() -> NarrativeReportLifecycle | None:
        nonlocal lifecycle, subject_id
        store = CompanyStore.from_local(
            config.store_dir,
            model_path=config.model_path,
            receipt_path=config.model_receipt_path,
        )
        snapshot = store.open()
        if snapshot is None:
            if config.research_enabled:
                raise CompanyReportConfigError("RESEARCH_AUTHORIZATION_REQUIRED")
            return None
        manifest = snapshot.manifest
        association = load_issuer_association(
            config.issuer_receipt_path,
            retained=snapshot,
            company_name=company_name,
            as_of=cutoff,
        )
        matches = [
            item.candidate
            for item in manifest.companies.values()
            if company_name.casefold()
            in {
                name.casefold()
                for name in (item.candidate.canonical_name, *item.candidate.aliases)
            }
            and (
                homepage_url is None
                or url_host(homepage_url) == url_host(item.candidate.homepage_url)
            )
        ]
        if len(matches) > 1:
            raise CompanyReportConfigError("AMBIGUOUS_ISSUER_SUBJECT")
        if not matches and association is None:
            if manifest.evidence:
                raise CompanyReportConfigError("ISSUER_ASSOCIATION_REQUIRED")
            if config.research_enabled:
                raise CompanyReportConfigError("RESEARCH_AUTHORIZATION_REQUIRED")
            return None
        # The service requires a subject DTO, not a legal identity or profile.
        # A reviewed issuer has no observed country or legal identifiers.
        if matches:
            candidate = matches[0]
        else:
            assert association is not None
            candidate = Candidate(
                schema_version="company-report-2",
                candidate_id=association.subject_id,
                canonical_name=association.name,
                aliases=[],
                country="unknown",
                homepage_url=homepage_url,
                legal_identifiers={},
                discovery_source_ids=[],
            )
        subject_id = candidate.candidate_id
        questions = analysis_questions()
        selected_ids: set[str] = set()
        for question in questions:
            started = clock.now()
            if started >= deadline:
                raise CompanyReportConfigError("NARRATIVE_DEADLINE_EXCEEDED")
            query = f"{company_name} {question.question}"
            hits = store.search_local(
                query,
                top_k=limits.retrieval_top_k,
                timeout_seconds=min(
                    limits.timeout_seconds, (deadline - started).total_seconds()
                ),
                snapshot=snapshot,
                current_report_id=run_id,
            )
            ids = tuple(
                sorted(
                    {
                        item.evidence_id
                        for hit in hits
                        for item in hit.original_evidence
                        if (item.scope == "company" and item.candidate_id == subject_id)
                        or (item.scope == "industry" and item.candidate_id is None)
                    }
                )
            )
            selected_ids.update(ids)
            local_records.append(
                RetrievalRecord(
                    schema_version="company-report-2",
                    retrieval_id=run_id + ":local:" + question.question_id,
                    run_id=run_id,
                    candidate_id=subject_id,
                    tool_name="company-store-retrieval",
                    query=query,
                    arguments_without_secrets={
                        "question_id": question.question_id,
                        "corpus_version": manifest.version,
                        "index_version": manifest.index_metadata.index_version,
                        "as_of": cutoff.isoformat(),
                        "original_evidence_hashes": {
                            eid: evidence_fingerprint(
                                manifest.evidence[eid],
                                manifest.sources[manifest.evidence[eid].source_id],
                            )
                            for eid in ids
                        },
                    },
                    started_at=started,
                    finished_at=clock.now(),
                    status="ok" if ids else "empty",
                    source_ids=sorted(
                        {hit.source.source_id for hit in hits}
                        | {manifest.evidence[eid].source_id for eid in ids}
                    ),
                    chunk_ids=sorted({hit.chunk.chunk_id for hit in hits}),
                    evidence_ids=list(ids),
                    cache_hit=True,
                )
            )
            if clock.now() >= deadline:
                raise CompanyReportConfigError("NARRATIVE_DEADLINE_EXCEEDED")
        ids = tuple(sorted(selected_ids))
        if not ids:
            if config.research_enabled:
                raise CompanyReportConfigError("RESEARCH_AUTHORIZATION_REQUIRED")
            return None
        context = build_writer_context(
            snapshot,
            company_name=company_name,
            as_of=cutoff,
            questions=questions,
            evidence_ids=ids,
            issuer_association=association,
        )
        service = build_narrative_research_service(
            config=config,
            store=store,
            candidate=candidate,
            as_of=cutoff,
            binding=binding,
            admit=lambda retained, selected, records: selected,
        )
        lifecycle = NarrativeReportLifecycle(
            context=context,
            snapshot=snapshot,
            service=service,
            binding=binding,
            allowance=allowance,
            issuer_association=association,
        )
        return lifecycle

    outcome: Literal["completed", "partial", "empty", "ambiguous", "failed"] = "failed"
    reasons = ["COMPANY_WORKFLOW_FAILED"]
    completeness: Literal["full", "partial", "none"] = "none"
    try:
        state = run_narrative_graph(
            provide=provider,
            settings=NarrativeModelSettings(
                llm_settings=document.llm,
                max_output_tokens=limits.request_output_tokens,
                api_key=key,
            ),
            output_dir=out,
            pdf_profile=load_pdf_profile(),
        )
        if state["has_evidence"]:
            assert lifecycle is not None
            writer = lifecycle.context.writer_context
            reply = AnalysisReply.model_validate_json(
                state["analysis"], context={"writer_context": writer}
            )
            partial = any(
                answer.status in ("partial", "unknown")
                for answer in reply.answers.values()
            )
            for name in (
                "analysis",
                "composition",
                "judgment",
                "render",
                "pdf_validation",
                "references",
            ):
                write(name.replace("_", "-") + ".json", json.loads(state[name]))
            hashes = [
                hashlib.sha256((out / name).read_bytes()).hexdigest()
                for name in ("judgment.json", "pdf-validation.json", "references.json")
            ]
            (out / "report.md").write_text(state["markdown"], encoding="utf-8")
            (out / "report.pdf").write_bytes(Path(state["pdf_path"]).read_bytes())
            md_path, pdf_path = str(out / "report.md"), str(out / "report.pdf")
            validation = "passed"
            outcome = "partial" if partial else "completed"
            completeness = "partial" if partial else "full"
            reasons = ["INCOMPLETE_COMPANY_EVIDENCE"] if partial else []
        else:
            outcome, reasons = "empty", ["NO_ADMISSIBLE_COMPANY_EVIDENCE"]
    except (OSError, ValueError, KeyError, SQLiteError) as exc:
        code = getattr(exc, "code", "COMPANY_WORKFLOW_FAILED")
        reasons = [code]
        if code == "AMBIGUOUS_ISSUER_SUBJECT":
            outcome, subject_id = "ambiguous", None
        elif lifecycle is not None:
            validation = "failed"
        # Failed drafts remain diagnostics, never public report artifacts.
        for name in ("report.md", "report.pdf"):
            (out / name).unlink(missing_ok=True)
        md_path = pdf_path = None
    if lifecycle is not None:
        write(
            "writer-input.json",
            lifecycle.context.writer_context.model_dump(mode="json"),
        )
        write(
            "lifecycle.json",
            {
                "information_rounds": lifecycle.information_rounds,
                "repairs": lifecycle.repair_count,
                "model_records": [
                    record.model_dump(mode="json") for record in lifecycle.model_records
                ],
            },
        )
        local_records.extend(
            record for record in lifecycle.collection_records if record.run_id == run_id
        )
    write(
        "provider-trace.json",
        {"records": [record.model_dump(mode="json") for record in local_records]},
    )
    result = NarrativeRunResult(
        schema_version="company-report-2",
        run_id=run_id,
        company_name=company_name,
        subject_id=subject_id,
        as_of=cutoff,
        outcome=outcome,
        completeness=completeness,
        reason_codes=reasons,
        report_md_path=md_path,
        pdf_path=pdf_path,
        validation=validation,
        context_id=lifecycle.context.context_id if lifecycle else None,
        validation_receipt_hashes=hashes,
        research_requested=config.research_enabled,
        collection_records=(
            [
                record
                for record in lifecycle.collection_records
                if config.research_enabled
                and record.run_id == run_id
                and record.tool_name != "company-store-retrieval"
            ]
            if lifecycle is not None
            else []
        ),
        model_usage=runtime.ledger.snapshot(),
    )
    write("run-result.json", result.model_dump(mode="json"))
    return out
