"""Pinned input data and separately configured operator authority.

Nothing deserialized from the packet grants permission. In particular archive
review flags, model metadata and receipt booleans are not authentication.
"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from skala_rag.agents.discovery import accept_discovery, normalize_candidates
from skala_rag.agents.eligibility import check_eligibility
from skala_rag.agents.finance_verification import ReviewedFinancialFact
from skala_rag.agents.founder_verification import ReviewedFounderAnchor
from skala_rag.agents.market import (
    MarketLink,
    MarketObservationReview,
    MarketTarget,
)
from skala_rag.agents.moat_verification import ReviewedMoatAnchor
from skala_rag.agents.source_fact_verification import (
    SourceBoundReview,
    SourceBoundReviewResolver,
    TrustedCapture,
    TrustedSource,
    verify_original_capture,
    verify_original_source,
)
from skala_rag.agents.technology_verification import ReviewedTechnologyAnchor
from skala_rag.contracts import (
    Candidate,
    Chunk,
    CompanyProfile,
    CompanyResearchBundle,
    DiscoveryBundle,
    EligibilityResult,
    EvaluationSnapshot,
    Evidence,
    ResearchGap,
    RetrievalBundle,
    RetrievalRecord,
    RunInput,
    Source,
    ToolBudget,
    ToolResult,
)
from skala_rag.contracts.interfaces import Clock
from skala_rag.contracts.state import create_initial_state
from skala_rag.contracts.v3 import ApplicabilityAssessment
from skala_rag.graph.research_artifacts_v3 import CompanyResearchArtifactsV3
from skala_rag.graph.snapshot import freeze_snapshot
from skala_rag.rag.adapter import IndexSnapshot
from skala_rag.rag.index_v3 import EmbeddingVector, IndexMetadata, IndexSettings
from skala_rag.rag.sqlite_index import SQLiteIndexStore
from skala_rag.scoring.aggregate_v3 import ApplicabilityVerifier
from skala_rag.scoring.approval_registry import PinnedApprovalRegistry
from skala_rag.scoring.approved_policy import LiveGateVerifier
from skala_rag.scoring.coverage import SupportCheck
from skala_rag.scoring.coverage_v3 import ApplicabilityCheck
from skala_rag.tools.company_archive import compose_archive_company_research
from skala_rag.tools.company_research import FieldObservation, assemble_bundle

if TYPE_CHECKING:
    from skala_rag.agents.company_report_research import ResearchAdmission
    from skala_rag.contracts.company_report import CompanyReportRequest
    from skala_rag.rag.company_store import CompanyIdentity, StoreSnapshot
    from skala_rag.scoring.approved_consumers import ActualAdmissionV3


class ActualInputError(ValueError):
    """A bounded machine code, never an input/credential-bearing error message."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


class _Packet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PinnedFile(_Packet):
    path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def verify(self) -> None:
        if file_digest(self.path) != self.sha256:
            raise ActualInputError("FILE_PIN_MISMATCH")


class ArchiveInput(_Packet):
    root: Path
    index_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ObservationInput(_Packet):
    observation: FieldObservation
    retrieval_id: str
    method: Literal["web", "api"]
    review_request: str = Field(min_length=1)
    review_subject: str = Field(min_length=1)


class CandidateInput(_Packet):
    observations: tuple[ObservationInput, ...]
    sources: dict[str, Source]
    chunks: dict[str, Chunk]
    records: tuple[RetrievalRecord, ...]
    evidence: dict[str, Evidence]
    initial_gaps: tuple[ResearchGap, ...]


class RetainedRetrievalV3(_Packet):
    """Pinned readonly retrieval assets; none of these fields grants authority."""

    run_input: RunInput
    index: PinnedFile
    reopen_input: PinnedFile
    model_root: Path
    model_files: dict[str, str]


class ActualInputsV3(RetainedRetrievalV3):
    """Only observations and commitments; no admission/reviewer/plugin fields."""

    run_id: str = Field(min_length=1)
    discovery: ToolResult[DiscoveryBundle]
    archives: tuple[ArchiveInput, ...]
    candidates: dict[str, CandidateInput]
    allowed_source_ids: frozenset[str]
    industry_evidence_ids: frozenset[str]
    industry_evidence_dimensions: tuple[str, ...]
    approval_reference: str = Field(min_length=1)


