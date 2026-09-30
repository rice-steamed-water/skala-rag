"""#28 / T16: 보고서 단계 Graph와 공유 수정 예산.

실제 설치 LangGraph로 context → generate → validate → judge → render → layout을
실행한다. Generator는 fixture 템플릿, Judge·renderer·layout은 주입 stub이다.
실모델 보고서 품질이나 PDF 페이지 준수의 증거가 아니다.
"""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.unit.test_report_context import POLICY, _input, world  # noqa: F401
from tests.unit.test_report_generator import make_recommend

from skala_rag.contracts import ReportDraft
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import LLMError
from skala_rag.contracts.state import create_initial_state
from skala_rag.graph.report import ReportNodes, build_report_graph
from skala_rag.reporting.format import HEADINGS
from skala_rag.reporting.generator import generate_fixture_report
from skala_rag.reporting.stubs import (
    STUB_JUDGE_NOTE,
    STUB_LAYOUT_CODE,
    StubJudge,
    StubLayout,
    StubRenderer,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"
NOW = datetime(2026, 9, 30, tzinfo=UTC)
MODES = ["no_recommendation", "single_candidate"]


def _state(w, **overrides):
    run_input = json.loads((FIXTURES / "contracts.json").read_text())["RunInput"]
    run_input["policy_version"] = POLICY.policy_version
    state = create_initial_state(run_input)
    for key, value in w["state"].items():
        if key != "run_input":
            state[key] = copy.deepcopy(value)
    state["report_input"] = _input(w).model_dump(mode="json")
    state.update(overrides)
    return state


class Scripted:
    """fixture Generator 출력에 단계별 변형을 적용하고 받은 feedback을 기록한다."""

    def __init__(self, *edits):
        self.edits = list(edits)
        self.feedback: list[list[str]] = []

    def __call__(self, context, feedback):
        self.feedback.append(list(feedback))
        draft = generate_fixture_report(context, feedback)
        edit = self.edits.pop(0) if self.edits else None
        return edit(draft) if edit else draft


def drop_heading(draft: ReportDraft) -> ReportDraft:
    """SUMMARY 다음 첫 번째 목차 heading을 지운다 → SV03 revise."""
    first = draft.markdown.split("\n## ")[1].split("\n")[0]
    assert first in HEADINGS["single_candidate"] + HEADINGS["no_recommendation"]
    return draft.model_copy(
        update={"markdown": draft.markdown.replace(f"## {first}\n", "", 1)}
    )


def run(
    w,
    *,
    generate=None,
    judge=("pass",),
    render=(True,),
    layout=(True,),
    policy=POLICY,
    state=None,
):
    nodes = ReportNodes(
        generate=generate or Scripted(),
        judge=StubJudge(judge),
        render=StubRenderer(render),
        layout=StubLayout(layout),
    )
    graph = build_report_graph(
        nodes,
        policy,
        run_id=w["snap"].run_id,
        schema_version="synthetic-common-1",
        clock=lambda: NOW,
        template="fixture-template",
    ).compile()
    result = graph.invoke(state or _state(w), {"recursion_limit": 50})
    return result, nodes


@pytest.fixture(params=MODES)
def w(request, world):  # noqa: F811
    return world if request.param == "no_recommendation" else make_recommend(world)


def _codes(result):
    return [e["error_code"] for e in result["errors"]]


def test_pass_path_completes_with_stub_marks(w):
    result, nodes = run(w)
    assert result["workflow_status"] == "completed"
    draft = ReportDraft.model_validate(result["report_draft"])
    assert result["report"] == draft.markdown and draft.revision == 0
    assert result["report_revision_count"] == 0
    assert result["report_validation"]["valid"]
    assert [f["reason"] for f in result["report_judgement"]["findings"]] == [
        STUB_JUDGE_NOTE
    ]
    pdf = result["pdf_validation"]
    assert pdf["checks"]["stub"] is True and pdf["checks"]["pdf_verified"] is False
    assert len(nodes.judge.calls) == len(nodes.render.calls) == 1
    assert result["errors"] == []


def test_structural_revise_regenerates_with_feedback(w):
    gen = Scripted(drop_heading)
    result, _ = run(w, generate=gen)
    assert result["workflow_status"] == "completed"
    assert result["report_revision_count"] == 1
    assert result["report_draft"]["revision"] == 1
    assert gen.feedback[0] == []
    assert any(f.startswith("SV03") for f in gen.feedback[1])


def test_structure_judge_layout_share_one_budget(w):
    # 구조 revise(1) → Judge revise(2) → layout 실패 → 한도 2 소진
    gen = Scripted(drop_heading)
    result, nodes = run(w, generate=gen, judge=("revise", "pass"), layout=(False,))
    assert result["workflow_status"] == "failed"
    assert _codes(result) == [ErrorCode.BUDGET_EXHAUSTED]
    assert len(gen.feedback) == 3 and result["report_revision_count"] == 2
    assert any(f.startswith("stub judge") for f in gen.feedback[2])
    # 초안·마지막 검증 결과 보존, 최종본 없음
    assert result["report"] is None
    assert result["report_draft"]["revision"] == 2
    assert result["pdf_validation"]["errors"][0]["code"] == STUB_LAYOUT_CODE
    assert result["pdf_validation"]["checks"]["pdf_verified"] is False


def test_layout_failure_feeds_back_and_can_recover(w):
    gen = Scripted()
    result, _ = run(
        w,
        generate=gen,
        judge=("pass", "pass"),
        render=(True, True),
        layout=(False, True),
    )
    assert result["workflow_status"] == "completed"
    assert result["report_revision_count"] == 1
    assert any(f.startswith(STUB_LAYOUT_CODE) for f in gen.feedback[1])


def test_judge_revise_only_until_budget(w):
    gen = Scripted()
    result, nodes = run(w, generate=gen, judge=("revise",) * 3)
    assert result["workflow_status"] == "failed"
    assert _codes(result) == [ErrorCode.BUDGET_EXHAUSTED]
    assert len(gen.feedback) == POLICY.budgets.max_report_revisions + 1
    assert nodes.render.calls == []
    assert result["report_judgement"]["verdict"] == "revise"


def test_judge_fail_is_immediately_failed(w):
    gen = Scripted()
    result, nodes = run(w, generate=gen, judge=("fail",))
    assert result["workflow_status"] == "failed"
    assert _codes(result) == [ErrorCode.REPORT_REJECTED]
    assert len(gen.feedback) == 1 and result["report_revision_count"] == 0
    assert nodes.render.calls == []
    assert result["report"] is None
    assert result["report_draft"] is not None
    assert result["report_judgement"]["verdict"] == "fail"


def test_zero_budget_fails_on_first_revise(w):
    policy = POLICY.model_copy(
        update={
            "budgets": POLICY.budgets.model_copy(update={"max_report_revisions": 0})
        }
    )
    gen = Scripted(drop_heading)
    result, _ = run(w, generate=gen, policy=policy)
    assert _codes(result) == [ErrorCode.BUDGET_EXHAUSTED]
    assert len(gen.feedback) == 1
    assert result["report_validation"]["valid"] is False


def test_render_failure_is_failed_without_revision(w):
    gen = Scripted()
    result, nodes = run(w, generate=gen, render=(False,))
    assert _codes(result) == [ErrorCode.TOOL_FAILED]
    assert len(gen.feedback) == 1 and nodes.layout.calls == []
    assert result["pdf_validation"]["checks"]["pdf_verified"] is False


def test_layout_claim_of_pdf_verification_is_ignored_for_stub(w):
    class Claiming(StubLayout):
        def __call__(self, draft, context, render):
            result = super().__call__(draft, context, render)
            checks = {**result.checks, "stub": False, "pdf_verified": True}
            return result.model_copy(update={"checks": checks})

    nodes_layout = Claiming([True])
    graph_nodes = ReportNodes(
        Scripted(), StubJudge(["pass"]), StubRenderer([True]), nodes_layout
    )
    graph = build_report_graph(
        graph_nodes,
        POLICY,
        run_id=w["snap"].run_id,
        schema_version="synthetic-common-1",
        clock=lambda: NOW,
        template="t",
    ).compile()
    result = graph.invoke(_state(w), {"recursion_limit": 50})
    assert result["pdf_validation"]["checks"]["stub"] is True
    assert result["pdf_validation"]["checks"]["pdf_verified"] is False


def test_context_error_fails_before_generation(w):
    state = _state(w)
    state["score_summaries"][w["cid"]]["observed_score"] = "12"
    gen = Scripted()
    result, _ = run(w, generate=gen, state=state)
    assert _codes(result) == [ErrorCode.UPSTREAM_INVALID]
    assert gen.feedback == [] and result["report_context"] is None


def test_generator_llm_error_preserves_code(w):
    def broken(context, feedback):
        raise LLMError(ErrorCode.LLM_TIMEOUT, "synthetic timeout")

    result, _ = run(w, generate=broken)
    assert _codes(result) == [ErrorCode.LLM_TIMEOUT]
    assert result["errors"][0]["retryable"] is True


def test_stale_judgement_is_rejected(w):
    class Stale(StubJudge):
        def __call__(self, draft, context):
            j = super().__call__(draft, context)
            return j.model_copy(update={"judged_artifact_hash": "sha256:other"})

    nodes = ReportNodes(Scripted(), Stale(["pass"]), StubRenderer([]), StubLayout([]))
    graph = build_report_graph(
        nodes,
        POLICY,
        run_id=w["snap"].run_id,
        schema_version="synthetic-common-1",
        clock=lambda: NOW,
        template="t",
    ).compile()
    result = graph.invoke(_state(w), {"recursion_limit": 50})
    assert _codes(result) == [ErrorCode.LLM_OUTPUT_INVALID]


def test_live_run_rejected_before_generation(w):
    state = _state(w)
    state["run_input"]["execution_mode"] = "live"
    gen = Scripted()
    result, _ = run(w, generate=gen, state=state)
    assert result["workflow_status"] == "failed" and gen.feedback == []


def test_already_failed_workflow_is_untouched(w):
    state = _state(w, workflow_status="failed", report_input=None)
    gen = Scripted()
    result, _ = run(w, generate=gen, state=state)
    assert result["errors"] == [] and gen.feedback == []
