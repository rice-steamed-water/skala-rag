"""Public structural DTOs; policy decisions remain OPEN."""

from .bundles import DiscoveryBundle, RetrievalBundle
from .candidates import Candidate, CompanyProfile, EligibilityResult, StageInfo
from .common import MonetaryObservation
from .coverage import CoverageResult, ResearchGap
from .evidence import Evidence, EvidenceProvenance
from .inputs import RunInput
from .retrieval import RetrievalRecord, RetrievalRequest
from .sources import Chunk, Source

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
]