class RetainedSourceInputsV3(RetainedRetrievalV3):
    """Bounded source preparation, without an executable packet or callbacks.

    Candidate identity is caller-supplied data, not an approved discovery receipt.
    ``index_originals`` pins each index Source's original bytes independently of
    the SQLite/reopen files; it cannot attribute those Sources to another company.
    """

    run_id: str = Field(min_length=1)
    candidates: tuple[Candidate, ...] = Field(min_length=1, max_length=40)
    archives: tuple[ArchiveInput, ...] = Field(min_length=1, max_length=8)
    index_originals: dict[str, PinnedFile] = Field(min_length=1, max_length=128)


class RetainedSourcePreparationV3(_Packet):
    status: Literal["preflight_blocked"] = "preflight_blocked"
    reason: Literal["EXTERNAL_AUTHORITY_REQUIRED"] = "EXTERNAL_AUTHORITY_REQUIRED"
    missing_requirements: tuple[str, ...] = (
        "EXTERNAL_AUTHORITY_REQUIRED",
        "ELIGIBILITY_FACTS_MISSING",
        "RATING_REVIEW_MISSING",
    )
    actual_provider_calls: Literal[0] = 0
    publication_allowed: Literal[False] = False
    final_allowed: Literal[False] = False
    source_compositions: dict[str, tuple[ToolResult[CompanyResearchBundle], ...]]
    eligibility: dict[str, EligibilityResult]
    index_version: str
    index_sources: dict[str, Source]
    index_chunks: tuple[Chunk, ...]
    verified_model_files: int


class _PreparationClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def prepare_retained_sources(
    inputs: RetainedSourceInputsV3,
) -> RetainedSourcePreparationV3:
    """Inspect original local closures and compose Sources, never execution.

    Reuse the archive composer and eligibility consumer without semantic reviews.
    Unknown profile facts remain null; byte integrity is not source authority.
    No encoder, runtime, credentials, campaign marker or provider is constructed.
    """
    supplied = RetainedSourceInputsV3.model_validate_json(
        inputs.model_dump_json(), context={"execution_mode": "live"}
    )
    if supplied.run_input.execution_mode != "live" or supplied.run_input.as_of != date(
        2026, 10, 7
    ):
        raise ActualInputError("RUN_SCOPE_MISMATCH")
    snapshot, metadata = open_index(supplied)
    verify_model_files(supplied)
    if set(supplied.index_originals) != set(snapshot.bundle.sources):
        raise ActualInputError("INDEX_ORIGINAL_CLOSURE_MISMATCH")
    for sid, original in supplied.index_originals.items():
        original.verify()
        if snapshot.bundle.sources[sid].content_hash != "sha256:" + original.sha256:
            raise ActualInputError("INDEX_ORIGINAL_PIN_MISMATCH")
    clock = _PreparationClock()
    budget = ToolBudget(
        schema_version=supplied.run_input.schema_version,
        max_calls=1,
        max_retries=0,
        timeout_seconds=30,
    )
    normalized = normalize_candidates(
        list(supplied.candidates), max_candidates=len(supplied.candidates)
    )
    compositions = {}
    eligibility = {}
    for candidate in normalized.candidates:
        converted = []
        sources = {}
        for archive in supplied.archives:
            result = compose_archive_company_research(
                archive_root=archive.root,
                expected_index_sha256=archive.index_sha256,
                candidate=candidate,
                run_input=supplied.run_input,
                run_id=supplied.run_id,
                budget=budget,
                clock=clock,
            )
            if result.status not in ("ok", "empty") or result.data is None:
                raise ActualInputError("ARCHIVE_COMPOSITION_FAILED")
            for sid, source in result.data.sources.items():
                if sid in sources and sources[sid] != source:
                    raise ActualInputError("SOURCE_CONFLICT")
                sources[sid] = source
            converted.append(result)
        bundle, _, _ = assemble_bundle(
            candidate,
            tuple(sources.values()),
            (),
            as_of=supplied.run_input.as_of,
            schema_version=supplied.run_input.schema_version,
        )
        compositions[candidate.candidate_id] = tuple(converted)
        eligibility[candidate.candidate_id] = check_eligibility(
            bundle.profile,
            bundle.evidence,
            {"policy_version": supplied.run_input.policy_version},
            run_id=supplied.run_id,
            evidence_revision=0,
        )
    return RetainedSourcePreparationV3(
        source_compositions=compositions,
        eligibility=eligibility,
        index_version=metadata.index_version,
        index_sources=dict(snapshot.bundle.sources),
        index_chunks=tuple(snapshot.bundle.chunks),
        verified_model_files=len(supplied.model_files),
    )


