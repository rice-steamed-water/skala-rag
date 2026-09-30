"""주입 인터페이스: contracts §7 함수 경계, LLM, clock.

모든 기능은 이 Protocol을 만족하는 객체를 주입받아 외부 호출 없이 테스트한다.
함수 경계는 ``__call__`` Protocol이라 일반 함수도 그대로 만족한다. 실제 adapter와
retry/backoff는 M2 범위다.

아직 타입이 없는 인자는 임시 타입을 쓴다. 담당 이슈가 타입을 만들면 교체한다.

- 적격성·보고서 검증 policy, rubric: ``JSONMap`` (로드된 설정 payload)
- PDF template: 템플릿 이름 또는 경로 ``str``
- Discovery 요청: ``RunInput``
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from skala_rag.scoring.catalog import ScoringPolicy

from .bundles import DiscoveryBundle, RetrievalBundle
from .candidates import Candidate, CompanyProfile, EligibilityResult
from .common import JSONMap
from .coverage import CoverageResult, ResearchGap
from .decisions import ScoreSummary
from .error_codes import ErrorCode, is_retryable
from .evaluation import Dimension, Evaluation, EvaluationResult, EvaluationSnapshot
from .evidence import Evidence
from .inputs import RunInput
from .reports import (
    ReportContext,
    ReportDraft,
    ReportInput,
    ReportJudgement,
    ValidationResult,
)
from .retrieval import RetrievalRequest
from .state import InvestmentState
from .tools import (
    CompanyResearchBundle,
    DecisionPolicyResult,
    EvidenceBundle,
    RenderResult,
    ToolBudget,
    ToolResult,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """시간대가 있는 현재 시각."""
        ...


class LLMError(Exception):
    """LLM 호출 실패. wrapper가 WorkflowError로 옮긴다. 메시지에 key를 넣지 않는다."""

    def __init__(self, error_code: ErrorCode, message_redacted: str) -> None:
        super().__init__(message_redacted)
        self.error_code = ErrorCode(error_code)
        self.message_redacted = message_redacted
        self.retryable = is_retryable(self.error_code)


@runtime_checkable
class StructuredLLM(Protocol):
    """provider 중립 structured output. 실패는 ``LLMError``로 낸다."""

    def generate(
        self, *, system: str, user: str, output_schema: type[ModelT]
    ) -> ModelT: ...


@runtime_checkable
class SearchCandidates(Protocol):
    def __call__(
        self, request: RunInput, budget: ToolBudget
    ) -> ToolResult[DiscoveryBundle]: ...


@runtime_checkable
class ResearchCompany(Protocol):
    def __call__(
        self, candidate: Candidate, budget: ToolBudget
    ) -> ToolResult[CompanyResearchBundle]: ...


@runtime_checkable
class Retrieve(Protocol):
    def __call__(self, request: RetrievalRequest) -> ToolResult[RetrievalBundle]: ...


@runtime_checkable
class CollectEvidence(Protocol):
    def __call__(
        self, candidate: Candidate, gaps: Sequence[ResearchGap], budget: ToolBudget
    ) -> ToolResult[EvidenceBundle]: ...


@runtime_checkable
class CheckEligibility(Protocol):
    def __call__(
        self,
        profile: CompanyProfile,
        evidence: Mapping[str, Evidence],
        policy: JSONMap,
    ) -> EligibilityResult: ...


@runtime_checkable
class CheckCoverage(Protocol):
    def __call__(
        self,
        candidate_id: str,
        evidence: Mapping[str, Evidence],
        catalog: ScoringPolicy,
    ) -> CoverageResult: ...


@runtime_checkable
class FreezeSnapshot(Protocol):
    def __call__(
        self, candidate_id: str, state: InvestmentState, run_input: RunInput
    ) -> EvaluationSnapshot: ...


@runtime_checkable
class EvaluateDimension(Protocol):
    def __call__(
        self, dimension: Dimension, snapshot: EvaluationSnapshot, rubric: JSONMap
    ) -> EvaluationResult: ...


@runtime_checkable
class AggregateScores(Protocol):
    def __call__(
        self, evaluations: Mapping[str, Evaluation], policy: ScoringPolicy
    ) -> ScoreSummary: ...


@runtime_checkable
class Decide(Protocol):
    def __call__(
        self,
        score_summary: ScoreSummary,
        eligibility: EligibilityResult,
        policy: ScoringPolicy,
    ) -> DecisionPolicyResult: ...


@runtime_checkable
class BuildReportContext(Protocol):
    def __call__(
        self, report_input: ReportInput, state: InvestmentState
    ) -> ReportContext: ...


@runtime_checkable
class GenerateReport(Protocol):
    def __call__(
        self, context: ReportContext, feedback: Sequence[str]
    ) -> ReportDraft: ...


@runtime_checkable
class ValidateReport(Protocol):
    def __call__(
        self, draft: ReportDraft, context: ReportContext, policy: JSONMap
    ) -> ValidationResult: ...


@runtime_checkable
class JudgeReport(Protocol):
    def __call__(
        self, draft: ReportDraft, context: ReportContext
    ) -> ReportJudgement: ...


@runtime_checkable
class RenderPdf(Protocol):
    def __call__(self, draft: ReportDraft, template: str) -> RenderResult: ...
