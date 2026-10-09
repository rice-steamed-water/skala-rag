"""SYNTHETIC authority and socket-blocked wires, never actual execution proof.

These tests drive the new callable and all original consumers. Native transport
tests intercept httpx.Client below OpenAIResponsesAttempt; no key is read and no
HTTP request leaves the process. Synthetic model files/vectors are not BGE proof.
"""

import importlib.util
import json
import socket
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from threading import Event, Lock

import httpx
import pytest
from tests.unit.test_company_archive import options, synthetic_archive
from tests.unit.test_index_v3 import (
    FakeEmbedder,
    chunk,
    corpus,
    document,
    settings,
    source,
)
from tests.unit.test_openai_attempt import body

from skala_rag.agents.founder_verification import ReviewedFounderAnchor
from skala_rag.agents.moat_verification import (
    _digest,
    core_artifact_digest,
    frozen_snapshot_digest,
)
from skala_rag.agents.source_fact_verification import (
    CompanyObservationReviewRequest,
    SourceBoundReview,
    SourceTextSpan,
    TrustedCapture,
    review_receipt_digest,
)
from skala_rag.contracts import (
    Candidate,
    DiscoveryBundle,
    Evidence,
    EvidenceProvenance,
    ResearchGap,
    RetrievalRecord,
    RunInput,
    ToolResult,
)
from skala_rag.contracts.v3 import BRANCH_DIMENSIONS
from skala_rag.graph import actual_runner_v3 as runner_module
from skala_rag.graph.actual_inputs_v3 import (
    ActualAuthorityV3,
    ActualInputsV3,
    ArchiveInput,
    CandidateInput,
    EvaluationInputsV3,
    ObservationInput,
    PinnedFile,
    RetainedSourceInputsV3,
    canonical,
    digest,
    file_digest,
)
from skala_rag.graph.actual_replay_v3 import CapturedLocalEncoder
from skala_rag.graph.actual_runner_v3 import ROOT, run_actual, run_replay
from skala_rag.prompts.evidence_extraction import ClaimDraft
from skala_rag.rag.corpus import manifest_hash
from skala_rag.rag.dense import QueryVector
from skala_rag.rag.index_v3 import build_index_plan, write_index
from skala_rag.rag.sqlite_index import SQLiteIndexStore
from skala_rag.scoring.approval_registry import pinned_approval_registry
from skala_rag.scoring.catalog import load_policy
from skala_rag.tools.company_research import FieldObservation, StageObservation
from skala_rag.tools.source_fetch import content_hash

SCHEMA = "synthetic-1"
TEXT = "Synthetic robotics company and independently controlled founder facts.\n"
REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
REQUEST = b'{"purpose":"SYNTHETIC original consumer integration"}'
NOW = datetime(2026, 10, 7, tzinfo=UTC)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*_args, **_kwargs):
        pytest.fail("No socket, credential lookup, encoder or paid call is allowed")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")


