"""SYNTHETIC authority/wires/vectors; real public callable, SQLite and PDF.

No test callback is an operator approval or an actual-provider QA substitute.
Sockets are denied before action; transport recording is synchronous.
"""

import json
import socket
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event, Lock

import httpx
import pytest
from pypdf import PdfReader
from tests.integration.test_v3_actual_runner import (
    NOW,
    REQUEST,
    REVISION,
    ROOT,
    SCHEMA,
    TEXT,
    wire,
)
from tests.integration.test_v3_actual_runner import (
    case as actual_case,
)
from tests.unit.test_approved_policy import gates_payload, runtime_binding
from tests.unit.test_index_v3 import chunk, document, settings
from tests.unit.test_openai_attempt import body

import skala_rag.company_report as public
from skala_rag.agents.company_report_research import (
    ResearchAdmission,
    ResearchTarget,
)
from skala_rag.agents.moat_verification import frozen_snapshot_digest
from skala_rag.agents.source_fact_verification import TrustedCapture
from skala_rag.contracts import Candidate, Chunk, EvaluationSnapshot
from skala_rag.contracts.company_report import (
    CompanyReportReceipt,
    ResearchLimits,
)
from skala_rag.graph.actual_inputs_v3 import (
    CompanyReportAuthorityV3,
    CompanyReportReadmissionV3,
    IdentityResearchAdmissionV3,
    canonical,
    digest,
    review_resolver,
)
from skala_rag.prompt.company_report_freshness import GapQuery
from skala_rag.rag import company_store as store_module
from skala_rag.rag.company_store import (
    CompanyIdentity,
    CompanyStore,
    RetainedSource,
)
from skala_rag.rag.corpus import ManifestDocument
from skala_rag.rag.local_bge_validation import LocalEncoder
from skala_rag.scoring.approval_registry import pinned_approval_registry
from skala_rag.scoring.approved_consumers import ActualAdmissionV3, ApprovedPolicySource
from skala_rag.settings import load_runtime_document
from skala_rag.tools.company_research import assemble_bundle
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt
from skala_rag.tools.runtime import Allowance, BudgetLedger
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM
from skala_rag.tools.source_fetch import FetchPolicy


def deny_socket(*_args, **_kwargs):
    pytest.fail("Task 5 forbids native requests, credentials and downloads")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", deny_socket)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_socket)
    monkeypatch.setattr(socket, "create_connection", deny_socket)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")


class SyntheticLocalEncoder(LocalEncoder):
    """Synthetic vectors only; production local-query and SQLite code run."""

    def __init__(self):
        super().__init__(None)

    def embed_texts(self, texts):
        return tuple((1.0,) + (0.0,) * 1023 for _ in texts)


