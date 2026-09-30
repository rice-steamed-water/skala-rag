"""All HTTP/model/index observations are synthetic; no live success evidence."""

import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from skala_rag.agents import m2_component_live as runner
from skala_rag.agents.evaluation import DimensionAssessmentOutput
from skala_rag.agents.m2_llm import M2LLMs
from skala_rag.agents.m2_trace import TraceInvalid
from skala_rag.contracts import (
    Candidate,
    Chunk,
    RetrievalBundle,
    RetrievalRecord,
    RunInput,
    Source,
    ToolResult,
)
from skala_rag.fakes import FakeClock
from skala_rag.prompts.eligibility_facts import EligibilityFactsOutput
from skala_rag.prompts.evidence_extraction import ExtractionOutput
from skala_rag.rag.adapter import IndexSnapshot
from skala_rag.scoring.catalog import load_policy
from skala_rag.tools.source_fetch import SafeFetcher
from skala_rag.tools.structured_llm import APPROVED_MODEL

NOW = datetime(2026, 9, 30, tzinfo=UTC)
KEY = "sk-synthetic-component-not-a-real-api-key"
NAME = "Synthetic Robot"
TEXT = NAME + " combines robot perception and control."
HOME = (
    NAME
    + " builds robotics. "
    + NAME
    + " completed Series A. "
    + NAME
    + " has not completed an acquisition or IPO exit."
)


