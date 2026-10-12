"""기업 보고서의 공개 진입점. 설정·자료·예산을 준비하고 결과 디렉터리를 반환한다.

예제에서 넘어온 호출은 이 파일에서 준비한 뒤 graph/company_report.py로 이어진다.
"""

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

from skala_rag.agents.company_analysis import CompanyAnalysisAttempt, analysis_questions
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
    CompanyContextError,
    CompanyWriterContext,
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
    """검색·모델 호출이 같은 UTC 시계로 실행 마감 시간을 판단하게 한다."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class _Binding:
    """조사 서비스와 모델 역할이 공유할 실행 환경·예산·호출 정보를 묶는다."""

    runtime: AdapterRuntime
    budget: ToolBudget
    call: CallContext
    readiness: Readiness
    tool_name: str


def _no_retry(seconds: float) -> None:
    # 재시도로 같은 외부 요청을 중복 전송하지 않도록 이 실행에서는 재시도를 금지한다.
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
    """보고서 또는 보고서를 만들지 못한 이유가 담긴 결과 디렉터리를 반환한다.

    잘못된 설정은 파일을 쓰기 전에 예외를 낸다. API 키는 실행 환경에서만 읽는다.
    홈페이지는 대상 구분용 힌트이지 수집 권한이 아니다.
    근거 없는 법적 신원·적격성·점수 판정을 만들지 않는다.
    """
    # 1. 외부 입력을 DTO로 검증하고 설정을 고정한다. DTO는 필드와 타입을 정한 계약이다.
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
    # 기존 결과를 덮어쓰지 않는다. 매 실행마다 새 출력 디렉터리를 사용한다.
    out = TypeAdapter(Path).validate_python(output_dir, strict=True).resolve()
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    clock = _Clock()
    run_id = f"company-{uuid4().hex}"
    cutoff = request.as_of or clock.now().date()
    document = config.runtime_document
    limits = document.profiles.actual_v3
    # 2. 전체 실행의 호출 수·토큰·비용을 하나의 원장으로 관리한다.
    # 역할이 바뀌거나 보고서를 수정해도 이 원장을 새로 만들지 않는다.
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
    # 키는 모델 호출에만 전달한다. 아래 설정·결과 JSON에는 저장하지 않는다.
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
    # 전체 입력 토큰 한도를 호출 수로 나눠 한 요청에 예약할 예산을 정한다.
    # 과거 설정의 request_input_tokens=1은 이 보고서의 요청 크기 제한이 아니다.
    # 각 역할은 실제 UTF-8 요청과 스키마가 이 예약 범위에 들어가는지 확인한다.
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
        # 3. 로컬 저장소와 모델을 열고 대상 기업에 속한 원문 근거를 찾는다.
        # None은 허용할 기업 근거가 없다는 뜻이다. 무결성·권한 문제는 예외로 구분한다.
        nonlocal lifecycle, subject_id
        folder_binding = None
        if config.operator_folder is not None:
            # 현재 raw·recipe·대상·세대를 모델 자산 로드와 검색보다 먼저 재검사한다.
            # 저장소 소유자의 canonical helper만 사용하며 폴더 신원을 추정하지 않는다.
            from skala_rag.rag.manual_pdf import (
                manual_index_settings,
                validate_operator_folder_input,
                validate_operator_import,
            )

            folder_binding = validate_operator_folder_input(
                config, company_name=company_name
            )
            store = CompanyStore.from_local(
                config.store_dir,
                model_path=config.model_path,
                receipt_path=config.model_receipt_path,
                index_settings=manual_index_settings(config.model_receipt_path),
            )
            # CURRENT 대신 admission 때 고정한 불변 버전을 명시적으로 연다.
            snapshot = store.open(version=folder_binding.store_version)
            ready = validate_operator_import(
                config,
                subject_id=folder_binding.subject_id,
                name=folder_binding.name,
                store=store,
            )
            if ready.binding != folder_binding or ready.snapshot != snapshot:
                raise CompanyReportConfigError("IMPORT_GENERATION_CHANGED")
        else:
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
        # 저장된 이름·별칭과 선택적 홈페이지 힌트로 대상을 좁힌다.
        # 검토 영수증은 기업과 원문을 연결하며, 법적 신원을 인증하지는 않는다.
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
        if not matches and association is None and folder_binding is None:
            if manifest.evidence:
                raise CompanyReportConfigError("ISSUER_ASSOCIATION_REQUIRED")
            if config.research_enabled:
                raise CompanyReportConfigError("RESEARCH_AUTHORIZATION_REQUIRED")
            return None
        # 서비스에 넘길 대상 DTO를 만든다. 검토된 원문 연결만 있는 경우에는
        # 알 수 없는 국가와 법적 식별자를 임의로 채우지 않는다.
        if folder_binding is not None:
            candidate = Candidate(
                schema_version="company-report-2",
                candidate_id=folder_binding.subject_id,
                canonical_name=folder_binding.name,
                aliases=[],
                country="unknown",
                homepage_url=homepage_url,
                legal_identifiers={},
                discovery_source_ids=[],
            )
        elif matches:
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
        question_ids: list[tuple[str, ...]] = []
        # 기업명만 검색하면 질문별 자료를 놓칠 수 있어 18개 질문 각각으로 검색한다.
        # BGE-M3는 여기서 유사한 원문을 찾는 임베딩 모델이며 글을 쓰는 모델이 아니다.
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
                dict.fromkeys(
                    item.evidence_id
                    for hit in hits
                    for item in (
                        hit.original_evidence
                        if hit.prior_report is not None and folder_binding is None
                        else manifest.evidence.values()
                    )
                    if (item.scope == "company" and item.candidate_id == subject_id)
                    or (item.scope == "industry" and item.candidate_id is None)
                    if (
                        hit.prior_report is not None
                        and folder_binding is None
                        and hit.prior_report.original_evidence_hashes.get(
                            item.evidence_id
                        )
                        == evidence_fingerprint(item, manifest.sources[item.source_id])
                    )
                    or (
                        item.source_id == hit.source.source_id
                        and any(
                            provenance.chunk_id == hit.chunk.chunk_id
                            for provenance in item.provenance
                        )
                    )
                )
            )
            # 여러 질문에서 같은 근거를 찾을 수 있다.
            # ID는 중복 제거하고 검색 기록은 남긴다.
            question_ids.append(ids)
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
        # 질문마다 첫 근거부터 번갈아 검토한다. 전체 청크를 빼며 원문을 자르지 않는다.
        selected_ids: list[str] = []
        context = None

        def check_context(projected: CompanyWriterContext) -> None:
            """초기 선택과 후속 보강에 동일한 실제 프롬프트·schema 예산을 적용한다."""
            CompanyAnalysisAttempt(
                projected,
                llm_settings=document.llm,
                clock=clock,
                max_output_tokens=limits.request_output_tokens,
                allowance=allowance,
                api_key=key,
            )

        for rank in range(max(map(len, question_ids), default=0)):
            for found in question_ids:
                if rank >= len(found) or found[rank] in selected_ids:
                    continue
                trial_ids = (*selected_ids, found[rank])
                try:
                    trial = build_writer_context(
                        snapshot,
                        company_name=company_name,
                        as_of=cutoff,
                        questions=questions,
                        evidence_ids=trial_ids,
                        issuer_association=association,
                        operator_folder=folder_binding,
                    )
                    # 실제 분석 프롬프트와 응답 schema를 포함한 기존 바이트·비용 gate다.
                    check_context(trial)
                except (CompanyContextError, CompanyReportConfigError) as exc:
                    if exc.code not in (
                        "WRITER_CHUNK_LIMIT_EXCEEDED",
                        "ROLE_ALLOWANCE_INSUFFICIENT",
                    ):
                        raise
                    continue
                selected_ids.append(found[rank])
                context = trial
        ids = tuple(selected_ids)
        if not ids:
            if config.research_enabled:
                raise CompanyReportConfigError("RESEARCH_AUTHORIZATION_REQUIRED")
            return None
        # 4. 작성 모델에는 근거 ID와 내용만 보여준다.
        # 원본 URL·위치·검색 이력은 별도 인용 장부에 남겨 코드가 출처를 연결한다.
        assert context is not None
        service = build_narrative_research_service(
            config=config,
            store=store,
            candidate=candidate,
            as_of=cutoff,
            binding=binding,
            admit=lambda retained, selected, records: selected,
        )
        # 수명주기 객체가 보강 한 번·공유 수정 두 번과 같은 실행 제약을 소유한다.
        lifecycle = NarrativeReportLifecycle(
            context=context,
            snapshot=snapshot,
            service=service,
            binding=binding,
            allowance=allowance,
            issuer_association=association,
            operator_folder=folder_binding,
            check_context=check_context,
        )
        return lifecycle

    outcome: Literal["completed", "partial", "empty", "ambiguous", "failed"] = "failed"
    reasons = ["COMPANY_WORKFLOW_FAILED"]
    completeness: Literal["full", "partial", "none"] = "none"
    # 5. 그래프가 분석 → 선택적 보강 → 작성·Judge → 인용·PDF 검사를 연결한다.
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
            # 검증 통과와 자료의 완전성은 별개다. 미상·부분 답변이 있으면 partial이다.
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
            # 그래프의 검증을 통과한 결과만 사용자용 report.md/report.pdf로 저장한다.
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
        # 실패 초안은 진단 자료로만 남긴다. 사용자용 보고서 파일로 오인되지 않게 지운다.
        for name in ("report.md", "report.pdf"):
            (out / name).unlink(missing_ok=True)
        md_path = pdf_path = None
    # 6. 성공·빈 결과·실패 모두 추적할 수 있도록 실행 기록과 최종 영수증을 남긴다.
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
    # 호출자는 이 영수증의 outcome·completeness·validation을 함께 읽어 결과를 판단한다.
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