class Harness:
    """Mutable scenario controls and a recorder subscribed before the public call."""

    def __init__(self, root, monkeypatch, *, known=True, kr=False, competitor=False):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        basis = root / "basis"
        basis.mkdir()
        self.base = actual_case.__wrapped__(basis, monkeypatch)
        packet = self.base["packet"]
        self.candidate = packet.discovery.data.candidates[0]
        if kr:
            self.candidate = self.candidate.model_copy(
                update={"country": "KR", "legal_identifiers": {"dart": "00123456"}}
            )
        supplied = packet.candidates["co-a"]
        bundle, rejected, _ = assemble_bundle(
            self.candidate,
            tuple(supplied.sources.values()),
            [(o.observation, o.retrieval_id, o.method) for o in supplied.observations],
            as_of=NOW.date(),
            schema_version=SCHEMA,
        )
        assert not rejected
        self.profile = bundle.profile
        evidence = {**supplied.evidence, **bundle.evidence}
        src = next(iter(bundle.sources.values()))
        doc = document(
            local_path=src.local_path,
            content_hash=src.content_hash,
            language=src.language,
        )
        piece = chunk(
            doc,
            text=TEXT,
            locator=src.url,
            page_start=None,
            page_end=None,
            language=src.language,
            embedding_revision=REVISION,
        )
        record = supplied.records[0].model_copy(
            update={"evidence_ids": sorted(evidence)}
        )
        self.material = RetainedSource(
            doc, src, TEXT.encode(), (piece,), tuple(evidence.values()), (record,)
        )
        fields = {"canonical_name": ["seed-evidence"], "country": ["seed-evidence"]}
        if self.candidate.homepage_url:
            fields["homepage_url"] = ["seed-evidence"]
        fields.update(
            {
                f"legal:{key}": ["seed-evidence"]
                for key in self.candidate.legal_identifiers
            }
        )
        self.identity = CompanyIdentity(
            schema_version=SCHEMA, candidate=self.candidate, field_evidence_ids=fields
        )
        self.index_settings = settings(
            model_revision=REVISION,
            tokenizer_revision=REVISION,
            dimension=1024,
            embedding_settings={
                "normalization": "l2",
                "mode": "dense",
                "device": "cpu",
            },
        )
        self.store = CompanyStore(
            root / "store",
            settings=self.index_settings,
            encoder=SyntheticLocalEncoder(),
        )
        materials = [self.material]
        identities = [self.identity] if known else []
        if competitor:
            other = self.competitor_material()
            materials.append(other)
            identities.append(
                CompanyIdentity(
                    schema_version=SCHEMA,
                    candidate=Candidate(
                        schema_version=SCHEMA,
                        candidate_id="co-b",
                        canonical_name="SYNTHETIC COMPETITOR",
                        aliases=[],
                        country="US",
                        homepage_url=None,
                        legal_identifiers={},
                        discovery_source_ids=["src-b"],
                    ),
                    field_evidence_ids={
                        "canonical_name": ["ev-b"],
                        "country": ["ev-b"],
                    },
                )
            )
        self.before = self.store.ingest_sources(materials, companies=identities)
        receipt = root / "model-receipt.json"
        receipt.write_bytes(
            canonical({"metadata": {"settings_snapshot": self.index_settings._payload}})
        )
        self.config = root / "company-report.json"
        self.config_data = {
            "schema_version": "company-report-1",
            "runtime_path": str(ROOT / "configs/runtime.json"),
            "store_dir": str(self.store.root),
            "model_path": str(root / "synthetic-model"),
            "model_receipt_path": str(receipt),
            "policy_path": str(ROOT / "configs/scoring.v3.json"),
            "catalog_path": str(ROOT / "configs/scoring.draft.json"),
            "research_enabled": False,
            "research_limits": {
                "max_calls": 12,
                "max_cost_usd": "1",
                "deadline_seconds": 20.0,
            },
        }
        self.config.write_bytes(canonical(self.config_data))
        self.document = load_runtime_document()
        self.events = []
        self.signal = Event()
        self.lock = Lock()
        self.stale = False
        self.hostile_gap = False
        self.judge_verdict = "pass"
        self.api_failure = False
        self.report_evidence_id = "seed-evidence"
        self.report_claim = "Controlled original."
        self.identity_status = "000"
        self.same_name_wrong_number = False
        self.eval_wire, _, _ = wire(self.base)
        monkeypatch.setattr(
            store_module,
            "load_local_encoder",
            lambda *_a, **_k: SyntheticLocalEncoder(),
        )
        self.real_attempt = OpenAIResponsesAttempt

        def attempt(**kwargs):
            return self.real_attempt(
                **kwargs, http_transport=httpx.MockTransport(self.respond)
            )

        monkeypatch.setattr(public, "OpenAIResponsesAttempt", attempt)
        self.bind()

    def competitor_material(self):
        original = self.material
        text = "SYNTHETIC competitor original fact."
        from skala_rag.tools.source_fetch import content_hash

        src = original.source.model_copy(
            update={
                "source_id": "src-b",
                "content_hash": content_hash(text.encode()),
                "local_path": "data/local/b.txt",
                "url": "https://competitor.example",
            }
        )
        doc = original.document.model_copy(
            update={
                "document_id": "doc-b",
                "source_id": "src-b",
                "content_hash": src.content_hash,
                "local_path": src.local_path,
                "candidate_ids": ("co-b",),
            }
        )
        piece = original.chunks[0].model_copy(
            update={
                "chunk_id": "chunk-b",
                "source_id": "src-b",
                "text": text,
                "locator": src.url,
                "candidate_ids": ["co-b"],
            }
        )
        evidence = original.evidence[0].model_copy(
            update={
                "evidence_id": "ev-b",
                "source_id": "src-b",
                "candidate_id": "co-b",
                "claim": text,
                "excerpt": text,
                "locator": src.url,
                "provenance": [
                    original.evidence[0]
                    .provenance[0]
                    .model_copy(update={"retrieval_id": "ret-b"})
                ],
            }
        )
        record = original.retrieval_records[0].model_copy(
            update={
                "retrieval_id": "ret-b",
                "candidate_id": "co-b",
                "source_ids": ["src-b"],
                "evidence_ids": ["ev-b"],
            }
        )
        return RetainedSource(doc, src, text.encode(), (piece,), (evidence,), (record,))

    def bind(self, retained=None, *, run_id="synthetic-run", max_calls=40):
        """Explicit SYNTHETIC authority for exactly one anticipated store version."""
        retained = retained or self.store.open()
        assert retained is not None
        manifest = retained.manifest
        payload = gates_payload()
        payload["provider"] = "openai"
        payload["limits"].update(
            max_calls=max_calls,
            tool_max_calls={
                "openai": max_calls,
                "official-homepage": max_calls,
                "opendart-company": max_calls,
            },
            max_input_tokens=2_000_000,
            max_output_tokens=100_000,
            max_cost_usd="1",
        )
        binding = runtime_binding(payload=payload, run_id=run_id, schema_version=SCHEMA)
        binding.runtime.clock.current = NOW
        binding = replace(
            binding,
            call=binding.call.model_copy(update={"candidate_id": "co-a"}),
            budget=binding.budget.model_copy(
                update={"max_calls": 1, "deadline": NOW + timedelta(seconds=30)}
            ),
        )

        def gate(_gate, gates):
            return gates == binding.gates

        extracted_root = retained.root / "synthetic-reviewed-text"
        extracted_root.mkdir(exist_ok=True)
        for sid, src in manifest.sources.items():
            if src.local_path is not None:
                (extracted_root / f"{sid}.txt").write_bytes(
                    (retained.root / src.local_path).read_bytes()
                )
        originals = {
            sid: TrustedCapture(
                path=retained.root / src.local_path,
                allowed_root=retained.root,
                extracted_path=extracted_root / f"{sid}.txt",
                format="text",
                charset="utf-8",
                source=src,
                corpus_version=manifest.version,
                extracted_text=(retained.root / src.local_path).read_text(),
                extracted_sha256=src.content_hash,
                approved_chunks=tuple(
                    c for c in manifest.chunks.values() if c.source_id == sid
                ),
            )
            for sid, src in manifest.sources.items()
            if src.local_path is not None
            and not src.bibliographic_metadata.get("generated_report")
        }
        options = CompanyReportAuthorityV3(
            profile_for=lambda candidate, retained, cutoff: self.profile,
            research_for=lambda candidate, actual: self.research_admission(actual),
            identity_research=lambda request, actual: (
                IdentityResearchAdmissionV3(
                    collection=self.research_admission(actual, identity=True),
                    api_key="SYNTHETIC-KEY-NOT-REAL",
                ),
            ),
            identity_review=self.review_identity,
        )

        def reviews_for(snapshot, rubric):
            # Synthetic reviewer binds the NEW local reread chunk, not old web
            # provenance. Production never rewrites operator review records.
            return tuple(
                review.model_copy(
                    update={
                        "spans": tuple(
                            replace(
                                span,
                                chunk_id=next(
                                    c.chunk_id
                                    for c in snapshot.chunks.values()
                                    if c.source_id == span.source_id
                                    and span.quote in c.text
                                ),
                            )
                            for span in review.spans
                        )
                    }
                )
                for review in self.base["authority"].reviews_for(snapshot, rubric)
            )

        self.authority = replace(
            self.base["authority"],
            sources=originals,
            live_gate_verifier=gate,
            company_report=options,
            reviews_for=reviews_for,
        )
        registry = pinned_approval_registry(ROOT)
        self.actual = ActualAdmissionV3(
            source=ApprovedPolicySource(
                path=ROOT / "configs/scoring.v3.json",
                approvals=registry.policy_approvals(),
                approval_verifier=registry.verify_policy,
                execution_mode="live",
                live_gates=binding.gates,
                live_gate_verifier=gate,
            ),
            runtime_binding=binding,
            registry=registry,
            run_input=self.base["packet"].run_input.model_copy(
                update={
                    "corpus_version": manifest.version,
                    "countries": [self.candidate.country],
                }
            ),
            index_version=manifest.index_metadata.index_version,
            review_resolvers={},
            execution_scope="controlled_response",
            review_resolver_for=lambda snapshot, rubric: review_resolver(
                snapshot, rubric, self.authority, registry
            )[0],
        )
        return self.actual

    def respond(self, request):
        assert request.method == "POST"
        assert str(request.url) == self.document.llm.endpoint
        assert "Authorization" not in request.headers
        payload = json.loads(request.content)
        user = json.loads(payload["input"][1]["content"])
        if "untrusted_evidence" in user:
            role = "freshness"
            output = {
                "judgments": [
                    {
                        "evidence_id": eid,
                        "status": "needs_update" if self.stale else "current",
                        "reason": "SYNTHETIC controlled assessment.",
                        "gap_queries": [
                            {
                                "candidate_id": "co-b" if self.hostile_gap else "co-a",
                                "scope": "company",
                                "field": "business",
                            }
                        ]
                        if self.stale
                        else [],
                    }
                    for eid in user["untrusted_evidence"]
                ]
            }
        elif "context" in user and "context_id" in user:
            role = "judge" if "draft" in user else "generator"
            if role == "generator":
                output = {
                    "schema_version": SCHEMA,
                    "summary": "SYNTHETIC test only.",
                    "company_team": (
                        f"{self.report_claim} [@evidence:{self.report_evidence_id}]"
                    ),
                    "technology": "Technology is unknown.",
                    "market": "Market is unknown.",
                    "assessment_risks": "Not actual investment evidence.",
                    "limitations": ["SYNTHETIC API responses and embeddings."],
                }
            else:
                output = {
                    "schema_version": SCHEMA,
                    "context_id": user["context_id"],
                    "verdict": self.judge_verdict,
                    "findings": [],
                    "revision_instructions": ["Revise"]
                    if self.judge_verdict == "revise"
                    else [],
                    "judged_artifact_hash": user["artifact_hash"],
                }
        else:
            role = user.get("dimension", "business_deal")
            output = None
        with self.lock:
            self.events.append(
                {
                    "kind": "analysis",
                    "role": role,
                    "url": str(request.url),
                    "model": payload["model"],
                    "max_output_tokens": payload["max_output_tokens"],
                }
            )
            self.signal.set()
        if self.api_failure:
            return httpx.Response(503)
        if output is None:
            return self.eval_wire(role).handle_request(request)
        return httpx.Response(
            200,
            json=body(
                json.dumps(output), usage={"input_tokens": 10, "output_tokens": 10}
            ),
        )

    def source_response(self, request):
        assert request.method == "GET"
        is_identity = request.url.host == "opendart.fss.or.kr"
        assert is_identity or str(request.url) == self.candidate.homepage_url
        self.events.append(
            {
                "kind": "identity" if is_identity else "collection",
                "url": str(request.url.copy_remove_param("crtfc_key")),
            }
        )
        self.signal.set()
        if is_identity:
            assert request.url.path == "/api/company.json"
            return httpx.Response(
                200,
                json={
                    "status": self.identity_status,
                    "message": "SYNTHETIC",
                    "corp_code": "99999999"
                    if self.same_name_wrong_number
                    else "00123456",
                    "corp_name": self.candidate.canonical_name,
                    "corp_cls": "E",
                    "bizr_no": "",
                    "jurir_no": "",
                    "hm_url": self.candidate.homepage_url,
                },
                headers={"content-type": "application/json"},
            )
        return httpx.Response(
            200,
            content=b"SYNTHETIC new target material mentions a competitor.",
            headers={"content-type": "text/plain"},
        )

    def prepare_source(self, bundle, raw, records):
        source = next(iter(bundle.sources.values()))
        source = source.model_copy(
            update={"local_path": f"data/local/{source.source_id}.txt"}
        )
        doc = ManifestDocument(
            schema_version=SCHEMA,
            document_id=source.source_id,
            source_id=source.source_id,
            local_path=source.local_path,
            content_hash=source.content_hash,
            title=source.title,
            language=source.language,
            permission_note="Synthetic test review, not operator approval.",
            candidate_ids=("co-a",),
            scope="company",
            extraction_status="ok",
            reviewer="synthetic-test",
            approved=True,
        )
        locators = sorted({e.locator for e in bundle.evidence.values()}) or [source.url]
        chunks = tuple(
            Chunk(
                schema_version=SCHEMA,
                chunk_id=f"{source.source_id}-{i}",
                source_id=source.source_id,
                corpus_version="pending",
                text=raw.content.decode(),
                locator=locator,
                candidate_ids=["co-a"],
                scope="company",
                language=source.language,
                embedding_model=self.index_settings.model_id,
                embedding_revision=REVISION,
            )
            for i, locator in enumerate(locators)
        )
        return RetainedSource(
            doc, source, raw.content, chunks, tuple(bundle.evidence.values()), records
        )

    def review_identity(self, candidate, bundles):
        ids = bundles[0].profile.field_evidence_ids.get("identity")
        if not ids:
            return None
        return self.identity.model_copy(
            update={
                "field_evidence_ids": {
                    field: ids for field in self.identity.field_evidence_ids
                }
            }
        )

    def research_admission(self, actual, *, identity=False):
        query = GapQuery(
            candidate_id="co-a",
            scope="company",
            field="identity" if identity else "business",
        )
        url = (
            "https://opendart.fss.or.kr/api/company.json?corp_code=00123456"
            if identity
            else self.candidate.homepage_url
        )
        targets = (
            (
                ResearchTarget(
                    query,
                    url,
                    provider="opendart-company" if identity else "official-homepage",
                ),
            )
            if identity or self.stale
            else ()
        )
        return ResearchAdmission(
            actual=actual,
            candidate_json=self.candidate.model_dump_json(),
            approved_limits=ResearchLimits(
                max_calls=12, max_cost_usd="1", deadline_seconds=20.0
            ),
            targets=targets,
            authorize=lambda c, ts, caps: c == self.candidate and ts == targets,
            prepare_source=self.prepare_source,
            fetch_policy=FetchPolicy(
                allowed_schemes=frozenset({"https"}),
                allowed_hosts=frozenset({"opendart.fss.or.kr", "synthetic.example"}),
                max_bytes=10000,
                timeout_seconds=2.0,
                max_redirects=0,
            ),
            readiness=actual.runtime_binding.readiness,
            http_transport=httpx.MockTransport(self.source_response),
            resolve=lambda _: ["93.184.216.34"],
        )

    def run(self, name="run", **changes):
        return public.run_company_report(
            self.candidate.canonical_name,
            config_path=self.config,
            output_dir=self.root / name,
            actual_admission=changes.pop("actual_admission", self.actual),
            authority=changes.pop("authority", self.authority),
            **changes,
        )


