"""동결된 문맥으로 서술형 보고서를 작성하고 별도 호출로 심사(Judge)한다.

두 역할은 일부러 분리되어 있다.

- Composition: 분석 결과(``AnalysisReply``)를 정해진 8개 제목의 본문으로 쓴다.
- Judge: 실제 근거와 분석, 완성된 초안을 함께 보고 의미적으로 맞는지 판단한다.
  (인용한 evidence ID가 존재하는지만 확인하는 구조 검사와는 다르다.)

두 역할은 ``ContextualRoleAttempt``를 상속해 요청/파싱 방식을 공유하고,
실제 호출은 라이프사이클의 ``execute_model``이 맡는다. 시도 횟수와 예산은
라이프사이클이 관리하고, 이 모듈은 "어떤 입력을 모델에 보내고 결과를 어떻게
판정하는가"만 정한다.
"""

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
    OperatorFolderBinding,
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

# 본문의 "## " 제목 8개. 이름과 순서가 정확히 같아야 구조 검사를 통과한다.
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
# Judge가 지적할 수 있는 문제 유형 코드. 이 코드만 다음 Composition 재시도에
# "피드백"으로 전달되고, 비공개 메타데이터나 자유 텍스트 지시는 전달되지 않는다.
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
    """Judge가 찾은 문제 1건: 유형 코드, 위치, 이유, 관련 evidence ID."""

    code: SemanticCode
    location: Text
    reason: Text
    evidence_ids: EvidenceIDs


class SemanticJudgeReply(_NarrativeContract):
    """Judge의 의미 판정 결과.

    "evidence ID가 목록에 있다"는 사실만으로 근거가 뒷받침한다고 보지 않는다.
    ``artifact_hash``는 어느 초안에 대한 판정인지 묶어 두는 값이다.
    """

    context_id: Text
    artifact_hash: SHA256
    verdict: Literal["pass", "revise", "fail"]
    issues: list[SemanticIssue]

    @model_validator(mode="after")
    def check_context(self, info: ValidationInfo) -> Self:
        # 파싱 시 주입된 writer_context(ContextualRoleAttempt 참고)로 교차 검증한다.
        writer = _writer_context(info)
        # 다른 실행/문맥에 대한 판정이면 거부한다.
        if self.context_id != writer.context_id:
            raise CompanyReportConfigError("STALE_JUDGE_CONTEXT")
        # pass이면 지적 사항이 없어야 하고, revise/fail이면 지적 사항이 있어야 한다.
        if (self.verdict == "pass") != (not self.issues):
            raise CompanyReportConfigError("JUDGE_VERDICT_ISSUES_MISMATCH")
        for issue in self.issues:
            # Judge가 반환한 근거 ID는 검증된 목록에 있어야 한다. 지어낸 ID는 거절한다.
            if not set(issue.evidence_ids) <= writer.evidence.keys():
                raise CompanyReportConfigError("INVALID_JUDGE_EVIDENCE")
            # 위치/이유 텍스트도 출처 식별자가 섞이지 않았는지 확인한다.
            _check_neutral_text([issue.location, issue.reason], set())
        return self


@dataclass(frozen=True, slots=True)
class NarrativeModelSettings:
    """Composition/Judge 호출에 공통으로 쓰는 모델 설정 묶음(4개 인자를 1개로)."""

    llm_settings: LLMSettings
    max_output_tokens: int
    api_key: str | None = None
    http_transport: httpx.BaseTransport | None = None


@dataclass(frozen=True, slots=True)
class NarrativeCompositionResult:
    """최종 결과: 통과한 초안, 그 초안에 대한 Judge 판정, 바인딩 해시."""

    composition: CompositionReply
    judgment: SemanticJudgeReply
    artifact_hash: str