@pytest.fixture
def case(tmp_path, monkeypatch):
    # Given: genuine temporary SQLite persistence, SYNTHETIC observations.
    original = tmp_path / "data/local/original.txt"
    original.parent.mkdir(parents=True)
    extracted = tmp_path / "extracted.txt"
    original.write_text(TEXT)
    extracted.write_text(TEXT)
    doc = document(
        local_path="data/local/original.txt",
        content_hash=content_hash(TEXT.encode()),
        language="en",
    )
    src = source(doc).model_copy(
        update={
            "url": "https://synthetic.example/robot",
            "published_at": NOW.date(),
        }
    )
    piece = chunk(
        doc,
        text=TEXT,
        locator=src.url,
        page_start=None,
        page_end=None,
        language="en",
        embedding_revision=REVISION,
    )
    manifest = corpus(doc)
    config = settings(
        model_revision=REVISION,
        tokenizer_revision=REVISION,
        embedding_settings={"normalization": "l2", "device": "cpu", "mode": "dense"},
    )
    plan = build_index_plan(
        manifest=manifest,
        expected_corpus_hash=manifest_hash(manifest),
        sources={src.source_id: src},
        chunks=[piece],
        settings=config,
    )
    index = tmp_path / "index.sqlite"
    write_index(plan, embedder=FakeEmbedder(), store=SQLiteIndexStore(index, plan=plan))
    reopen = tmp_path / "reopen.json"
    reopen.write_bytes(
        canonical(
            {
                "metadata": asdict(plan.metadata),
                "top_k": 1,
                "query_vector": {
                    "chunk_id": "query",
                    "model_id": config.model_id,
                    "model_revision": REVISION,
                    "values": [0.6, 0.8],
                },
                "chunks": [piece.model_dump(mode="json")],
                "sources": [src.model_dump(mode="json")],
            }
        )
    )
    candidate = Candidate(
        schema_version=SCHEMA,
        candidate_id="co-a",
        canonical_name="SYNTHETIC",
        aliases=[],
        country="US",
        legal_identifiers={},
        homepage_url=src.url,
        discovery_source_ids=[src.source_id],
    )
    registry = pinned_approval_registry(ROOT)
    catalog = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
    evidence = Evidence(
        schema_version=SCHEMA,
        evidence_id="seed-evidence",
        candidate_id="co-a",
        scope="company",
        criterion_ids=[c.criterion_id for c in catalog.criteria],
        claim=TEXT.strip(),
        source_id=src.source_id,
        locator=src.url,
        excerpt=TEXT.strip(),
        provenance=[
            EvidenceProvenance(
                schema_version=SCHEMA,
                retrieval_id="seed-record",
                method="web",
            )
        ],
        evidence_kind="reported",
        confidence="unknown",
        limitations=["SYNTHETIC"],
        supporting_evidence_ids=[],
        conflicts_with=[],
    )
    record = RetrievalRecord(
        schema_version=SCHEMA,
        retrieval_id="seed-record",
        run_id="synthetic-run",
        candidate_id="co-a",
        tool_name="synthetic-capture",
        query=None,
        arguments_without_secrets={"synthetic": True},
        started_at=NOW,
        finished_at=NOW,
        status="ok",
        source_ids=[src.source_id],
        chunk_ids=[],
        evidence_ids=[evidence.evidence_id],
        error_id=None,
        cost=None,
        cache_hit=False,
    )
    values = {
        "domain_match": True,
        "is_listed": False,
        "exit_completed": False,
        "identity": None,
        "business": None,
        "stage": StageObservation("Series A", "series_a", "explicit"),
    }
    span = SourceTextSpan(
        source_id=src.source_id,
        start=0,
        end=len(TEXT.strip()),
        quote=TEXT.strip(),
        text_sha256=content_hash(TEXT.encode()),
    )
    observations = []
    for name, value in values.items():
        observation = FieldObservation(
            field=name,
            value=value,
            source_id=src.source_id,
            locator=src.url,
            claim=f"{name}: {TEXT.strip()}",
            excerpt=TEXT.strip(),
            identity_basis="official_domain",
        )
        review_request = CompanyObservationReviewRequest.model_validate(
            {
                "kind": "company-research-observation",
                "field": name,
                "candidate_id": candidate.candidate_id,
                "as_of": NOW.date(),
                "subject": candidate.canonical_name,
                "observation": observation,
                "source": src,
                "span": span,
            }
        )
        observations.append(
            ObservationInput(
                observation=observation,
                retrieval_id=record.retrieval_id,
                method="web",
                review_request=review_request.model_dump_json(),
                review_subject=candidate.canonical_name,
            )
        )
    gap = ResearchGap(
        schema_version=SCHEMA,
        gap_id="gap",
        candidate_id="co-a",
        criterion_id="technology.integration",
        missing_fields=["claim"],
        reason="SYNTHETIC",
        priority_weight=1,
        suggested_queries=["synthetic robotics"],
        attempted_retrieval_ids=[],
        status="open",
    )
    model_root = tmp_path / "model"
    model_root.mkdir()
    model_files = {}
    for number in range(15):
        path = model_root / f"synthetic-{number}"
        path.write_text("SYNTHETIC model commitment, not model weights")
        model_files[path.name] = file_digest(path)
    packet = ActualInputsV3(
        run_id="synthetic-run",
        run_input=RunInput(
            schema_version=SCHEMA,
            investment_theme="SYNTHETIC only",
            countries=["US"],
            languages=["en"],
            as_of=NOW.date(),
            policy_version="v3-operational-1.0.0",
            corpus_version=manifest.corpus_version,
            execution_mode="live",
        ),
        discovery=ToolResult[DiscoveryBundle](
            schema_version=SCHEMA,
            status="ok",
            data=DiscoveryBundle(
                schema_version=SCHEMA,
                candidates=[candidate],
                sources={src.source_id: src},
            ),
            retrieval_records=[],
            errors=[],
        ),
        archives=(),
        candidates={
            "co-a": CandidateInput(
                observations=tuple(observations),
                sources={src.source_id: src},
                chunks={},
                records=(record,),
                evidence={evidence.evidence_id: evidence},
                initial_gaps=(gap,),
            )
        },
        index=PinnedFile(path=index, sha256=file_digest(index)),
        reopen_input=PinnedFile(path=reopen, sha256=file_digest(reopen)),
        model_root=model_root,
        model_files=model_files,
        allowed_source_ids=frozenset([src.source_id]),
        industry_evidence_ids=frozenset(),
        industry_evidence_dimensions=(),
        approval_reference="SYNTHETIC-separate-operator-authorization",
    )
    trusted = TrustedCapture(
        path=original,
        allowed_root=tmp_path,
        extracted_path=extracted,
        format="text",
        charset="utf-8",
        source=src,
        corpus_version=manifest.corpus_version,
        extracted_text=TEXT,
        extracted_sha256=content_hash(TEXT.encode()),
    )
    snapshots = []

    def evaluation_inputs(snapshot):
        snapshots.append(snapshot.snapshot_id)
        anchors = {
            c.criterion_id: ReviewedFounderAnchor(
                review_reference=f"synthetic:{c.criterion_id}",
                artifact_sha256=core_artifact_digest(registry.rubric("core-0.1.0")),
                snapshot_sha256=frozen_snapshot_digest(snapshot),
                criterion_id=c.criterion_id,
                rating=3,
                evidence_ids=("seed-evidence",),
                founder_person_ids=("person",),
                person_by_evidence_id=(("seed-evidence", "person"),),
                anchor_facts_reviewed=True,
                minimum_evidence_reviewed=True,
                person_identity_reviewed=True,
                employment_identity_reviewed=True,
                independent_corroboration_reviewed=True,
            )
            for c in catalog.criteria
            if c.dimension == "founder"
        }
        return EvaluationInputsV3(
            review_request=REQUEST,
            review_subject="SYNTHETIC subject",
            founder_person_ids=("person",),
            verified_person_by_evidence_id={"seed-evidence": "person"},
            founder_anchors=anchors,
            technology_anchors={},
            moat_anchors={},
            market_target=None,
            market_links={},
            market_reviews={},
            financial_facts=(),
        )

    def reviews_for(snapshot, rubric):
        if rubric["rubric_version"] == "finance-0.1.0":
            return ()
        anchor_reviews = tuple(
            SourceBoundReview(
                review_reference=anchor.review_reference,
                request_sha256=digest(REQUEST),
                receipt_sha256=review_receipt_digest(anchor),
                candidate_id=snapshot.candidate_id,
                subject="SYNTHETIC subject",
                as_of=snapshot.as_of,
                snapshot_sha256=frozen_snapshot_digest(snapshot),
                rubric_sha256=_digest(dict(rubric)),
                decision="accepted",
                spans=(
                    SourceTextSpan(
                        source_id=src.source_id,
                        start=0,
                        end=len(TEXT.strip()),
                        quote=TEXT.strip(),
                        text_sha256=content_hash(TEXT.encode()),
                    ),
                ),
                rationale="SYNTHETIC independently supplied test record",
            )
            for anchor in evaluation_inputs(snapshot).founder_anchors.values()
        )
        return anchor_reviews + tuple(
            SourceBoundReview(
                review_reference=f"synthetic:eligibility:{o.observation.field}",
                request_sha256=digest(o.review_request.encode()),
                receipt_sha256=review_receipt_digest(o.observation),
                candidate_id=snapshot.candidate_id,
                subject=o.review_subject,
                as_of=snapshot.as_of,
                snapshot_sha256=frozen_snapshot_digest(snapshot),
                rubric_sha256=_digest(dict(rubric)),
                decision="accepted",
                spans=(span,),
                rationale="SYNTHETIC independently supplied field review",
            )
            for o in observations
        )

    authority = ActualAuthorityV3(
        campaign_id="SYNTHETIC-USD1-20261009",
        campaign_directory=tmp_path / "campaigns",
        authenticate_inputs=lambda supplied: supplied == packet,
        sources={src.source_id: trusted},
        live_gate_verifier=lambda gate, gates: (
            getattr(gates, f"{gate}_reference") == packet.approval_reference
            and gates.limits.max_calls == 40
            and gates.limits.max_cost_usd == 1
        ),
        reviews_for=reviews_for,
        evaluation_inputs_for=evaluation_inputs,
        support_check=lambda criterion, items: any(
            e.evidence_id == "seed-evidence"
            and e.claim == TEXT.strip()
            and criterion.criterion_id in e.criterion_ids
            for e in items
        ),
        applicability_assessments=lambda _: {},
        applicability_check=lambda *_: False,
        applicability_verifier=None,
        verify_replay_origin=lambda saved: saved["execution_scope"] == "actual",
    )
    encoder_calls = []

    class SyntheticEncoder:
        retry_owner = "runtime"

        def encode_once(self, query, *, settings, timeout_seconds):
            assert settings.model_revision == REVISION and timeout_seconds > 0
            encoder_calls.append(query)
            return QueryVector(config.model_id, REVISION, (0.6, 0.8))

    original_encoder_init = CapturedLocalEncoder.__init__

    def controlled_encoder(self, packet, journal):
        original_encoder_init(self, packet, journal)
        if not journal.replay:
            self._encoder = SyntheticEncoder()

    monkeypatch.setattr(CapturedLocalEncoder, "__init__", controlled_encoder)
    packet_path = tmp_path / "packet.json"
    packet_path.write_bytes(canonical(packet.model_dump(mode="json")))
    return {
        "packet": packet,
        "path": packet_path,
        "authority": authority,
        "tmp": tmp_path,
        "catalog": catalog,
        "snapshots": snapshots,
        "encoder_calls": encoder_calls,
        "source": original,
    }


