"""Synthetic model HTTP/vectors; actual public API, graph, citations and saved PDF."""

import hashlib
import inspect
import json
import socket
from dataclasses import replace
from datetime import date

import httpx
import pytest
from pydantic import ValidationError
from pypdf import PdfReader
from tests.integration.test_narrative_report_graph import make_case
from tests.unit.test_company_report_research import (
    HOME,
    TEXT,
    configured_service,
)
from tests.unit.test_company_report_research import make_case as make_research_case
from tests.unit.test_company_store import identity
from tests.unit.test_openai_attempt import body

import skala_rag.company_report as public
from skala_rag.agents.company_analysis import analysis_questions
from skala_rag.contracts.company_report import (
    QUESTION_IDS,
    NarrativeRunResult,
)
from skala_rag.rag.company_store import CompanyStore, evidence_fingerprint
from skala_rag.reporting.narrative import NARRATIVE_HEADINGS
from skala_rag.settings import load_runtime_document
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt

VERSION = "narrative-report-1"


def deny_socket(*args, **kwargs):
    pytest.fail("Synthetic transport must not open a network connection")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", deny_socket)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_socket)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-SYNTHETIC-NOT-A-CREDENTIAL")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")


class Harness:
    """The only substitutions are explicit local-model loader and HTTP transports."""

    def __init__(self, root, monkeypatch, *, mode="partial", needs=False):
        self.root, self.mode, self.needs = root, mode, needs
        root.mkdir(parents=True, exist_ok=True)
        self.store = make_case(root).controller._service.store
        self.requests = []
        self.roles = []
        self.judge_verdict = "pass"
        self.invalid_ids = False
        self.http_status = 200
        self.config = root / "company-report.json"
        runtime = load_runtime_document().model_dump(mode="json")
        self.runtime = root / "runtime.json"
        self.runtime.write_text(json.dumps(runtime))
        self.config.write_text(
            json.dumps(
                {
                    "schema_version": "company-report-2",
                    "runtime_path": str(self.runtime),
                    "store_dir": str(self.store.root),
                    "model_path": str(root / "synthetic-model"),
                    "model_receipt_path": str(root / "synthetic-receipt"),
                    "research_enabled": False,
                }
            )
        )
        monkeypatch.setattr(CompanyStore, "from_local", lambda *a, **k: self.store)
        original = OpenAIResponsesAttempt.__init__

        def transport_init(attempt, **kwargs):
            if kwargs.get("http_transport") is None:
                kwargs["http_transport"] = httpx.MockTransport(self.respond)
            original(attempt, **kwargs)

        monkeypatch.setattr(OpenAIResponsesAttempt, "__init__", transport_init)

    def respond(self, request):
        wire = json.loads(request.content)
        user = json.loads(wire["input"][1]["content"])
        self.requests.append(user)
        if self.http_status != 200:
            return httpx.Response(self.http_status, json={"error": "synthetic"})
        writer = user["writer_context"]
        context_id = writer["context_id"]
        eid = next(iter(writer["evidence"]))
        claim = writer["evidence"][eid]["claim"]
        name = wire["text"]["format"]["name"]
        self.roles.append(name)
        if name == "AnalysisReply":
            answers = {
                qid: {
                    "schema_version": VERSION,
                    "status": "answered",
                    "findings": [
                        {
                            "schema_version": VERSION,
                            "kind": "fact",
                            "text": claim,
                            "evidence_ids": ["forged"] if self.invalid_ids else [eid],
                        }
                    ],
                    "limitation": None,
                    "verification_task": None,
                }
                for qid in QUESTION_IDS
            }
            if self.mode == "partial":
                answers[QUESTION_IDS[-1]] = {
                    "schema_version": VERSION,
                    "status": "unknown",
                    "findings": [],
                    "limitation": "해당 질문에 대한 관측이 없다.",
                    "verification_task": "거래조건 원문을 확인한다.",
                }
            reply = {
                "schema_version": VERSION,
                "context_id": context_id,
                "answers": answers,
                "needs": (
                    [
                        {
                            "schema_version": VERSION,
                            "question_id": QUESTION_IDS[-1],
                            "information_needed": "Warehouse observation",
                            "related_evidence_ids": [eid],
                        }
                    ]
                    if self.needs and writer["may_request_information"]
                    else []
                ),
            }
        elif name == "CompositionReply":
            tasks = [
                {
                    "schema_version": VERSION,
                    "question_id": qid,
                    "task": answer["verification_task"],
                }
                for qid, answer in user["analysis"]["answers"].items()
                if answer["status"] == "unknown"
            ]
            sections = []
            for heading in NARRATIVE_HEADINGS:
                text = f"{claim} [@evidence:{eid}]"
                if tasks and heading == "SUMMARY":
                    text += " 자료가 부족하여 부분 분석으로 제시한다."
                if tasks and heading == NARRATIVE_HEADINGS[-2]:
                    text += "\n\n" + "\n".join(
                        answer["limitation"]
                        for answer in user["analysis"]["answers"].values()
                        if answer["status"] == "unknown"
                    )
                if tasks and heading == NARRATIVE_HEADINGS[-1]:
                    text += "\n\n" + "\n".join(
                        f"- {task['question_id']}: {task['task']}" for task in tasks
                    )
                sections.append(f"## {heading}\n\n{text}")
            reply = {
                "schema_version": VERSION,
                "context_id": context_id,
                "markdown_without_reference": "\n\n".join(sections),
                "cited_evidence_ids": [eid],
                "strengths": [],
                "weaknesses": [],
                "verification_tasks": tasks,
            }
        else:
            reply = {
                "schema_version": VERSION,
                "context_id": context_id,
                "artifact_hash": user["artifact_hash"],
                "verdict": self.judge_verdict,
                "issues": (
                    []
                    if self.judge_verdict == "pass"
                    else [
                        {
                            "schema_version": VERSION,
                            "code": "unsupported_claim",
                            "location": "summary",
                            "reason": "근거로 확인되지 않는다.",
                            "evidence_ids": [eid],
                        }
                    ]
                ),
            }
        return httpx.Response(
            200,
            json=body(
                json.dumps(reply),
                usage={"input_tokens": 120, "output_tokens": 1000},
            ),
        )

    def run(self, **kwargs):
        return public.run_company_report(
            identity().candidate.canonical_name,
            config_path=self.config,
            output_dir=self.root / "output",
            as_of=date(2026, 10, 10),
            **kwargs,
        )


