"""Fixture report stage — #28, architecture §2·§5·§6, reporting.md §6, T16.

context → generate → structural validate → Semantic Judge → render → layout.
구조 revise·Judge revise·layout 실패가 ``max_report_revisions`` 하나를 함께 쓴다.
Judge fail·context/upstream 오류·렌더러 실패는 재수정 없이 workflow failed다.
실패해도 마지막 draft와 검증·판정 결과를 State에 남기고 ``report``는 비운다.
Generator·Judge·renderer·layout은 주입한다. 이 builder는 fixture 실행만 받는다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from langgraph.graph import END, START, StateGraph

from skala_rag.contracts import (
    RenderResult,
    ReportContext,
    ReportDraft,
    ReportInput,
    ReportJudgement,
    ValidationResult,
    WorkflowError,
)
from skala_rag.contracts.error_codes import ErrorCode, is_retryable
from skala_rag.contracts.inputs import RunInput
from skala_rag.contracts.interfaces import (
    GenerateReport,
    JudgeReport,
    LLMError,
    RenderPdf,
)
from skala_rag.contracts.state import InvestmentState
from skala_rag.reporting.context import ReportContextError, build_report_context
from skala_rag.reporting.validator import artifact_hash, is_current, validate_report
from skala_rag.scoring.catalog import ScoringPolicy

CheckLayout = Callable[[ReportDraft, ReportContext, RenderResult], ValidationResult]
FIXTURE = {"execution_mode": "fixture"}


@dataclass(frozen=True)
class ReportNodes:
    generate: GenerateReport
    judge: JudgeReport
    render: RenderPdf
    layout: CheckLayout


class _Fail(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _dump(model) -> dict:
    return model.model_dump(mode="json")


def report_feedback(state: InvestmentState) -> list[str]:
    """직전 draft의 구조 오류·Judge 수정 요청·layout 오류 → Generator feedback."""
    feedback = []
    for field in ("report_validation", "pdf_validation"):
        result = state.get(field)
        if result and not result["valid"]:
            feedback += [
                f"{e['code']} {e['location']}: {e['message']}" for e in result["errors"]
            ]
    judgement = state.get("report_judgement")
    if judgement and judgement["verdict"] == "revise":
        feedback += judgement["revision_instructions"] or [
            f["reason"] for f in judgement["findings"]
        ]
    return feedback


def build_report_graph(
    nodes: ReportNodes,
    policy: ScoringPolicy,
    *,
    run_id: str,
    schema_version: str,
    clock: Callable[[], datetime],
    template: str,
):
    """ReportInput이 있는 State에서 보고서 단계를 실행하는 StateGraph builder."""
    for name, value in (
        ("run_id", run_id),
        ("schema_version", schema_version),
        ("template", template),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Missing {name}")
    budget = policy.budgets.max_report_revisions

    def require(condition: bool, code: ErrorCode, message: str) -> None:
        if not condition:
            raise _Fail(code, message)

    def failure(state, node: str, code: ErrorCode, message: str) -> dict:
        revision = state.get("report_revision_count", 0)
        error = WorkflowError(
            schema_version=schema_version,
            error_id=f"report:{run_id}:{node}:{revision}",
            run_id=run_id,
            node=f"report_{node}",
            error_code=ErrorCode(code).value,
            message_redacted=message,
            retryable=is_retryable(code),
            attempt=revision + 1,
            timestamp=clock(),
        )
        return {"errors": [_dump(error)], "workflow_status": "failed"}

    def context_of(state) -> ReportContext:
        return ReportContext.model_validate(state["report_context"], context=FIXTURE)

    def draft_of(state) -> ReportDraft:
        return ReportDraft.model_validate(state["report_draft"])

    def node(name: str, body: Callable[[InvestmentState], dict]):
        def call(state):
            try:
                return body(state)
            except _Fail as exc:
                return failure(state, name, exc.code, exc.message)

        return call

    def context(state):
        try:
            run = RunInput.model_validate(state["run_input"])
        except (KeyError, ValueError):
            raise _Fail(ErrorCode.UPSTREAM_INVALID, "Invalid RunInput") from None
        require(
            run.execution_mode == "fixture",
            ErrorCode.UPSTREAM_INVALID,
            "Report graph is fixture-only",
        )
        require(
            run.policy_version == policy.policy_version,
            ErrorCode.UPSTREAM_INVALID,
            "Policy version mismatch",
        )
        require(
            bool(state.get("report_input")),
            ErrorCode.CONTEXT_INVALID,
            "ReportInput 없음",
        )
        try:
            report_input = ReportInput.model_validate(state["report_input"])
            require(
                report_input.run_id == run_id,
                ErrorCode.CONTEXT_INVALID,
                "다른 실행의 ReportInput",
            )
            ctx = build_report_context(
                report_input, state, policy=policy, execution_mode="fixture"
            )
        except ReportContextError as exc:
            raise _Fail(exc.code, "ReportContext 조립 실패") from None
        except ValueError:
            raise _Fail(ErrorCode.UPSTREAM_INVALID, "Invalid ReportInput") from None
        fixed = state.get("report_context")
        require(
            fixed is None or fixed["context_id"] == ctx.context_id,
            ErrorCode.CONTEXT_INVALID,
            "고정된 context와 다른 context",
        )
        return {"report_context": _dump(ctx)}

    def generate(state):
        ctx = context_of(state)
        try:
            draft = ReportDraft.model_validate(
                nodes.generate(ctx, report_feedback(state))
            )
        except LLMError as exc:
            raise _Fail(exc.error_code, exc.message_redacted) from None
        except ValueError:
            raise _Fail(ErrorCode.LLM_OUTPUT_INVALID, "Invalid ReportDraft") from None
        except Exception:
            raise _Fail(ErrorCode.LLM_FAILED, "Report generation failed") from None
        require(
            draft.context_id == ctx.context_id,
            ErrorCode.LLM_OUTPUT_INVALID,
            "Draft for another context",
        )
        draft = draft.model_copy(
            update={"revision": state.get("report_revision_count", 0)}
        )
        # 새 draft에는 이전 draft의 검증·판정 결과를 재사용하지 않는다(SV09).
        return {
            "report_draft": _dump(draft),
            "report_validation": None,
            "report_judgement": None,
            "pdf_validation": None,
        }

    def validate(state):
        result = validate_report(draft_of(state), context_of(state))
        delta = {"report_validation": _dump(result)}
        if result.checks["action"] == "fail":
            delta.update(
                failure(
                    state,
                    "validate",
                    ErrorCode.CONTEXT_INVALID,
                    "Structural Validator: context·upstream 오류",
                )
            )
        return delta

    def judge(state):
        draft, ctx = draft_of(state), context_of(state)
        validation = ValidationResult.model_validate(state["report_validation"])
        require(
            validation.valid and is_current(validation, draft, ctx),
            ErrorCode.UPSTREAM_INVALID,
            "Judge requires a current structural pass",
        )
        try:
            judgement = ReportJudgement.model_validate(nodes.judge(draft, ctx))
        except LLMError as exc:
            raise _Fail(exc.error_code, exc.message_redacted) from None
        except Exception:
            raise _Fail(
                ErrorCode.LLM_OUTPUT_INVALID, "Invalid ReportJudgement"
            ) from None
        require(
            judgement.context_id == ctx.context_id
            and judgement.judged_artifact_hash == artifact_hash(draft),
            ErrorCode.LLM_OUTPUT_INVALID,
            "Judgement for another draft or context",
        )
        delta = {"report_judgement": _dump(judgement)}
        if judgement.verdict == "fail":
            delta.update(
                failure(
                    state,
                    "judge",
                    ErrorCode.REPORT_REJECTED,
                    "Semantic Judge fail",
                )
            )
        return delta

    def render(state):
        draft, ctx = draft_of(state), context_of(state)
        judgement = ReportJudgement.model_validate(state["report_judgement"])
        require(
            judgement.verdict == "pass"
            and judgement.judged_artifact_hash == artifact_hash(draft),
            ErrorCode.UPSTREAM_INVALID,
            "Render requires a current Judge pass",
        )
        try:
            rendered = RenderResult.model_validate(nodes.render(draft, template))
        except Exception:
            raise _Fail(ErrorCode.TOOL_FAILED, "PDF render failed") from None
        stub = rendered.layout_measurements.get("stub") is True
        if rendered.artifact_path is None:
            result = ValidationResult(
                schema_version=schema_version,
                valid=False,
                context_id=ctx.context_id,
                checks={"stub": stub, "pdf_verified": False, "action": "fail"},
                errors=rendered.errors,
                artifact_hash=artifact_hash(draft),
            )
            return {
                "pdf_validation": _dump(result),
                **failure(state, "render", ErrorCode.TOOL_FAILED, "PDF render failed"),
            }
        try:
            layout = ValidationResult.model_validate(nodes.layout(draft, ctx, rendered))
        except Exception:
            raise _Fail(ErrorCode.TOOL_FAILED, "Layout check failed") from None
        require(
            is_current(layout, draft, ctx),
            ErrorCode.TOOL_FAILED,
            "Layout result for another draft or context",
        )
        stub = stub or layout.checks.get("stub") is True
        checks = {
            **layout.checks,
            "stub": stub,
            # stub renderer·layout 결과는 PDF 검증으로 표시하지 않는다.
            "pdf_verified": not stub and layout.checks.get("pdf_verified") is True,
            "action": "pass" if layout.valid else "revise",
        }
        return {"pdf_validation": _dump(layout.model_copy(update={"checks": checks}))}

    def retry(state):
        count = state.get("report_revision_count", 0)
        require(
            count < budget,
            ErrorCode.BUDGET_EXHAUSTED,
            "Report revision budget exhausted",
        )
        return {"report_revision_count": count + 1}

    def complete(state):
        draft, ctx = draft_of(state), context_of(state)
        for field in ("report_validation", "pdf_validation"):
            result = ValidationResult.model_validate(state[field])
            require(
                result.valid and is_current(result, draft, ctx),
                ErrorCode.UPSTREAM_INVALID,
                "Stale report validation",
            )
        return {"report": draft.markdown, "workflow_status": "completed"}

    def failed(state) -> bool:
        return state["workflow_status"] == "failed"

    def after_validate(state):
        if failed(state):
            return "end"
        return "judge" if state["report_validation"]["valid"] else "retry"

    def after_judge(state):
        if failed(state):
            return "end"
        return "render" if state["report_judgement"]["verdict"] == "pass" else "retry"

    def after_render(state):
        if failed(state):
            return "end"
        return "complete" if state["pdf_validation"]["valid"] else "retry"

    graph = StateGraph(InvestmentState)
    for name, body in (
        ("context", context),
        ("generate", generate),
        ("validate", validate),
        ("judge", judge),
        ("render", render),
        ("retry", retry),
        ("complete", complete),
    ):
        graph.add_node(name, node(name, body))

    def proceed(following: str):
        return lambda state: "end" if failed(state) else following

    graph.add_conditional_edges(
        START, proceed("context"), {"end": END, "context": "context"}
    )
    for name, following in (
        ("context", "generate"),
        ("generate", "validate"),
        ("retry", "generate"),
    ):
        graph.add_conditional_edges(
            name, proceed(following), {"end": END, following: following}
        )
    graph.add_conditional_edges(
        "validate", after_validate, {"end": END, "judge": "judge", "retry": "retry"}
    )
    graph.add_conditional_edges(
        "judge", after_judge, {"end": END, "render": "render", "retry": "retry"}
    )
    graph.add_conditional_edges(
        "render",
        after_render,
        {"end": END, "complete": "complete", "retry": "retry"},
    )
    graph.add_edge("complete", END)
    return graph
