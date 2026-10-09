"""Single-company local research demo; never a fixture investment decision."""

import hashlib
import json
import multiprocessing
import os
import shutil
import signal
import time
from contextlib import nullcontext
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from skala_rag.agents.m2_local_rag import LocalRAG
from skala_rag.agents.m2_research_live import Clock
from skala_rag.contracts import Candidate, RunInput
from skala_rag.contracts.common import Contract, Text
from skala_rag.demo_budget import Campaign
from skala_rag.demo_context import (
    CANDIDATE_ID,
    COMPANY,
    SCHEMA,
    WARNING,
    build_research_context,
    research_material,
)
from skala_rag.demo_scoring import CriterionRating, load_demo_rubric
from skala_rag.prompt.local_demo import (
    GENERATOR_SUFFIX,
    JUDGE_SUFFIX,
    REVIEWER_SYSTEM,
    common_suffix,
)
from skala_rag.prompt.versions import LOCAL_DEMO_COMPOSITION_VERSION
from skala_rag.reporting.korean_report import build_korean_report_pdf
from skala_rag.reporting.v3_context import canonical
from skala_rag.reporting.v3_pipeline import (
    ReportContentV3,
    ReportGeneratorV3,
    ReportRunV3,
    SemanticJudgeV3,
    validate_report_v3,
)
from skala_rag.settings import (
    LocalDemoSettings,
    RuntimeDocument,
    load_runtime_document,
    resolve_demo_credential,
)
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt, byte_bound_allowance
from skala_rag.tools.structured_llm import APPROVED_MODEL

ROLES = ("founder", "market", "technology", "moat", "business_deal")
DIAGNOSTIC_CODES = frozenset(
    {
        "LOCAL_RETRIEVAL_FAILED",
        "LOCAL_INDEX_MISSING",
        "CAMPAIGN_EXPIRED",
        "BUDGET_EXHAUSTED",
        "REPORT_RETRY_REQUIRES_APPROVAL",
        "REPORT_REVISION_REQUIRED",
        "JUDGE_PASS_CONTRADICTORY",
        "GENERATOR_EVIDENCE_INVALID",
        "GENERATOR_CITATIONS_MISSING",
        "REVIEW_EVIDENCE_INVALID",
        "REVIEW_RATING_INVALID",
        "REVIEW_CRITERIA_INVALID",
        "DEMO_RUBRIC_INVALID",
        "CONTEXT_INVALID",
        "REPORT_REJECTED",
        "REPORT_NOT_VERIFIED",
        "PDF_PROOF_INVALID",
        "TOOL_FAILED",
        "TOOL_TIMEOUT",
        "TOOL_RESPONSE_INVALID",
        "TOOL_AUTH_FAILED",
        "LLM_FAILED",
        "LLM_TIMEOUT",
        "LLM_OUTPUT_INVALID",
        "DEMO_EXECUTION_FAILED",
        "DEMO_WORKER_EXITED",
    }
)


def _diagnostic(exc):
    code = getattr(exc, "error_code", None)
    if isinstance(code, str) and code in DIAGNOSTIC_CODES:
        return code
    if type(exc) is ValueError and len(exc.args) == 1:
        code = exc.args[0]
        if isinstance(code, str) and code in DIAGNOSTIC_CODES:
            return code
    return "DEMO_EXECUTION_FAILED"


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: Text
    evidence_ids: list[Text] = Field(min_length=1)


class Review(Contract):
    criteria: list[CriterionRating]
    observations: list[Claim]
    interpretations: list[Claim]
    missing: list[Text]


class ResearchSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    facts: list[Claim]
    interpretation: Text
    unknown: Text


class ResearchContent(Contract):
    summary: ResearchSection
    company_team: ResearchSection
    technology: ResearchSection
    market: ResearchSection
    assessment_risks: ResearchSection
    limitations: list[Text]