def receipt(path):
    return CompanyReportReceipt.model_validate_json(
        (path / "run-result.json").read_bytes()
    )


def extracting_research(case):
    """Controlled extraction of a fact absent from the initial retained corpus."""
    original = case.authority.company_report.research_for
    text = "SYNTHETIC newly observed target builds warehouse robots."

    def source(request):
        assert request.method == "GET"
        assert str(request.url) == case.candidate.homepage_url
        case.events.append({"kind": "collection", "url": str(request.url)})
        return httpx.Response(
            200, content=text.encode(), headers={"content-type": "text/plain"}
        )

    def extraction(request):
        assert request.method == "POST"
        assert str(request.url) == case.document.llm.endpoint
        assert "Authorization" not in request.headers
        case.events.append({"kind": "analysis", "role": "eligibility_extraction"})
        return httpx.Response(
            200,
            json=body(
                json.dumps(
                    {
                        "facts": [
                            {
                                "field": "business",
                                "value": None,
                                "stage_label": None,
                                "event_date": None,
                                "subject": case.candidate.canonical_name,
                                "claim": text,
                                "excerpt": text,
                                "confidence": "unknown",
                            }
                        ]
                    }
                ),
                usage={"input_tokens": 10, "output_tokens": 10},
            ),
        )

    def factory(candidate, actual):
        binding = actual.runtime_binding
        llm = RuntimeStructuredLLM(
            runtime=binding.runtime,
            call=binding.call.model_copy(update={"node": "eligibility_extraction"}),
            budget=binding.budget,
            readiness=binding.readiness,
            transport=OpenAIResponsesAttempt(
                api_key=None,
                prompt_version="synthetic-readmission-extraction",
                schema_version=SCHEMA,
                clock=binding.runtime.clock,
                llm_settings=case.document.llm,
                http_transport=httpx.MockTransport(extraction),
            ),
            allowance_for=lambda *_: Allowance(
                schema_version=SCHEMA,
                input_tokens=20000,
                output_tokens=1000,
                max_cost_usd=Decimal("0.1"),
            ),
        )
        return replace(
            original(candidate, actual),
            llm=llm,
            domain_definition="Robotics",
            max_input_chars=10000,
            http_transport=httpx.MockTransport(source),
        )

    case.stale = True
    case.authority = replace(
        case.authority,
        company_report=replace(case.authority.company_report, research_for=factory),
    )