@dataclass(frozen=True, kw_only=True)
class EvaluationInputsV3:
    """Existing domain receipt types, selected for this exact frozen snapshot."""

    review_request: bytes
    review_subject: str
    founder_person_ids: tuple[str, ...]
    verified_person_by_evidence_id: Mapping[str, str]
    founder_anchors: Mapping[str, ReviewedFounderAnchor]
    technology_anchors: Mapping[str, ReviewedTechnologyAnchor]
    moat_anchors: Mapping[str, ReviewedMoatAnchor]
    market_target: MarketTarget | None
    market_links: Mapping[str, MarketLink]
    market_reviews: Mapping[str, MarketObservationReview]
    financial_facts: tuple[ReviewedFinancialFact, ...]


@dataclass(frozen=True, kw_only=True)
class IdentityResearchAdmissionV3:
    """Separate OpenDART permission; URLs/identifiers alone grant no consent."""

    collection: "ResearchAdmission"
    api_key: str = field(repr=False)


@dataclass(frozen=True, kw_only=True)
class CompanyReportAuthorityV3:
    """Optional operator-owned company inputs; never deserialized or inferred.

    profile_for authenticates retained eligibility field assignments against the
    supplied originals. identity_review authenticates every identity field after
    separately admitted identity collection. Model proposals are not reviewers.
    Research factories return the existing admission, with the same actual object.
    """

    profile_for: Callable[[Candidate, "StoreSnapshot", date], CompanyProfile]
    research_for: (
        Callable[[Candidate, "ActualAdmissionV3"], "ResearchAdmission | None"] | None
    ) = None
    identity_research: (
        Callable[
            ["CompanyReportRequest", "ActualAdmissionV3"],
            tuple[IdentityResearchAdmissionV3, ...],
        ]
        | None
    ) = None
    identity_review: (
        Callable[[Candidate, Sequence[CompanyResearchBundle]], "CompanyIdentity | None"]
        | None
    ) = None


@dataclass(frozen=True, kw_only=True)
class ActualAuthorityV3:
    """Operator Python configuration, never loaded from the input packet.

    ``authenticate_inputs`` must resolve the *entire* detached packet, including
    discovery, field observations, criterion attribution and model commitments,
    against independently authenticated records. A byte hash alone is not an
    implementation of that callback. ``reviews_for`` returns independently
    authenticated records, not freshly approved model proposals.

    ``campaign_id`` identifies the approved cumulative campaign, not a run,
    packet hash or output path. All invocations share ``campaign_directory``:
    an absolute persistent directory outside individual outputs. The marker
    filename is derived only from that operator-owned identity. Reconciliation
    belongs to the operator's existing controls; this runner cannot reset or
    resume a started campaign.
    """

    campaign_id: str
    campaign_directory: Path
    authenticate_inputs: Callable[[ActualInputsV3], bool]
    sources: Mapping[str, TrustedSource | TrustedCapture]
    live_gate_verifier: LiveGateVerifier
    reviews_for: Callable[
        [EvaluationSnapshot, Mapping[str, JsonValue]], tuple[SourceBoundReview, ...]
    ]
    evaluation_inputs_for: Callable[[EvaluationSnapshot], EvaluationInputsV3]
    support_check: SupportCheck
    applicability_assessments: Callable[[str], Mapping[str, ApplicabilityAssessment]]
    applicability_check: ApplicabilityCheck
    applicability_verifier: ApplicabilityVerifier | None
    verify_replay_origin: Callable[[Mapping[str, JsonValue]], bool]
    company_report: CompanyReportAuthorityV3 | None = None

    @property
    def campaign_marker(self) -> Path:
        """One persistent latch, independent of input revisions and output paths."""
        key = digest(canonical(["actual-v3-campaign", self.campaign_id]))
        return self.campaign_directory.resolve() / f"{key}.json"


def load_inputs(path: Path, expected_sha256: str) -> ActualInputsV3:
    raw = path.read_bytes()
    if digest(raw) != expected_sha256:
        raise ActualInputError("INPUT_PIN_MISMATCH")
    packet = ActualInputsV3.model_validate_json(raw, context={"execution_mode": "live"})
    if (
        packet.run_input.execution_mode != "live"
        or packet.run_input.as_of != date(2026, 10, 7)
        or not packet.candidates
    ):
        raise ActualInputError("RUN_SCOPE_MISMATCH")
    return packet


def verify_sources(
    sources: Mapping[str, Source], authority: ActualAuthorityV3, corpus: str
) -> None:
    """Read original bytes without running any operator callback."""
    for sid, source in sources.items():
        trusted = authority.sources.get(sid)
        match trusted:
            case TrustedSource():
                proof = verify_original_source(trusted)
            case TrustedCapture():
                proof = verify_original_capture(trusted)
            case _:
                raise ActualInputError("SOURCE_AUTHORITY_MISSING")
        if source != proof.source or proof.corpus_version != corpus:
            raise ActualInputError("SOURCE_AUTHORITY_MISMATCH")


