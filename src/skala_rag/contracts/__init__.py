"""Public structural DTOs and IDs; policy computation belongs to controllers."""

from .assessment import CriterionAssessment
from .bundles import DiscoveryBundle, RetrievalBundle
from .candidates import Candidate, CompanyProfile, EligibilityResult, StageInfo
from .common import MonetaryObservation
from .coverage import CoverageResult, ResearchGap
from .decisions import InvestmentDecision, ScoreSummary
from .errors import WorkflowError
from .evaluation import Evaluation, EvaluationResult, EvaluationSnapshot
from .evidence import Evidence, EvidenceProvenance
from .ids import (
    decision_id,
    eligibility_result_id,
    evaluation_key,
    evidence_id,
    normalize_claim,
    score_summary_id,
    snapshot_id,
)
from .inputs import RunInput
from .manifest import ArtifactMetadata, RunManifest
from .reports import (
    CandidateOutcome,
    ReportContext,
    ReportDraft,
    ReportFinding,
    ReportInput,
    ReportJudgement,
    ValidationErrorDetail,
    ValidationResult,
)
from .retrieval import RetrievalRecord, RetrievalRequest
from .sources import Chunk, Source
from .tools import (
    CompanyResearchBundle,
    DecisionPolicyResult,
    EvidenceBundle,
    RenderResult,
    ToolBudget,
    ToolResult,
)

__all__ = [
    "RunInput",
    "Candidate",
    "StageInfo",
    "CompanyProfile",
    "EligibilityResult",
    "MonetaryObservation",
    "Source",
    "Chunk",
    "Evidence",
    "EvidenceProvenance",
    "DiscoveryBundle",
    "RetrievalBundle",
    "RetrievalRequest",
    "RetrievalRecord",
    "ResearchGap",
    "CoverageResult",
    "CriterionAssessment",
    "EvaluationSnapshot",
    "Evaluation",
    "EvaluationResult",
    "ScoreSummary",
    "InvestmentDecision",
    "CandidateOutcome",
    "ReportInput",
    "ReportContext",
    "ReportDraft",
    "ValidationErrorDetail",
    "ValidationResult",
    "ReportFinding",
    "ReportJudgement",
    "WorkflowError",
    "ArtifactMetadata",
    "RunManifest",
    "ToolBudget",
    "ToolResult",
    "EvidenceBundle",
    "CompanyResearchBundle",
    "DecisionPolicyResult",
    "RenderResult",
    "snapshot_id",
    "eligibility_result_id",
    "score_summary_id",
    "decision_id",
    "evaluation_key",
    "evidence_id",
    "normalize_claim",
]