def receipt(path):
    return NarrativeRunResult.model_validate_json(
        (path / "run-result.json").read_bytes()
    )


@pytest.mark.parametrize(
    "mode,outcome", [("full", "completed"), ("partial", "partial")]
)
def test_narrative_full_and_partial_public_api(tmp_path, monkeypatch, mode, outcome):
    # Given: synthetic model HTTP and vectors with real retained originals.
    harness = Harness(tmp_path, monkeypatch, mode=mode)
    # When: only the production public entry point runs.
    out = harness.run()
    # Then: current validation receipts bind an actual saved PDF and exact references.
    result = receipt(out)
    assert result.outcome == outcome
    assert result.validation == "passed"
    assert len(PdfReader(result.pdf_path).pages) >= 2
    if mode == "partial":
        assert QUESTION_IDS[-1] in "\n".join(
            page.extract_text() for page in PdfReader(result.pdf_path).pages
        )
    assert result.model_usage["calls"] == 3
    assert harness.roles == ["AnalysisReply", "CompositionReply", "SemanticJudgeReply"]
    assert result.collection_records == []
    assert (
        len({request["writer_context"]["context_id"] for request in harness.requests})
        == 2
    )
    assert not harness.requests[-1]["writer_context"]["may_request_information"]
    assert set(json.loads((out / "references.json").read_bytes())) == {
        "context_id",
        "references",
    }
    assert result.validation_receipt_hashes == [
        hashlib.sha256((out / name).read_bytes()).hexdigest()
        for name in ("judgment.json", "pdf-validation.json", "references.json")
    ]
    assert not any("citation_registry" in request for request in harness.requests)
    assert (
        harness.requests[1]["analysis"]["answers"]
        == json.loads((out / "analysis.json").read_bytes())["answers"]
    )
    assert (
        harness.requests[1]["analysis"]["context_id"]
        == harness.requests[1]["writer_context"]["context_id"]
    )
    records = json.loads((out / "provider-trace.json").read_bytes())["records"]
    assert [record["query"] for record in records] == [
        f"{identity().candidate.canonical_name} {question.question}"
        for question in analysis_questions()
    ]
    assert len({record["retrieval_id"] for record in records}) == 18
    assert all(record["status"] == "ok" for record in records)