def reviewed_readmission(case, retained, snapshot):
    """Synthetic exact final-source reviewers; never a production approver."""
    manifest = retained.manifest
    extracted_root = retained.root / "synthetic-final-reviewed-text"
    extracted_root.mkdir(exist_ok=True)
    originals = {}
    for sid, src in snapshot.sources.items():
        assert src.local_path is not None
        path = retained.root / src.local_path
        extracted = extracted_root / f"{sid}.txt"
        extracted.write_bytes(path.read_bytes())
        originals[sid] = TrustedCapture(
            path=path,
            allowed_root=retained.root,
            extracted_path=extracted,
            format="text",
            charset="utf-8",
            source=src,
            corpus_version=manifest.version,
            extracted_text=path.read_text(),
            extracted_sha256=src.content_hash,
            approved_chunks=tuple(
                c for c in manifest.chunks.values() if c.source_id == sid
            ),
        )
    return CompanyReportReadmissionV3(
        retained_sha256=digest(retained.payload),
        snapshot_sha256=frozen_snapshot_digest(snapshot),
        sources=originals,
        reviews_for=case.authority.reviews_for,
        evaluation_inputs_for=case.authority.evaluation_inputs_for,
    )


def enable_readmission(case, callback=None):
    """Install only an explicit synthetic Python callback, never future pins."""
    case.authority = replace(
        case.authority,
        company_report=replace(
            case.authority.company_report,
            readmit_after_research=callback
            or (
                lambda retained, snapshot: reviewed_readmission(
                    case, retained, snapshot
                )
            ),
        ),
    )