def invoke(case, name="actual", **options):
    return run_actual(
        case["tmp"] / name,
        inputs=case["path"],
        inputs_sha256=file_digest(case["path"]),
        authority=case["authority"],
        **options,
    )


def receipt(path):
    return json.loads((path / "receipt.json").read_bytes())


def wire(
    case, *, revisions=0, invalid_business=False, extract_claim=False, stub_judge=False
):
    seen = []
    lock = Lock()
    first = Event()
    counts = {}

    def respond(role, request):
        with lock:
            counts[role] = counts.get(role, 0) + 1
            seen.append(role)
            first.set()
        payload = json.loads(request.content)
        user = json.loads(payload["input"][1]["content"])
        if role in BRANCH_DIMENSIONS:
            outputs = {}
            for dimension in BRANCH_DIMENSIONS[role]:
                outputs[dimension] = {
                    "criteria": [
                        {
                            "criterion_id": c.criterion_id,
                            "status": "observed" if role == "founder" else "missing",
                            "rating": 3 if role == "founder" else None,
                            "evidence_ids": ["seed-evidence"]
                            if role == "founder"
                            else [],
                            "rationale": "SYNTHETIC",
                            "missing_reason": None
                            if role == "founder"
                            else "not_disclosed",
                            "applicability_note": None,
                            **(
                                {
                                    "schema_version": SCHEMA,
                                    "applicability_reason": None,
                                    "applicability_rule_id": None,
                                    "applicability_evidence_ids": None,
                                }
                                if role == "business_deal"
                                else {}
                            ),
                        }
                        for c in case["catalog"].criteria
                        if c.dimension == dimension
                    ],
                    "research_gaps": [],
                    "caveats": ["SYNTHETIC"],
                }
            if role == "business_deal" and invalid_business:
                outputs["deal_terms"]["criteria"] = []
            output = outputs if role == "business_deal" else outputs[role]
        elif role == "evidence_research":
            output = {
                "claims": [
                    ClaimDraft(
                        claim="Synthetic robotics company",
                        excerpt=TEXT.strip(),
                        subject="SYNTHETIC",
                        limitations=["SYNTHETIC extraction"],
                    ).model_dump(mode="json")
                ]
                if extract_claim
                else []
            }
        elif role == "generator":
            output = {
                "schema_version": SCHEMA,
                "summary": "SYNTHETIC 연결 검증.",
                "company_team": "SYNTHETIC 기업이며 실측이 아니다.",
                "technology": "기술 관측은 미상이다.",
                "market": "시장 수치는 미상이다.",
                "assessment_risks": "합성 결과이며 투자 판단에 사용할 수 없다.",
                "limitations": ["SYNTHETIC controlled wire; provider calls are mocked"],
            }
        else:
            assert role == "judge"
            revise = counts[role] <= revisions
            output = {
                "schema_version": SCHEMA,
                "context_id": user["context_id"],
                "verdict": "revise" if revise else "pass",
                "findings": [
                    {
                        "schema_version": SCHEMA,
                        "severity": "stub" if stub_judge else "warning",
                        "claim_location": "whole_report",
                        "evidence_ids": [],
                        "reason": "SYNTHETIC semantic judgement",
                    }
                ]
                if stub_judge or revise
                else [],
                "revision_instructions": ["SYNTHETIC revise"] if revise else [],
                "judged_artifact_hash": user["artifact_hash"],
            }
        assert payload["max_output_tokens"] <= 2000
        return httpx.Response(
            200,
            json=body(
                json.dumps(output), usage={"input_tokens": 10, "output_tokens": 10}
            ),
        )

    return (
        lambda role: httpx.MockTransport(lambda request: respond(role, request)),
        seen,
        first,
    )


