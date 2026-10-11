"""Frozen narrative composition and separate model-based semantic judgment."""

import hashlib
import json
from dataclasses import dataclass
from typing import Final, Literal, Self, TypedDict, assert_never

import httpx
from pydantic import ValidationInfo, model_validator

from skala_rag.agents.company_analysis import ContextualRoleAttempt
from skala_rag.contracts.common import Text
from skala_rag.contracts.company_report import (
    SHA256,
    AnalysisReply,
    CompanyReportConfigError,
    CompositionReply,
    EvidenceIDs,
    WriterContext,
    _NarrativeContract,
    _writer_context,
)
from skala_rag.graph.company_report import NarrativeReportLifecycle
from skala_rag.prompt.reporting import (
    NARRATIVE_COMPOSITION_SYSTEM,
    NARRATIVE_JUDGE_SYSTEM,
    build_narrative_payload,
)
from skala_rag.reporting.company_context import (
    CompanyWriterContext,
    _check_neutral_text,
)
from skala_rag.settings import LLMSettings

NARRATIVE_HEADINGS: Final = (
    "SUMMARY",
    "기술·제품",
    "시장·고객",
    "사업 모델",
    "경쟁·진입장벽",
    "팀·실행",
    "재무·투자유치·거래조건",
    "장점·위험·추가 확인 과제",
)
SemanticCode = Literal[
    "unsupported_claim",
    "wrong_entity",
    "wrong_metric",
    "wrong_unit",
    "wrong_currency",
    "wrong_date",
    "uncertainty",
    "recommendation",
    "score",
    "composition",
    "injection",
]


class SemanticIssue(_NarrativeContract):
    code: SemanticCode
    location: Text
    reason: Text
    evidence_ids: EvidenceIDs


class SemanticJudgeReply(_NarrativeContract):
    """A semantic decision, not evidence-ID membership masquerading as support."""

    context_id: Text
    artifact_hash: SHA256
    verdict: Literal["pass", "revise", "fail"]
    issues: list[SemanticIssue]

    @model_validator(mode="after")
    def check_context(self, info: ValidationInfo) -> Self:
        writer = _writer_context(info)
        if self.context_id != writer.context_id:
            raise CompanyReportConfigError("STALE_JUDGE_CONTEXT")
        if (self.verdict == "pass") != (not self.issues):
            raise CompanyReportConfigError("JUDGE_VERDICT_ISSUES_MISMATCH")
        for issue in self.issues:
            if not set(issue.evidence_ids) <= writer.evidence.keys():
                raise CompanyReportConfigError("INVALID_JUDGE_EVIDENCE")
            _check_neutral_text([issue.location, issue.reason], set())
        return self


@dataclass(frozen=True, slots=True)
class NarrativeModelSettings:
    llm_settings: LLMSettings
    max_output_tokens: int
    api_key: str | None = None
    http_transport: httpx.BaseTransport | None = None


@dataclass(frozen=True, slots=True)
class NarrativeCompositionResult:
    composition: CompositionReply
    judgment: SemanticJudgeReply
    artifact_hash: str


