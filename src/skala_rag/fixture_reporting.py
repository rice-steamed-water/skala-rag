"""#29 fixture adapter for merged #94 pipeline and #95 actual PDF renderer."""

import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from skala_rag.contracts import ReportJudgement
from skala_rag.contracts.state import RunOutcome
from skala_rag.reporting.pdf import PDFLayoutValidator, PDFRenderer, load_pdf_profile
from skala_rag.reporting.v3_context import build_report_context_v3
from skala_rag.reporting.v3_pipeline import (
    ReportContentV3,
    ReportGeneratorV3,
    SemanticJudgeV3,
    run_report_v3,
    validate_report_v3,
)
from skala_rag.run_finalization import ReportCompletion


class FixtureReportLLM:
    """Deterministic offline responses, explicitly stub semantic proof."""

    def generate(self, *, system, user, output_schema):
        del system
        payload = json.loads(user)
        data = payload["context"]
        if output_schema is ReportContentV3:
            eid = next(iter(sorted(data["evidence"])), None)
            claim = (
                f"가상 관측: {data['evidence'][eid]['claim']} [@evidence:{eid}]"
                if eid
                else "인용 가능한 근거 없음."
            )
            return ReportContentV3(
                schema_version=data["schema_version"],
                summary="가상 후보 비교; 실제 투자 권고가 아니다.",
                company_team=claim,
                technology_market=claim,
                assessment_risks=(
                    "가상 정책의 점수·판정이며 실제 모델 품질은 검증하지 않았다."
                ),
                limitations=["fixture·stub이며 실제 기업·API 사실성 검증 아님"],
            )
        return ReportJudgement(
            schema_version=data["schema_version"],
            verdict="pass",
            context_id=payload["context_id"],
            judged_artifact_hash=payload["artifact_hash"],
            revision_instructions=[],
            findings=[
                dict(
                    schema_version=data["schema_version"],
                    severity="stub",
                    claim_location="fixture",
                    evidence_ids=[],
                    reason="synthetic semantic Judge",
                )
            ],
        )


def outcome_for(result):
    if result.selection.selected_candidate_id:
        return RunOutcome.RECOMMENDED
    if not result.outcomes:
        return RunOutcome.NO_CANDIDATES
    if all(o.status == "failed" for o in result.outcomes.values()):
        return RunOutcome.TECHNICAL_FAILURE
    if any(
        o.status in ("failed", "eligibility_unknown") for o in result.outcomes.values()
    ):
        return RunOutcome.INSUFFICIENT_EVIDENCE
    return RunOutcome.NO_RECOMMENDATION


class FixtureReportAdapter:
    def __init__(self, *, snapshots, run_input, destination, trace, pdf_profile):
        self.snapshots = snapshots
        self.run_input = run_input
        self.destination = Path(destination)
        self.trace = trace
        self.pdf_profile = pdf_profile
        self.context = self.pipeline = self.rendered = None

    def _traced(self, step, fn, run_id):
        def call(*args):
            started = datetime.now(timezone.utc).isoformat()
            timer = perf_counter()
            status, output_ids = "failed", []
            inputs = [
                getattr(a, "context_id") for a in args if hasattr(a, "context_id")
            ]
            try:
                result = fn(*args)
                status = "ok"
                if hasattr(result, "valid") and not result.valid:
                    status = (
                        "revise"
                        if result.checks.get("action") == "revise"
                        else "failed"
                    )
                if hasattr(result, "verdict"):
                    status = "ok" if result.verdict == "pass" else result.verdict
                output_ids = [
                    getattr(result, key)
                    for key in (
                        "report_id",
                        "context_id",
                        "artifact_hash",
                        "judged_artifact_hash",
                    )
                    if getattr(result, key, None)
                ]
                return result
            finally:
                self.trace.append(
                    dict(
                        run_id=run_id,
                        candidate_id=None,
                        step=step,
                        status=status,
                        started_at=started,
                        duration_seconds=perf_counter() - timer,
                        input_ids=inputs,
                        output_ids=output_ids,
                        execution_mode="fixture",
                    )
                )

        return call

    def __call__(self, result):
        self.context = self._traced(
            "report_context",
            lambda: build_report_context_v3(
                result,
                {cid: s for cid, s in self.snapshots.items() if cid in result.scores},
                as_of=self.run_input.as_of,
                corpus_version=self.run_input.corpus_version,
                execution_mode="fixture",
            ),
            result.run_id,
        )()
        self.trace[-1]["input_ids"] = [
            result.scores[cid].score_summary_id for cid in sorted(result.scores)
        ]
        llm = FixtureReportLLM()
        generate = self._traced(
            "report_generate", ReportGeneratorV3(llm), result.run_id
        )
        judge = self._traced("report_judge", SemanticJudgeV3(llm), result.run_id)
        validate = self._traced("report_validate", validate_report_v3, result.run_id)

        def check_pdf(draft, context, structural, judged):
            profile = load_pdf_profile(Path(self.pdf_profile))
            render = PDFRenderer(
                profile=profile,
                output_dir=self.destination,
                proof=lambda _: (structural, judged),
                execution_mode="fixture",
            )
            self.rendered = render(draft, profile.version)
            return PDFLayoutValidator()(draft, context, self.rendered)

        self.pipeline = run_report_v3(
            self.context,
            generate=generate,
            judge=judge,
            validate=validate,
            check_pdf=self._traced("report_pdf", check_pdf, result.run_id),
        )
        if self.pipeline.draft is None:
            raise ValueError("fixture report context/generation failed")
        return ReportCompletion(
            outcome=outcome_for(result),
            draft=self.pipeline.draft,
            structural=self.pipeline.validation,
            semantic=self.pipeline.judgement,
            revisions=self.pipeline.revisions,
            fatal=self.pipeline.status == "failed",
            pdf=self.pipeline.pdf_validation,
        )