def composition_hash(draft: CompositionReply) -> str:
    """초안의 모든 필드를 대상으로 해시를 만들어 승인을 묶는다.

    본문만 해시하면 인용 ID나 강점/약점 목록이 달라져도 같은 승인으로 보일 수 있다.
    키 정렬과 고정 구분자로 직렬화해 같은 내용이면 항상 같은 해시가 나온다.
    """
    return hashlib.sha256(
        json.dumps(
            draft.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _neutral_reply(context: CompanyWriterContext, prose: list[str]) -> None:
    """다른 모델에 넘기기 전, 모델이 쓴 문장/피드백이 source-neutral인지 확인한다.

    "source-neutral"은 회사 이름이 아닌 출처 식별 정보(URL, 경로, 출판사, 저자 등)가
    문장에 섞여 들어가지 않았다는 뜻이다. 등록된 인용 영수증에서 금지 토큰을
    모아 둔 뒤 문장에 포함되면 오류로 처리한다.
    """
    tokens: set[str] = set()
    registry = context.citation_registry
    if registry["operator_folder"] is not None:
        # 검증된 원문 claim/excerpt의 예외는 모델 생성 문장에 적용하지 않는다.
        folder = OperatorFolderBinding.model_validate(registry["operator_folder"])
        tokens.update(
            (folder.generation_id, folder.input_fingerprint, folder.store_version)
        )
    # 영수증마다 출처 메타데이터 값과 chunk ID를 금지 토큰으로 수집한다.
    # 대상 회사 이름과 겹치는 값은 회사명을 쓰는 것이므로 제외한다.
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
    # 원본 evidence ID에 비공개 source ID가 섞일 수 있지만, 인용 표식
    # ``[@evidence:ID]``는 공개 형식이므로 검사 전에 제거해 오탐을 막는다.
    cleaned = []
    for text in prose:
        for eid in context.writer_context.evidence:
            text = text.replace(f"[@evidence:{eid}]", "")
        cleaned.append(text)
    _check_neutral_text(cleaned, tokens)


def _final_analysis(
    context: CompanyWriterContext, analysis: AnalysisReply
) -> AnalysisReply:
    """작성/심사에 넘길 분석이 "최종"인지 다시 파싱해 확인한다.

    추가 정보 요청(``needs``)이 남아 있으면 아직 분석이 끝난 것이 아니므로
    거부한다. 통과하면 답변 문장, 한계, 검증 과제에 대해 source-neutral 검사를 한다.
    """
    writer = context.writer_context
    # 신뢰할 수 없는 입력일 수 있으므로 writer_context를 넣어 다시 파싱한다.
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
    """초안의 구조 오류를 다음 시도에 알려 주는 피드백(숫자·ID만, 자유 텍스트 없음)."""

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
    """초안의 구조 차이를 정확히 계산한다.

    신뢰할 수 없는 문장이나 메타데이터는 되돌려 주지 않는다.

    확인 항목은 네 가지다. (1) ``## `` 제목이 8개 정해진 제목과 순서대로인지,
    (2) 본문에서 실제로 인용한 ID와 초안이 선언한 ID가 같은지,
    (3) 깨진 인용 표식이 없는지, (4) partial/unknown 답변마다 확인 과제가 있는지.
    모두 맞으면 ``None``을 돌려준다.
    """
    from skala_rag.reporting.validator import TOKEN

    body = draft.markdown_without_reference
    headings = tuple(
        line.removeprefix("## ") for line in body.splitlines() if line.startswith("## ")
    )
    # 근거가 부족해 "일부/모름"으로 남은 질문은 후속 확인 과제가 반드시 필요하다.
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
    # 승인된(실제 수집된) 원본 ID만 피드백에 그대로 쓴다. 깨졌거나 모르는 ID는
    # 문자열을 되돌려 주지 않고 개수만 알려 주어, 프롬프트 주입 통로가 되지 않게 한다.
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
    """작성(Composition) 역할 시도. 실행은 기존 라이프사이클을 통해서만 한다.

    분석 결과로 지정된 제목의 본문을 쓰도록 요청을 만든다. 재시도에서는 Judge의
    유형 코드(``feedback``)와 구조 피드백(``structural_feedback``)을 payload에 더한다.
    """

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
        # 최종 분석인지, 그리고 source-neutral인지 확인한 뒤에만 payload를 만든다.
        analysis = _final_analysis(context, analysis)
        payload = json.loads(
            build_narrative_payload(
                writer, analysis, headings=NARRATIVE_HEADINGS, feedback=feedback
            )
        )
        # 구조 피드백은 이전 시도가 실패했을 때만 있다.
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
    """심사(Judge) 역할 시도. Composition과 분리된 별도 모델 호출이다.

    실제 근거, 승인된 분석, 판정 대상 초안을 함께 받는다. 초안이 어떤 판정에
    묶이는지 확인할 수 있도록 ``artifact_hash``를 속성으로 남긴다.
    """

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
        # 넘겨받은 초안도 다시 파싱해 문맥과 맞는 값인지 확인한다.
        draft = CompositionReply.model_validate(
            composition, context={"writer_context": writer}
        )
        # Judge에게 보내기 전 초안 본문, 강점/약점, 검증 과제를 모두 검사한다.
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
    """Composition과 Judge를 실행하고, 공유 repair 2회 한도 안에서 고친다.

    라이프사이클은 repair 횟수를 구조 오류와 의미 재작성(``revise``)에 공유해서
    세는다. ``consume_repair``가 한도를 넘기면 예외로 루프가 끝난다.
    사실 수집은 다시 열지 않는다(문맥이 바뀌면 중단).

    흐름: 초안 작성 -> 구조 검사(실패 시 repair) -> Judge ->
    pass면 반환, fail이면 중단, revise면 repair 후 처음부터 다시.
    """
    frozen = lifecycle.context
    frozen.verify_binding()
    analysis_json = _final_analysis(frozen, analysis).model_dump_json()
    structural_feedback: _CompositionFeedback | None = None
    while True:
        # 반복 중 문맥이 달라지면 이후 결과를 신뢰할 수 없으므로 중단한다.
        if lifecycle.context != frozen:
            raise CompanyReportConfigError("FROZEN_NARRATIVE_CONTEXT_CHANGED")
        # 매 반복마다 저장한 JSON에서 새 DTO를 만들어 이전 수정이 섞이지 않게 한다.
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
            # 응답이 스키마/문맥 검증에 실패한 경우만 repair 1회를 써서 재시도한다.
            # 네트워크 등 다른 실패는 여기서 고치지 않고 중단한다.
            if composed.errors[-1].error_code == "TOOL_RESPONSE_INVALID":
                lifecycle.consume_repair(
                    context_id=frozen.context_id, kind="composition"
                )
                feedback = ("composition",)
                structural_feedback = None
                continue
            raise CompanyReportConfigError("COMPOSITION_TRANSPORT_FAILED")
        draft = composed.data
        # 구조가 틀리면 Judge에 가기 전에 repair를 써서 불필요한 판정 비용을 아낀다.
        structural_feedback = _composition_feedback(
            draft, parsed, frozen.writer_context
        )
        if structural_feedback:
            lifecycle.consume_repair(context_id=frozen.context_id, kind="composition")
            feedback = ("composition",)
            continue
        # 구조 검사를 통과한 초안만 Judge가 판정한다.
        judge = SemanticJudgeAttempt(lifecycle, parsed, draft, settings=settings)
        judged = lifecycle.execute_model(transport=judge)
        if judged.data is None:
            raise CompanyReportConfigError("JUDGE_TRANSPORT_FAILED")
        judgment = judged.data
        # 판정이 내가 보낸 바로 그 초안에 대한 것인지(해시), 문맥이 그대로인지 확인한다.
        if lifecycle.context != frozen or judgment.artifact_hash != judge.artifact_hash:
            raise CompanyReportConfigError("JUDGE_ARTIFACT_BINDING_MISMATCH")
        # verdict는 세 가지뿐이라 match + assert_never로 빠짐없이 다룬다.
        match judgment.verdict:
            case "pass":
                return NarrativeCompositionResult(draft, judgment, judge.artifact_hash)
            case "fail":
                raise CompanyReportConfigError("NARRATIVE_SEMANTIC_FAILED")
            case "revise":
                lifecycle.consume_repair(context_id=frozen.context_id, kind="semantic")
                # 피드백은 기계가 읽는 유형 코드만 쓴다. 비공개 메타데이터나
                # 모델이 쓴 지시문은 다음 호출로 전달하지 않는다.
                feedback = tuple(issue.code for issue in judgment.issues)
            case unreachable:
                assert_never(unreachable)
