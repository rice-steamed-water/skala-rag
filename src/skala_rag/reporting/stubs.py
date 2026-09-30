"""Semantic Judge·PDF render·layout stub — #28, T16.

주입된 결과만 순서대로 돌려준다. 실제 Judge LLM·PDF renderer·페이지 측정(M3)이
아니다. 모든 결과에 stub 표시를 남기며, stub 통과는 의미 검증이나 PDF 검증의
증거가 아니다. 준비한 결과보다 많이 호출하면 ``FakeExhausted``.
"""

from collections import deque
from collections.abc import Iterable
from typing import Literal

from skala_rag.contracts import (
    ReportContext,
    ReportDraft,
    ReportFinding,
    ReportJudgement,
    ValidationErrorDetail,
    ValidationResult,
)
from skala_rag.contracts.tools import RenderResult
from skala_rag.fakes import FakeExhausted
from skala_rag.reporting.validator import artifact_hash

Verdict = Literal["pass", "revise", "fail"]
STUB_JUDGE_NOTE = "stub judge: 주입된 verdict이며 실모델 의미 검증이 아니다"
STUB_RENDER_PATH = "stub://pdf-not-rendered"
STUB_LAYOUT_CODE = "LAYOUT_STUB"


class _Queue:
    def __init__(self, outputs: Iterable) -> None:
        self._outputs = deque(outputs)
        self.calls: list[tuple] = []

    def _next(self, *args):
        self.calls.append(args)
        if not self._outputs:
            raise FakeExhausted(f"{type(self).__name__} called {len(self.calls)} times")
        return self._outputs.popleft()


class StubJudge(_Queue):
    """``JudgeReport``. 주입된 verdict를 같은 draft·context에 대해 반환한다."""

    def __init__(self, verdicts: Iterable[Verdict]) -> None:
        super().__init__(verdicts)

    def __call__(self, draft: ReportDraft, context: ReportContext) -> ReportJudgement:
        verdict = self._next(draft, context)
        return ReportJudgement(
            schema_version=draft.schema_version,
            verdict=verdict,
            context_id=context.context_id,
            findings=[
                ReportFinding(
                    schema_version=draft.schema_version,
                    severity="stub",
                    claim_location="report",
                    evidence_ids=[],
                    reason=STUB_JUDGE_NOTE,
                )
            ],
            revision_instructions=(
                ["stub judge: 주입된 revise 요청"] if verdict == "revise" else []
            ),
            judged_artifact_hash=artifact_hash(draft),
        )


class StubRenderer(_Queue):
    """``RenderPdf``. PDF를 만들지 않는다. ``True``면 stub artifact, 아니면 실패."""

    def __init__(self, outcomes: Iterable[bool]) -> None:
        super().__init__(outcomes)

    def __call__(self, draft: ReportDraft, template: str) -> RenderResult:
        ok = self._next(draft, template)
        measurements = {"stub": True, "pdf_verified": False}
        if ok:
            return RenderResult(
                schema_version=draft.schema_version,
                artifact_path=STUB_RENDER_PATH,
                page_count=0,  # 측정값 아님
                layout_measurements=measurements,
                errors=[],
            )
        return RenderResult(
            schema_version=draft.schema_version,
            layout_measurements=measurements,
            errors=[
                ValidationErrorDetail(
                    schema_version=draft.schema_version,
                    code="RENDER_STUB_FAILED",
                    location="pdf",
                    message="stub renderer: 주입된 렌더링 실패",
                )
            ],
        )


class StubLayout(_Queue):
    """Layout Validator stub. 주입된 통과 여부만 반환하고 페이지를 측정하지 않는다."""

    def __init__(self, outcomes: Iterable[bool]) -> None:
        super().__init__(outcomes)

    def __call__(
        self, draft: ReportDraft, context: ReportContext, render: RenderResult
    ) -> ValidationResult:
        ok = self._next(draft, context, render)
        return ValidationResult(
            schema_version=draft.schema_version,
            valid=ok,
            context_id=context.context_id,
            checks={
                "stub": True,
                "pdf_verified": False,
                "action": "pass" if ok else "revise",
            },
            errors=[]
            if ok
            else [
                ValidationErrorDetail(
                    schema_version=draft.schema_version,
                    code=STUB_LAYOUT_CODE,
                    location="pdf",
                    message="stub layout: 주입된 layout 실패",
                )
            ],
            artifact_hash=artifact_hash(draft),
        )