def open_index(packet: RetainedRetrievalV3) -> tuple[IndexSnapshot, IndexMetadata]:
    """Reopen the existing readonly SQLite store without loading an encoder."""
    packet.index.verify()
    packet.reopen_input.verify()
    receipt = json.loads(packet.reopen_input.path.read_bytes())
    payload = dict(receipt["metadata"])
    payload["chunk_ids"] = tuple(payload["chunk_ids"])
    payload["document_ids"] = tuple(payload["document_ids"])
    metadata = IndexMetadata(**payload)
    store = SQLiteIndexStore(packet.index.path)
    if store.read_metadata(metadata.index_version) != metadata:
        raise ActualInputError("INDEX_METADATA_MISMATCH")
    hits = store.search(
        EmbeddingVector(**receipt["query_vector"]),
        expected=metadata,
        top_k=len(metadata.chunk_ids),
    )
    chunks = {
        c["chunk_id"]: Chunk.model_validate(c, context={"execution_mode": "live"})
        for c in receipt["chunks"]
    }
    sources = {
        s["source_id"]: Source.model_validate(s, context={"execution_mode": "live"})
        for s in receipt["sources"]
    }
    if (
        len(hits) != len(chunks)
        or any(
            h.chunk != chunks.get(h.chunk.chunk_id)
            or h.source != sources.get(h.source.source_id)
            for h in hits
        )
        or metadata.corpus_version != packet.run_input.corpus_version
        or metadata.model_id != "BAAI/bge-m3"
        or metadata.model_revision != "5617a9f61b028005a4858fdac845db406aefb181"
    ):
        raise ActualInputError("INDEX_CLOSURE_MISMATCH")
    settings = IndexSettings(**json.loads(metadata.settings_snapshot))
    return IndexSnapshot(
        schema_version=packet.run_input.schema_version,
        corpus_version=metadata.corpus_version,
        corpus_hash=metadata.corpus_hash,
        index_version=metadata.index_version,
        embedding_model=metadata.model_id,
        embedding_revision=metadata.model_revision,
        search_settings={
            "index_settings": settings.snapshot(),
            "search": {"metric": "cosine"},
        },
        bundle=RetrievalBundle(
            schema_version=packet.run_input.schema_version,
            chunks=list(chunks.values()),
            sources=sources,
        ),
    ), metadata


def verify_model_files(packet: RetainedRetrievalV3) -> None:
    if len(packet.model_files) != 15:
        raise ActualInputError("MODEL_CLOSURE_MISSING")
    for name, pin in packet.model_files.items():
        path = packet.model_root / name
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or not path.resolve().is_relative_to(packet.model_root.resolve())
            or file_digest(path) != pin
        ):
            raise ActualInputError("MODEL_PIN_MISMATCH")


@dataclass(frozen=True)
class PreparedCandidate:
    candidate: Candidate
    seed: CompanyResearchArtifactsV3
    eligibility: EligibilityResult
    snapshot: EvaluationSnapshot | None