@pytest.fixture
def wired(tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("synthetic runner attempted real network")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("OPENDART_API_KEY", "synthetic-dart-secret")
    monkeypatch.setattr(runner, "Clock", lambda: FakeClock(NOW))
    root = tmp_path
    (root / "configs").mkdir()
    (root / "configs/scoring.draft.json").write_text(
        Path("configs/scoring.draft.json").read_text()
    )
    rubric = yaml.safe_load(Path("configs/rubrics/core.yaml").read_text())
    rubric["status"] = "approved"  # synthetic gate fixture, not a policy approval
    (root / "core.yaml").write_text(yaml.safe_dump(rubric, allow_unicode=True))
    candidate = dict(
        schema_version="test",
        candidate_id="co-synthetic",
        canonical_name=NAME,
        aliases=[],
        country="KR",
        homepage_url="https://robot.example/",
        legal_identifiers={"dart": "01234567", "brn": "1234567890"},
        discovery_source_ids=[],
    )
    config = dict(
        candidate=candidate,
        run_input=dict(
            schema_version="test",
            investment_theme="Robotics",
            countries=["KR"],
            languages=["en"],
            as_of="2026-09-30",
            policy_version="main-draft-0.1.0",
            corpus_version="test-corpus",
            execution_mode="live",
        ),
        run_id="synthetic-run",
        local_rag=dict(
            model_path="unused",
            store_path="unused",
            receipt_path="unused",
            query="robot control",
        ),
        domain_definition="Robotics",
        campaign_cost_usd_accounted="0.00",
        rubric_path="core.yaml",
    )
    config_path = root / "input.json"
    config_path.write_text(json.dumps(config))
    source = Source(
        schema_version="test",
        source_id="src-technology",
        title="Synthetic paper",
        source_kind="paper",
        url="https://robot.example/paper.pdf",
        retrieved_at=NOW,
        content_hash="synthetic-hash",
        language="en",
        access_notes="synthetic",
        bibliographic_metadata={},
    )
    chunk = Chunk(
        schema_version="test",
        chunk_id="chunk-technology",
        source_id=source.source_id,
        corpus_version="test-corpus",
        text=TEXT,
        page_start=1,
        page_end=1,
        locator=source.url + "#page=1",
        candidate_ids=["co-synthetic"],
        scope="company",
        language="en",
        embedding_model="synthetic-model",
        embedding_revision="synthetic-revision",
    )
    bundle = RetrievalBundle(
        schema_version="test", chunks=[chunk], sources={source.source_id: source}
    )
    snapshot = IndexSnapshot(
        schema_version="test",
        corpus_version="test-corpus",
        corpus_hash="synthetic-hash",
        index_version="test-index",
        embedding_model="synthetic-model",
        embedding_revision="synthetic-revision",
        search_settings={},
        bundle=bundle,
    )
    seen = dict(http=[], llm=[], retrieval=[])

    class RAG:
        def __init__(self, **kwargs):
            self.snapshot = snapshot

        def retrieve(self, **kwargs):
            seen["retrieval"].append(kwargs)
            record = RetrievalRecord(
                schema_version="test",
                retrieval_id="retrieval-tech",
                run_id="synthetic-run",
                candidate_id="co-synthetic",
                tool_name="synthetic-retrieve",
                query="robot control",
                arguments_without_secrets=dict(
                    corpus_version="test-corpus",
                    index_version="test-index",
                    as_of="2026-09-30",
                ),
                started_at=NOW,
                finished_at=NOW,
                status="ok",
                source_ids=[source.source_id],
                chunk_ids=[chunk.chunk_id],
                evidence_ids=[],
                error_id=None,
                cost=None,
                cache_hit=False,
            )
            return ToolResult[RetrievalBundle](
                schema_version="test",
                status="ok",
                data=bundle,
                retrieval_records=[record],
                errors=[],
            )

    monkeypatch.setattr(runner, "LocalRAG", RAG)

    def http(request):
        seen["http"].append(request)
        if request.url.host == "robot.example":
            return httpx.Response(
                200,
                text="<html>" + HOME + "</html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(
            200,
            json=dict(
                status="000",
                message="synthetic",
                corp_code="01234567",
                corp_name=NAME,
                bizr_no="1234567890",
                jurir_no="",
                corp_cls="E",
            ),
        )

    monkeypatch.setattr(
        runner,
        "SafeFetcher",
        lambda policy, **kwargs: SafeFetcher(
            policy,
            **kwargs,
            transport=httpx.MockTransport(http),
            resolve=lambda host: ["93.184.216.34"],
        ),
    )

    def llm(request):
        payload = json.loads(request.content)
        user = json.loads(payload["input"][1]["content"])
        seen["llm"].append(user)
        version = user["prompt_version"]
        if version == "eligibility-facts-v1":
            content = dict(
                facts=[
                    dict(
                        field=field,
                        value=value,
                        stage_label=stage,
                        subject=NAME,
                        claim=excerpt,
                        excerpt=excerpt,
                    )
                    for field, value, stage, excerpt in [
                        ("domain_match", True, None, NAME + " builds robotics."),
                        ("business", None, None, NAME + " builds robotics."),
                        ("stage", None, "Series A", NAME + " completed Series A."),
                        (
                            "exit_completed",
                            False,
                            None,
                            NAME + " has not completed an acquisition or IPO exit.",
                        ),
                    ]
                ]
            )
        elif version == "evidence-extraction-v1":
            content = dict(claims=[dict(claim=TEXT, excerpt=TEXT, subject=NAME)])
        else:
            eid = user["evidence"][0]["evidence_id"]
            content = dict(
                criteria=[
                    dict(
                        criterion_id=c,
                        status="observed"
                        if c == "technology.integration"
                        else "missing",
                        rating=3 if c == "technology.integration" else None,
                        evidence_ids=[eid] if c == "technology.integration" else [],
                        rationale="synthetic",
                        missing_reason=None
                        if c == "technology.integration"
                        else "not_disclosed",
                    )
                    for c in user["criteria"]
                ]
            )
        schema = {
            "eligibility-facts-v1": EligibilityFactsOutput,
            "evidence-extraction-v1": ExtractionOutput,
            "technology-evaluation-v1": DimensionAssessmentOutput,
        }[version]
        content = schema.model_validate(content).model_dump(mode="json")
        return httpx.Response(
            200,
            json=dict(
                model=APPROVED_MODEL,
                status="completed",
                output=[
                    dict(
                        type="message",
                        content=[dict(type="output_text", text=json.dumps(content))],
                    )
                ],
                usage=dict(input_tokens=100, output_tokens=50),
            ),
        )

    monkeypatch.setattr(
        runner,
        "M2LLMs",
        lambda **kwargs: M2LLMs(**kwargs, http_transport=httpx.MockTransport(llm)),
    )
    return root, config_path, root / "outputs/component", config, seen


def test_preflight_never_fetches_or_calls_llm_even_when_key_is_present(wired):
    root, path, output, _, seen = wired
    receipt = runner.run(root=root, input_path=path, output_dir=output)
    assert receipt["execution_started"] is False
    assert receipt["whole_m2_verified"] is False
    assert not output.exists()
    assert seen == {"http": [], "llm": [], "retrieval": []}


@pytest.mark.parametrize("missing", ["key", "rubric"])
def test_missing_required_readiness_blocks_all_requests(wired, monkeypatch, missing):
    root, path, output, _, seen = wired
    if missing == "key":
        monkeypatch.delenv("OPENAI_API_KEY")
    else:
        rubric = yaml.safe_load((root / "core.yaml").read_text())
        rubric["status"] = "proposed"
        (root / "core.yaml").write_text(yaml.safe_dump(rubric))
    with pytest.raises(ValueError):
        runner.run(root=root, input_path=path, output_dir=output, live=True)
    assert seen == {"http": [], "llm": [], "retrieval": []}
    assert not output.exists()


def test_mocked_provider_to_technology_chain_persists_state_and_shared_usage(wired):
    root, path, output, _, seen = wired
    receipt = runner.run(root=root, input_path=path, output_dir=output, live=True)
    assert receipt["status"] == "technology_component_trace_verified"
    assert len(seen["http"]) == 2
    assert len(seen["llm"]) == 3
    assert len(seen["retrieval"]) == 1
    assert receipt["llm_runtime"]["ledger"]["calls"] == 3
    admitted = json.loads((output / "research-state.json").read_text())
    assert admitted["eligibility_results"]["co-synthetic"]["status"] == "eligible"
    evaluated = json.loads((output / "technology/research-state.json").read_text())
    assert (
        evaluated["evaluation_results"]["co-synthetic:1:technology"]["status"]
        == "success"
    )
    assert receipt["stage_receipt"]["trace"][0]["page_start"] == 1
    assert receipt["whole_m2_verified"] is False
    for artifact in output.rglob("*.json"):
        assert KEY not in artifact.read_text()
        assert "synthetic-dart-secret" not in artifact.read_text()


def test_unknown_admission_never_loads_model_or_calls_technology(wired, monkeypatch):
    root, path, output, _, seen = wired
    monkeypatch.delenv("OPENDART_API_KEY")
    receipt = runner.run(root=root, input_path=path, output_dir=output, live=True)
    assert receipt["status"] == "blocked_admission"
    assert receipt["stage_receipt"]["eligibility_status"] == "unknown"
    assert len(seen["llm"]) == 1
    assert seen["retrieval"] == []
    assert not (output / "technology").exists()


def test_campaign_cap_is_checked_before_any_request(wired):
    root, path, output, config, seen = wired
    config["campaign_cost_usd_accounted"] = "2.01"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        runner.run(root=root, input_path=path, output_dir=output, live=True)
    assert seen == {"http": [], "llm": [], "retrieval": []}


def test_failed_trace_keeps_admission_and_terminal_usage(wired, monkeypatch):
    root, path, output, _, seen = wired

    def fail(**kwargs):
        raise TraceInvalid("synthetic trace failure")

    monkeypatch.setattr(runner, "run_technology_trace", fail)
    with pytest.raises(TraceInvalid):
        runner.run(root=root, input_path=path, output_dir=output, live=True)
    assert (output / "research-state.json").exists()
    assert not (output / "technology").exists()
    terminal = json.loads((output / "component-receipt.json").read_text())
    assert terminal["status"] == "component_failed"
    assert terminal["llm_runtime"]["ledger"]["calls"] == 1
    assert len(seen["llm"]) == 1
    assert terminal["actual_cost_usd"] is None


def test_required_llm_auth_failure_is_not_reported_as_missing_success(
    wired, monkeypatch
):
    root, path, output, _, seen = wired
    calls = []

    def auth_failure(request):
        calls.append(request)
        return httpx.Response(401, text=KEY)

    monkeypatch.setattr(
        runner,
        "M2LLMs",
        lambda **kwargs: M2LLMs(
            **kwargs, http_transport=httpx.MockTransport(auth_failure)
        ),
    )
    receipt = runner.run(root=root, input_path=path, output_dir=output, live=True)
    assert receipt["status"] == "required_llm_failed"
    assert len(calls) == 1
    assert receipt["llm_runtime"]["ledger"]["calls"] == 1
    assert seen["retrieval"] == []
    assert KEY not in (output / "component-receipt.json").read_text()


@pytest.mark.parametrize("all_over_bound", [False, True])
def test_chunk_selection_preserves_text_and_skips_overlarge_returns(
    wired, all_over_bound
):
    root, _, _, config, seen = wired
    bundle = runner.LocalRAG().snapshot.bundle.model_copy(deep=True)
    original = bundle.chunks[0].model_copy(deep=True)
    oversized = original.model_copy(
        update={"chunk_id": "oversized", "text": "x" * 9000}
    )
    bundle.chunks = [oversized] if all_over_bound else [oversized, original]
    retrieval = ToolResult[RetrievalBundle](
        schema_version="test", status="ok", data=bundle, retrieval_records=[], errors=[]
    )
    clock = FakeClock(NOW)
    llms = M2LLMs(
        api_key=KEY,
        run_id="synthetic",
        candidate_id="co-synthetic",
        schema_version="test",
        execution_mode="fixture",
        clock=clock,
        deadline=NOW.replace(hour=1),
        http_transport=httpx.MockTransport(
            lambda request: pytest.fail("selection attempted HTTP")
        ),
    )
    kwargs = dict(
        candidate=Candidate.model_validate(config["candidate"]),
        run_input=RunInput.model_validate(config["run_input"]),
        policy=load_policy(
            root / "configs/scoring.draft.json", execution_mode="fixture"
        ),
        llm=llms.stages["evidence"],
    )
    if all_over_bound:
        with pytest.raises(TraceInvalid):
            runner.select_bounded_chunk(retrieval, **kwargs)
    else:
        assert runner.select_bounded_chunk(retrieval, **kwargs) == [original.chunk_id]
        assert bundle.chunks[1].text == TEXT
    assert llms.runtime.ledger.snapshot()["calls"] == 0
    assert seen["llm"] == []