@pytest.fixture
def case(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def test_stored_only_report_and_next_run_reuse(case):
    # Given: retained originals and controlled wires; no collection consent.
    original = case.before.manifest.evidence
    # When: call the public surface and reopen its actual output/store.
    first = case.run()
    assert receipt(first).outcome == "completed", (
        first / "run-result.json"
    ).read_text()
    case.bind(run_id="synthetic-next-run")
    second = case.run("next")
    # Then: both PDFs are real; next-run retrieval resolves original IDs only.
    assert receipt(second).outcome == "completed"
    for path in (first, second):
        rendered = json.loads((path / "render.json").read_bytes())
        pdf = Path(rendered["artifact_path"])
        assert pdf.read_bytes().startswith(b"%PDF")
        assert 1 <= len(PdfReader(pdf).pages) <= 5
        assert receipt(path).report_validation == "passed"
        assert receipt(path).publication_allowed is False
    reopened = case.store.open()
    assert reopened.manifest.evidence == original
    assert len(reopened.manifest.reports) == 2
    context = json.loads((second / "context.json").read_bytes())
    assert context["company_context"]["prior_interpretations"]
    assert set(context["snapshots"]["co-a"]["evidence"]) == set(original)
    assert all(event["kind"] == "analysis" for event in case.events)
    assert case.signal.is_set()
    assert REQUEST not in (second / "run-result.json").read_bytes()


@pytest.mark.parametrize("kind", ["unknown", "ineligible"])
def test_unknown_eligibility_no_report_or_fetch(case, kind):
    # Given
    case.profile = case.profile.model_copy(
        update={"domain_match": None} if kind == "unknown" else {"is_listed": True}
    )
    # When
    out = case.run()
    # Then
    assert receipt(out).outcome == (
        "eligibility_unknown" if kind == "unknown" else "ineligible"
    )
    assert not (out / "report.md").exists()
    assert not case.events
    assert case.store.open().payload == case.before.payload


@pytest.mark.parametrize("verdict", ["fail", "revise"])
def test_rejected_report_never_becomes_retrievable(case, verdict):
    # Given
    case.judge_verdict = verdict
    # When
    out = case.run()
    # Then
    assert receipt(out).outcome == ("failed" if verdict == "fail" else "warning")
    assert receipt(out).report_validation == "failed"
    assert receipt(out).ingestion_status == "not_attempted"
    assert case.store.open().payload == case.before.payload
    report = json.loads((out / "report-result.json").read_bytes())
    assert report["revisions"] == (0 if verdict == "fail" else 2)


def test_ingestion_failure_preserves_report_and_old_index(case, monkeypatch):
    # Given
    def fail(_self, _descriptor):
        raise OSError("SYNTHETIC publication failure")

    monkeypatch.setattr(CompanyStore, "ingest_report", fail)
    # When
    out = case.run()
    # Then
    result = receipt(out)
    assert result.outcome == "warning"
    assert result.reason_codes == ("REPORT_INGESTION_FAILED",)
    assert result.report_validation == "passed"
    assert result.report_path.is_file()
    assert json.loads((out / "render.json").read_bytes())["artifact_path"]
    assert case.store.open().payload == case.before.payload


def test_missing_review_inputs_blocks_before_evaluation(case):
    # Given
    case.authority = replace(case.authority, evaluation_inputs_for=lambda _: None)
    # When
    out = case.run()
    # Then
    assert receipt(out).outcome == "research_blocked"
    assert receipt(out).reason_codes == ("SNAPSHOT_REVIEW_MISSING",)
    assert {event["role"] for event in case.events} <= {"freshness"}
    assert (out / "missing-review-requests.json").is_file()
    assert not case.store.open().manifest.reports


@pytest.mark.parametrize("authority", [False, True])
def test_unknown_identity_false_never_fetches(tmp_path, monkeypatch, authority):
    # Given
    case = Harness(tmp_path, monkeypatch, known=False)
    # When
    out = case.run(authority=case.authority if authority else None, research=False)
    # Then
    assert receipt(out).outcome == "identity_unknown"
    assert not case.events


def test_unknown_identity_true_no_authority_blocks(tmp_path, monkeypatch):
    # Given
    case = Harness(tmp_path, monkeypatch, known=False)
    # When: a homepage hint is not consent or an operator identity authority.
    out = case.run(
        research=True, homepage_url=case.candidate.homepage_url, authority=None
    )
    # Then
    assert receipt(out).outcome == "research_blocked"
    assert not case.events


def test_unknown_identity_true_ambiguous_never_guesses(tmp_path, monkeypatch):
    # Given
    case = Harness(tmp_path, monkeypatch, known=False, kr=True)
    original = case.authority.company_report.identity_research

    def ambiguous(request, actual):
        one = original(request, actual)[0]
        other = case.candidate.model_copy(update={"candidate_id": "namesake"})
        return (
            one,
            replace(
                one,
                collection=replace(
                    one.collection, candidate_json=other.model_dump_json()
                ),
            ),
        )

    case.authority = replace(
        case.authority,
        company_report=replace(
            case.authority.company_report, identity_research=ambiguous
        ),
    )
    # When
    out = case.run(research=True)
    # Then
    assert receipt(out).outcome == "identity_ambiguous"
    assert receipt(out).matching_candidate_ids == ("co-a", "namesake")
    assert not case.events


@pytest.mark.parametrize("bad", ["date", "company", "corpus"])
def test_admission_mismatch_never_dispatches(case, bad):
    # Given
    if bad == "company":
        case.actual = replace(
            case.actual,
            runtime_binding=replace(
                case.actual.runtime_binding,
                call=case.actual.runtime_binding.call.model_copy(
                    update={"candidate_id": "other"}
                ),
            ),
        )
    elif bad == "corpus":
        case.actual.run_input.corpus_version = "foreign"
    # When
    out = case.run(
        **({"as_of": NOW.date() + timedelta(days=1)} if bad == "date" else {})
    )
    # Then
    assert receipt(out).outcome == "research_blocked"
    assert not case.events


def test_malformed_configuration_has_no_side_effects(case):
    # Given
    case.config.write_text('{"schema_version":"company-report-1","unexpected":true}')
    # When / Then
    with pytest.raises(ValueError):
        case.run()
    assert not (case.root / "run").exists()
    assert not case.events


def test_missing_caps_is_a_no_fetch_receipt(case):
    # Given
    case.config_data.pop("research_limits")
    case.config.write_bytes(canonical(case.config_data))
    # When
    out = case.run(research=True)
    # Then
    assert receipt(out).reason_codes == ("RESEARCH_CAPS_REQUIRED",)
    assert receipt(out).outcome == "research_blocked"
    assert not case.events


def test_explicit_false_overrides_file_true(case):
    # Given
    case.config_data["research_enabled"] = True
    case.config.write_bytes(canonical(case.config_data))
    case.stale = True
    # When
    out = case.run(research=False)
    # Then
    assert receipt(out).outcome == "completed"
    assert receipt(out).research_requested is False
    assert all(e["kind"] == "analysis" for e in case.events)


def test_api_failure_returns_failed_receipt(case):
    # Given
    case.api_failure = True
    # When
    out = case.run()
    # Then
    assert receipt(out).outcome == "failed"
    assert not case.store.open().manifest.reports
    assert case.actual.runtime_binding.runtime.ledger.snapshot()["calls"] == 1


def test_pdf_rejection_never_ingests(case, monkeypatch):
    # Given: real PDF renderer detects absent pinned font assets.
    import skala_rag.reporting.pdf as pdf

    monkeypatch.setattr(pdf, "FONT_ROOT", case.root / "absent-fonts")
    # When
    out = case.run()
    # Then
    assert receipt(out).outcome == "failed"
    assert receipt(out).report_validation == "failed"
    assert not case.store.open().manifest.reports


def test_requested_research_cannot_fetch_competitor(case):
    # Given: hostile freshness attempts to reattribute the target's gap.
    case.stale = case.hostile_gap = True
    # When
    out = case.run(research=True)
    # Then
    assert receipt(out).outcome == "failed"
    assert all(e["kind"] == "analysis" for e in case.events)


def test_budget_exhaustion_never_becomes_success(case):
    # Given: admission is valid but one freshness call consumes the total ceiling.
    case.bind(max_calls=1)
    # When
    out = case.run()
    # Then
    assert receipt(out).outcome != "completed"
    assert len(case.events) == 1
    assert not case.store.open().manifest.reports


def test_review_helper_private_alias_is_preserved():
    # Given / When
    from skala_rag.graph.actual_reviews_v3 import _Reviews
    from skala_rag.graph.actual_runner_v3 import _Reviews as compatibility

    # Then
    assert compatibility is _Reviews


def planned_research_case(
    tmp_path, monkeypatch, *, identity=False, competitor=False, with_target=False
):
    """Prepare a separate controlled intake, not an actual review or test result.

    Immutable index identity is content-addressed. Operator inputs must name the
    final generation in advance; production never changes an admission after fetch.
    """
    with monkeypatch.context() as preview_patch:
        preview = Harness(
            tmp_path / "preview",
            preview_patch,
            known=not identity,
            kr=identity,
            competitor=competitor,
        )
        preview.stale = not identity or with_target
        output = preview.run(research=True)
        assert receipt(output).reason_codes == ("CORPUS_ADMISSION_MISMATCH",), (
            output / "run-result.json"
        ).read_text()
        anticipated = preview.store.open()
    case = Harness(
        tmp_path / "actual-controlled",
        monkeypatch,
        known=not identity,
        kr=identity,
        competitor=competitor,
    )
    case.stale = not identity or with_target
    case.bind(anticipated)
    return case


def test_research_target_only_with_stored_competitors(tmp_path, monkeypatch):
    # Given: synthetic authority anticipates exactly the admitted new store.
    # This source-retention case does not establish unknown future facts:
    # the homepage adapter has no extractor, and the preview pre-pins its bytes.
    case = planned_research_case(tmp_path, monkeypatch, competitor=True)
    actual, authority = case.actual, case.authority
    run_input = actual.run_input.model_dump(mode="json")
    index_version = actual.index_version
    # When
    out = case.run(research=True)
    # Then
    result = receipt(out)
    assert result.outcome == "completed", (out / "run-result.json").read_text()
    assert result.report_validation == "passed"
    assert result.ingestion_status == "succeeded"
    assert case.actual is actual and case.authority is authority
    assert actual.run_input.model_dump(mode="json") == run_input
    assert actual.index_version == index_version
    frozen = EvaluationSnapshot.model_validate_json(
        (out / "snapshot.json").read_bytes()
    )
    assert frozen.corpus_version != case.before.manifest.version
    assert frozen.index_version != case.before.manifest.index_metadata.index_version
    assert frozen.corpus_version == actual.run_input.corpus_version
    assert frozen.index_version == actual.index_version
    digest = frozen_snapshot_digest(frozen)
    reviews = json.loads((out / "reviews.json").read_bytes())
    assert set(reviews) == {digest}
    supplied = [
        review for records in reviews[digest]["reviews"].values() for review in records
    ]
    assert supplied and all(r["snapshot_sha256"] == digest for r in supplied)
    for version in ("core-0.1.0", "finance-0.1.0"):
        actual.verify_snapshot(frozen, actual.registry.rubric(version))
    collected = [e for e in case.events if e["kind"] == "collection"]
    assert collected == [{"kind": "collection", "url": case.candidate.homepage_url}]
    context = json.loads((out / "context.json").read_bytes())
    company = context["company_context"]
    assert company["competitor_index_version"] == case.before.manifest.version
    assert company["competitors"] == [
        {
            "candidate_id": "co-b",
            "canonical_name": "SYNTHETIC COMPETITOR",
            "evidence_ids": ["ev-b"],
        }
    ]
    assert len(case.store.open().manifest.sources) > len(case.before.manifest.sources)
    assert receipt(out).collection_records


def test_research_from_starting_admission_retains_but_cannot_report(case):
    # Given: no preview or replacement approval for the unknown next generation.
    case.stale = True
    actual, authority = case.actual, case.authority
    run_input = actual.run_input.model_dump(mode="json")
    index_version = actual.index_version
    runtime = actual.runtime_binding.runtime
    ledger = runtime.ledger
    # When
    out = case.run(research=True)
    # Then: successful collection is not successful report admission.
    result = receipt(out)
    assert result.outcome == "research_blocked"
    assert result.reason_codes == ("CORPUS_ADMISSION_MISMATCH",)
    assert result.collection_records
    retained = case.store.open()
    assert retained is not None
    assert set(retained.manifest.sources) - set(case.before.manifest.sources)
    assert retained.manifest.version != run_input["corpus_version"]
    assert retained.manifest.index_metadata.index_version != index_version
    assert case.actual is actual and case.authority is authority
    assert actual.run_input.model_dump(mode="json") == run_input
    assert actual.index_version == index_version
    assert actual.runtime_binding.runtime is runtime and runtime.ledger is ledger
    assert ledger.snapshot()["calls"] == len(case.events)
    assert all(
        e.get("role") == "freshness" for e in case.events if e["kind"] == "analysis"
    )
    assert not retained.manifest.reports
    assert not (out / "reviews.json").exists()
    assert not (out / "render.json").exists()
    # Removing the public guard would still fail in the trusted consumer.
    frozen = public._snapshot(
        retained, case.candidate, tuple(retained.manifest.chunks), actual
    )
    with pytest.raises(ValueError, match="snapshot run/corpus/cutoff/index mismatch"):
        actual.verify_snapshot(frozen, actual.registry.rubric("core-0.1.0"))


def test_new_facts_readmission_reports_and_reuses_without_future_pins(
    tmp_path, monkeypatch
):
    # Given: only the starting corpus is admitted; no preview collection.
    case = Harness(tmp_path, monkeypatch, competitor=True)
    extracting_research(case)
    original = case.actual
    original_input = original.run_input.model_dump(mode="json")
    binding = original.runtime_binding
    ledger = binding.runtime.ledger
    phases = []
    evaluate = public.evaluate_actual_branch_v3

    def capture(role, snapshot, **kwargs):
        phases.append(kwargs["admission"])
        return evaluate(role, snapshot, **kwargs)

    monkeypatch.setattr(public, "evaluate_actual_branch_v3", capture)

    def approve(retained, snapshot):
        new_ids = set(snapshot.evidence) - set(case.before.manifest.evidence)
        assert len(new_ids) == 1
        case.report_evidence_id = next(iter(new_ids))
        case.report_claim = snapshot.evidence[case.report_evidence_id].claim
        return reviewed_readmission(case, retained, snapshot)

    enable_readmission(case, approve)
    # When: the actual public call collects/extracts and freezes before approval.
    out = case.run(research=True)
    # Then: real report validation/intake and the same cumulative budget.
    assert receipt(out).outcome == "completed", (out / "run-result.json").read_text()
    assert receipt(out).report_validation == "passed"
    assert receipt(out).ingestion_status == "succeeded"
    assert len(phases) == 5 and all(p is phases[0] for p in phases)
    phase = phases[0]
    assert phase is not original and case.actual is original
    assert phase.runtime_binding is binding
    assert phase.runtime_binding.runtime.ledger is ledger
    assert phase.source is original.source and phase.registry is original.registry
    assert original.run_input.model_dump(mode="json") == original_input
    assert original.index_version == case.before.manifest.index_metadata.index_version
    assert (
        phase.run_input.model_copy(
            update={"corpus_version": original.run_input.corpus_version}
        )
        == original.run_input
    )
    assert ledger.snapshot()["calls"] == len(case.events)
    frozen = EvaluationSnapshot.model_validate_json(
        (out / "snapshot.json").read_bytes()
    )
    assert case.report_evidence_id in frozen.evidence
    assert case.report_evidence_id not in case.before.manifest.evidence
    with pytest.raises(ValueError, match="snapshot run/corpus/cutoff/index mismatch"):
        original.verify_snapshot(frozen, original.registry.rubric("core-0.1.0"))
    audit = json.loads((out / "readmission.json").read_bytes())
    assert audit["ledger_before_review"]["calls"] == 3
    assert audit["ledger_before_review"] == audit["ledger_after_review"]
    assert audit["shared_runtime_binding"] and audit["shared_ledger"]
    context = json.loads((out / "context.json").read_bytes())
    assert context["company_context"]["competitor_index_version"] == (
        case.before.manifest.version
    )
    assert context["company_context"]["competitors"][0]["evidence_ids"] == ["ev-b"]
    reopened = case.store.open()
    assert reopened is not None
    stored_report = next(iter(reopened.manifest.reports.values()))
    assert case.report_evidence_id in stored_report.original_evidence_hashes
    assert PdfReader(
        json.loads((out / "render.json").read_bytes())["artifact_path"]
    ).pages
    # A later invocation has its own operator admission, never a mid-run reset.
    case.bind(run_id="synthetic-new-fact-reuse")
    case.events.clear()
    reused = case.run("reuse", research=False)
    assert receipt(reused).outcome == "completed"
    assert all(e["kind"] == "analysis" for e in case.events)
    second = json.loads((reused / "context.json").read_bytes())
    assert case.report_evidence_id in second["snapshots"]["co-a"]["evidence"]
    assert second["company_context"]["prior_interpretations"]


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        ("denied", "READMISSION_DENIED"),
        ("json", "READMISSION_DENIED"),
        ("snapshot", "READMISSION_SNAPSHOT_MISMATCH"),
        ("retained", "READMISSION_SNAPSHOT_MISMATCH"),
        ("stale_reviews", "SNAPSHOT_REVIEW_MISSING"),
        ("stale_sources", "SOURCE_AUTHORITY_MISMATCH"),
        ("reset_ledger", "READMISSION_RUNTIME_CHANGED"),
        ("reset_usage", "READMISSION_RUNTIME_CHANGED"),
        ("raise_limits", "READMISSION_RUNTIME_CHANGED"),
        ("extend_deadline", "READMISSION_RUNTIME_CHANGED"),
    ],
)
def test_new_fact_readmission_refusals_never_dispatch(case, bad, reason):
    # Given: genuine new extraction with a hostile operator response or mutation.
    extracting_research(case)
    original = case.actual
    old = public._snapshot(
        case.before, case.candidate, tuple(case.before.manifest.chunks), original
    )
    old_reviews = case.authority.reviews_for
    binding = original.runtime_binding

    def reject(retained, snapshot):
        supplied = reviewed_readmission(case, retained, snapshot)
        match bad:
            case "denied":
                return None
            case "json":
                return {"snapshot_sha256": supplied.snapshot_sha256}
            case "snapshot":
                return replace(supplied, snapshot_sha256="0" * 64)
            case "retained":
                return replace(supplied, retained_sha256="0" * 64)
            case "stale_reviews":
                return replace(supplied, reviews_for=lambda _s, r: old_reviews(old, r))
            case "stale_sources":
                return replace(supplied, sources=case.authority.sources)
            case "reset_ledger":
                binding.runtime.ledger = BudgetLedger(binding.runtime.ledger.limits)
            case "reset_usage":
                binding.runtime.ledger._calls = 0
            case "raise_limits":
                binding.runtime.ledger.limits = (
                    binding.runtime.ledger.limits.model_copy(update={"max_calls": 400})
                )
            case "extend_deadline":
                binding.budget.deadline += timedelta(seconds=60)
        return supplied

    enable_readmission(case, reject)
    # When
    out = case.run(research=True)
    # Then: intake remains, but neither evaluator nor report is dispatched.
    assert receipt(out).outcome == "research_blocked"
    assert receipt(out).reason_codes == (reason,), (out / "run-result.json").read_text()
    assert all(e.get("role") not in public.BRANCH_DIMENSIONS for e in case.events)
    reopened = case.store.open()
    assert reopened is not None
    assert (
        len(set(reopened.manifest.evidence) - set(case.before.manifest.evidence)) == 1
    )
    assert not reopened.manifest.reports