def test_preflight_defaults_to_no_paid_action(case):
    # When
    out = invoke(case)
    # Then
    assert receipt(out)["status"] == "preflight_ready", receipt(out)
    assert receipt(out)["actual_provider_calls"] == 0
    assert receipt(out)["ledger"]["calls"] == 0
    assert not case["encoder_calls"]
    assert not case["authority"].campaign_marker.exists()


@pytest.mark.parametrize(
    "damage", ["source", "authority", "budget", "native_mock", "pin"]
)
def test_tamper_blocks_before_provider_or_encoder(case, damage):
    # Given
    transport, seen, _ = wire(case)
    options = {"execute": True}
    if damage == "source":
        case["source"].write_text("changed")
    elif damage == "authority":
        case["authority"] = replace(case["authority"], authenticate_inputs=lambda _: 1)
    elif damage == "budget":
        case["authority"] = replace(
            case["authority"], live_gate_verifier=lambda *_: False
        )
    elif damage == "native_mock":
        options["transport_for"] = transport
    else:
        case["packet"].index.path.write_bytes(b"tampered")
    # When
    out = invoke(case, **options)
    # Then
    assert receipt(out)["status"] == "preflight_blocked", receipt(out)
    assert receipt(out)["actual_provider_calls"] == 0
    assert seen == [] and case["encoder_calls"] == []


def test_source_only_unknowns_are_blocked_not_false_eligible(case):
    # Given: authenticate the packet, not unsupported field facts.
    packet = case["packet"]
    packet.candidates["co-a"] = packet.candidates["co-a"].model_copy(
        update={"observations": ()}
    )
    case["path"].write_bytes(canonical(packet.model_dump(mode="json")))
    # When
    out = invoke(case, execute=True)
    # Then
    assert receipt(out)["reason"] == "ELIGIBILITY_FACTS_MISSING", receipt(out)
    eligibility = json.loads((out / "eligibility.json").read_bytes())["co-a"]
    assert eligibility["status"] == "unknown"
    assert eligibility["checks"]["listing"]["value"] is None
    assert receipt(out)["ledger"]["calls"] == 0


def test_missing_independent_review_emits_bounded_request_before_paid_call(case):
    # Given
    case["authority"] = replace(case["authority"], reviews_for=lambda *_: ())
    # When
    out = invoke(case, execute=True)
    # Then
    assert receipt(out)["reason"] == "SNAPSHOT_REVIEW_MISSING", receipt(out)
    assert receipt(out)["ledger"]["calls"] == 0
    requests = json.loads((out / "missing-review-requests.json").read_bytes())
    assert len(requests) == 1
    assert next(iter(requests.values()))["snapshot"]["candidate_id"] == "co-a"


def test_packet_authentication_cannot_replace_bound_eligibility_reviews(case):
    # Given: exact authenticated data and ratings, but no field review records.
    original_reviews = case["authority"].reviews_for
    case["authority"] = replace(
        case["authority"],
        reviews_for=lambda snapshot, rubric: tuple(
            review
            for review in original_reviews(snapshot, rubric)
            if not review.review_reference.startswith("synthetic:eligibility:")
        ),
    )
    # When
    out = invoke(case, execute=True)
    # Then: no eligible result is published before its independent review.
    assert receipt(out)["reason"] == "ELIGIBILITY_REVIEW_MISSING", receipt(out)
    assert receipt(out)["ledger"]["calls"] == 0
    assert not (out / "eligibility.json").exists()


def test_generated_snapshot_cannot_copy_seed_reviews_or_pay_next_call(case):
    # Given: externally authenticated seed records, but no reviewed new snapshot.
    original_reviews = case["authority"].reviews_for
    case["authority"] = replace(
        case["authority"],
        reviews_for=lambda snapshot, rubric: (
            original_reviews(snapshot, rubric)
            if snapshot.evidence_revision == 0
            else ()
        ),
    )
    transport, seen, _ = wire(case, extract_claim=True)
    # When
    out = invoke(
        case,
        execute=True,
        execution_scope="controlled_response",
        transport_for=transport,
    )
    # Then: one extraction is not permission for any evaluator or Generator.
    assert seen == ["evidence_research"], receipt(out)
    assert receipt(out)["status"] == "execution_blocked", receipt(out)
    requests = json.loads((out / "missing-review-requests.json").read_bytes())
    assert any(r["snapshot"]["evidence_revision"] == 1 for r in requests.values())
    assert receipt(out)["ledger"]["tool_calls"]["openai"] == 1


