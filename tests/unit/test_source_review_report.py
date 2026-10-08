"""Synthetic FakeLLM controls, never actual Generator/Judge evidence."""

import hashlib
import json
from copy import deepcopy

import pytest

from skala_rag.fakes import FakeLLM
from skala_rag.reporting.source_review import build_source_review_context
from skala_rag.reporting.v3_context import canonical
from skala_rag.reporting.v3_pipeline import (
    ReportGeneratorV3,
    SemanticJudgeV3,
    outcome_block,
    run_source_review_report,
    validate_report_v3,
)
from skala_rag.reporting.validator import artifact_hash


def pin(value):
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


def capsule():
    source = {
        "schema_version": "test-1",
        "source_id": "synthetic-dexory",
        "title": "Synthetic company announcement",
        "publisher": "Dexory (synthetic)",
        "author": None,
        "source_kind": "web",
        "url": "fixture://dexory/announcement",
        "local_path": None,
        "published_at": "2025-01-01",
        "retrieved_at": "2026-10-08T00:00:00Z",
        "content_hash": "synthetic-not-raw-integrity",
        "language": "en",
        "access_notes": "Synthetic control; not a real capture",
        "bibliographic_metadata": {},
    }
    quote = "Dexory reports warehouse inventory robots."
    evidence = {
        "schema_version": "test-1",
        "evidence_id": "synthetic-quote",
        "candidate_id": "dexory",
        "scope": "company",
        "criterion_ids": [],
        "claim": quote,
        "source_id": source["source_id"],
        "locator": source["url"],
        "excerpt": quote,
        "provenance": [],
        "evidence_kind": "reported",
        "confidence": "unknown",
        "limitations": ["Synthetic self-report; not independently verified"],
        "supporting_evidence_ids": [],
        "conflicts_with": [],
    }
    return {
        "schema_version": "test-1",
        "run_id": "synthetic-source-review",
        "as_of": "2026-10-08",
        "execution_mode": "fixture",
        "research_subject": "Dexory",
        "sources": {source["source_id"]: source},
        "source_texts": {source["source_id"]: quote},
        "evidence": {evidence["evidence_id"]: evidence},
    }


def context(data=None):
    data = capsule() if data is None else data
    return build_source_review_context(
        data, expected_input_id=pin(data), expected_sources=deepcopy(data["sources"])
    )


def content():
    claim = "기업은 창고 재고 로봇을 설명한다. [@evidence:synthetic-quote]"
    return {
        "schema_version": "test-1",
        "summary": claim,
        "company_team": claim + " 팀의 현재 구성은 미확인이다.",
        "technology": claim + " 독립 성능 검증 자료는 없다.",
        "market": "시장 규모와 경쟁 점유율은 미확인이다.",
        "assessment_risks": "기업 발표만으로 현재 실적을 판단할 수 없다.",
        "limitations": ["합성 제어군이며 실제 검증 결과가 아니다."],
    }


def draft(ctx=None, body=None):
    ctx = context() if ctx is None else ctx
    return ReportGeneratorV3(FakeLLM([content() if body is None else body]))(ctx, [])


def judgement(ctx, report, verdict="pass"):
    return {
        "schema_version": "test-1",
        "context_id": ctx.context_id,
        "judged_artifact_hash": artifact_hash(report),
        "verdict": verdict,
        "findings": [],
        "revision_instructions": ["Unsupported claim: keep unknown"]
        if verdict == "revise"
        else [],
    }


def run(ctx, generator, judge, **kwargs):
    return run_source_review_report(
        ctx,
        expected_context_id=ctx.context_id,
        expected_input_id=ctx.snapshot()["input_id"],
        generate=ReportGeneratorV3(generator),
        judge=SemanticJudgeV3(judge),
        **kwargs,
    )


def test_detached_inputs_and_snapshot():
    data = capsule()
    ctx = context(data)
    original = ctx.snapshot()
    data["sources"]["synthetic-dexory"]["title"] = "changed"
    data["evidence"]["synthetic-quote"]["claim"] = "changed"
    ctx.snapshot()["source_texts"].clear()
    assert ctx.snapshot() == original
    assert not any(
        original[k] for k in ("scores", "decisions", "snapshots", "outcomes")
    )
    assert "selection" not in original


@pytest.mark.parametrize("field", ["run_id", "as_of", "source_texts", "evidence"])
def test_input_pin_mismatch(field):
    data = capsule()
    expected = pin(data)
    data[field] = "tampered"
    with pytest.raises(ValueError, match="input mismatch"):
        build_source_review_context(
            data, expected_input_id=expected, expected_sources=capsule()["sources"]
        )


def test_source_pin_mismatch():
    data = capsule()
    sources = deepcopy(data["sources"])
    data["sources"]["synthetic-dexory"]["title"] = "replacement"
    with pytest.raises(ValueError, match="source mismatch"):
        build_source_review_context(
            data, expected_input_id=pin(data), expected_sources=sources
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("claim", "unsupported paraphrase"),
        ("excerpt", "not in original text"),
        ("source_id", "missing-source"),
        ("candidate_id", "another-company"),
        ("confidence", "high"),
        ("criterion_ids", ["scoring-criterion"]),
    ],
)
def test_quote_and_attribution_closure(field, value):
    data = capsule()
    data["evidence"]["synthetic-quote"][field] = value
    with pytest.raises(ValueError):
        context(data)