def test_unknown_identity_readmission_continues_without_future_pins(
    tmp_path, monkeypatch
):
    # Given: no stored target identity and no anticipated corpus.
    case = Harness(tmp_path, monkeypatch, known=False, kr=True)
    original = case.actual
    enable_readmission(case)
    # When
    out = case.run(research=True)
    # Then
    assert receipt(out).outcome == "completed", (out / "run-result.json").read_text()
    assert case.actual is original
    assert original.run_input.corpus_version == case.before.manifest.version
    assert [e["kind"] for e in case.events if e["kind"] != "analysis"] == ["identity"]
    reopened = case.store.open()
    assert reopened is not None and reopened.manifest.reports
    assert original.runtime_binding.runtime.ledger.snapshot()["calls"] == len(
        case.events
    )


def test_default_mode_never_invokes_readmission_callback(case):
    # Given: explicit callback cannot override stored-only mode.
    enable_readmission(case, lambda *_: pytest.fail("Stored-only must not re-admit"))
    case.stale = True
    # When
    out = case.run(research=False)
    # Then
    assert receipt(out).outcome == "completed"
    assert all(e["kind"] == "analysis" for e in case.events)
    assert not (out / "readmission.json").exists()


def test_researched_snapshot_rejects_old_reviews(tmp_path, monkeypatch):
    # Given: source-bound review records belong to the pre-research snapshot.
    case = planned_research_case(tmp_path, monkeypatch)
    old = public._snapshot(
        case.before, case.candidate, tuple(case.before.manifest.chunks), case.actual
    )
    old_reviews = case.authority.reviews_for
    case.authority = replace(
        case.authority, reviews_for=lambda _s, r: old_reviews(old, r)
    )
    # When
    out = case.run(research=True)
    # Then
    assert receipt(out).reason_codes == ("SNAPSHOT_REVIEW_MISSING",)
    assert any(e["kind"] == "collection" for e in case.events)
    assert all(e.get("role") not in public.BRANCH_DIMENSIONS for e in case.events)
    assert not case.store.open().manifest.reports
    assert case.store.open().manifest.version != case.before.manifest.version