def test_transport_failure_retains_original_errors_without_fabricated_wire(
    case, monkeypatch
):
    # Given: the HTTP layer fails before a response body exists.
    seen = []
    reserved_costs = []
    original_allowance = runner_module.byte_bound_allowance

    def observe_allowance(*args, **kwargs):
        allowance = original_allowance(*args, **kwargs)
        assert allowance.max_cost_usd is not None
        reserved_costs.append(allowance.max_cost_usd)
        return allowance

    monkeypatch.setattr(runner_module, "byte_bound_allowance", observe_allowance)

    def transport(role):
        def fail(request):
            seen.append(role)
            raise httpx.ConnectError(
                "SYNTHETIC private body must not persist", request=request
            )

        return httpx.MockTransport(fail)

    # When
    out = invoke(
        case,
        execute=True,
        execution_scope="controlled_response",
        transport_for=transport,
    )
    # Then
    assert seen and len(seen) == len(set(seen)), receipt(out)
    assert not (out / "wire.json").exists()
    errors = json.loads((out / "runtime-errors.json").read_bytes())
    assert errors and all(e["error_code"] == "TOOL_FAILED" for e in errors.values())
    assert "private body" not in (out / "runtime-errors.json").read_text()
    assert receipt(out)["ledger"]["unknown_cost_requests"] >= 1
    assert Decimal(receipt(out)["ledger"]["cost_usd_accounted"]) == sum(
        reserved_costs, Decimal(0)
    )
    assert reserved_costs and all(cost > 0 for cost in reserved_costs)


def test_controlled_capture_is_not_actual_replay_authority(case):
    # Given
    transport, _, _ = wire(case)
    original = invoke(
        case,
        execute=True,
        execution_scope="controlled_response",
        transport_for=transport,
    )
    callbacks = []
    authority = replace(
        case["authority"],
        authenticate_inputs=lambda _: callbacks.append("input") or True,
        verify_replay_origin=lambda _: callbacks.append("origin") or True,
    )
    # When
    out = run_replay(
        case["tmp"] / "rejected-replay",
        inputs=case["path"],
        inputs_sha256=file_digest(case["path"]),
        authority=authority,
        original_capture=original / "capture.json",
        original_capture_sha256=file_digest(original / "capture.json"),
    )
    # Then
    assert receipt(out)["reason"] == "REPLAY_ORIGIN_MISMATCH", receipt(out)
    assert receipt(out)["actual_provider_calls"] == 0 and callbacks == []


def test_capture_pin_tamper_precedes_all_authority_callbacks(case):
    # Given
    transport, _, _ = wire(case)
    original = invoke(
        case,
        execute=True,
        execution_scope="controlled_response",
        transport_for=transport,
    )
    capture = original / "capture.json"
    external_pin = file_digest(capture)
    capture.write_bytes(capture.read_bytes() + b" ")
    callbacks = []
    authority = replace(
        case["authority"],
        authenticate_inputs=lambda _: callbacks.append("input") or True,
        verify_replay_origin=lambda _: callbacks.append("origin") or True,
    )
    # When
    out = run_replay(
        case["tmp"] / "tampered-replay",
        inputs=case["path"],
        inputs_sha256=file_digest(case["path"]),
        authority=authority,
        original_capture=capture,
        original_capture_sha256=external_pin,
    )
    # Then
    assert receipt(out)["reason"] == "REPLAY_PIN_MISMATCH"
    assert callbacks == [] and receipt(out)["actual_provider_calls"] == 0


def test_json_approval_bits_cannot_supply_authority(case):
    # Given: re-pinning edited data does not authenticate an approval.
    payload = json.loads(case["path"].read_bytes())
    payload["approved"] = True
    case["path"].write_bytes(canonical(payload))
    # When
    out = invoke(case, execute=True)
    # Then
    assert receipt(out)["status"] == "preflight_blocked"
    assert receipt(out)["actual_provider_calls"] == 0
    assert case["encoder_calls"] == []


def test_shared_ledger_rejects_invalid_usage_before_next_paid_role(case):
    # Given
    make_transport, seen, _ = wire(case)

    def transport(role):
        original = make_transport(role)

        def respond(request):
            response = original.handle_request(request)
            payload = response.json()
            payload["usage"]["output_tokens"] = 2001
            return httpx.Response(200, json=payload)

        return httpx.MockTransport(respond)

    # When
    out = invoke(
        case,
        execute=True,
        execution_scope="controlled_response",
        transport_for=transport,
    )
    # Then
    assert seen == ["evidence_research"], receipt(out)
    assert receipt(out)["ledger"]["usage_invalid"] is True
    assert receipt(out)["ledger"]["tool_calls"]["openai"] == 1
    assert receipt(out)["reserved_model_attempts"] == 1
    assert receipt(out)["observed_controlled_responses"] == 1
    assert receipt(out)["observed_native_responses"] == 0
    assert receipt(out)["uncertain_transport_dispatch_failures"] == 0
    assert receipt(out)["status"] != "completed"


def test_original_parallel_branches_share_serialized_paid_dispatch(case, monkeypatch):
    # Given: subscribe to all original evaluator entries before graph dispatch.
    entered = set()
    all_entered = Event()
    guard = Lock()
    active = 0
    maximum = 0
    for role in BRANCH_DIMENSIONS:
        name = f"evaluate_{role}_approved"
        original = getattr(runner_module, name)

        def enter(*args, _original=original, _role=role, **kwargs):
            with guard:
                entered.add(_role)
                if entered == set(BRANCH_DIMENSIONS):
                    all_entered.set()
            return _original(*args, **kwargs)

        monkeypatch.setattr(runner_module, name, enter)
    make_transport, _, _ = wire(case)

    def transport(role):
        original = make_transport(role)

        def respond(request):
            nonlocal active, maximum
            if role not in BRANCH_DIMENSIONS:
                return original.handle_request(request)
            with guard:
                active += 1
                maximum = max(maximum, active)
            try:
                assert all_entered.wait(10), (
                    "Original five-way graph must stay parallel"
                )
                return original.handle_request(request)
            finally:
                with guard:
                    active -= 1

        return httpx.MockTransport(respond)

    # When
    out = invoke(
        case,
        execute=True,
        execution_scope="controlled_response",
        transport_for=transport,
    )
    # Then
    assert receipt(out)["status"] == "completed", receipt(out)
    assert all_entered.is_set() and maximum == 1


