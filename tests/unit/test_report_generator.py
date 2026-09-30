"""#28 / T16: fixture Report Generator와 Judge·render·layout stub.

가상 fixture로 draft 형식을 확인한다. 실모델 보고서·Judge 품질의 증거가 아니다.
"""

import copy

import pytest
from tests.unit.test_report_context import DIMS, POLICY, SV, _input, world  # noqa: F401

from skala_rag.contracts import Evaluation, EvaluationResult
from skala_rag.contracts.interfaces import GenerateReport, JudgeReport, RenderPdf
from skala_rag.fakes import FakeExhausted
from skala_rag.reporting.context import build_report_context, permitted_evidence_ids
from skala_rag.reporting.format import EMPTY_REFERENCE_PREFIX, FIXTURE_MARK
from skala_rag.reporting.generator import generate_fixture_report
from skala_rag.reporting.stubs import (
    STUB_JUDGE_NOTE,
    StubJudge,
    StubLayout,
    StubRenderer,
)
from skala_rag.reporting.validator import TOKEN, artifact_hash, validate_report
from skala_rag.scoring import build_investment_decision, build_score_summary


def make_recommend(world):  # noqa: F811
    """observed criterion을 모두 5점으로 바꿔 같은 snapshot의 RECOMMEND world."""
    w = dict(world)
    state = copy.deepcopy(world["state"])
    cid, snap = world["cid"], world["snap"]
    results = []
    for dim in DIMS:
        payload = state["evaluations"][f"{cid}:{snap.evaluation_round}:{dim}"]
        for criterion in payload["criteria"]:
            if criterion["status"] == "observed":
                criterion["rating"] = 5
        e = Evaluation.model_validate(payload)
        results.append(
            EvaluationResult(
                schema_version=SV,
                run_id=e.run_id,
                candidate_id=cid,
                dimension=dim,
                evaluation_round=e.evaluation_round,
                snapshot_id=e.snapshot_id,
                evidence_revision=e.evidence_revision,
                policy_version=e.policy_version,
                status="success",
                evaluation=e,
                errors=[],
            )
        )
    summary = build_score_summary(results, POLICY, schema_version=SV)
    decision = build_investment_decision(
        summary, POLICY, schema_version=SV, evidence_ids=[], rationale="가상 설명"
    )
    assert decision.label == "RECOMMEND"
    state["score_summaries"][cid] = summary.model_dump(mode="json")
    state["investment_decisions"][cid] = decision.model_dump(mode="json")
    outcomes = [
        o.model_copy(
            update={"status": "recommend", "decision_id": decision.decision_id}
        )
        if o.candidate_id == cid
        else o
        for o in world["outcomes"]
    ]
    w.update(state=state, summary=summary, decision=decision, outcomes=outcomes)
    return w


@pytest.fixture(params=["no_recommendation", "single_candidate"])
def any_world(request, world):  # noqa: F811
    return world if request.param == "no_recommendation" else make_recommend(world)


@pytest.fixture
def ctx(any_world):
    return build_report_context(_input(any_world), any_world["state"], policy=POLICY)


def test_protocols():
    assert isinstance(generate_fixture_report, GenerateReport)
    assert isinstance(StubJudge([]), JudgeReport)
    assert isinstance(StubRenderer([]), RenderPdf)


def test_generated_draft_passes_structural_validation(ctx):
    draft = generate_fixture_report(ctx, [])
    result = validate_report(draft, ctx)
    assert result.valid, result.errors
    assert draft.context_id == ctx.context_id and draft.revision == 0
    assert FIXTURE_MARK in draft.markdown.split("## 1.")[0]


def test_generated_draft_is_deterministic_and_ignores_feedback(ctx):
    a = generate_fixture_report(ctx, [])
    b = generate_fixture_report(ctx, ["SV03 headings: 누락"])
    assert a == b


def test_citations_resolve_to_context_sources(ctx):
    draft = generate_fixture_report(ctx, [])
    body = draft.markdown.split("## REFERENCE")[0]
    cited = set(TOKEN.findall(body))
    assert (
        set(draft.cited_evidence_ids) == cited <= set(ctx.input.permitted_evidence_ids)
    )
    assert set(draft.reference_source_ids) == {ctx.evidence[e].source_id for e in cited}


def test_single_candidate_names_first_recommend_not_best(world):  # noqa: F811
    w = make_recommend(world)
    ctx = build_report_context(_input(w), w["state"], policy=POLICY)
    draft = generate_fixture_report(ctx, [])
    summary = draft.markdown.split("## 1.")[0]
    assert w["cid"] in summary and "RECOMMEND" in summary
    assert "최우수" not in summary and draft.cited_evidence_ids


def test_zero_candidates_empty_reference_with_reason(world):  # noqa: F811
    ri = _input(world, outcomes=[], permitted_evidence_ids=[])
    assert permitted_evidence_ids([], world["state"]) == []
    ctx = build_report_context(ri, world["state"], policy=POLICY)
    draft = generate_fixture_report(ctx, [])
    assert validate_report(draft, ctx).valid
    assert draft.reference_source_ids == [] and draft.cited_evidence_ids == []
    assert f"{EMPTY_REFERENCE_PREFIX} " in draft.markdown
    assert "평가 후보 없음" in draft.markdown


@pytest.mark.parametrize("verdict", ["pass", "revise", "fail"])
def test_stub_judge_marks_injected_verdict(ctx, verdict):
    draft = generate_fixture_report(ctx, [])
    judge = StubJudge([verdict])
    judgement = judge(draft, ctx)
    assert judgement.verdict == verdict
    assert judgement.context_id == ctx.context_id
    assert judgement.judged_artifact_hash == artifact_hash(draft)
    assert [f.reason for f in judgement.findings] == [STUB_JUDGE_NOTE]
    assert bool(judgement.revision_instructions) == (verdict == "revise")
    with pytest.raises(FakeExhausted):
        judge(draft, ctx)


def test_render_and_layout_stub_never_claim_pdf_verified(ctx):
    draft = generate_fixture_report(ctx, [])
    renderer = StubRenderer([True, False])
    ok, bad = renderer(draft, "t"), renderer(draft, "t")
    assert ok.layout_measurements == {"stub": True, "pdf_verified": False}
    assert bad.artifact_path is None and bad.errors
    layout = StubLayout([True, False])
    passed, failed = layout(draft, ctx, ok), layout(draft, ctx, ok)
    assert passed.valid and passed.checks["pdf_verified"] is False
    assert not failed.valid and failed.checks["stub"] is True