def test_unknown_identity_true_resolves_retains_and_continues(tmp_path, monkeypatch):
    # Given: separately admitted OpenDART identity, not a homepage/LLM guess.
    case = planned_research_case(tmp_path, monkeypatch, identity=True)
    # When
    out = case.run(research=True)
    # Then
    assert receipt(out).outcome == "completed", (out / "run-result.json").read_text()
    stored = case.store.open().manifest
    assert "co-a" in stored.companies and stored.reports
    assert [e["kind"] for e in case.events if e["kind"] != "analysis"] == ["identity"]
    ids = stored.companies["co-a"].field_evidence_ids["canonical_name"]
    assert ids and all(stored.evidence[eid].source_id != "src-a" for eid in ids)
    assert case.actual.runtime_binding.runtime.ledger.snapshot()["calls"] == len(
        case.events
    )
    assert "SYNTHETIC-KEY-NOT-REAL" not in "".join(
        p.read_text() for p in out.glob("*.json")
    )


def test_identity_then_target_research_retains_distinct_receipts(tmp_path, monkeypatch):
    # Given: both admitted provider stages share one actual run and one ledger.
    case = planned_research_case(tmp_path, monkeypatch, identity=True, with_target=True)
    # When
    out = case.run(research=True)
    # Then: the second adapter cannot collide with the first adapter's call 1.
    result = receipt(out)
    assert result.outcome == "completed", (out / "run-result.json").read_text()
    assert [e["kind"] for e in case.events if e["kind"] != "analysis"] == [
        "identity",
        "collection",
    ]
    ids = [r.retrieval_id for r in result.collection_records]
    assert len(ids) == len(set(ids))
    reopened = case.store.open()
    assert reopened is not None and reopened.manifest.reports
    provider_records = [
        r
        for r in reopened.manifest.retrieval_records.values()
        if r.tool_name.startswith("company-research/")
    ]
    assert {r.tool_name for r in provider_records} == {
        "company-research/opendart-company",
        "company-research/official-homepage",
    }
    assert {r.run_id for r in provider_records} == {result.run_id}
    assert case.actual.runtime_binding.runtime.ledger.snapshot()["calls"] == len(
        case.events
    )