@pytest.mark.parametrize("invalid_business", [False, True])
def test_original_five_branches_promote_business_deal_atomically(
    case, invalid_business
):
    # Given
    transport, seen, first = wire(case, invalid_business=invalid_business)
    # When
    out = invoke(
        case,
        execute=True,
        execution_scope="controlled_response",
        transport_for=transport,
    )
    # Then: event is observed after synchronous return; never a timed sleep.
    assert first.is_set(), receipt(out)
    assert set(BRANCH_DIMENSIONS) <= set(seen), receipt(out)
    states = json.loads((out / "states.json").read_bytes())
    promoted = states["co-a"].get("evaluations_v3", {})
    assert len(promoted) == (0 if invalid_business else 6), receipt(out)
    assert receipt(out)["actual_provider_calls"] == 0
    assert receipt(out)["publication_allowed"] is False
    assert receipt(out)["status"] == (
        "execution_blocked" if invalid_business else "completed"
    )


@pytest.mark.parametrize("changed_packet", [False, True])
def test_campaign_marker_prevents_fresh_output_budget_reset(case, changed_packet):
    # Given: first admitted controlled campaign has consumed its one-shot marker.
    transport, seen, _ = wire(case)
    original = invoke(
        case,
        execute=True,
        execution_scope="controlled_response",
        transport_for=transport,
    )
    before = len(seen)
    marker = case["authority"].campaign_marker
    marker_bytes = marker.read_bytes()
    ledger_bytes = (original / "ledger.json").read_bytes()
    recorded = json.loads(marker_bytes)
    assert recorded["campaign_id"] == case["authority"].campaign_id
    assert recorded["approval_reference"] == case["packet"].approval_reference
    assert marker.parent == case["authority"].campaign_directory.resolve()
    if changed_packet:
        packet = case["packet"].model_copy(
            update={"approval_reference": "SYNTHETIC-changed-input-reference"}
        )
        case["path"].write_bytes(canonical(packet.model_dump(mode="json")))
        case["authority"] = replace(
            case["authority"], authenticate_inputs=lambda supplied: supplied == packet
        )
    # When
    out = invoke(
        case,
        "relaunch",
        execute=True,
        execution_scope="controlled_response",
        transport_for=transport,
    )
    # Then
    assert receipt(out)["reason"] == "CAMPAIGN_ALREADY_STARTED", receipt(out)
    assert len(seen) == before
    assert receipt(out)["actual_provider_calls"] == 0
    assert case["authority"].campaign_marker == marker
    assert marker.read_bytes() == marker_bytes
    assert (original / "ledger.json").read_bytes() == ledger_bytes


def test_interrupted_campaign_blocks_native_relaunch_without_reading_approval(case):
    # Given: an exclusive marker survived interruption before its body was flushed.
    authority = case["authority"]
    authority.campaign_directory.mkdir()
    authority.campaign_marker.write_bytes(b"{")
    callbacks = []
    case["authority"] = replace(
        authority,
        authenticate_inputs=lambda _: callbacks.append("authenticate") or True,
    )
    # When: a new output and native transport cannot imply a new USD1 campaign.
    out = invoke(case, "native-relaunch", execute=True, api_key="SYNTHETIC-NOT-A-KEY")
    # Then
    assert receipt(out)["reason"] == "CAMPAIGN_ALREADY_STARTED", receipt(out)
    assert receipt(out)["actual_provider_calls"] == 0
    assert callbacks == [] and case["encoder_calls"] == []
    assert authority.campaign_marker.read_bytes() == b"{"


@pytest.mark.parametrize("campaign_id", ["", " leading-space", "trailing-space "])
def test_campaign_identity_must_be_explicit_and_stable(case, campaign_id):
    # Given
    case["authority"] = replace(case["authority"], campaign_id=campaign_id)
    # When
    out = invoke(case, execute=True)
    # Then
    assert receipt(out)["reason"] == "STABLE_CAMPAIGN_ID_REQUIRED", receipt(out)
    assert receipt(out)["actual_provider_calls"] == 0


def test_campaign_directory_cannot_be_scoped_to_run_output(case):
    # Given: this location would make a fresh output look like a fresh campaign.
    case["authority"] = replace(
        case["authority"], campaign_directory=case["tmp"] / "actual" / "campaigns"
    )
    # When
    out = invoke(case, execute=True)
    # Then
    assert receipt(out)["reason"] == "PERSISTENT_CAMPAIGN_DIRECTORY_REQUIRED"
    assert receipt(out)["actual_provider_calls"] == 0
    assert not case["authority"].campaign_directory.exists()


def intercept_native(monkeypatch, transport):
    """SYNTHETIC HTTP interception; the runner still admits native transport=None."""
    original_client = httpx.Client
    factories = {}

    def native_client(**kwargs):
        assert kwargs["transport"] is None

        def respond(request):
            payload = json.loads(request.content)
            name = payload["text"]["format"]["name"]
            user = json.loads(payload["input"][1]["content"])
            if "claims" in payload["text"]["format"]["schema"]["properties"]:
                role = "evidence_research"
            elif name == "ReportContentV3":
                role = "generator"
            elif name == "ReportJudgement":
                role = "judge"
            elif "traction" in payload["text"]["format"]["schema"]["properties"]:
                role = "business_deal"
            else:
                role = user["dimension"]
            factories.setdefault(role, transport(role))
            return factories[role].handle_request(request)

        kwargs["transport"] = httpx.MockTransport(respond)
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "Client", native_client)
    return original_client