def test_local_only_single_enrichment_retains_shared_budget(tmp_path, monkeypatch):
    # Given: a factual need and no research permission.
    harness = Harness(tmp_path, monkeypatch, needs=True)
    # When: the public graph settles that need.
    out = harness.run()
    # Then: one local round, same four model calls, closed final generation.
    assert receipt(out).outcome == "partial"
    state = json.loads((out / "lifecycle.json").read_bytes())
    assert state["information_rounds"] == 1
    assert state["repairs"] == 0
    assert receipt(out).model_usage["calls"] == 4
    assert harness.roles == [
        "AnalysisReply",
        "AnalysisReply",
        "CompositionReply",
        "SemanticJudgeReply",
    ]
    assert harness.requests[0]["writer_context"]["may_request_information"]
    assert not harness.requests[1]["writer_context"]["may_request_information"]


@pytest.mark.parametrize(
    ("mode", "fetches", "research_models"),
    [
        ("allowed", 1, 1),
        ("disabled", 0, 0),
        ("homepage_only", 0, 0),
        ("denied", 0, 0),
        ("failure", 1, 0),
    ],
)
def test_public_configured_research_factory_records_actual_usage(
    tmp_path, monkeypatch, mode, fetches, research_models
):
    harness = Harness(tmp_path / "public", monkeypatch, needs=True)
    case = make_research_case(
        tmp_path / "research",
        status=503 if mode == "failure" else 200,
    )
    receipt_path = None
    cutoff = date(2099, 12, 31)
    if mode in ("allowed", "failure", "denied"):
        _, receipt_path, _ = configured_service(
            case,
            tmp_path / "research",
            mode="denied" if mode == "denied" else "allowed",
        )
        receipt_data = json.loads(receipt_path.read_bytes())
        receipt_data["as_of"] = cutoff.isoformat()
        receipt_path.write_text(json.dumps(receipt_data))
    original_snapshot = harness.store.open()
    assert original_snapshot is not None
    original_evidence_ids = set(original_snapshot.manifest.evidence)
    search = harness.store.search_local
    monkeypatch.setattr(
        harness.store,
        "search_local",
        lambda query, **kwargs: (
            () if query == "Warehouse observation" else search(query, **kwargs)
        ),
    )
    config = json.loads(harness.config.read_bytes())
    config.update(
        acquisition_receipt_path=(
            str(receipt_path) if receipt_path is not None else None
        ),
        research_enabled=mode != "disabled",
        research_limits={
            "max_calls": 4,
            "max_cost_usd": "1",
            "deadline_seconds": 20,
        }
        if mode != "disabled"
        else None,
    )
    harness.config.write_text(json.dumps(config))
    acquisition_requests = []

    def acquisition_response(request):
        if request.method == "GET":
            return case.recorder(request)
        acquisition_requests.append(request)
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
                                "subject": identity().candidate.canonical_name,
                                "claim": TEXT,
                                "excerpt": TEXT,
                                "confidence": "unknown",
                            }
                        ]
                    }
                )
            ),
        )

    build_factory = public.build_narrative_research_service

    def controlled_factory(**kwargs):
        return build_factory(
            **kwargs,
            execution_scope="controlled_response",
            http_transport=httpx.MockTransport(acquisition_response),
            resolve=case.admission.resolve,
        )

    monkeypatch.setattr(public, "build_narrative_research_service", controlled_factory)
    out = public.run_company_report(
        identity().candidate.canonical_name,
        config_path=harness.config,
        output_dir=tmp_path / "public" / "output",
        as_of=cutoff,
    )
    result = receipt(out)
    assert len(case.recorder.requests) == fetches
    assert len(acquisition_requests) == research_models
    assert result.model_usage["calls"] == (
        len(harness.requests) + fetches + research_models
    )
    assert result.outcome == "partial"
    trace = json.loads((out / "provider-trace.json").read_bytes())["records"]
    trace_ids = {record["retrieval_id"] for record in trace}
    assert all(record.retrieval_id in trace_ids for record in result.collection_records)
    retained = harness.store.open()
    assert retained is not None
    assert original_evidence_ids <= set(retained.manifest.evidence)
    if mode in ("allowed", "failure"):
        assert result.collection_records
        assert any(
            record.tool_name == "official-homepage"
            for record in result.collection_records
        )
    else:
        assert result.collection_records == []
    if mode == "allowed":
        assert len(retained.manifest.evidence) > len(original_evidence_ids)
        writer = json.loads((out / "writer-input.json").read_bytes())
        acquired_ids = set(retained.manifest.evidence) - original_evidence_ids
        assert acquired_ids <= set(writer["evidence"])
        assert all(
            item["status"] == "provided" and item["reason"] is None
            for item in writer["information_results"]
        )
        assert acquired_ids <= {
            eid
            for item in writer["information_results"]
            for eid in item["evidence_ids"]
        }
        assert all(
            retained.manifest.evidence[eid] == original_snapshot.manifest.evidence[eid]
            and writer["evidence"][eid]
            == harness.requests[0]["writer_context"]["evidence"][eid]
            for eid in original_evidence_ids
        )
        acquired = next(
            source
            for source in retained.manifest.sources.values()
            if source.url == HOME
        )
        assert acquired.local_path is not None
        assert (harness.store.root / acquired.local_path).read_bytes() == TEXT.encode()
    else:
        assert set(retained.manifest.evidence) == original_evidence_ids