def cited_content(content, allowed):
    bodies, used = {}, set()
    for name in ("summary", "company_team", "technology", "market", "assessment_risks"):
        section = getattr(content, name)
        facts = []
        for claim in section.facts:
            if not set(claim.evidence_ids) <= allowed:
                raise ValueError("GENERATOR_EVIDENCE_INVALID")
            used.update(claim.evidence_ids)
            citations = " ".join(f"[@evidence:{eid}]" for eid in claim.evidence_ids)
            facts.append("[자료에서 확인] " + claim.text + " " + citations)
        bodies[name] = "\n\n".join(
            [
                *facts,
                "[분석·해석] " + section.interpretation,
                "[판단 불가] " + section.unknown,
            ]
        )
    if not used:
        raise ValueError("GENERATOR_CITATIONS_MISSING")
    return enforce_scope_notice(
        ReportContentV3(
            schema_version=content.schema_version,
            **bodies,
            limitations=content.limitations,
        )
    )


class DemoState(TypedDict, total=False):
    material: dict
    rubric: dict
    reviews: dict
    report_status: str


def enforce_scope_notice(content):
    if WARNING not in content.summary:
        return content.model_copy(update={"summary": WARNING + "\n" + content.summary})
    return content


def _save(path, value):
    def encode(item):
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        if isinstance(item, Decimal):
            return str(item)
        raise TypeError("unsupported artifact payload")

    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, indent=2, default=encode)
    )
    pending = path.with_name(path.name + ".pending")
    with pending.open("w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)


def _retrieve(*, root, run_id, deadline, runtime_document=None):
    rag = LocalRAG(
        root=root,
        model_path=root / "data/local/models/bge-m3-5617a9f",
        store_path=root / "outputs/issue180-local-bge/index.sqlite",
        receipt_path=root / "outputs/issue180-local-bge/validation.json",
        runtime_document=runtime_document,
    )
    run_input = RunInput(
        schema_version=SCHEMA,
        investment_theme=COMPANY + " 투자 검토",
        countries=["US"],
        languages=["ko", "en"],
        as_of=datetime.now(UTC).date(),
        policy_version="unscored-research-only-1",
        corpus_version=rag.snapshot.corpus_version,
        execution_mode="live",
    )
    candidate = Candidate(
        schema_version=SCHEMA,
        candidate_id=CANDIDATE_ID,
        canonical_name=COMPANY,
        aliases=[],
        country="US",
        legal_identifiers={},
        discovery_source_ids=list(rag.snapshot.bundle.sources),
    )
    result = rag.retrieve(
        candidate=candidate,
        run_input=run_input,
        run_id=run_id,
        query=(
            "pi0 pi0.5 robot foundation model generalization "
            "limitations training evaluation"
        ),
        clock=Clock(),
        deadline=deadline,
    )
    if result.status != "ok" or result.data is None:
        raise ValueError("LOCAL_RETRIEVAL_FAILED")
    return research_material(
        root=root, bundle=result.data, records=result.retrieval_records, run_id=run_id
    )


def _run_report_once(context, *, generate, judge, check_pdf):
    """The demo approval allows one draft, one Judge and no repair calls."""
    draft = validation = judgement = pdf = None
    code = None
    try:
        draft = generate(context, ())
        validation = validate_report_v3(draft, context)
        if not validation.valid:
            code = (
                "CONTEXT_INVALID"
                if validation.checks.get("action") == "fail"
                else "REPORT_REVISION_REQUIRED"
            )
        else:
            judgement = judge(draft, context)
            if judgement.verdict == "revise":
                code = "REPORT_REVISION_REQUIRED"
            elif judgement.verdict == "fail":
                code = "REPORT_REJECTED"
            elif judgement.findings or judgement.revision_instructions:
                code = "JUDGE_PASS_CONTRADICTORY"
            else:
                pdf = check_pdf(draft, context, validation, judgement)
                if (
                    pdf.context_id != context.context_id
                    or pdf.artifact_hash != validation.artifact_hash
                ):
                    code = "PDF_PROOF_INVALID"
                elif not pdf.valid:
                    code = (
                        "TOOL_FAILED"
                        if pdf.checks.get("action") == "fail"
                        else "REPORT_REVISION_REQUIRED"
                    )
    except Exception as exc:
        code = _diagnostic(exc)
    return ReportRunV3(
        status="failed" if code else "completed",
        warning=False,
        draft=draft,
        validation=validation,
        judgement=judgement,
        revisions=0,
        context_id=context.context_id,
        error_code=code,
        pdf_validation=pdf,
    )


class DemoLLM:
    """One actual request per node. No transport or report repair retries."""

    def __init__(
        self,
        *,
        key,
        campaign,
        node,
        receipt,
        destination,
        progress,
        runtime_document: RuntimeDocument | None = None,
    ):
        if not key or not key.strip():
            raise ValueError("api_key is required")
        self.runtime_document = (
            runtime_document
            if runtime_document is not None
            else getattr(campaign, "runtime_document", None) or load_runtime_document()
        )
        self.settings = self.runtime_document.profiles.local_demo
        LocalDemoSettings.model_validate_json(
            self.settings.model_dump_json(), strict=True
        )
        self.campaign, self.node = campaign, node
        self.receipt, self.destination, self.progress = receipt, destination, progress
        self.called = False
        self.transport = OpenAIResponsesAttempt(
            api_key=key,
            prompt_version=LOCAL_DEMO_COMPOSITION_VERSION,
            schema_version=SCHEMA,
            clock=Clock(),
            llm_settings=self.runtime_document.llm,
        )

    def generate(self, *, system, user, output_schema):
        if self.called:
            raise ValueError("REPORT_RETRY_REQUIRES_APPROVAL")
        self.called = True
        self.progress(self.node)
        system += common_suffix(SCHEMA)
        if self.node == "generator":
            output_schema = ResearchContent
            system += GENERATOR_SUFFIX
        if self.node == "judge":
            system += JUDGE_SUFFIX
        allowance = byte_bound_allowance(
            system,
            user,
            output_schema,
            schema_version=SCHEMA,
            max_output_tokens=self.settings.request_output_tokens,
            usd_per_input_token=self.runtime_document.llm.usd_per_input_token,
            usd_per_output_token=self.runtime_document.llm.usd_per_output_token,
        )
        self.campaign.reserve(allowance)
        remaining = (
            datetime.fromisoformat(self.campaign.state["deadline"]) - datetime.now(UTC)
        ).total_seconds()
        try:
            response = self.transport.generate_once(
                system=system,
                user=user,
                output_schema=output_schema,
                timeout_seconds=min(
                    self.settings.request_timeout_seconds,
                    max(self.settings.minimum_timeout_seconds, remaining),
                ),
                input_token_limit=allowance.input_tokens,
                output_token_limit=allowance.output_tokens,
            )
            self.campaign.check_time()
            _save(self.destination / f"{self.node}-response.json", response.data)
            if (
                self.node == "judge"
                and response.data.verdict == "pass"
                and (response.data.findings or response.data.revision_instructions)
            ):
                raise ValueError("JUDGE_PASS_CONTRADICTORY")
            return (
                cited_content(
                    response.data, set(json.loads(user)["context"]["evidence"])
                )
                if self.node == "generator"
                else response.data
            )
        finally:
            for call in self.transport.llm_calls:
                self.receipt["llm_calls"].append(
                    {
                        "node": self.node,
                        "model": call.model,
                        "status": call.status,
                        "input_tokens": call.input_tokens,
                        "output_tokens": call.output_tokens,
                        "error_code": call.error_code,
                    }
                )
            _save(self.destination / "run-result.json", self.receipt)


def run_demo(*, root: Path, company: str, progress) -> Path:
    """Supervise all blocking work under the campaign's absolute deadline.

    The parent alone owns admission and finalization. A POSIX process group
    contains retrieval, model transport and renderer descendants; deadline
    cancellation kills that group rather than abandoning a running thread.
    """
    if company.strip().casefold() not in (COMPANY.casefold(), "피지컬 인텔리전스"):
        raise ValueError("COMPANY_NOT_SUPPORTED")
    key = resolve_demo_credential(root)
    if not key or not key.strip():
        raise ValueError("OPENAI_API_KEY_MISSING")
    root = root.resolve()
    with Campaign(root) as campaign:
        destination = root / "outputs" / ("demo180-" + uuid4().hex)
        destination.mkdir(exist_ok=False)
        receipt = {
            "run_id": destination.name,
            "status": "running",
            "execution_mode": "live",
            "company": COMPANY,
            "publication_allowed": False,
            "whole_m3_verified": False,
            "warnings": [WARNING],
            "llm_calls": [],
            "actual_billing_usd": None,
        }
        _save(destination / "run-result.json", receipt)
        # A supervisor crash also requires explicit review, never automatic retry.
        campaign.finish_run(destination.name, "running")
        context = multiprocessing.get_context("fork")
        receiver, sender = context.Pipe(duplex=False)
        worker = context.Process(
            target=_demo_process,
            kwargs=dict(
                root=root,
                key=key,
                campaign=campaign,
                destination=destination,
                sender=sender,
            ),
        )
        start = time.monotonic()
        remaining = (
            datetime.fromisoformat(campaign.state["deadline"]) - datetime.now(UTC)
        ).total_seconds()
        deadline = start + max(0, remaining)
        failure = None
        try:
            worker.start()
            sender.close()
            while worker.is_alive():
                if time.monotonic() >= deadline:
                    failure = "CAMPAIGN_EXPIRED"
                    break
                if receiver.poll(min(0.05, max(0, deadline - time.monotonic()))):
                    try:
                        progress(receiver.recv())
                    except EOFError:
                        worker.join(timeout=0.01)
            if not failure and worker.exitcode != 0:
                failure = "DEMO_WORKER_EXITED"
            if not failure and time.monotonic() >= deadline:
                failure = "CAMPAIGN_EXPIRED"
        except BaseException as exc:
            failure = _diagnostic(exc)
        finally:
            if worker.pid is not None:
                # Kill renderer children too, even when their direct parent exited.
                try:
                    os.killpg(worker.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if worker.is_alive():
                    worker.kill()
                worker.join(timeout=1)
            receiver.close()
            sender.close()
            campaign.state = json.loads(campaign.path.read_text())
            receipt = json.loads((destination / "run-result.json").read_text())
            if failure or receipt.get("status") == "running":
                receipt.update(
                    status="failed",
                    error_code=failure or "DEMO_WORKER_EXITED",
                    report_verified=False,
                    pdf_verified=False,
                )
                for name in ("report.pdf", "report.html", "report.md"):
                    (destination / name).unlink(missing_ok=True)
            campaign.finish_run(destination.name, receipt["status"])
            receipt["campaign"] = campaign.state.copy()
            receipt["elapsed_seconds"] = time.monotonic() - start
            receipt["artifact_hashes"] = {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in destination.iterdir()
                if p.is_file()
                and p.name != "run-result.json"
                and not p.name.endswith(".pending")
            }
            _save(destination / "run-result.json", receipt)
        return destination


def _demo_process(*, root, key, campaign, destination, sender):
    os.setsid()
    try:
        _run_demo_body(
            root=root,
            key=key,
            campaign=campaign,
            destination=destination,
            progress=sender.send,
        )
    finally:
        sender.close()


def _run_demo_body(*, root, key, campaign, destination, progress):
    runtime_document = (
        getattr(campaign, "runtime_document", None) or load_runtime_document()
    )
    with nullcontext(campaign):
        started = time.monotonic()
        run_id = destination.name
        trace = []
        receipt = {
            "run_id": run_id,
            "status": "running",
            "execution_mode": "live",
            "company": COMPANY,
            "publication_allowed": False,
            "whole_m3_verified": False,
            "warnings": [WARNING],
            "llm_calls": [],
            "approval_reference": campaign.state["approval_reference"],
            "actual_billing_usd": None,
            "model": APPROVED_MODEL,
            "pricing_reference": runtime_document.llm.pricing_reference,
            "pricing_verified_on": runtime_document.llm.pricing_checked_on,
            "reused_generated_report": False,
        }

        def stage(name):
            campaign.check_time()
            trace.append({"node": name, "elapsed_seconds": time.monotonic() - started})
            _save(destination / "trace.json", trace)
            progress(name)

        def llm(node):
            return DemoLLM(
                key=key,
                campaign=campaign,
                node=node,
                receipt=receipt,
                destination=destination,
                progress=stage,
                runtime_document=runtime_document,
            )

        def retrieve_node(_):
            stage("local_rag")
            material = _retrieve(
                root=root,
                run_id=run_id,
                deadline=datetime.fromisoformat(campaign.state["deadline"]),
                runtime_document=runtime_document,
            )
            _save(destination / "retrieval.json", material)
            _save(destination / "evidence.json", material["evidence"])
            _save(destination / "sources.json", material["sources"])
            rubric = load_demo_rubric(root)
            _save(destination / "scoring-rubric.json", rubric)
            return {"material": material, "reviews": {}, "rubric": rubric}

        def reviewer(role):
            def review(state):
                result = llm(role).generate(
                    system=REVIEWER_SYSTEM,
                    user=canonical(
                        {
                            "role": role,
                            "rubric": {
                                "criteria": {
                                    cid: state["rubric"]["criteria"][cid]
                                    for cid in state["rubric"]["roles"][role]
                                },
                                "common_rules": state["rubric"]["common_rules"],
                                "artifacts": state["rubric"]["artifacts"],
                            },
                            "evidence": state["material"]["evidence"],
                        }
                    ),
                    output_schema=Review,
                )
                reviews = {**state["reviews"], role: result.model_dump(mode="json")}
                build_research_context(
                    run_id=run_id,
                    material=state["material"],
                    reviews=reviews,
                    rubric=state["rubric"],
                )
                _save(destination / "reviews.json", reviews)
                return {"reviews": reviews}

            return review

        def report_node(state):
            context = build_research_context(
                run_id=run_id,
                material=state["material"],
                reviews=state["reviews"],
                rubric=state["rubric"],
            )
            _save(destination / "report-context.json", context.payload)
            _save(destination / "role-scores.json", context.snapshot()["role_scores"])
            receipt.update(
                role_scoring_method=state["rubric"]["method"],
                role_evaluation_performed=True,
                eligibility_checked=False,
                recommendation_performed=False,
            )
            rendered = None

            def pdf(draft, ctx, structural, judged):
                nonlocal rendered
                stage("pdf")
                rendered, validation = build_korean_report_pdf(
                    ctx, draft, structural, judged, destination / "rendered", "live"
                )
                _save(destination / "render-result.json", rendered)
                return validation

            result = _run_report_once(
                context,
                generate=ReportGeneratorV3(llm("generator")),
                judge=SemanticJudgeV3(llm("judge")),
                check_pdf=pdf,
            )
            _save(destination / "report-pipeline.json", asdict(result))
            if result.draft:
                _save(destination / "draft.md", result.draft.markdown)
            valid = (
                result.status == "completed"
                and not result.warning
                and result.pdf_validation is not None
                and result.pdf_validation.valid
            )
            if valid and rendered and rendered.artifact_path:
                campaign.check_time()
                source = Path(rendered.artifact_path)
                shutil.copyfile(source, destination / "report.pdf")
                shutil.copyfile(
                    source.with_suffix(".html"), destination / "report.html"
                )
                _save(destination / "report.md", result.draft.markdown)
                receipt.update(
                    status="completed",
                    report_verified=True,
                    pdf_verified=True,
                    pdf_pages=rendered.page_count,
                    layout_measurements=rendered.layout_measurements,
                )
            else:
                receipt.update(
                    status="warning" if result.warning else "failed",
                    error_code=result.error_code or "REPORT_NOT_VERIFIED",
                )
            return {"report_status": receipt["status"]}

        try:
            graph = StateGraph(DemoState)
            graph.add_node("retrieve", retrieve_node)
            previous = "retrieve"
            graph.add_edge(START, previous)
            for role in ROLES:
                graph.add_node(role, reviewer(role))
                graph.add_edge(previous, role)
                previous = role
            graph.add_node("report", report_node)
            graph.add_edge(previous, "report")
            graph.add_edge("report", END)
            graph.compile().invoke({})
        except Exception as exc:
            # Never expose raw exception text, credentials or transport bodies.
            receipt.update(
                status="failed",
                error_code=_diagnostic(exc),
            )
        finally:
            receipt["elapsed_seconds"] = time.monotonic() - started
            receipt["campaign"] = campaign.state.copy()
            _save(destination / "trace.json", trace)
            receipt["artifact_hashes"] = {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in destination.iterdir()
                if p.is_file() and p.name != "run-result.json"
            }
            _save(destination / "run-result.json", receipt)
        return destination
