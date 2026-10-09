"""Original v3 composition with controlled wire responses, never company claims.

Run from this checkout with:
uv run --offline --no-sync python examples/v3_connection_check.py [OUTPUT]
uv run --offline --no-sync python examples/v3_connection_check.py OUTPUT CAPTURE

No credentials are read. MockTransport is mandatory in both modes. Source bytes
prove integrity only; positive semantic reviews/facts are deliberately absent.
All 23 unknown criteria stay Missing, including unknown founder/market identity.
"""

# allow: SIZE_OK - one explicit composition recipe; scope forbids helper modules.
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from pathlib import Path
from typing import Final, Literal, TypedDict, assert_never
from unittest.mock import patch
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

from skala_rag.agents.business_deal import evaluate_business_deal_approved
from skala_rag.agents.evaluation_v3_adapter import adapt_baseline_branch_result
from skala_rag.agents.evidence_research import EvidenceResearch
from skala_rag.agents.founder import evaluate_founder_approved
from skala_rag.agents.market import evaluate_market_approved
from skala_rag.agents.moat import evaluate_moat_approved
from skala_rag.agents.source_fact_verification import (
    SourceBoundReviewResolver,
    TrustedCapture,
    verify_original_capture,
)
from skala_rag.agents.technology import evaluate_technology_approved
from skala_rag.contracts import (
    Candidate,
    Chunk,
    EligibilityResult,
    Evidence,
    EvidenceProvenance,
    ResearchGap,
    RetrievalBundle,
    RetrievalRecord,
    RunInput,
    Source,
    ToolBudget,
)
from skala_rag.contracts.state import create_initial_state
from skala_rag.contracts.v3 import (
    BRANCH_DIMENSIONS,
    EvaluationBranchResult,
    EvaluationSnapshot,
)
from skala_rag.fakes import FakeClock
from skala_rag.graph.candidate_workflow_v3 import run_candidate_report_v3
from skala_rag.graph.candidates_v3 import CandidateStagesV3
from skala_rag.graph.research_artifacts_v3 import (
    CompanyResearchArtifactsV3,
    EvidenceResearchBindingV3,
)
from skala_rag.graph.snapshot import freeze_snapshot
from skala_rag.rag.adapter import IndexedRetriever, IndexSnapshot
from skala_rag.reporting.pdf import PDFLayoutValidator, PDFRenderer, load_pdf_profile
from skala_rag.reporting.v3_runtime import build_report_nodes_v3
from skala_rag.scoring.approval_registry import pinned_approval_registry
from skala_rag.scoring.approved_consumers import ActualAdmissionV3, ApprovedPolicySource
from skala_rag.scoring.approved_policy import LiveScoringGates, ScoringRuntimeBinding
from skala_rag.scoring.catalog import load_policy
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt
from skala_rag.tools.runtime import (
    AdapterRuntime,
    Allowance,
    BudgetLedger,
    CallContext,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
)
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM
from skala_rag.tools.source_fetch import content_hash
from skala_rag.tools.structured_llm import APPROVED_MODEL

ROOT: Final = Path(__file__).resolve().parents[1]
START: Final = datetime(2026, 9, 30, tzinfo=UTC)
TEXT: Final = "Controlled response only; no actual company observations.\n"
ROLES: Final = {*BRANCH_DIMENSIONS, "evidence_research", "generator", "judge"}
URL: Final = "https://example.invalid/control"
BranchRole = Literal["founder", "technology", "market", "moat", "business_deal"]
Role = BranchRole | Literal["evidence_research", "generator", "judge"]


class ReviewContext(TypedDict):
    review_request: bytes
    review_subject: str


class ConnectionCheckError(ValueError):
    """Connection or capture failed; the named stage is available to callers."""

    def __init__(self, stage: str):
        self.stage = stage
        super().__init__(f"connection check rejected: {stage}")


class WireRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    request_sha256: str
    response_body: str


class WireCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    execution_scope: Literal["controlled_response"]
    source_sha256: str
    wires: dict[Role, WireRecord]
    stable_hashes: dict[str, str]


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(value: JsonValue) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


class ControlledSearch:
    """Return the exact locally supplied index, without an encoder or network."""

    retry_owner = "runtime"

    def search_once(self, request, *, snapshot, allowed_chunk_ids, timeout_seconds):
        return snapshot.bundle.model_copy(
            deep=True,
            update={
                "chunks": [
                    c for c in snapshot.bundle.chunks if c.chunk_id in allowed_chunk_ids
                ]
            },
        )


def run_connection_check(
    output_dir: Path = ROOT / "outputs/issue168-connection-check",
    *,
    replay_capture: Path | None = None,
) -> Path:
    """Return receipt directory after real graph/report/PDF execution.

    Replay validates the complete capture commitment before any callback or
    request. The adjacent sha256 file is an integrity commitment, not a signature.
    Each run has a fresh shared runtime; no test helpers or evaluator stubs.
    """
    saved = None
    if replay_capture is not None:
        raw = replay_capture.read_bytes()
        if _hash(raw) != replay_capture.with_suffix(".sha256").read_text().strip():
            raise ConnectionCheckError("capture hash")
        saved = WireCapture.model_validate_json(raw)
        if saved.execution_scope != "controlled_response" or set(saved.wires) != ROLES:
            raise ConnectionCheckError("capture scope/roles")
        verify_path = replay_capture.parent / "source.capture"
        if (
            content_hash(verify_path.read_bytes()) != saved.source_sha256
            or (
                content_hash((replay_capture.parent / "source.txt").read_bytes())
                != saved.source_sha256
            )
            or saved.source_sha256 != content_hash(TEXT.encode())
        ):
            raise ConnectionCheckError("source capture")
    out = output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / "source.capture").write_text(TEXT, encoding="utf-8")
    (out / "source.txt").write_text(TEXT, encoding="utf-8")
    schema, run_id, cid, corpus, index = (
        "connection-check-1",
        "controlled-run",
        "controlled-candidate",
        "controlled-corpus",
        "controlled-index",
    )
    registry = pinned_approval_registry(ROOT)
    catalog = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
    source = Source(
        schema_version=schema,
        source_id="controlled-source",
        title="Controlled input",
        publisher=None,
        author=None,
        source_kind="report",
        url=URL,
        local_path=None,
        published_at=START.date(),
        retrieved_at=START,
        content_hash=content_hash(TEXT.encode()),
        language="en",
        access_notes="controlled_response; synthetic, not company evidence",
        bibliographic_metadata={"execution_scope": "controlled_response"},
    )
    trusted = TrustedCapture(
        path=out / "source.capture",
        allowed_root=out,
        extracted_path=out / "source.txt",
        format="text",
        charset="utf-8",
        source=source,
        corpus_version=corpus,
        extracted_text=TEXT,
        extracted_sha256=content_hash(TEXT.encode()),
    )
    verify_original_capture(trusted)
    candidate = Candidate(
        schema_version=schema,
        candidate_id=cid,
        canonical_name="Controlled candidate",
        aliases=[],
        country="US",
        legal_identifiers={},
        discovery_source_ids=[source.source_id],
    )
    run_input = RunInput(
        schema_version=schema,
        investment_theme="controlled_response; synthetic only",
        countries=["US"],
        languages=["en"],
        as_of=START.date(),
        policy_version="v3-operational-1.0.0",
        corpus_version=corpus,
        execution_mode="live",
    )
    clock = FakeClock(START)
    readiness = Readiness(
        schema_version=schema,
        required=True,
        configured=True,
        credential_required=False,
        credential_present=False,
        model_required=True,
        model_available=True,
        index_required=True,
        index_available=True,
    )
    limits = RuntimeLimits(
        schema_version=schema,
        max_calls=12,
        tool_max_calls={"openai": 10, "retrieve": 2},
        max_input_tokens=1000000,
        max_output_tokens=100000,
        max_cost_usd=Decimal("12"),
    )
    allowance = Allowance(
        schema_version=schema,
        input_tokens=100000,
        output_tokens=2000,
        max_cost_usd=Decimal("1"),
    )
    runtime_policy = RuntimePolicy(
        schema_version=schema,
        execution_mode="live",
        retry_delays_seconds=(),
        live_approval_reference="controlled_response:scope",
        timing_approval_reference="controlled_response:timing",
    )
    runtime = AdapterRuntime(
        policy=runtime_policy,
        ledger=BudgetLedger(limits),
        clock=clock,
        sleep=lambda _: (_ for _ in ()).throw(ConnectionCheckError("unexpected retry")),
    )
    budget = ToolBudget(
        schema_version=schema,
        max_calls=2,
        max_retries=0,
        timeout_seconds=30,
        deadline=START + timedelta(minutes=5),
    )
    gates = LiveScoringGates(
        run_id=run_id,
        policy_version="v3-operational-1.0.0",
        provider="openai",
        open_decisions=[],
        readiness=readiness,
        limits=limits,
        allowance=allowance,
        runtime_readiness_reference="controlled_response:readiness",
        call_budget_reference="controlled_response:calls",
        cost_budget_reference="controlled_response:cost",
    )
    call = CallContext(
        schema_version=schema,
        call_id="controlled-call",
        run_id=run_id,
        candidate_id=cid,
        tool_name="openai",
        node="controlled-preflight",
    )
    binding = ScoringRuntimeBinding(
        runtime=runtime,
        gates=gates,
        policy=runtime_policy,
        call=call,
        budget=budget,
        readiness=readiness,
        allowance=allowance,
        provider="openai",
        tool_name="openai",
    )
    approved_source = ApprovedPolicySource(
        path=ROOT / "configs/scoring.v3.json",
        approvals=registry.policy_approvals(),
        approval_verifier=registry.verify_policy,
        execution_mode="live",
        live_gates=gates,
        live_gate_verifier=lambda _gate, observed: observed == gates,
    )
    # Synthetic coverage support is not a positive semantic review or a rating.
    evidence = Evidence(
        schema_version=schema,
        evidence_id="controlled-evidence",
        candidate_id=cid,
        scope="company",
        criterion_ids=[c.criterion_id for c in catalog.criteria],
        claim=TEXT.strip(),
        source_id=source.source_id,
        locator=URL,
        excerpt=TEXT.strip(),
        provenance=[
            EvidenceProvenance(
                schema_version=schema,
                retrieval_id="controlled-seed-record",
                method="manual",
            )
        ],
        evidence_kind="reported",
        confidence="unknown",
        limitations=["controlled_response; no company facts"],
        supporting_evidence_ids=[],
        conflicts_with=[],
    )
    record = RetrievalRecord(
        schema_version=schema,
        retrieval_id="controlled-seed-record",
        run_id=run_id,
        candidate_id=cid,
        tool_name="controlled_source",
        query=None,
        arguments_without_secrets={"execution_scope": "controlled_response"},
        started_at=START,
        finished_at=START,
        status="ok",
        source_ids=[source.source_id],
        chunk_ids=[],
        evidence_ids=[evidence.evidence_id],
        error_id=None,
        cost=None,
        cache_hit=False,
    )
    eligibility = EligibilityResult(
        schema_version=schema,
        eligibility_result_id="controlled-eligibility",
        run_id=run_id,
        candidate_id=cid,
        evidence_revision=0,
        policy_version=run_input.policy_version,
        as_of=START.date(),
        status="eligible",
        checks={"execution_scope": "controlled_response", "synthetic": True},
        reason_codes=["CONTROLLED_RESPONSE"],
        evidence_ids=[evidence.evidence_id],
    )
    seed = CompanyResearchArtifactsV3(
        cid,
        run_id,
        schema,
        0,
        {source.source_id: source},
        {},
        [record],
        {evidence.evidence_id: evidence},
    )
    initial = create_initial_state(run_input.model_dump(mode="json"))
    initial.update(
        candidates=[candidate.model_dump(mode="json")],
        current_candidate_id=cid,
        sources={source.source_id: source.model_dump(mode="json")},
        evidence={evidence.evidence_id: evidence.model_dump(mode="json")},
        retrieval_history=[record.model_dump(mode="json")],
        eligibility_results={cid: eligibility.model_dump(mode="json")},
        evidence_revisions={cid: 0},
        evaluation_rounds={cid: 0},
    )
    frozen = freeze_snapshot(
        cid,
        initial,
        run_input,
        run_id=run_id,
        index_version=index,
        schema_version=schema,
        allowed_source_ids=[source.source_id],
        industry_evidence_ids=(),
        clock=clock.now,
    )
    admission = ActualAdmissionV3(
        source=approved_source,
        runtime_binding=binding,
        registry=registry,
        run_input=run_input,
        index_version=index,
        execution_scope="controlled_response",
        review_resolvers={
            (frozen.snapshot_id, version): SourceBoundReviewResolver(
                frozen,
                registry.rubric(version),
                sources={source.source_id: trusted},
                reviews=(),
            )
            for version in ("core-0.1.0", "finance-0.1.0")
        },
    )
    policy = admission.load_policy().operational
    wires: dict[Role, WireRecord] = {}
    mock_requests: list[str] = []
    evaluator_snapshots: dict[str, str] = {}

    def transport(role: Role) -> OpenAIResponsesAttempt:
        def respond(request: httpx.Request) -> httpx.Response:
            mock_requests.append(role)
            request_hash = _hash(request.content)
            if saved is not None:
                wire = saved.wires[role]
                if request_hash != wire.request_sha256:
                    raise ConnectionCheckError("replay request")
                response_body = wire.response_body
            else:
                payload = json.loads(request.content)
                user = json.loads(payload["input"][1]["content"])
                match role:
                    case "founder" | "technology" | "market" | "moat" | "business_deal":
                        dimensions = BRANCH_DIMENSIONS[role]
                        outputs = {}
                        for dimension in dimensions:
                            criteria = [
                                {
                                    "criterion_id": c.criterion_id,
                                    "status": "missing",
                                    "rating": None,
                                    "evidence_ids": [],
                                    "rationale": "controlled_response; facts unknown",
                                    "missing_reason": (
                                        "not_disclosed"
                                        if role == "market"
                                        else "independent facts not supplied"
                                    ),
                                    "applicability_note": None,
                                    **(
                                        {
                                            "schema_version": schema,
                                            "applicability_reason": None,
                                            "applicability_rule_id": None,
                                            "applicability_evidence_ids": None,
                                        }
                                        if role == "business_deal"
                                        else {}
                                    ),
                                }
                                for c in policy.criteria
                                if c.dimension == dimension
                            ]
                            outputs[dimension] = dict(
                                criteria=criteria,
                                research_gaps=[],
                                caveats=["controlled_response"],
                            )
                        response_output = (
                            outputs if role == "business_deal" else outputs[role]
                        )
                    case "evidence_research":
                        response_output = {"claims": []}
                    case "generator":
                        response_output = dict(
                            schema_version=schema,
                            summary=(
                                "controlled_response 합성 연결 점검이다. "
                                "실제 기업 평가가 아니다."
                            ),
                            company_team=(
                                "합성 제어 입력이다. 창업자 신원과 이력은 미상이다."
                            ),
                            technology="기술 관측은 미상이며 성능을 주장하지 않는다.",
                            market="시장과 시장 수치는 미상이다.",
                            assessment_risks=(
                                "독립된 사실과 검토가 없으므로 "
                                "투자 판단에 사용할 수 없다."
                            ),
                            limitations=[
                                "controlled_response; synthetic; "
                                "actual_provider_calls=0"
                            ],
                        )
                    case "judge":
                        response_output = dict(
                            schema_version=schema,
                            verdict="pass",
                            context_id=user["context_id"],
                            findings=[
                                {
                                    "schema_version": schema,
                                    "severity": "stub",
                                    "claim_location": "whole_report",
                                    "evidence_ids": [],
                                    "reason": (
                                        "controlled_response; synthetic Judge, "
                                        "not semantic authority"
                                    ),
                                }
                            ],
                            revision_instructions=[],
                            judged_artifact_hash=user["artifact_hash"],
                        )
                    case unreachable:
                        assert_never(unreachable)
                response = dict(
                    model=APPROVED_MODEL,
                    status="completed",
                    output=[
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps(response_output),
                                }
                            ],
                        }
                    ],
                    usage={"input_tokens": 10, "output_tokens": 10},
                )
                response_body = httpx.Response(200, json=response).content.decode(
                    "utf-8"
                )
            wires[role] = WireRecord(
                request_sha256=request_hash,
                response_body=response_body,
            )
            return httpx.Response(
                200,
                content=response_body.encode("utf-8"),
                headers={"content-type": "application/json"},
            )

        return OpenAIResponsesAttempt(
            api_key="CONTROLLED-NOT-A-CREDENTIAL",
            prompt_version="controlled_response-1",
            schema_version=schema,
            clock=clock,
            http_transport=httpx.MockTransport(respond),
        )

    def llm(role: Role) -> RuntimeStructuredLLM:
        return RuntimeStructuredLLM(
            runtime=runtime,
            call=call.model_copy(update={"node": f"{role}_evaluation"}),
            budget=budget,
            readiness=readiness,
            transport=transport(role),
            allowance_for=lambda _system, _user, _schema: allowance,
        )

    def evaluate(
        role: BranchRole, snapshot: EvaluationSnapshot
    ) -> EvaluationBranchResult:
        evaluator_snapshots[role] = _hash(_json(snapshot.model_dump(mode="json")))
        options: ReviewContext = dict(
            review_request=b"controlled_response",
            review_subject="Controlled subject",
        )
        match role:
            case "founder":
                result = evaluate_founder_approved(
                    snapshot,
                    actual_admission=admission,
                    founder_person_ids=(),
                    verified_person_by_evidence_id={},
                    llm=llm(role),
                    reviewed_anchors={},
                    **options,
                )
            case "technology":
                result = evaluate_technology_approved(
                    snapshot,
                    actual_admission=admission,
                    llm=llm(role),
                    receipts={},
                    **options,
                ).result
            case "market":
                result = evaluate_market_approved(
                    snapshot,
                    actual_admission=admission,
                    llm=llm(role),
                    target_market=None,
                    market_links={},
                    rubric=registry.rubric("core-0.1.0"),
                )
            case "moat":
                return evaluate_moat_approved(
                    snapshot,
                    actual_admission=admission,
                    llm=llm(role),
                    reviewed_anchors={},
                    **options,
                )
            case "business_deal":
                return evaluate_business_deal_approved(
                    snapshot,
                    admission=admission,
                    llm=llm(role),
                    rubric=registry.rubric("finance-0.1.0"),
                    **options,
                )
            case unreachable:
                assert_never(unreachable)
        return adapt_baseline_branch_result(
            result,
            branch_id=role,
            snapshot=snapshot,
            criteria=policy.criteria,
            industry_evidence_dimensions=(),
            execution_mode="live",
        )

    callbacks = {
        role: lambda snapshot, role=role: evaluate(role, snapshot)
        for role in ("founder", "technology", "market", "moat", "business_deal")
    }
    chunk = Chunk(
        schema_version=schema,
        chunk_id="controlled-chunk",
        source_id=source.source_id,
        corpus_version=corpus,
        text=TEXT,
        locator=URL,
        candidate_ids=[cid],
        scope="company",
        language="en",
        embedding_model="controlled-no-encoder",
        embedding_revision="controlled-1",
    )
    index_snapshot = IndexSnapshot(
        schema_version=schema,
        corpus_version=corpus,
        corpus_hash=source.content_hash,
        index_version=index,
        embedding_model=chunk.embedding_model,
        embedding_revision=chunk.embedding_revision,
        search_settings={"controlled_response": True},
        bundle=RetrievalBundle(
            schema_version=schema, chunks=[chunk], sources={source.source_id: source}
        ),
    )
    retrieve = IndexedRetriever(
        snapshot=index_snapshot,
        backend=ControlledSearch(),
        runtime=runtime,
        readiness=readiness,
        budget=budget,
        allowance=Allowance(
            schema_version=schema,
            input_tokens=0,
            output_tokens=0,
            max_cost_usd=Decimal(0),
        ),
        run_id=run_id,
        schema_version=schema,
        tool_name="retrieve",
    )
    gap = ResearchGap(
        schema_version=schema,
        gap_id="controlled-gap",
        candidate_id=cid,
        criterion_id="technology.integration",
        missing_fields=["claim"],
        reason="controlled_response",
        priority_weight=1,
        suggested_queries=["controlled_response"],
        attempted_retrieval_ids=[],
        status="open",
    )
    research_llm = llm("evidence_research")
    research_llm.call = call.model_copy(update={"node": "evidence_research"})
    producer = EvidenceResearch(
        retrieve=retrieve,
        rag_required=True,
        llm=research_llm,
        initial_plan=lambda _: [gap],
        run_id=run_id,
        corpus_version=corpus,
        index_version=index,
        as_of=START.date(),
        top_k=1,
        allowed_source_ids=[source.source_id],
        clock=clock,
        schema_version=schema,
        execution_mode="live",
    )
    research_binding = EvidenceResearchBindingV3(
        research=producer,
        budget=budget,
        run_input=run_input,
        run_id=run_id,
        schema_version=schema,
        index_version=index,
        allowed_source_ids=frozenset([source.source_id]),
        industry_evidence_ids=frozenset(),
        actual_admission=admission,
    )

    def legacy_denied(*_args):
        raise ConnectionCheckError("legacy research/freeze")

    stages = CandidateStagesV3(
        discover=lambda: [candidate],
        normalize=lambda items: items,
        research=lambda _: seed,
        eligibility=lambda *_: eligibility,
        collect=legacy_denied,
        freeze=legacy_denied,
        evidence_research=research_binding,
    )
    generator, judge = build_report_nodes_v3(
        runtime=runtime,
        generator_call=call.model_copy(update={"node": "report_generator"}),
        judge_call=call.model_copy(update={"node": "report_judge"}),
        budget=budget,
        readiness=readiness,
        generator_transport=transport("generator"),
        judge_transport=transport("judge"),
        allowance_for=lambda *_: allowance,
    )
    profile = load_pdf_profile(ROOT / "configs/pdf.layout.v1.json")
    renders = []

    def check_pdf(draft, context, structural, judged):
        render = PDFRenderer(
            profile=profile,
            output_dir=out,
            proof=lambda _: (structural, judged),
            execution_mode="live",  # Match original context; wire finding locks final.
        )(draft, profile.version)
        renders.append(render)
        return PDFLayoutValidator()(draft, context, render)

    identities = count(1)
    # IDs are explicit synthetic controls; real runtime accounting still executes.
    with (
        patch("skala_rag.tools.runtime.uuid4", lambda: UUID(int=next(identities))),
        patch("skala_rag.rag.adapter.uuid4", lambda: UUID(int=next(identities))),
        patch("reportlab.rl_config.invariant", 1),
    ):
        result, context, report = run_candidate_report_v3(
            stages,
            callbacks,
            run_input=run_input,
            generate=generator,
            judge=judge,
            check_pdf=check_pdf,
            policy=policy,
            catalog=catalog,
            catalog_policy_version=catalog.policy_version,
            run_id=run_id,
            schema_version=schema,
            support_check=lambda _criterion, items: bool(items),
            applicability_assessments=lambda _: {},
            applicability_check=lambda *_: False,
            applicability_verifier=None,
            industry_evidence_dimensions=(),
            clock=clock.now,
            approved_policy_source=approved_source,
            actual_admission=admission,
        )
    state = result.research_artifacts[cid]["state"]
    if not renders:
        raise ConnectionCheckError(
            f"report={report.error_code}; wires={sorted(wires)}; "
            f"runtime_errors={[e.error_code for e in runtime.error_history.values()]}; "
            f"validation={report.validation}"
        )
    render = renders[-1]
    if (
        report.status != "completed"
        or report.warning
        or report.pdf_validation is None
        or not report.pdf_validation.valid
        or report.draft is None
        or report.final_allowed
        or set(wires) != ROLES
        or len(state.get("evaluations_v3", {})) != 6
        or set(evaluator_snapshots) != set(BRANCH_DIMENSIONS)
    ):
        raise ConnectionCheckError(
            f"original composition/report: status={report.status}; "
            f"dimensions={len(state.get('evaluations_v3', {}))}; "
            f"pdf={report.pdf_validation}; "
            f"branches={sorted(evaluator_snapshots)}; errors={result.errors}"
        )
    stable_hashes = {
        "state": _hash(_json(state)),
        "context": _hash(_json(context.snapshot())),
        "report": _hash(_json(report.draft.model_dump(mode="json"))),
        "pdf": _hash(Path(render.artifact_path).read_bytes()),
        **{
            name: _hash((ROOT / "configs" / name).read_bytes())
            for name in ("scoring.v3.json", "rubrics/core.yaml", "rubrics/finance.yaml")
        },
    }
    if saved is not None and stable_hashes != saved.stable_hashes:
        raise ConnectionCheckError("replay artifact hashes")
    capture = WireCapture(
        execution_scope="controlled_response",
        source_sha256=source.content_hash,
        wires=wires,
        stable_hashes=stable_hashes,
    )
    (out / "capture.json").write_bytes(_json(capture.model_dump(mode="json")))
    (out / "capture.sha256").write_text(_hash((out / "capture.json").read_bytes()))
    for name, payload in (
        ("state", state),
        ("context", context.snapshot()),
        ("report", report.draft.model_dump(mode="json")),
    ):
        (out / f"{name}.json").write_bytes(_json(payload))
    (out / "report.md").write_text(report.draft.markdown, encoding="utf-8")
    receipt = {
        "execution_scope": "controlled_response",
        "synthetic": True,
        "synthetic_controls": [
            "candidate",
            "eligibility",
            "coverage",
            "source",
            "index",
            "wire",
            "ids",
        ],
        "semantic_reviews": [],
        "financial_facts": [],
        "actual_provider_calls": 0,
        "external_requests": 0,
        "replay": saved is not None,
        "final_allowed": report.final_allowed,
        "publication_allowed": False,
        "branches": sorted(evaluator_snapshots),
        "evaluator_snapshot_hashes": evaluator_snapshots,
        "promoted_dimensions": sorted(
            e["dimension"] for e in state["evaluations_v3"].values()
        ),
        "ledger": runtime.ledger.snapshot(),
        "mock_transport_requests": len(mock_requests),
        "stable_hashes": stable_hashes,
        "context_id": context.context_id,
        "snapshot_id": frozen.snapshot_id,
        "report_status": report.status,
        "pdf": {
            "path": render.artifact_path,
            "sha256": _hash(Path(render.artifact_path).read_bytes()),
            "page_count": render.page_count,
            "summary_fraction": render.layout_measurements["summary_fraction"],
            "verified": render.layout_measurements["pdf_verified"],
            "final_allowed": render.layout_measurements["final_allowed"],
        },
        "capture": str(out / "capture.json"),
    }
    (out / "receipt.json").write_bytes(
        _json(TypeAdapter(JsonValue).validate_python(receipt))
    )
    return out


if __name__ == "__main__":
    destination = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else ROOT / "outputs/issue168-connection-check"
    )
    replay = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    print(run_connection_check(destination, replay_capture=replay) / "receipt.json")
