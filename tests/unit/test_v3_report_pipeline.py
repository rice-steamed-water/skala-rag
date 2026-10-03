"""Synthetic v3 handoff + structured model stubs; no factuality/live claim."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from tests.integration.test_v3_candidates import scenario

from skala_rag.contracts import ReportJudgement
from skala_rag.contracts.evaluation import EvaluationSnapshot
from skala_rag.reporting.v3_context import build_report_context_v3
from skala_rag.reporting.v3_pipeline import (
    ReportContentV3,
    ReportGeneratorV3,
    SemanticJudgeV3,
    run_report_v3,
    validate_report_v3,
)
from skala_rag.reporting.validator import artifact_hash


def context(rating=5, statuses=("eligible",)):
    snapshots = {}

    def capture(raw):
        snapshots[raw["candidate_id"]] = deepcopy(raw)

    result, _ = scenario(statuses=statuses, ratings=(rating,), freeze_mutation=capture)
    snapshots = {
        cid: EvaluationSnapshot.model_validate(s, context={"execution_mode": "fixture"})
        for cid, s in snapshots.items()
        if cid in result.scores
    }
    if snapshots:
        snap = next(iter(snapshots.values()))
        as_of, corpus = snap.as_of, snap.corpus_version
    else:
        as_of, corpus = date(2026, 9, 1), "synthetic"
    return build_report_context_v3(
        result, snapshots, as_of=as_of, corpus_version=corpus, execution_mode="fixture"
    )


class Stub:
    def __init__(self, verdict="pass"):
        self.calls = []
        self.verdict = verdict

    def generate(self, *, system, user, output_schema):
        self.calls.append((system, json.loads(user)))
        payload = json.loads(user)
        if output_schema is ReportContentV3:
            evidence = next(iter(payload["context"]["evidence"]), None)
            claim = (
                f"가상 관측 [@evidence:{evidence}]"
                if evidence
                else "인용 가능한 근거 없음"
            )
            return ReportContentV3(
                schema_version=payload["context"]["schema_version"],
                summary=claim,
                company_team=claim,
                technology_market=claim,
                assessment_risks=claim,
                limitations=["가상 데이터; 실측 아님"],
            )
        return ReportJudgement(
            schema_version=payload["context"]["schema_version"],
            verdict=self.verdict,
            context_id=payload["context_id"],
            findings=[],
            revision_instructions=["unsupported fact"]
            if self.verdict == "revise"
            else [],
            judged_artifact_hash=payload["artifact_hash"],
        )


@pytest.mark.parametrize("rating", [5, 1])
def test_v3_handoff_to_generator_judge_preserves_fixed_context(rating):
    ctx = context(rating)
    generator_llm, judge_llm = Stub(), Stub()
    result = run_report_v3(
        ctx, generate=ReportGeneratorV3(generator_llm), judge=SemanticJudgeV3(judge_llm)
    )
    assert result.status == "completed" and not result.warning
    assert not result.final_allowed
    assert result.validation.valid
    assert (
        generator_llm.calls[0][1]["context"]
        == judge_llm.calls[0][1]["context"]
        == ctx.snapshot()
    )
    assert ctx.snapshot()["mode"] == (
        "single_candidate" if rating == 5 else "no_recommendation"
    )
    assert "normalized_score" in result.draft.markdown
    assert "not_applicable_weight" in result.draft.markdown


def test_shared_revision_exhaustion_warning_keeps_last_artifact():
    ctx = context()
    generator, judge = Stub(), Stub("revise")
    result = run_report_v3(
        ctx, generate=ReportGeneratorV3(generator), judge=SemanticJudgeV3(judge)
    )
    assert result.status == "completed" and result.warning and result.revisions == 2
    assert len(generator.calls) == len(judge.calls) == 3
    assert result.judgement.judged_artifact_hash == artifact_hash(result.draft)
    assert not result.final_allowed


def test_structural_and_semantic_revisions_share_budget():
    ctx = context()
    generate = ReportGeneratorV3(Stub())
    calls = []

    def broken_first(ctx, feedback):
        draft = generate(ctx, feedback)
        calls.append(feedback)
        return (
            draft.model_copy(
                update={
                    "markdown": draft.markdown.replace(
                        "normalized_score", "wrong_score"
                    )
                }
            )
            if len(calls) == 1
            else draft
        )

    judge_llm = Stub("revise")
    result = run_report_v3(ctx, generate=broken_first, judge=SemanticJudgeV3(judge_llm))
    assert result.warning and result.revisions == 2
    assert len(calls) == 3 and len(judge_llm.calls) == 2


@pytest.mark.parametrize(
    "change", ["citation", "score", "selection", "reference", "context"]
)
def test_unsupported_evidence_or_mutated_upstream_rejected(change):
    ctx = context()
    draft = ReportGeneratorV3(Stub())(ctx, [])
    changes = {
        "citation": {
            "markdown": draft.markdown.replace("[@evidence:", "[@evidence:invented-")
        },
        "score": {"markdown": draft.markdown.replace("normalized_score", "fixed100")},
        "selection": {"markdown": draft.markdown.replace("선택 결과:", "선택 변경:")},
        "reference": {"reference_source_ids": []},
        "context": {"context_id": "other"},
    }
    assert not validate_report_v3(draft.model_copy(update=changes[change]), ctx).valid


def test_judge_fail_and_stale_result_are_fatal():
    ctx = context()
    gen = ReportGeneratorV3(Stub())
    failed = run_report_v3(ctx, generate=gen, judge=SemanticJudgeV3(Stub("fail")))
    assert failed.status == "failed" and failed.error_code == "REPORT_REJECTED"

    def stale(draft, ctx):
        return ReportJudgement(
            schema_version=ctx.snapshot()["schema_version"],
            verdict="pass",
            context_id=ctx.context_id,
            findings=[],
            revision_instructions=[],
            judged_artifact_hash="old",
        )

    assert run_report_v3(ctx, generate=gen, judge=stale).status == "failed"


def test_empty_candidate_context_does_not_invent_score():
    ctx = context(statuses=())
    result = run_report_v3(
        ctx, generate=ReportGeneratorV3(Stub()), judge=SemanticJudgeV3(Stub())
    )
    assert result.status == "completed"
    assert "성공 평가 후보 없음" in result.draft.markdown
    assert ctx.snapshot()["scores"] == {}


def test_context_mutation_detected_before_generation():
    ctx = context()
    result = run_report_v3(
        replace(ctx, payload="{}"),
        generate=lambda *_: pytest.fail("must not generate"),
        judge=Stub(),
    )
    assert result.status == "failed" and result.draft is None


def test_real_adapter_mock_http_uses_shared_runtime_ledger():
    import httpx
    from tests.unit.test_openai_attempt import body, build

    from skala_rag.reporting.v3_runtime import build_report_nodes_v3
    from skala_rag.tools.runtime import BudgetLedger

    stub = Stub()

    def handler(request):
        payload = json.loads(request.content)
        schema = payload["text"]["format"]["name"]
        output = stub.generate(
            system=payload["input"][0]["content"],
            user=payload["input"][1]["content"],
            output_schema=ReportContentV3
            if schema == "ReportContentV3"
            else ReportJudgement,
        )
        return httpx.Response(200, json=body(output.model_dump_json()))

    base, attempt, seen = build(
        handler, max_input_tokens=200000, max_output_tokens=2000
    )
    runtime = base.runtime
    limits = runtime.ledger.limits.model_copy(
        update={
            "max_calls": 2,
            "tool_max_calls": {base.call.tool_name: 2},
            "max_output_tokens": 4000,
        }
    )
    runtime.ledger = BudgetLedger(limits)
    budget = base.budget.model_copy(update={"max_calls": 2})
    generator, judge = build_report_nodes_v3(
        runtime=runtime,
        generator_call=base.call.model_copy(
            update={"node": "report_generate", "run_id": "run"}
        ),
        judge_call=base.call.model_copy(
            update={"node": "report_judge", "call_id": "judge-call", "run_id": "run"}
        ),
        budget=budget,
        readiness=base.readiness,
        generator_transport=base.transport,
        judge_transport=base.transport,
        allowance_for=base.allowance_for,
    )
    result = run_report_v3(context(), generate=generator, judge=judge)
    assert result.status == "completed" and not result.warning
    assert len(seen) == 2
    assert generator.llm.runtime is judge.llm.runtime
    assert len(base.transport.llm_calls) == 2
    assert all(
        record.model == "gpt-4.1-mini-2025-04-14" for record in base.transport.llm_calls
    )
    # Shared physical request budget is exhausted: no additional HTTP attempt.
    assert run_report_v3(context(), generate=generator, judge=judge).status == "failed"
    assert len(seen) == 2


def test_pdf_layout_consumes_same_revision_budget():
    from skala_rag.contracts import ValidationErrorDetail, ValidationResult

    ctx = context()
    revisions = []

    def layout(draft, context, structural, judged):
        revisions.append(draft.revision)
        return ValidationResult(
            schema_version=draft.schema_version,
            valid=False,
            context_id=context.context_id,
            artifact_hash=artifact_hash(draft),
            checks={"action": "revise"},
            errors=[
                ValidationErrorDetail(
                    schema_version=draft.schema_version,
                    code="PDF_SUMMARY",
                    location="pdf",
                    message="synthetic layout error",
                )
            ],
        )

    result = run_report_v3(
        ctx,
        generate=ReportGeneratorV3(Stub()),
        judge=SemanticJudgeV3(Stub()),
        check_pdf=layout,
    )
    assert revisions == [0, 1, 2]
    assert result.warning and result.status == "completed"
    assert result.pdf_validation is not None and not result.pdf_validation.valid
    assert not result.final_allowed


def test_pdf_error_is_fatal_without_retry():
    def broken(*_):
        raise RuntimeError("SECRET raw renderer body")

    result = run_report_v3(
        context(),
        generate=ReportGeneratorV3(Stub()),
        judge=SemanticJudgeV3(Stub()),
        check_pdf=broken,
    )
    assert result.status == "failed" and result.error_code == "TOOL_FAILED"
    assert result.revisions == 0


def test_actual_fixture_pdf_accepts_v3_context_and_current_proofs(tmp_path):
    from skala_rag.reporting.pdf import (
        PDFLayoutValidator,
        PDFRenderer,
        load_pdf_profile,
    )

    ctx = context()

    def layout(draft, context, structural, judged):
        profile = load_pdf_profile(Path("configs/pdf.layout.v1.json"))
        renderer = PDFRenderer(
            profile=profile,
            output_dir=tmp_path,
            proof=lambda _: (structural, judged),
            execution_mode="fixture",
        )
        render = renderer(draft, profile.version)
        assert render.artifact_path is not None, render.errors
        return PDFLayoutValidator()(draft, context, render)

    result = run_report_v3(
        ctx,
        generate=ReportGeneratorV3(Stub()),
        judge=SemanticJudgeV3(Stub()),
        check_pdf=layout,
    )
    assert result.status == "completed" and not result.warning
    assert result.pdf_validation.valid
    assert not result.final_allowed


@pytest.mark.parametrize(
    "change", ["generation", "corpus", "selector", "decision", "source"]
)
def test_context_rejects_upstream_tampering(change):
    captured = {}
    result, _ = scenario(
        statuses=("eligible",),
        ratings=(5,),
        freeze_mutation=lambda raw: captured.update(
            {raw["candidate_id"]: deepcopy(raw)}
        ),
    )
    snap = next(iter(captured.values()))
    as_of, corpus = date.fromisoformat(snap["as_of"]), snap["corpus_version"]
    if change == "generation":
        snap["evidence_revision"] += 1
    if change == "corpus":
        snap["corpus_version"] = "other"
    if change == "selector":
        result = replace(result, selection=replace(result.selection, run_id="other"))
    if change == "decision":
        result.decisions["company-0"] = result.decisions["company-0"].model_copy(
            update={"score_summary_id": "other"}
        )
    if change == "source":
        snap["sources"] = {}
    with pytest.raises(ValueError):
        build_report_context_v3(
            result,
            {"company-0": snap},
            as_of=as_of,
            corpus_version=corpus,
            execution_mode="fixture",
        )


def test_unsupported_numeric_claim_reaches_semantic_revision_warning():
    from skala_rag.contracts.reports import ReportFinding

    ctx = context()
    base = ReportGeneratorV3(Stub())

    def generated(ctx, feedback):
        draft = base(ctx, feedback)
        return draft.model_copy(
            update={"markdown": draft.markdown.replace("가상 관측", "매출 999억 확정")}
        )

    def judge(draft, ctx):
        return ReportJudgement(
            schema_version=draft.schema_version,
            verdict="revise",
            context_id=ctx.context_id,
            judged_artifact_hash=artifact_hash(draft),
            findings=[
                ReportFinding(
                    schema_version=draft.schema_version,
                    severity="error",
                    claim_location="SUMMARY",
                    evidence_ids=[],
                    reason="unsupported numeric claim",
                )
            ],
            revision_instructions=["remove unsupported numeric claim"],
        )

    result = run_report_v3(ctx, generate=generated, judge=judge)
    assert result.warning and result.revisions == 2 and not result.final_allowed
    assert result.judgement.findings[0].reason == "unsupported numeric claim"
