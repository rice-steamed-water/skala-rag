"""Offline regression tests; these are not actual model/API evidence."""

import pytest


def test_only_approved_company_and_missing_credentials_make_no_calls(
    tmp_path, monkeypatch
):
    from skala_rag.local_demo import run_demo

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="COMPANY_NOT_SUPPORTED"):
        run_demo(root=tmp_path, company="Other company", progress=lambda _: None)
    with pytest.raises(ValueError, match="OPENAI_API_KEY_MISSING"):
        run_demo(
            root=tmp_path, company="Physical Intelligence", progress=lambda _: None
        )
    assert not (tmp_path / "outputs").exists()


def retrieval_fixture(tmp_path):
    """Synthetic local content, never used by the actual demo runner."""
    import hashlib
    from datetime import UTC, datetime

    from skala_rag.contracts import Chunk, RetrievalBundle, RetrievalRecord, Source

    text = "Synthetic robot learns manipulation."
    path = tmp_path / "paper.txt"
    path.write_text(text)
    now = datetime.now(UTC)
    source = Source(
        schema_version="demo-v1",
        source_id="src-test",
        title="Synthetic paper",
        publisher="Synthetic author",
        author=None,
        source_kind="paper",
        url="https://example.com/paper",
        local_path=str(path),
        published_at=None,
        retrieved_at=now,
        content_hash="sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        language="en",
        access_notes="Synthetic test only",
        bibliographic_metadata={},
    )
    chunk = Chunk(
        schema_version="demo-v1",
        chunk_id="chunk-test",
        source_id=source.source_id,
        corpus_version="test-corpus",
        text=text,
        page_start=1,
        page_end=1,
        section=None,
        locator="https://example.com/paper#page=1",
        candidate_ids=["co-physical-intelligence"],
        scope="company",
        language="en",
        embedding_model="test-embedding",
        embedding_revision="test-revision",
    )
    record = RetrievalRecord(
        schema_version="demo-v1",
        retrieval_id="retrieval-test",
        run_id="test-run",
        candidate_id="co-physical-intelligence",
        tool_name="test-retriever",
        query="robot",
        arguments_without_secrets={},
        started_at=now,
        finished_at=now,
        status="ok",
        source_ids=[source.source_id],
        chunk_ids=[chunk.chunk_id],
        evidence_ids=[],
        cache_hit=False,
    )
    return RetrievalBundle(
        schema_version="demo-v1", chunks=[chunk], sources={source.source_id: source}
    ), [record]


def test_research_context_keeps_real_trace_without_inventing_scores(tmp_path):
    from skala_rag.demo_context import build_research_context, research_material

    bundle, records = retrieval_fixture(tmp_path)
    material = research_material(
        root=tmp_path, bundle=bundle, records=records, run_id="test-run"
    )
    context = build_research_context(run_id="test-run", material=material, reviews={})
    data = context.snapshot()
    assert data["scores"] == data["decisions"] == {}
    assert data["selection"]["selected_candidate_id"] is None
    assert data["research_subject"] == "Physical Intelligence"
    assert len(data["evidence"]) == 1
    evidence = next(iter(data["evidence"].values()))
    assert evidence["provenance"][0]["chunk_id"] == "chunk-test"
    assert evidence["excerpt"] == bundle.chunks[0].text


@pytest.mark.parametrize("broken", ["hash", "trace", "company", "run"])
def test_broken_research_trace_is_rejected(tmp_path, broken):
    from skala_rag.demo_context import research_material

    bundle, records = retrieval_fixture(tmp_path)
    if broken == "hash":
        (tmp_path / "paper.txt").write_text("tampered")
    elif broken == "trace":
        records[0].chunk_ids = []
    elif broken == "company":
        bundle.chunks[0].candidate_ids = ["other-company"]
    elif broken == "run":
        records[0].run_id = "old-run"
    with pytest.raises(ValueError):
        research_material(
            root=tmp_path, bundle=bundle, records=records, run_id="test-run"
        )


def test_runner_records_failure_without_fake_pdf(tmp_path, monkeypatch):
    import json

    from tests.unit.test_demo_budget import approve

    import skala_rag.local_demo as demo

    approve(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-secret")
    monkeypatch.setattr(
        demo,
        "_retrieve",
        lambda **_: (_ for _ in ()).throw(ValueError("LOCAL_INDEX_MISSING")),
        raising=False,
    )
    output = demo.run_demo(
        root=tmp_path, company="Physical Intelligence", progress=lambda _: None
    )
    receipt = json.loads((output / "run-result.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["publication_allowed"] is False
    assert receipt["llm_calls"] == []
    assert not (output / "report.pdf").exists()
    assert "synthetic-test-secret" not in (output / "run-result.json").read_text()


def test_review_rejects_invented_evidence(tmp_path):
    from skala_rag.demo_context import build_research_context, research_material

    bundle, records = retrieval_fixture(tmp_path)
    material = research_material(
        root=tmp_path, bundle=bundle, records=records, run_id="test-run"
    )
    reviews = {
        "technology": {
            "observations": [{"text": "made up", "evidence_ids": ["invented"]}],
            "interpretations": [],
        }
    }
    with pytest.raises(ValueError, match="REVIEW_EVIDENCE_INVALID"):
        build_research_context(run_id="test-run", material=material, reviews=reviews)


def test_scope_notice_is_not_optional_model_text():
    from skala_rag.demo_context import WARNING
    from skala_rag.local_demo import enforce_scope_notice
    from skala_rag.reporting.v3_pipeline import ReportContentV3

    content = ReportContentV3(
        schema_version="local-demo-v1",
        summary="Test",
        company_team="Test",
        technology_market="Test",
        assessment_risks="Test",
        limitations=[],
    )
    result = enforce_scope_notice(content)
    assert WARNING in result.summary
    assert enforce_scope_notice(result).summary == result.summary


def test_demo_llm_uses_existing_approved_transport_limits(tmp_path):
    import json

    import httpx
    from tests.unit.test_demo_budget import approve

    from skala_rag.demo_budget import Campaign
    from skala_rag.local_demo import SCHEMA, Clock, DemoLLM, Review
    from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt
    from skala_rag.tools.structured_llm import APPROVED_MODEL

    approve(tmp_path)
    calls = []

    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload["max_output_tokens"] <= 2000
        return httpx.Response(
            200,
            json={
                "model": APPROVED_MODEL,
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "schema_version": SCHEMA,
                                        "criteria": [],
                                        "observations": [],
                                        "interpretations": [],
                                        "missing": ["synthetic"],
                                    }
                                ),
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 30},
            },
        )

    with Campaign(tmp_path) as campaign:
        llm = DemoLLM(
            key="synthetic",
            campaign=campaign,
            node="founder",
            receipt={"llm_calls": []},
            destination=tmp_path,
            progress=lambda _: None,
        )
        llm.transport = OpenAIResponsesAttempt(
            api_key="synthetic",
            prompt_version="test",
            schema_version=SCHEMA,
            clock=Clock(),
            http_transport=httpx.MockTransport(respond),
        )
        result = llm.generate(
            system="Synthetic only", user="Synthetic", output_schema=Review
        )
        assert result.missing == ["synthetic"]
        assert len(calls) == 1