def test_same_runner_actual_origin_replay_and_repeated_report_roles(case, monkeypatch):
    # Given: instrument native httpx below the admitted transport, with synthetic
    # records. An "actual" scope here is NOT a measurement of actual execution.
    transport, seen, _ = wire(case, revisions=1, extract_claim=True)
    original_client = intercept_native(monkeypatch, transport)
    original = invoke(case, execute=True, api_key="SYNTHETIC-NOT-A-CREDENTIAL")
    assert receipt(original)["status"] == "completed", receipt(original)
    assert seen.count("generator") == seen.count("judge") == 2
    monkeypatch.setattr(httpx, "Client", original_client)
    before = len(seen)
    encoders_before = len(case["encoder_calls"])
    original_marker = case["authority"].campaign_marker.read_bytes()
    # When: same composition, original independently pinned campaign.
    replay = run_replay(
        case["tmp"] / "replay",
        inputs=case["path"],
        inputs_sha256=file_digest(case["path"]),
        authority=case["authority"],
        original_capture=original / "capture.json",
        original_capture_sha256=file_digest(original / "capture.json"),
    )
    # Then
    assert receipt(replay)["status"] == "completed", receipt(replay)
    assert receipt(original)["stable_hashes"] == receipt(replay)["stable_hashes"]
    assert receipt(replay)["actual_provider_calls"] == 0
    assert receipt(original)["observed_native_responses"] == len(seen)
    assert receipt(original)["reserved_model_attempts"] == len(seen)
    assert receipt(original)["uncertain_transport_dispatch_failures"] == 0
    assert receipt(replay)["observed_native_responses"] == 0
    assert receipt(replay)["replayed_responses"] == len(seen)
    assert len(seen) == before
    assert len(case["encoder_calls"]) == encoders_before
    assert len(set(case["snapshots"])) >= 2
    assert case["authority"].campaign_marker.read_bytes() == original_marker
    assert len(list(case["authority"].campaign_directory.glob("*.json"))) == 1
    assert (
        json.loads((replay / "context.json").read_bytes())["execution_scope"]
        == "actual"
    )


def test_native_intercepted_stub_judge_cannot_complete_or_authorize_replay(
    case, monkeypatch
):
    # Given: fixture-compatible stub pass through the real SemanticJudgeV3.
    transport, seen, _ = wire(case, stub_judge=True)
    original_client = intercept_native(monkeypatch, transport)
    # When
    out = invoke(case, execute=True, api_key="SYNTHETIC-NOT-A-CREDENTIAL")
    # Then: the completed report DTO is not an actual completion authority.
    assert seen.count("judge") == 1
    assert receipt(out)["status"] == "execution_blocked", receipt(out)
    assert receipt(out)["reason"] == "STUB_JUDGE_FORBIDDEN"
    manifest = json.loads((out / "manifest.json").read_bytes())
    assert manifest["workflow_status"] == "failed"
    assert manifest["validation_results"]["judge"]["findings"][0]["severity"] == "stub"
    assert (
        json.loads((out / "render.json").read_bytes())["layout_measurements"][
            "final_allowed"
        ]
        is False
    )
    monkeypatch.setattr(httpx, "Client", original_client)


def test_replay_stub_judge_manifest_rejected_before_authority_callbacks(
    case, monkeypatch
):
    # Given: synthetic nonstub origin, then an externally pinned but invalid
    # manifest whose Judge carries a fixture-only stub finding.
    transport, _, _ = wire(case)
    original_client = intercept_native(monkeypatch, transport)
    original = invoke(case, execute=True, api_key="SYNTHETIC-NOT-A-CREDENTIAL")
    assert receipt(original)["status"] == "completed", receipt(original)
    monkeypatch.setattr(httpx, "Client", original_client)
    manifest_path = original / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["validation_results"]["judge"]["findings"] = [
        {
            "schema_version": SCHEMA,
            "severity": "stub",
            "claim_location": "whole_report",
            "evidence_ids": [],
            "reason": "SYNTHETIC",
        }
    ]
    manifest_path.write_bytes(canonical(manifest))
    capture_path = original / "capture.json"
    capture = json.loads(capture_path.read_bytes())
    capture["files"]["manifest.json"] = file_digest(manifest_path)
    capture_path.write_bytes(canonical(capture))
    callbacks = []
    authority = replace(
        case["authority"],
        authenticate_inputs=lambda _: callbacks.append("input") or True,
        verify_replay_origin=lambda _: callbacks.append("origin") or True,
    )
    # When
    out = run_replay(
        case["tmp"] / "stub-replay",
        inputs=case["path"],
        inputs_sha256=file_digest(case["path"]),
        authority=authority,
        original_capture=capture_path,
        original_capture_sha256=file_digest(capture_path),
    )
    # Then
    assert receipt(out)["reason"] == "REPLAY_JUDGE_REJECTED", receipt(out)
    assert receipt(out)["actual_provider_calls"] == 0
    assert callbacks == []


def test_nonfinal_output_preserves_measured_layout_and_draft_name(case, monkeypatch):
    # Given: observe the original physical renderer without modifying its result.
    rendered = []
    original_renderer = runner_module.PDFRenderer.__call__

    def observe_render(self, *args, **kwargs):
        result = original_renderer(self, *args, **kwargs)
        rendered.append(result.model_dump(mode="json"))
        return result

    monkeypatch.setattr(runner_module.PDFRenderer, "__call__", observe_render)
    transport, _, _ = wire(case)
    # When
    out = invoke(
        case,
        execute=True,
        execution_scope="controlled_response",
        transport_for=transport,
    )
    # Then
    assert receipt(out)["status"] == "completed", receipt(out)
    assert json.loads((out / "render.json").read_bytes()) == rendered[-1]
    assert rendered[-1]["layout_measurements"]["final_allowed"] is True
    assert receipt(out)["publication_allowed"] is receipt(out)["final_allowed"] is False
    assert (out / "draft.md").read_text() == json.loads(
        (out / "draft.json").read_bytes()
    )["markdown"]
    assert not (out / "report.md").exists()
    assert not (out / "final").exists()
    assert not (out / "final" / "report.md").exists()