@pytest.mark.parametrize("review", ["valid", "missing", "stale"])
def test_reviewed_originals_without_stored_company(tmp_path, monkeypatch, review):
    # Given: retained originals without any stored company/legal identity.
    harness = Harness(tmp_path, monkeypatch)
    snapshot = harness.store.open()
    assert snapshot is not None
    manifest = snapshot.manifest
    manifest.companies.clear()
    monkeypatch.setattr(
        harness.store,
        "open",
        lambda: replace(snapshot, payload=manifest.model_dump_json().encode()),
    )
    association = {
        "schema_version": "issuer-association-1",
        "subject_id": "co-a",
        "name": identity().candidate.canonical_name,
        "evidence_hashes": {
            "ev-a": evidence_fingerprint(
                manifest.evidence["ev-a"], manifest.sources["src-a"]
            )
        },
    }
    if review == "stale":
        association["evidence_hashes"]["ev-a"] = "sha256:" + "0" * 64
    path = tmp_path / "issuer.json"
    path.write_text(json.dumps(association))
    config = json.loads(harness.config.read_bytes())
    if review != "missing":
        config["issuer_receipt_path"] = path.name
    harness.config.write_text(json.dumps(config))
    # When: the simplified public invocation loads the effective local review.
    out = harness.run()
    # Then: only valid reviewed originals reach the three production roles.
    result = receipt(out)
    if review == "valid":
        assert result.outcome == "partial"
        assert result.subject_id == "co-a"
        assert result.model_usage["calls"] == 3
        assert result.validation == "passed"
        assert len(PdfReader(result.pdf_path).pages) >= 2
        assert "issuer_association" not in json.dumps(harness.requests)
        retained = harness.store.open()
        assert retained is not None and not retained.manifest.companies
    else:
        assert result.outcome == "failed"
        assert result.reason_codes == [
            "ISSUER_ASSOCIATION_REQUIRED"
            if review == "missing"
            else "ISSUER_ASSOCIATION_MISMATCH"
        ]
        assert not harness.requests


def test_last_approved_question_can_find_initial_evidence(tmp_path, monkeypatch):
    # Given: only the final approved question returns genuine retained hits.
    harness = Harness(tmp_path, monkeypatch)
    search = harness.store.search_local
    last = analysis_questions()[-1]

    def only_last(query, **kwargs):
        return search(query, **kwargs) if query.endswith(last.question) else ()

    monkeypatch.setattr(harness.store, "search_local", only_last)
    # When: initial retrieval runs before deciding whether the corpus is empty.
    out = harness.run()
    # Then: all searches are recorded and the admitted originals are deduplicated.
    assert receipt(out).outcome == "partial"
    records = json.loads((out / "provider-trace.json").read_bytes())["records"]
    assert len(records) == 18
    assert [record["status"] for record in records] == ["empty"] * 17 + ["ok"]
    writer = json.loads((out / "writer-input.json").read_bytes())
    assert set(writer["evidence"]) == set(records[-1]["evidence_ids"])