def test_generated_citations_are_structural_not_optional_prompt_text():
    from skala_rag.demo_context import SCHEMA
    from skala_rag.local_demo import ResearchContent, cited_content

    section = {
        "facts": [{"text": "Synthetic fact", "evidence_ids": ["ev-real"]}],
        "interpretation": "Synthetic interpretation",
        "unknown": "Not verified",
    }
    content = ResearchContent(
        schema_version=SCHEMA,
        summary=section,
        company_team=section,
        technology_market=section,
        assessment_risks=section,
        limitations=["Synthetic only"],
    )
    converted = cited_content(content, {"ev-real"})
    assert "[@evidence:ev-real]" in converted.summary
    assert "[자료에서 확인]" in converted.summary
    assert "[분석·해석]" in converted.summary
    assert "[판단 불가]" in converted.summary
    with pytest.raises(ValueError, match="GENERATOR_EVIDENCE_INVALID"):
        cited_content(content, {"different-evidence"})
    for name in ("summary", "company_team", "technology_market", "assessment_risks"):
        getattr(content, name).facts = []
    with pytest.raises(ValueError, match="GENERATOR_CITATIONS_MISSING"):
        cited_content(content, {"ev-real"})


@pytest.mark.parametrize("field", ["findings", "revision_instructions"])
def test_real_demo_llm_rejects_contradictory_judge_pass(tmp_path, field):
    import json

    import httpx
    from tests.unit.test_demo_budget import approve

    from skala_rag.contracts import ReportJudgement
    from skala_rag.demo_budget import Campaign
    from skala_rag.local_demo import SCHEMA, Clock, DemoLLM
    from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt
    from skala_rag.tools.structured_llm import APPROVED_MODEL

    approve(tmp_path)
    judgement = dict(
        schema_version=SCHEMA,
        verdict="pass",
        context_id="ctx",
        judged_artifact_hash="hash",
        findings=[],
        revision_instructions=[],
    )
    judgement[field] = (
        [
            dict(
                schema_version=SCHEMA,
                severity="stub",
                claim_location="summary",
                evidence_ids=[],
                reason="fix this",
            )
        ]
        if field == "findings"
        else ["fix this"]
    )
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": APPROVED_MODEL,
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps(judgement)}
                        ],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 30},
            },
        )

    with Campaign(tmp_path) as campaign:
        llm = DemoLLM(
            key="synthetic",
            campaign=campaign,
            node="judge",
            receipt={"llm_calls": []},
            destination=tmp_path,
            progress=lambda _: None,
        )
        llm.transport = OpenAIResponsesAttempt(
            api_key="synthetic",
            prompt_version="test",
            schema_version=SCHEMA,
            clock=Clock(),
            http_transport=httpx.MockTransport(respond),
        )
        with pytest.raises(ValueError, match="JUDGE_PASS_CONTRADICTORY"):
            llm.generate(system="Synthetic", user="{}", output_schema=ReportJudgement)
        assert len(requests) == campaign.state["calls"] == 1
        assert json.loads((tmp_path / "judge-response.json").read_text()) == judgement


@pytest.mark.parametrize("known", [True, False])
def test_receipt_uses_allowlisted_diagnostic_codes_only(tmp_path, monkeypatch, known):
    import json

    from tests.unit.test_demo_budget import approve

    import skala_rag.local_demo as demo

    approve(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    error = ValueError("LOCAL_RETRIEVAL_FAILED" if known else "secret-body")
    if not known:
        error.error_code = "secret-body"
    monkeypatch.setattr(demo, "_retrieve", lambda **_: (_ for _ in ()).throw(error))
    output = demo.run_demo(
        root=tmp_path, company="Physical Intelligence", progress=lambda _: None
    )
    text = (output / "run-result.json").read_text()
    receipt = json.loads(text)
    assert receipt["error_code"] == (
        "LOCAL_RETRIEVAL_FAILED" if known else "DEMO_EXECUTION_FAILED"
    )
    assert "secret-body" not in text