@pytest.mark.parametrize("field", ["expected_context_id", "expected_input_id"])
def test_controller_identity_mismatch_before_calls(field):
    ctx = context()
    llm = FakeLLM([])
    pins = dict(
        expected_context_id=ctx.context_id, expected_input_id=ctx.snapshot()["input_id"]
    )
    pins[field] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="controller identity"):
        run_source_review_report(
            ctx, generate=ReportGeneratorV3(llm), judge=SemanticJudgeV3(llm), **pins
        )
    assert not llm.calls


@pytest.mark.parametrize(
    "text,code",
    [
        ("점수: 80", "SOURCE_REVIEW_ASSESSMENT_FORBIDDEN"),
        ("RECOMMEND_PRIORITY", "SOURCE_REVIEW_ASSESSMENT_FORBIDDEN"),
        ("투자를 추천한다", "SOURCE_REVIEW_ASSESSMENT_FORBIDDEN"),
        ("매출은 999달러다", "SOURCE_REVIEW_UNSUPPORTED_NUMBER"),
        ("[@evidence:invented]", "EVIDENCE_INVALID"),
    ],
)
def test_machine_guards(text, code):
    ctx = context()
    body = content()
    body["market"] = text
    result = validate_report_v3(draft(ctx, body), ctx)
    assert not result.valid
    assert code in {e.code for e in result.errors}


def test_unknown_disclosure_and_reference_closure():
    ctx = context()
    report = draft(ctx)
    assert validate_report_v3(report, ctx).valid
    for changed in (
        report.markdown.replace(outcome_block(ctx.snapshot()), ""),
        report.markdown.replace("fixture://dexory/announcement", "fixture://wrong"),
    ):
        assert not validate_report_v3(
            report.model_copy(update={"markdown": changed}), ctx
        ).valid


@pytest.mark.parametrize("verdict", ["pass", "fail", "revise"])
def test_original_judge_bounded_lifecycle(verdict):
    ctx = context()
    base = draft(ctx)
    generator = FakeLLM([content()] * 3)
    judge = FakeLLM(
        [
            judgement(ctx, base.model_copy(update={"revision": i}), verdict)
            for i in range(3)
        ]
    )
    result = run(ctx, generator, judge)
    assert not result.final_allowed
    if verdict == "fail":
        assert result.status == "failed" and result.error_code == "REPORT_REJECTED"
        assert len(generator.calls) == len(judge.calls) == 1
    elif verdict == "revise":
        assert result.status == "completed" and result.warning and result.revisions == 2
        assert result.error_code == "BUDGET_EXHAUSTED"
        assert len(generator.calls) == len(judge.calls) == 3
        assert result.judgement.judged_artifact_hash == artifact_hash(result.draft)
    else:
        assert result.status == "completed" and not result.warning
        assert result.validation.valid
        assert result.pdf_validation is None


def test_unsupported_non_numeric_claim_is_not_automatically_admitted():
    ctx = context()
    body = content()
    body["market"] = "독립 검증으로 시장 독점이 확인되었다. [@evidence:synthetic-quote]"
    report = draft(ctx, body)
    # Structure is not semantic admission: the original Judge rejects this claim.
    judge = FakeLLM([judgement(ctx, report, "fail")])
    result = run(ctx, FakeLLM([body]), judge)
    assert result.status == "failed" and result.error_code == "REPORT_REJECTED"


def test_structural_then_semantic_revision_share_original_budget():
    ctx = context()
    invalid = content()
    invalid["summary"] += " [@evidence:fabricated]"
    base = draft(ctx)
    generator = FakeLLM([invalid, content(), content()])
    judge = FakeLLM(
        [
            judgement(ctx, base.model_copy(update={"revision": 1}), "revise"),
            judgement(ctx, base.model_copy(update={"revision": 2}), "pass"),
        ]
    )
    result = run(ctx, generator, judge)
    assert result.status == "completed" and not result.warning and result.revisions == 2
    assert len(generator.calls) == 3 and len(judge.calls) == 2
    assert result.validation.valid and not result.final_allowed


def test_source_review_prompt_binds_machine_output_identity_and_citations():
    ctx = context()
    llm = FakeLLM([content()])
    report = ReportGeneratorV3(llm)(ctx, ())
    request = json.loads(llm.calls[0].user)
    assert request["required_output_schema_version"] == ctx.snapshot()["schema_version"]
    assert request["required_citation_tokens"] == ["[@evidence:synthetic-quote]"]
    judge = FakeLLM([judgement(ctx, report)])
    SemanticJudgeV3(judge)(report, ctx)
    request = json.loads(judge.calls[0].user)
    assert request["required_output_schema_version"] == ctx.snapshot()["schema_version"]