def composition_hash(draft: CompositionReply) -> str:
    """Bind semantic acceptance to every composition field, not just its prose."""
    return hashlib.sha256(
        json.dumps(
            draft.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _neutral_reply(context: CompanyWriterContext, prose: list[str]) -> None:
    """Keep model prose/feedback source-neutral before another model receives it."""
    tokens: set[str] = set()
    registry = context.citation_registry
    for receipt in registry["citations"].values():
        source = receipt["source"]
        tokens.update(
            token
            for key in (
                "source_id",
                "url",
                "local_path",
                "content_hash",
                "title",
                "publisher",
                "author",
            )
            if (token := source.get(key))
            and token.casefold() not in context.writer_context.target.name.casefold()
        )
        tokens.update(receipt["chunks"])
    # Original evidence IDs can contain private source IDs; they remain public.
    cleaned = []
    for text in prose:
        for eid in context.writer_context.evidence:
            text = text.replace(f"[@evidence:{eid}]", "")
        cleaned.append(text)
    _check_neutral_text(cleaned, tokens)


def _final_analysis(
    context: CompanyWriterContext, analysis: AnalysisReply
) -> AnalysisReply:
    writer = context.writer_context
    parsed = AnalysisReply.model_validate(analysis, context={"writer_context": writer})
    if writer.may_request_information or parsed.needs:
        raise CompanyReportConfigError("COMPOSITION_REQUIRES_FINAL_ANALYSIS")
    _neutral_reply(
        context,
        [
            text
            for answer in parsed.answers.values()
            for text in (
                *[finding.text for finding in answer.findings],
                *([answer.limitation] if answer.limitation else []),
                *([answer.verification_task] if answer.verification_task else []),
            )
        ],
    )
    return parsed


class _CompositionFeedback(TypedDict):
    citation_marker_template: str
    recognized_body_evidence_ids: list[str]
    declared_but_unused_evidence_ids: list[str]
    undeclared_body_evidence_ids: list[str]
    invalid_body_citation_count: int
    malformed_citation_count: int
    headings_valid: bool
    missing_verification_question_ids: list[str]


def _composition_feedback(
    draft: CompositionReply, analysis: AnalysisReply, writer: WriterContext
) -> _CompositionFeedback | None:
    """Exact structural differences, without echoing untrusted prose or metadata."""
    from skala_rag.reporting.validator import TOKEN

    body = draft.markdown_without_reference
    headings = tuple(
        line.removeprefix("## ") for line in body.splitlines() if line.startswith("## ")
    )
    required_tasks = {
        qid
        for qid, answer in analysis.answers.items()
        if answer.status in ("partial", "unknown")
    }
    cited = set(TOKEN.findall(body))
    declared = set(draft.cited_evidence_ids)
    missing_tasks = required_tasks - {
        task.question_id for task in draft.verification_tasks
    }
    malformed_count = TOKEN.sub("", body).count("[@evidence:")
    if (
        headings == NARRATIVE_HEADINGS
        and cited == declared
        and not malformed_count
        and not missing_tasks
    ):
        return None
    # Only admitted original IDs may be echoed; malformed/foreign text is counted.
    admitted = set(writer.evidence)
    return {
        "citation_marker_template": "[@evidence:ID]",
        "recognized_body_evidence_ids": sorted(cited & admitted),
        "declared_but_unused_evidence_ids": sorted(declared - cited),
        "undeclared_body_evidence_ids": sorted((cited - declared) & admitted),
        "invalid_body_citation_count": len(cited - admitted),
        "malformed_citation_count": malformed_count,
        "headings_valid": headings == NARRATIVE_HEADINGS,
        "missing_verification_question_ids": sorted(missing_tasks),
    }


class CompositionAttempt(ContextualRoleAttempt[CompositionReply]):
    """Configured composition role; dispatch through the existing lifecycle only."""

    def __init__(
        self,
        lifecycle: NarrativeReportLifecycle,
        analysis: AnalysisReply,
        *,
        settings: NarrativeModelSettings,
        feedback: tuple[SemanticCode, ...] = (),
        structural_feedback: _CompositionFeedback | None = None,
    ) -> None:
        context = lifecycle.context
        context.verify_binding()
        writer = context.writer_context
        analysis = _final_analysis(context, analysis)
        payload = json.loads(
            build_narrative_payload(
                writer, analysis, headings=NARRATIVE_HEADINGS, feedback=feedback
            )
        )
        if structural_feedback:
            payload["structural_feedback"] = structural_feedback
        super().__init__(
            writer_context=writer,
            output_schema=CompositionReply,
            system=NARRATIVE_COMPOSITION_SYSTEM,
            user=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            prompt_version="narrative-composition-1",
            llm_settings=settings.llm_settings,
            clock=lifecycle._runtime.clock,
            max_output_tokens=settings.max_output_tokens,
            allowance=lifecycle._allowance,
            api_key=settings.api_key,
            http_transport=settings.http_transport,
        )


class SemanticJudgeAttempt(ContextualRoleAttempt[SemanticJudgeReply]):
    """Separate semantic role receives actual evidence, analysis and exact draft."""

    def __init__(
        self,
        lifecycle: NarrativeReportLifecycle,
        analysis: AnalysisReply,
        composition: CompositionReply,
        *,
        settings: NarrativeModelSettings,
    ) -> None:
        context = lifecycle.context
        context.verify_binding()
        writer = context.writer_context
        analysis = _final_analysis(context, analysis)
        draft = CompositionReply.model_validate(
            composition, context={"writer_context": writer}
        )
        _neutral_reply(
            context,
            [
                draft.markdown_without_reference,
                *[item.text for item in draft.strengths + draft.weaknesses],
                *[item.task for item in draft.verification_tasks],
            ],
        )
        self.artifact_hash = composition_hash(draft)
        super().__init__(
            writer_context=writer,
            output_schema=SemanticJudgeReply,
            system=NARRATIVE_JUDGE_SYSTEM,
            user=build_narrative_payload(
                writer,
                analysis,
                headings=NARRATIVE_HEADINGS,
                composition=draft,
                artifact_hash=self.artifact_hash,
            ),
            prompt_version="narrative-judge-1",
            llm_settings=settings.llm_settings,
            clock=lifecycle._runtime.clock,
            max_output_tokens=settings.max_output_tokens,
            allowance=lifecycle._allowance,
            api_key=settings.api_key,
            http_transport=settings.http_transport,
        )


def compose_and_judge(
    lifecycle: NarrativeReportLifecycle,
    analysis: AnalysisReply,
    *,
    settings: NarrativeModelSettings,
    feedback: tuple[SemanticCode, ...] = (),
) -> NarrativeCompositionResult:
    """Compose/Judge with two shared repairs; never reopen factual acquisition."""
    frozen = lifecycle.context
    frozen.verify_binding()
    analysis_json = _final_analysis(frozen, analysis).model_dump_json()
    structural_feedback: _CompositionFeedback | None = None
    while True:
        if lifecycle.context != frozen:
            raise CompanyReportConfigError("FROZEN_NARRATIVE_CONTEXT_CHANGED")
        parsed = AnalysisReply.model_validate_json(
            analysis_json, context={"writer_context": frozen.writer_context}
        )
        composed = lifecycle.execute_model(
            transport=CompositionAttempt(
                lifecycle,
                parsed,
                settings=settings,
                feedback=feedback,
                structural_feedback=structural_feedback,
            )
        )
        if composed.data is None:
            if composed.errors[-1].error_code == "TOOL_RESPONSE_INVALID":
                lifecycle.consume_repair(
                    context_id=frozen.context_id, kind="composition"
                )
                feedback = ("composition",)
                structural_feedback = None
                continue
            raise CompanyReportConfigError("COMPOSITION_TRANSPORT_FAILED")
        draft = composed.data
        structural_feedback = _composition_feedback(
            draft, parsed, frozen.writer_context
        )
        if structural_feedback:
            lifecycle.consume_repair(context_id=frozen.context_id, kind="composition")
            feedback = ("composition",)
            continue
        judge = SemanticJudgeAttempt(lifecycle, parsed, draft, settings=settings)
        judged = lifecycle.execute_model(transport=judge)
        if judged.data is None:
            raise CompanyReportConfigError("JUDGE_TRANSPORT_FAILED")
        judgment = judged.data
        if lifecycle.context != frozen or judgment.artifact_hash != judge.artifact_hash:
            raise CompanyReportConfigError("JUDGE_ARTIFACT_BINDING_MISMATCH")
        match judgment.verdict:
            case "pass":
                return NarrativeCompositionResult(draft, judgment, judge.artifact_hash)
            case "fail":
                raise CompanyReportConfigError("NARRATIVE_SEMANTIC_FAILED")
            case "revise":
                lifecycle.consume_repair(context_id=frozen.context_id, kind="semantic")
                # Feedback is machine codes, never private metadata or instructions.
                feedback = tuple(issue.code for issue in judgment.issues)
            case unreachable:
                assert_never(unreachable)