def prepare_candidates(
    packet: ActualInputsV3,
    authority: ActualAuthorityV3,
    *,
    clock: Clock,
    budget: ToolBudget,
    index_version: str,
) -> tuple[Sequence[PreparedCandidate], dict[str, JsonValue]]:
    """Run original discovery, normalization, archive assembly and eligibility."""
    discovered = accept_discovery(packet.discovery)
    if discovered.status != "found" or discovered.bundle is None:
        raise ActualInputError("DISCOVERY_NOT_FOUND")
    normalized = normalize_candidates(
        discovered.bundle.candidates,
        max_candidates=len(discovered.bundle.candidates),
    )
    if set(packet.candidates) != {c.candidate_id for c in normalized.candidates}:
        raise ActualInputError("CANDIDATE_POPULATION_MISMATCH")
    prepared = []
    for candidate in normalized.candidates:
        cid = candidate.candidate_id
        supplied = packet.candidates[cid]
        sources = {**discovered.bundle.sources, **supplied.sources}
        records = list(supplied.records)
        for archive in packet.archives:
            result = compose_archive_company_research(
                archive_root=archive.root,
                expected_index_sha256=archive.index_sha256,
                candidate=candidate,
                run_input=packet.run_input,
                run_id=packet.run_id,
                budget=budget,
                clock=clock,
            )
            if result.status not in ("ok", "empty") or result.data is None:
                raise ActualInputError("ARCHIVE_COMPOSITION_FAILED")
            for sid, source in result.data.sources.items():
                if sid in sources and sources[sid] != source:
                    raise ActualInputError("SOURCE_CONFLICT")
                sources[sid] = source
            records.extend(result.retrieval_records)
        verify_sources(sources, authority, packet.run_input.corpus_version)
        observations = [
            (o.observation, o.retrieval_id, o.method) for o in supplied.observations
        ]
        bundle, rejected, _ = assemble_bundle(
            candidate,
            tuple(sources.values()),
            observations,
            as_of=packet.run_input.as_of,
            schema_version=packet.run_input.schema_version,
        )
        if rejected:
            raise ActualInputError("ELIGIBILITY_OBSERVATION_REJECTED")
        evidence = dict(supplied.evidence)
        for eid, item in bundle.evidence.items():
            if eid in evidence and evidence[eid] != item:
                raise ActualInputError("EVIDENCE_CONFLICT")
            evidence[eid] = item
        # This is a new lossless assembly, not a historical provider measurement.
        # Original supplied records remain committed in inputs.json.
        linked = []
        for original in records:
            item = original.model_copy(deep=True)
            item.evidence_ids = sorted(
                set(item.evidence_ids)
                | {
                    e.evidence_id
                    for e in evidence.values()
                    if any(p.retrieval_id == item.retrieval_id for p in e.provenance)
                }
            )
            linked.append(item)
        eligibility = check_eligibility(
            bundle.profile,
            evidence,
            {"policy_version": packet.run_input.policy_version},
            run_id=packet.run_id,
            evidence_revision=0,
        )
        seed = CompanyResearchArtifactsV3(
            cid,
            packet.run_id,
            packet.run_input.schema_version,
            0,
            bundle.sources,
            supplied.chunks,
            linked,
            evidence,
        )
        frozen = None
        if eligibility.status == "eligible":
            state = create_initial_state(packet.run_input.model_dump(mode="json"))
            state.update(
                candidates=[candidate.model_dump(mode="json")],
                current_candidate_id=cid,
                sources={
                    sid: s.model_dump(mode="json") for sid, s in bundle.sources.items()
                },
                chunks={
                    key: c.model_dump(mode="json") for key, c in supplied.chunks.items()
                },
                evidence={
                    eid: e.model_dump(mode="json") for eid, e in evidence.items()
                },
                retrieval_history=[r.model_dump(mode="json") for r in linked],
                eligibility_results={cid: eligibility.model_dump(mode="json")},
                evidence_revisions={cid: 0},
                evaluation_rounds={cid: 0},
            )
            frozen = freeze_snapshot(
                cid,
                state,
                packet.run_input,
                run_id=packet.run_id,
                index_version=index_version,
                schema_version=packet.run_input.schema_version,
                allowed_source_ids=packet.allowed_source_ids,
                industry_evidence_ids=packet.industry_evidence_ids,
                clock=clock.now,
            )
        prepared.append(PreparedCandidate(candidate, seed, eligibility, frozen))
    receipt = {
        "discovery_status": discovered.status,
        "candidate_ids": [c.candidate_id for c in normalized.candidates],
        "merges": [
            {
                "kept_candidate_id": m.kept_candidate_id,
                "merged_candidate_id": m.merged_candidate_id,
                "matched_on": m.matched_on,
            }
            for m in normalized.merges
        ],
        "dropped_candidate_ids": normalized.dropped_candidate_ids,
    }
    return prepared, TypeAdapter(dict[str, JsonValue]).validate_python(receipt)


def review_resolver(
    snapshot: EvaluationSnapshot,
    rubric: Mapping[str, JsonValue],
    authority: ActualAuthorityV3,
    registry: PinnedApprovalRegistry,
) -> tuple[SourceBoundReviewResolver, tuple[SourceBoundReview, ...]]:
    sources = {}
    for sid in snapshot.sources:
        trusted = authority.sources[sid]
        # Retrieval freezes a subset of independently approved pages. Narrow
        # that pre-existing approval set; never manufacture or relabel a Chunk.
        sources[sid] = replace(
            trusted,
            approved_chunks=tuple(
                c for c in trusted.approved_chunks if c.chunk_id in snapshot.chunks
            ),
        )
    records = authority.reviews_for(snapshot.model_copy(deep=True), rubric)
    resolver = SourceBoundReviewResolver(
        snapshot,
        rubric,
        sources=sources,
        reviews=records,
        approval_registry=registry
        if rubric["rubric_version"] == "core-0.1.0"
        else None,
    )
    return resolver, records