def test_unknown_identity_true_rejects_namesake_identifier(tmp_path, monkeypatch):
    # Given
    case = Harness(tmp_path, monkeypatch, known=False, kr=True)
    case.same_name_wrong_number = True
    # When
    out = case.run(research=True)
    # Then
    assert receipt(out).outcome == "identity_unknown"
    assert len(case.events) == 1 and case.events[0]["kind"] == "identity"
    reopened = case.store.open()
    assert reopened is not None and reopened.payload == case.before.payload


def test_shared_research_cap_stops_after_identity(tmp_path, monkeypatch):
    # Given
    case = Harness(tmp_path, monkeypatch, known=False, kr=True)
    # When
    out = case.run(research=True, research_limits={"max_calls": 1})
    # Then: one shared requested ceiling, not one fresh budget per phase.
    assert receipt(out).outcome == "research_blocked"
    assert receipt(out).reason_codes == ("RESEARCH_CAP_EXHAUSTED",)
    assert len(case.events) == 1 and case.events[0]["kind"] == "identity"
    reopened = case.store.open()
    assert reopened is not None and not reopened.manifest.reports


def test_runtime_override_is_shared_by_every_analysis_role(case, monkeypatch):
    # Given
    case.store.ingest_sources((case.competitor_material(),))
    case.bind()
    search = store_module.StoreSnapshot.search
    local_results = []

    def observed_search(snapshot, *args, **kwargs):
        hits = search(snapshot, *args, **kwargs)
        local_results.append((kwargs["top_k"], len(hits)))
        return hits

    monkeypatch.setattr(store_module.StoreSnapshot, "search", observed_search)
    document = case.document.model_copy(
        update={
            "profiles": case.document.profiles.model_copy(
                update={
                    "actual_v3": case.document.profiles.actual_v3.model_copy(
                        update={"request_output_tokens": 1000, "retrieval_top_k": 1}
                    )
                }
            )
        }
    )
    # When
    out = case.run(runtime_document=document)
    # Then
    assert receipt(out).outcome == "completed"
    assert local_results == [(1, 1)]
    assert {e["max_output_tokens"] for e in case.events} == {1000}
    assert {e["model"] for e in case.events} == {document.llm.model}
    effective = json.loads((out / "effective-config.json").read_bytes())
    assert (
        effective["runtime_document"]["profiles"]["actual_v3"]["request_output_tokens"]
        == 1000
    )
    assert case.document.profiles.actual_v3.request_output_tokens == 2000


def test_missing_local_assets_never_downloads(case, monkeypatch):
    # Given
    def absent(*args, **kwargs):
        raise FileNotFoundError("Synthetic missing local model")

    monkeypatch.setattr(store_module, "load_local_encoder", absent)
    # When
    out = case.run()
    # Then
    assert receipt(out).reason_codes == ("LOCAL_STORE_NOT_READY",)
    assert not case.events


def test_invalid_authority_callback_fails_before_output(case):
    # Given
    invalid = replace(case.authority, evaluation_inputs_for=None)
    # When / Then
    with pytest.raises(ValueError, match="AUTHORITY_CALLBACK_REQUIRED"):
        case.run(authority=invalid)
    assert not (case.root / "run").exists() and not case.events


def test_denied_research_returns_blocked_without_fetch(case):
    # Given
    case.stale = True
    case.authority = replace(
        case.authority,
        company_report=replace(
            case.authority.company_report,
            research_for=lambda _, actual: replace(
                case.research_admission(actual), authorize=lambda *_: False
            ),
        ),
    )
    # When
    out = case.run(research=True)
    # Then
    assert receipt(out).reason_codes == ("RESEARCH_ADMISSION_DENIED",)
    assert receipt(out).outcome == "research_blocked"
    assert all(e["kind"] == "analysis" for e in case.events)


def test_stored_namesakes_are_ambiguous_before_any_dispatch(case):
    # Given
    other = case.competitor_material()
    identity = CompanyIdentity(
        schema_version=SCHEMA,
        candidate=case.candidate.model_copy(
            update={
                "candidate_id": "co-b",
                "homepage_url": None,
                "discovery_source_ids": ["src-b"],
            }
        ),
        field_evidence_ids={"canonical_name": ["ev-b"], "country": ["ev-b"]},
    )
    case.store.ingest_sources((other,), companies=(identity,))
    # When
    out = case.run()
    # Then
    assert receipt(out).outcome == "identity_ambiguous"
    assert receipt(out).matching_candidate_ids == ("co-a", "co-b")
    assert not case.events


@pytest.mark.parametrize("interruptions", [1, 2])
def test_interruptions_preserve_store_and_consumed_shared_budget(case, interruptions):
    # Given: inject the exact post-fetch event, with no timing or polling.
    case.stale = True
    captured = Event()

    def interrupt(*_args):
        captured.set()
        raise KeyboardInterrupt

    case.authority = replace(
        case.authority,
        company_report=replace(
            case.authority.company_report,
            research_for=lambda _, actual: replace(
                case.research_admission(actual), prepare_source=interrupt
            ),
        ),
    )
    # When: interrupted calls cannot resume over their existing output.
    for attempt in range(interruptions):
        with pytest.raises(KeyboardInterrupt):
            case.run(f"interrupted-{attempt}", research=True)
        with pytest.raises(FileExistsError):
            case.run(f"interrupted-{attempt}", research=True)
    # Then: no report or new index, cumulative reservations never reset.
    assert captured.is_set()
    assert case.store.open().payload == case.before.payload
    assert (
        case.actual.runtime_binding.runtime.ledger.snapshot()["calls"]
        == 2 * interruptions
    )
    assert all(not p.exists() for p in case.root.glob("interrupted-*/report.md"))


def test_expired_authority_deadline_has_no_dispatch(case):
    # Given: deterministic clock movement instead of a sleep.
    case.actual.runtime_binding.runtime.clock.advance(timedelta(seconds=31))
    # When
    out = case.run()
    # Then
    assert receipt(out).outcome == "research_blocked"
    assert not case.events


def test_research_cost_reservation_cannot_exceed_requested_cap(case):
    # Given / When: zero is an explicit requested cap, not unknown cost.
    out = case.run(research=True, research_limits={"max_cost_usd": 0})
    # Then
    assert receipt(out).reason_codes == ("RESEARCH_CAP_EXHAUSTED",)
    assert receipt(out).outcome == "research_blocked"
    assert not case.events