@pytest.mark.parametrize("empty_store", [True, False])
def test_empty_never_dispatches_or_publishes(tmp_path, monkeypatch, empty_store):
    # Given: no stored corpus or a successful local search with no results.
    harness = Harness(tmp_path, monkeypatch)
    if empty_store:
        monkeypatch.setattr(harness.store, "open", lambda: None)
    else:
        monkeypatch.setattr(harness.store, "search_local", lambda *a, **k: ())
    # When: the public API searches.
    out = harness.run()
    # Then: no fabricated report or model dispatch.
    assert receipt(out).outcome == "empty"
    assert not harness.requests
    assert not (out / "report.pdf").exists()
    records = json.loads((out / "provider-trace.json").read_bytes())["records"]
    assert len(records) == (0 if empty_store else 18)


def test_stored_namesakes_are_ambiguous_before_dispatch(tmp_path, monkeypatch):
    # Given: two observed stored subjects sharing a name.
    harness = Harness(tmp_path, monkeypatch)
    snapshot = harness.store.open()
    assert snapshot is not None
    manifest = snapshot.manifest
    second = identity().model_copy(
        update={
            "candidate": identity().candidate.model_copy(
                update={"candidate_id": "co-b"}
            )
        }
    )
    manifest.companies["co-b"] = second
    monkeypatch.setattr(
        harness.store,
        "open",
        lambda: replace(snapshot, payload=manifest.model_dump_json().encode()),
    )
    # When: the public API resolves the subject.
    out = harness.run()
    # Then: ambiguity is not empty or report success.
    assert receipt(out).outcome == "ambiguous"
    assert receipt(out).subject_id is None
    assert not harness.requests


@pytest.mark.parametrize(
    "failure", ["provider", "model", "forged", "semantic", "budget"]
)
def test_technical_and_validation_failures_never_publish(
    tmp_path, monkeypatch, failure
):
    # Given: an explicit failure at the actual production boundary.
    harness = Harness(tmp_path, monkeypatch)
    if failure == "provider":

        def broken(*args, **kwargs):
            raise OSError("synthetic local-model failure")

        monkeypatch.setattr(harness.store, "search_local", broken)
    elif failure == "model":
        harness.http_status = 503
    elif failure == "forged":
        harness.invalid_ids = True
    elif failure == "semantic":
        harness.judge_verdict = "fail"
    else:
        runtime = json.loads(harness.runtime.read_bytes())
        runtime["profiles"]["actual_v3"]["max_calls"] = 1
        harness.runtime.write_text(json.dumps(runtime))
    # When: the public graph executes.
    out = harness.run()
    # Then: technical failures remain failed, and no substantive report is published.
    assert receipt(out).outcome == "failed"
    assert not (out / "report.pdf").exists()
    assert not (out / "report.md").exists()
    assert receipt(out).pdf_path is None


def test_v1_config_rejection_precedes_output(tmp_path, monkeypatch):
    # Given: an archived v1 config.
    harness = Harness(tmp_path, monkeypatch)
    data = json.loads(harness.config.read_bytes())
    data["schema_version"] = "company-report-1"
    harness.config.write_text(json.dumps(data))
    # When: the new public API receives it.
    with pytest.raises(ValidationError) as error:
        harness.run()
    # Then: explicit migration rejection has no output side effect.
    assert (
        error.value.errors()[0]["ctx"]["error"].code
        == "COMPANY_REPORT_V1_MIGRATION_REQUIRED"
    )
    assert not (tmp_path / "output").exists()


def test_public_signature_has_only_simplified_inputs():
    assert list(inspect.signature(public.run_company_report).parameters) == [
        "company_name",
        "config_path",
        "output_dir",
        "research",
        "homepage_url",
        "as_of",
        "research_limits",
    ]