def test_native_transport_failure_keeps_request_count_unknown(case, monkeypatch):
    # Given: native dispatch fails without any observed response bytes.
    seen = []

    def transport(role):
        def fail(request):
            seen.append(role)
            raise httpx.ReadError("SYNTHETIC dispatch uncertainty", request=request)

        return httpx.MockTransport(fail)

    intercept_native(monkeypatch, transport)
    # When
    out = invoke(case, execute=True, api_key="SYNTHETIC-NOT-A-CREDENTIAL")
    # Then: reservations cannot be reported as measured provider requests.
    result = receipt(out)
    assert result["reserved_model_attempts"] == len(seen) > 0
    assert result["observed_native_responses"] == 0
    assert result["uncertain_transport_dispatch_failures"] == len(seen)
    assert result["actual_provider_calls"] is None
    assert not (out / "wire.json").exists()
    assert json.loads((out / "runtime-errors.json").read_bytes())


@pytest.mark.parametrize("other_status", ["unknown", "ineligible"])
def test_mixed_candidates_archive_then_advance_to_eligible(case, other_status):
    # Given: original outer graph must skip an unknown/ineligible candidate.
    packet = case["packet"]
    first = packet.discovery.data.candidates[0]
    other = first.model_copy(
        update={
            "candidate_id": "co-other",
            "canonical_name": "SYNTHETIC OTHER",
            "homepage_url": "https://other.example/",
            "legal_identifiers": {"synthetic": "other"},
        }
    )
    packet.discovery.data.candidates.insert(0, other)
    supplied = packet.candidates["co-a"]
    observations = ()
    if other_status == "ineligible":
        listing = next(
            o for o in supplied.observations if o.observation.field == "is_listed"
        )
        observations = (
            listing.model_copy(
                update={
                    "observation": replace(
                        listing.observation,
                        value=True,
                        identity_basis="legal_identifier",
                        matched_identifiers={"synthetic": "other"},
                    ),
                    "review_subject": other.canonical_name,
                }
            ),
        )
    packet.candidates["co-other"] = supplied.model_copy(
        update={
            "observations": observations,
            "evidence": {},
            "records": (),
            "initial_gaps": (),
        }
    )
    case["path"].write_bytes(canonical(packet.model_dump(mode="json")))
    transport, seen, _ = wire(case)
    # When
    out = invoke(
        case,
        execute=True,
        execution_scope="controlled_response",
        transport_for=transport,
    )
    # Then: only the eligible candidate evaluates; all candidates are archived.
    assert receipt(out)["status"] == "completed", receipt(out)
    result = json.loads((out / "candidate-result.json").read_bytes())
    assert result["candidate_index"] == 2
    assert result["outcomes"]["co-other"]["status"] == (
        "eligibility_unknown" if other_status == "unknown" else "ineligible"
    )
    assert set(result["scores"]) == {"co-a"}
    assert set(BRANCH_DIMENSIONS) <= set(seen)
    assert set(json.loads((out / "states.json").read_bytes())) == {"co-a"}


@pytest.mark.parametrize("damage", [None, "archive", "index_original", "model"])
def test_retained_source_preflight_needs_no_authority_callbacks(case, damage):
    # Given: source-only original archive composition and readonly SQLite closure.
    spec = importlib.util.spec_from_file_location(
        "v3_actual_run", ROOT / "examples/v3_actual_run.py"
    )
    assert spec is not None and spec.loader is not None
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)

    archive = case["tmp"] / "archive"
    pin = synthetic_archive(archive)
    candidate = options(archive, pin)["candidate"].model_copy(
        update={"schema_version": SCHEMA}
    )
    packet = case["packet"]
    sid = next(iter(packet.discovery.data.sources))
    sources = RetainedSourceInputsV3(
        run_input=packet.run_input,
        run_id="SYNTHETIC-source-preparation",
        candidates=(candidate,),
        archives=(ArchiveInput(root=archive, index_sha256=pin),),
        index=packet.index,
        reopen_input=packet.reopen_input,
        index_originals={
            sid: PinnedFile(path=case["source"], sha256=file_digest(case["source"]))
        },
        model_root=packet.model_root,
        model_files=packet.model_files,
    )
    if damage == "archive":
        (archive / "raw/synthetic-source.html").write_bytes(b"changed")
    elif damage == "index_original":
        case["source"].write_bytes(b"changed")
    elif damage == "model":
        (packet.model_root / next(iter(packet.model_files))).write_bytes(b"changed")
    originals = [
        packet.index.path,
        packet.reopen_input.path,
        archive / "collection-index.json",
    ]
    before = {str(path): file_digest(path) for path in originals}
    # When: no execution authority object or placeholder callbacks supplied.
    out = example.preflight_sources(case["tmp"] / "sources-only", sources=sources)
    # Then: no true/false/zero is invented for missing company facts.
    result = receipt(out)
    assert result["status"] == "preflight_blocked"
    assert result["actual_provider_calls"] == 0
    assert result["publication_allowed"] is False
    assert not case["encoder_calls"]
    assert not case["authority"].campaign_marker.exists()
    assert before == {str(path): file_digest(path) for path in originals}
    if damage is not None:
        assert result["reason"] != "EXTERNAL_AUTHORITY_REQUIRED"
        assert not (out / "source-preparation.json").exists()
        return
    prepared = json.loads((out / "source-preparation.json").read_bytes())
    assert result["reason"] == "EXTERNAL_AUTHORITY_REQUIRED"
    assert prepared["verified_model_files"] == 15
    assert len(prepared["index_chunks"]) == 1
    assert set(prepared["index_sources"]) == {sid}
    composition = prepared["source_compositions"]["synthetic-company"][0]
    assert len(composition["data"]["sources"]) == 1
    assert composition["data"]["evidence"] == {}
    eligibility = prepared["eligibility"]["synthetic-company"]
    assert eligibility["status"] == "unknown"
    assert eligibility["checks"]["listing"]["value"] is None
