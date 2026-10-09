"""Source-backed ``technology.integration`` fact adjudication (#201).

Two separate authorities are checked here, never merged:

* **Source proof**: trusted controller-selected local bytes -> SHA-256 -> existing
  ``extract_pdf`` -> ``verify_text_review`` -> exact approved Chunk equality ->
  page/raw offset/quote. No fetch, download, model, encoder or LLM call. Any
  failure is a technical ``SourceFactError``; it is never converted to Missing.
* **Semantic admission**: registry membership is necessary, not entailment.
  The exact whole source sentence must assert the proposed subject/relation/
  object in a documented narrow affirmative form. Hz command-frequency roles
  are checked, not inferred from a literal number. Unsupported language fails
  closed. LLM/assistant proposals are never authority; unreviewed -> deny.

Ratings only follow the approved Core anchor texts verbatim (3/4/5). Anchors 1–2
(negative facts) are not adjudicated in this slice; undecidable -> rating None.
"""

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Literal, TypeAlias, assert_never

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)
from pydantic_core import PydanticSerializationError

if TYPE_CHECKING:
    from skala_rag.scoring.approval_registry import PinnedApprovalRegistry

from skala_rag.agents.finance_verification import ReviewedFinancialFact
from skala_rag.agents.founder_verification import ReviewedFounderAnchor
from skala_rag.agents.moat_verification import (
    ReviewedMoatAnchor,
    _digest,
    core_artifact_digest,
    frozen_snapshot_digest,
)
from skala_rag.agents.technology_verification import (
    ReviewedTechnologyAnchor,
    _exact_tree,
    checked_snapshot,
)
from skala_rag.contracts import Chunk, Source
from skala_rag.contracts.assessment import CriterionAssessment
from skala_rag.contracts.common import Text
from skala_rag.contracts.evaluation import EvaluationSnapshot
from skala_rag.rag.corpus import ManifestDocument
from skala_rag.rag.extraction import PageChunkSettings, extract_pdf
from skala_rag.rag.reviewed_extraction import verify_text_review
from skala_rag.rag.text_review import text_hash
from skala_rag.tools.company_archive import _ArchiveText
from skala_rag.tools.company_research import FieldObservation, StageObservation
from skala_rag.tools.source_fetch import check_as_of, content_hash

CRITERION = "technology.integration"
COMPONENT = "component_of_system"
CONNECTION = "directed_connection"
SELF_DEVELOPED = "component_self_developed"
EXTERNAL = "component_external_platform"
INDEPENDENT = "independent_integration_confirmation"
# Closed vocabulary. Keyword counts, author lists, arXiv publication, performance,
# contracts or customer stability are deliberately absent: not integration facts.
INTEGRATION_PREDICATES = frozenset(
    {COMPONENT, CONNECTION, SELF_DEVELOPED, EXTERNAL, INDEPENDENT}
)


class SourceFactError(ValueError):
    """Technical rejection (tamper, stale binding, resolver failure); not Missing."""


@dataclass(frozen=True)
class TrustedSource:
    """Controller-selected approved local snapshot; never a model-supplied path."""

    path: Path
    allowed_root: Path
    document: ManifestDocument
    source: Source
    corpus_version: str
    embedding_model: str
    embedding_revision: str
    approved_chunks: tuple[Chunk, ...]
    independent_of_candidate: bool = False


@dataclass(frozen=True)
class SourceProof:
    source_id: str
    content_hash: str
    chunks: Mapping[str, Chunk]
    page_text_hashes: Mapping[str, str]
    independent_of_candidate: bool
    source: Source
    corpus_version: str


def verify_original_source(trusted: TrustedSource) -> SourceProof:
    """Re-read original bytes and re-derive the approved page Chunks exactly."""
    try:
        if type(trusted) is not TrustedSource:
            raise ValueError("exact TrustedSource required")
        for value in (
            trusted.corpus_version,
            trusted.embedding_model,
            trusted.embedding_revision,
        ):
            _text(value)
        if (
            not isinstance(trusted.path, Path)
            or not isinstance(trusted.allowed_root, Path)
            or type(trusted.independent_of_candidate) is not bool
            or type(trusted.approved_chunks) is not tuple
            or not trusted.approved_chunks
        ):
            raise ValueError("invalid trusted source fields")
        document = _checked_model(trusted.document, ManifestDocument)
        source = _checked_model(trusted.source, Source)
        for name in ("source_id", "schema_version", "local_path", "title", "language"):
            if getattr(document, name) != getattr(source, name):
                raise ValueError("document/source identity mismatch")
        if (
            not document.approved
            or (document.origin_url is not None and document.origin_url != source.url)
            or (
                document.publication_date is not None
                and document.publication_date
                != (
                    source.published_at.date()
                    if hasattr(source.published_at, "date")
                    else source.published_at
                )
            )
        ):
            raise ValueError("document/source metadata mismatch")
        review = document.text_index_review
        if review is None:
            raise ValueError("approved text review required")
        path = Path(trusted.path).resolve()
        if not path.is_relative_to(Path(trusted.allowed_root).resolve()):
            raise ValueError("path outside approved root")
        relative = Path(document.local_path).relative_to("data/local")
        if (Path(trusted.allowed_root) / relative).resolve() != path:
            raise ValueError("document path mismatch")
        if path.suffix.lower() != ".pdf" or not path.is_file():
            raise ValueError("approved local PDF required")
        content = path.read_bytes()
        digest = content_hash(content)
        if digest != source.content_hash or digest != document.content_hash:
            raise ValueError("source bytes hash mismatch")
        result = extract_pdf(
            content,
            document,
            source,
            corpus_version=trusted.corpus_version,
            schema_version=document.schema_version,
            settings=PageChunkSettings(**review.extraction_settings),
            sections_by_page={},
            embedding_model=trusted.embedding_model,
            embedding_revision=trusted.embedding_revision,
            execution_mode="live",  # real local PDF parsing, not provider runtime
        )
        verify_text_review(result, review)
        extracted = {c.chunk_id: c for c in result.chunks}
        if not trusted.approved_chunks:
            raise ValueError("approved chunks required")
        for original_chunk in trusted.approved_chunks:
            chunk = _checked_model(original_chunk, Chunk)
            again = extracted.get(chunk.chunk_id)
            if again is None or again.model_dump(mode="json") != chunk.model_dump(
                mode="json"
            ):
                raise ValueError("approved chunk differs from re-extraction")
    except SourceFactError:
        raise
    except Exception:  # resolver failure is technical, never Missing
        raise SourceFactError("SOURCE_PROOF_REJECTED") from None
    return SourceProof(
        source_id=source.source_id,
        content_hash=digest,
        chunks=MappingProxyType(
            {c.chunk_id: c.model_copy(deep=True) for c in trusted.approved_chunks}
        ),
        page_text_hashes=MappingProxyType(dict(review.page_text_hashes)),
        independent_of_candidate=trusted.independent_of_candidate,
        source=source.model_copy(deep=True),
        corpus_version=trusted.corpus_version,
    )


@dataclass(frozen=True)
class SourceSpan:
    """Raw page offsets into the approved page Chunk text (no normalization)."""

    source_id: str
    chunk_id: str
    page: int
    start: int
    end: int
    quote: str
    page_text_sha256: str


def verify_span(proof: SourceProof, span: SourceSpan) -> None:
    """Prove the quote exists at the raw offsets; says nothing about meaning."""
    _check_proof(proof)
    _check_span(span)
    chunk = proof.chunks.get(span.chunk_id)
    if (
        chunk is None
        or span.source_id != proof.source_id
        or type(span.page) is not int
        or chunk.page_start != span.page
        or chunk.page_end != span.page
        or text_hash(chunk.text) != span.page_text_sha256
        or proof.page_text_hashes.get(str(span.page)) != span.page_text_sha256
        or type(span.start) is not int
        or type(span.end) is not int
        or not 0 <= span.start < span.end <= len(chunk.text)
        or not span.quote
        or chunk.text[span.start : span.end] != span.quote
    ):
        raise SourceFactError("SOURCE_SPAN_REJECTED")


@dataclass(frozen=True)
class Measurement:
    value: str
    unit: str
    metric_role: str
    conditions: str | None


@dataclass(frozen=True)
class AtomicFact:
    """One proposed claim; authority comes only from a registered exact review."""

    fact_id: str
    evidence_id: str
    span: SourceSpan
    subject: str
    predicate: str
    object: str
    measurement: Measurement | None = None
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class FactReview:
    review_reference: str
    fact_sha256: str
    decision: Literal["accepted", "rejected"]
    rationale: str


@dataclass(frozen=True)
class FactArtifact:
    run_id: str
    candidate_id: str
    evaluation_round: int
    evidence_revision: int
    snapshot_sha256: str
    policy_version: str
    rubric_sha256: str
    criterion_id: str
    system_subject: str
    minimum_evidence_text: tuple[str, ...]
    anchor_texts: Mapping[int, str]
    proposer: Literal["llm", "assistant", "human"]
    facts: tuple[AtomicFact, ...]
    reviews: tuple[FactReview, ...]


@dataclass(frozen=True)
class TrustedReviewRegistry:
    """Controller-owned: review reference -> exact accepted fact digests."""

    accepted: Mapping[str, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class FactDecision:
    fact_id: str
    source_proof: Literal["PASS"]
    semantic: Literal["ADMITTED", "NOT_ESTABLISHED"]
    reason: str | None


@dataclass(frozen=True)
class IntegrationAdjudication:
    criterion_id: str
    status: Literal["supported", "unsupported"]
    rating: int | None
    anchor_text: str | None
    minimum_evidence_supported: bool
    evidence_ids: tuple[str, ...]
    admitted_fact_ids: tuple[str, ...]
    denied: tuple[tuple[str, str], ...]
    snapshot_sha256: str
    rubric_sha256: str


def _text(value: object) -> None:
    if type(value) is not str or not value.strip():
        raise SourceFactError("TYPED_INPUT_REJECTED")


def _texts(
    values: tuple[str, ...] | list[str] | frozenset[str], container: type = tuple
) -> None:
    if type(values) is not container:
        raise SourceFactError("TYPED_INPUT_REJECTED")
    for value in values:
        _text(value)


def _checked_model(value, model):
    try:
        if type(value) is not model:
            raise ValueError("exact model required")
        checked = model.model_validate(
            value.model_dump(), context={"execution_mode": "fixture"}
        )
        _exact_tree(value, checked, checked.schema_version)
        return checked
    except Exception:
        raise SourceFactError("TYPED_INPUT_REJECTED") from None


def _check_span(span: object) -> None:
    if type(span) is not SourceSpan:
        raise SourceFactError("SOURCE_SPAN_REJECTED")
    for name in ("source_id", "chunk_id", "quote", "page_text_sha256"):
        _text(getattr(span, name))
    if any(type(getattr(span, name)) is not int for name in ("page", "start", "end")):
        raise SourceFactError("SOURCE_SPAN_REJECTED")


def _check_fact(fact: object) -> None:
    if type(fact) is not AtomicFact:
        raise SourceFactError("FACT_REJECTED")
    for name in ("fact_id", "evidence_id", "subject", "predicate", "object"):
        _text(getattr(fact, name))
    _check_span(fact.span)
    _texts(fact.limitations)
    if fact.measurement is not None:
        if type(fact.measurement) is not Measurement:
            raise SourceFactError("FACT_REJECTED")
        for name in ("value", "unit", "metric_role"):
            _text(getattr(fact.measurement, name))
        if fact.measurement.conditions is not None:
            _text(fact.measurement.conditions)


def _check_reviews(registry: object, reviews: object) -> None:
    if (
        type(registry) is not TrustedReviewRegistry
        or not isinstance(registry.accepted, Mapping)
        or type(reviews) is not tuple
    ):
        raise SourceFactError("REVIEW_REJECTED")
    for reference, digests in registry.accepted.items():
        _text(reference)
        _texts(digests, frozenset)
    for review in reviews:
        if type(review) is not FactReview:
            raise SourceFactError("REVIEW_REJECTED")
        for name in ("review_reference", "fact_sha256", "decision", "rationale"):
            _text(getattr(review, name))
        if review.decision not in ("accepted", "rejected"):
            raise SourceFactError("REVIEW_REJECTED")


def _check_proof(proof: object) -> None:
    if (
        type(proof) is not SourceProof
        or type(proof.independent_of_candidate) is not bool
        or not isinstance(proof.chunks, Mapping)
        or not isinstance(proof.page_text_hashes, Mapping)
    ):
        raise SourceFactError("SOURCE_PROOF_REJECTED")
    for name in ("source_id", "content_hash", "corpus_version"):
        _text(getattr(proof, name))
    source = _checked_model(proof.source, Source)
    if source.source_id != proof.source_id or source.content_hash != proof.content_hash:
        raise SourceFactError("SOURCE_PROOF_REJECTED")
    for key, chunk in proof.chunks.items():
        _text(key)
        checked = _checked_model(chunk, Chunk)
        if (
            checked.chunk_id != key
            or checked.source_id != proof.source_id
            or checked.corpus_version != proof.corpus_version
        ):
            raise SourceFactError("SOURCE_PROOF_REJECTED")
    for key, value in proof.page_text_hashes.items():
        _text(key)
        _text(value)


def _check_artifact(artifact: object, registry: object) -> None:
    if type(artifact) is not FactArtifact:
        raise SourceFactError("ARTIFACT_REJECTED")
    for name in (
        "run_id",
        "candidate_id",
        "snapshot_sha256",
        "policy_version",
        "rubric_sha256",
        "criterion_id",
        "system_subject",
        "proposer",
    ):
        _text(getattr(artifact, name))
    if (
        any(
            type(getattr(artifact, n)) is not int or getattr(artifact, n) < 0
            for n in ("evaluation_round", "evidence_revision")
        )
        or artifact.proposer not in ("llm", "assistant", "human")
        or type(artifact.facts) is not tuple
        or not isinstance(artifact.anchor_texts, Mapping)
    ):
        raise SourceFactError("ARTIFACT_REJECTED")
    _texts(artifact.minimum_evidence_text)
    for key, text in artifact.anchor_texts.items():
        if type(key) is not int:
            raise SourceFactError("ARTIFACT_REJECTED")
        _text(text)
    _check_reviews(registry, artifact.reviews)
    for fact in artifact.facts:
        _check_fact(fact)


def fact_digest(fact: AtomicFact) -> str:
    _check_fact(fact)
    return _digest(asdict(fact))


def _source_correspondence(fact: AtomicFact, proof: SourceProof) -> bool:
    """Closed affirmative sentence forms, not arbitrary language entailment.

    Exact subject/object strings and a whole source sentence are required; a
    registered review cannot turn a mention, negation or different relation into
    an assertion. Unsupported phrasing fails closed (including alias inference).
    """
    span = fact.span
    text = proof.chunks[span.chunk_id].text
    before, after = text[: span.start].rstrip(), text[span.end :].lstrip()
    if (before and before[-1] not in ".!?\n") or (
        after and not span.quote.endswith(".")
    ):
        return False
    # Do not allow cutting a positive-looking clause out of a larger sentence.
    if text[: span.start] and not text[: span.start].endswith(("\n", " ", "\t")):
        return False
    s, o, quote = fact.subject, fact.object, span.quote
    if fact.predicate == COMPONENT:
        relation = quote in (
            f"{o} is a component of {s}.",
            f"{o} is a core component of {s}.",
        )
    elif fact.predicate == SELF_DEVELOPED:
        relation = quote == f"{s} is a self-developed core component of {o}."
    elif fact.predicate == EXTERNAL:
        relation = quote == f"{s} is an external robot platform of {o}."
    elif fact.predicate == CONNECTION:
        relation = (
            quote in (f"{s} conditions {o}.", f"{s} sends targets to {o}.")
            or re.fullmatch(
                re.escape(f"{s} sends targets to {o} at ")
                + r"[0-9]+(?:\.[0-9]+)? Hz\.",
                quote,
            )
            is not None
        )
    else:
        nodes = o.split("->")
        relation = (
            len(nodes) >= 2
            and len(set(nodes)) == len(nodes)
            and all(re.fullmatch(r"[A-Za-z0-9_-]+", n) for n in nodes)
            and quote
            == f"Independent testing confirms integration outcome of {s}: {o}."
        )
    if not relation:
        return False
    measurement = fact.measurement
    if measurement is None:
        return True
    return (
        fact.predicate == CONNECTION
        and measurement.metric_role == "command_frequency"
        and measurement.unit == "Hz"
        and measurement.conditions is None
        and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", measurement.value) is not None
        and quote == f"{s} sends targets to {o} at {measurement.value} Hz."
    )


def assess_fact(
    fact: AtomicFact,
    proof: SourceProof,
    registry: TrustedReviewRegistry,
    reviews: tuple[FactReview, ...] = (),
) -> FactDecision:
    """Source proof (raises on failure) then semantic admission (deny reason)."""
    _check_fact(fact)
    _check_reviews(registry, reviews)
    verify_span(proof, fact.span)
    digest = fact_digest(fact)
    reviewed = any(
        type(r) is FactReview
        and r.fact_sha256 == digest
        and r.decision == "accepted"
        and digest in registry.accepted.get(r.review_reference, frozenset())
        for r in reviews
    )
    reason = None
    if not reviewed:
        reason = "unreviewed"
    elif fact.predicate not in INTEGRATION_PREDICATES:
        reason = "not_integration_predicate"
    elif fact.measurement is not None and (
        type(fact.measurement) is not Measurement
        or f"{fact.measurement.value} {fact.measurement.unit}" not in fact.span.quote
    ):
        reason = "measurement_not_in_quote"
    elif fact.predicate == INDEPENDENT and not proof.independent_of_candidate:
        reason = "not_independent_source"
    elif not _source_correspondence(fact, proof):
        reason = "source_relation_not_established"
    return FactDecision(
        fact.fact_id, "PASS", "NOT_ESTABLISHED" if reason else "ADMITTED", reason
    )


def _spec(rubric: Mapping[str, object]) -> Mapping:
    try:
        spec = rubric
        for key in ("dimensions", "technology", "criteria", CRITERION):
            value = spec[key]
            if not isinstance(value, Mapping):
                raise SourceFactError("RUBRIC_REJECTED")
            spec = value
        if not isinstance(spec["anchors"], Mapping):
            raise ValueError("invalid integration rubric")
        return spec
    except Exception:
        raise SourceFactError("RUBRIC_REJECTED") from None


def adjudicate_technology_integration(
    snapshot: EvaluationSnapshot,
    rubric: Mapping[str, object],
    artifact: FactArtifact,
    *,
    sources: Mapping[str, TrustedSource],
    registry: TrustedReviewRegistry,
) -> IntegrationAdjudication:
    """Deterministic minimum-evidence/anchor decision over admitted facts only."""
    _check_artifact(artifact, registry)
    if not isinstance(sources, Mapping):
        raise SourceFactError("SOURCE_NOT_TRUSTED")
    try:
        validated = checked_snapshot(snapshot)
    except Exception:
        raise SourceFactError("SNAPSHOT_REJECTED") from None
    spec = _spec(rubric)
    try:
        snapshot_sha = frozen_snapshot_digest(snapshot)
        rubric_sha = core_artifact_digest(rubric)
        anchors = {int(k): v for k, v in dict(spec["anchors"]).items()}
        if set(anchors) != set(range(1, 6)):
            raise ValueError("invalid anchor coverage")
        for value in anchors.values():
            _text(value)
        _texts(spec["minimum_evidence"], list)
    except Exception:
        raise SourceFactError("RUBRIC_REJECTED") from None
    if (
        artifact.criterion_id != CRITERION
        or artifact.snapshot_sha256 != snapshot_sha
        or artifact.rubric_sha256 != rubric_sha
        or artifact.run_id != validated.run_id
        or artifact.candidate_id != validated.candidate_id
        or artifact.evaluation_round != validated.evaluation_round
        or artifact.evidence_revision != validated.evidence_revision
        or artifact.policy_version != validated.policy_version
        or tuple(artifact.minimum_evidence_text) != tuple(spec["minimum_evidence"])
        or dict(artifact.anchor_texts) != anchors
        or not artifact.system_subject
        or len({f.fact_id for f in artifact.facts}) != len(artifact.facts)
    ):
        raise SourceFactError("BINDING_REJECTED")
    proofs: dict[str, SourceProof] = {}
    for fact in artifact.facts:
        if type(fact) is not AtomicFact or type(fact.span) is not SourceSpan:
            raise SourceFactError("FACT_REJECTED")
        sid = fact.span.source_id
        if sid not in proofs:
            if sid not in sources or sid not in validated.sources:
                raise SourceFactError("SOURCE_NOT_TRUSTED")
            proofs[sid] = verify_original_source(sources[sid])
            if (
                proofs[sid].source.model_dump(mode="json")
                != validated.sources[sid].model_dump(mode="json")
                or proofs[sid].source_id != sid
                or proofs[sid].corpus_version != validated.corpus_version
                or not check_as_of(proofs[sid].source, validated.as_of).admitted
            ):
                raise SourceFactError("SNAPSHOT_SOURCE_REJECTED")
        chunk = validated.chunks.get(fact.span.chunk_id)
        approved = proofs[sid].chunks.get(fact.span.chunk_id)
        evidence = validated.evidence.get(fact.evidence_id)
        if (
            chunk is None
            or approved is None
            or chunk.model_dump(mode="json") != approved.model_dump(mode="json")
            or evidence is None
            or evidence.scope != "company"
            or evidence.candidate_id != validated.candidate_id
            or CRITERION not in evidence.criterion_ids
            or evidence.source_id != sid
            or (
                evidence.event_date is not None
                and evidence.event_date > validated.as_of
            )
            or (
                evidence.value_as_of is not None
                and evidence.value_as_of > validated.as_of
            )
            or evidence.conflicts_with
            or fact.span.quote not in evidence.excerpt
            or not any(p.chunk_id == chunk.chunk_id for p in evidence.provenance)
        ):
            raise SourceFactError("EVIDENCE_BINDING_REJECTED")
    decisions = [
        assess_fact(f, proofs[f.span.source_id], registry, artifact.reviews)
        for f in artifact.facts
    ]
    admitted = [
        f for f, d in zip(artifact.facts, decisions, strict=True) if not d.reason
    ]
    denied = tuple((d.fact_id, d.reason) for d in decisions if d.reason)
    system = artifact.system_subject
    components = {
        f.object for f in admitted if f.predicate == COMPONENT and f.subject == system
    }
    edges = {
        (f.subject, f.object)
        for f in admitted
        if f.predicate == CONNECTION
        and f.subject in components
        and f.object in components
        and f.subject != f.object
    }
    own = {
        f.subject
        for f in admitted
        if f.predicate == SELF_DEVELOPED
        and f.subject in components
        and f.object == system
    }
    external = {
        f.subject
        for f in admitted
        if f.predicate == EXTERNAL and f.subject in components and f.object == system
    }
    core = {
        f.object
        for f in admitted
        if f.predicate == COMPONENT
        and f.subject == system
        and f.span.quote == f"{f.object} is a core component of {system}."
    }
    # Connected structures, not a global count of unrelated component claims.
    groups: list[set[str]] = []
    remaining = set().union(*(set(e) for e in edges)) if edges else set()
    while remaining:
        group = {remaining.pop()}
        while True:
            expanded = group | set().union(*(set(e) for e in edges if set(e) & group))
            if expanded == group:
                break
            group = expanded
        remaining -= group
        groups.append(group)
    minimum = bool(edges)  # 구성요소와 연결 구조
    rating = None
    owned_core = (own - external) & core
    r4_groups = sorted(
        (g for g in groups if len(g & owned_core) >= 3), key=lambda g: sorted(g)
    )
    witness: set[str] = set()
    confirmation_id = None
    if r4_groups:
        witness = r4_groups[0]
        rating = 4  # 핵심 3요소 이상 자체 개발, 통합 구조 설명
        for f in admitted:
            if f.predicate != INDEPENDENT or f.subject != system:
                continue
            nodes = f.object.split("->")
            confirmed_edges = set(zip(nodes, nodes[1:]))
            if (
                confirmed_edges <= edges
                and len(set(nodes) & owned_core) >= 3
                and any(set(nodes) <= group for group in r4_groups)
            ):
                rating = 5  # 이 시스템의 동일한 통합 구조·성과에 대한 독립 확인
                witness = next(group for group in r4_groups if set(nodes) <= group)
                confirmation_id = f.fact_id
                break
    else:
        r3_groups = sorted(
            (g for g in groups if g & owned_core and g & external),
            key=lambda g: sorted(g),
        )
        if r3_groups:
            witness = r3_groups[0]
            rating = 3  # 핵심 일부 자체 개발 + 외부 플랫폼 통합
    # Cite the demonstrated system structure, not unrelated admitted statements.
    used = (
        sorted(
            {
                f.evidence_id
                for f in admitted
                if (
                    f.predicate == COMPONENT
                    and f.subject == system
                    and f.object in witness
                )
                or (
                    f.predicate == CONNECTION
                    and (f.subject, f.object) in edges
                    and f.subject in witness
                    and f.object in witness
                )
                or (
                    f.predicate == SELF_DEVELOPED
                    and f.object == system
                    and f.subject in witness & owned_core
                )
                or (
                    rating == 3
                    and f.predicate == EXTERNAL
                    and f.object == system
                    and f.subject in witness & external
                )
                or f.fact_id == confirmation_id
            }
        )
        if rating
        else []
    )
    return IntegrationAdjudication(
        criterion_id=CRITERION,
        status="supported" if rating else "unsupported",
        rating=rating,
        anchor_text=anchors[rating] if rating else None,
        minimum_evidence_supported=minimum,
        evidence_ids=tuple(used),
        admitted_fact_ids=tuple(f.fact_id for f in admitted),
        denied=denied,
        snapshot_sha256=snapshot_sha,
        rubric_sha256=rubric_sha,
    )


@dataclass(frozen=True, slots=True)
class TrustedCapture:
    """Controller-selected original and retained text, not semantic authority.

    HTML uses the archive's exact charset/parser contract. Text originals are
    UTF-8 and retained verbatim. Optional Chunks contain the whole retained text;
    this format does not supply PDF pages or a chunking algorithm.
    """

    path: Path
    allowed_root: Path
    extracted_path: Path
    format: Literal["html", "text"]
    charset: str
    source: Source
    corpus_version: str
    extracted_text: str
    extracted_sha256: str
    approved_chunks: tuple[Chunk, ...] = ()


@dataclass(frozen=True, slots=True)
class CaptureProof:
    source: Source
    corpus_version: str
    text: str
    text_sha256: str
    chunks: Mapping[str, Chunk]


@dataclass(frozen=True, slots=True)
class SourceTextSpan:
    """Offsets in retained text, with no invented PDF page number."""

    source_id: str
    start: int
    end: int
    quote: str
    text_sha256: str
    chunk_id: str | None = None


def verify_original_capture(trusted: TrustedCapture) -> CaptureProof:
    """Re-read both files and reproduce only the supplied capture format."""
    try:
        if type(trusted) is not TrustedCapture:
            raise SourceFactError("SOURCE_PROOF_REJECTED")
        source = _checked_model(trusted.source, Source)
        _text(trusted.corpus_version)
        _text(trusted.charset)
        path, extracted = trusted.path.resolve(), trusted.extracted_path.resolve()
        root = trusted.allowed_root.resolve()
        if (
            not path.is_relative_to(root)
            or not extracted.is_relative_to(root)
            or path == extracted
            or type(trusted.approved_chunks) is not tuple
            or (
                source.local_path is not None
                and (root / source.local_path).resolve() != path
            )
        ):
            raise SourceFactError("SOURCE_PROOF_REJECTED")
        raw, retained = path.read_bytes(), extracted.read_bytes()
        if (
            content_hash(raw) != source.content_hash
            or content_hash(retained) != trusted.extracted_sha256
            or retained.decode("utf-8") != trusted.extracted_text
        ):
            raise SourceFactError("SOURCE_PROOF_REJECTED")
        match trusted.format:
            case "html":
                reader = _ArchiveText()
                reader.feed(raw.decode(trusted.charset, errors="replace"))
                text = reader.text()
            case "text":
                if trusted.charset != "utf-8":
                    raise SourceFactError("SOURCE_PROOF_REJECTED")
                text = raw.decode("utf-8")
            case unreachable:
                assert_never(unreachable)
        if text != trusted.extracted_text:
            raise SourceFactError("SOURCE_PROOF_REJECTED")
        receipt = source.bibliographic_metadata.get("original_receipt")
        if receipt is not None:
            if not isinstance(receipt, dict) or (
                receipt.get("source_id") != source.source_id
                or receipt.get("raw_path") != path.relative_to(root).as_posix()
                or receipt.get("extracted_path")
                != extracted.relative_to(root).as_posix()
                or receipt.get("raw_sha256")
                != source.content_hash.removeprefix("sha256:")
                or receipt.get("extracted_sha256")
                != trusted.extracted_sha256.removeprefix("sha256:")
                or receipt.get("charset") != trusted.charset
            ):
                raise SourceFactError("SOURCE_PROOF_REJECTED")
        chunks = {}
        for original in trusted.approved_chunks:
            chunk = _checked_model(original, Chunk)
            if (
                chunk.source_id != source.source_id
                or chunk.corpus_version != trusted.corpus_version
                or chunk.text != text
                or chunk.page_start is not None
                or chunk.page_end is not None
                or chunk.chunk_id in chunks
            ):
                raise SourceFactError("SOURCE_PROOF_REJECTED")
            chunks[chunk.chunk_id] = chunk.model_copy(deep=True)
    except (OSError, ValueError, LookupError):
        raise SourceFactError("SOURCE_PROOF_REJECTED") from None
    return CaptureProof(
        source.model_copy(deep=True),
        trusted.corpus_version,
        text,
        text_hash(text),
        MappingProxyType(chunks),
    )


ReviewReceipt: TypeAlias = (
    ReviewedFounderAnchor
    | ReviewedTechnologyAnchor
    | ReviewedMoatAnchor
    | ReviewedFinancialFact
    | FieldObservation
    | CriterionAssessment
)
ReviewDigest: TypeAlias = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CompanyObservationReviewRequest(BaseModel):
    """Closed review grammar; conclusions are supplied by the independent agent.

    The observation, original Source DTO and exact quoted span must be included
    in the request bytes before review. Hashes do not infer boolean/stage values.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["company-research-observation"]
    field: Literal[
        "domain_match", "is_listed", "exit_completed", "stage", "identity", "business"
    ]
    candidate_id: Text
    as_of: date
    subject: Text
    observation: FieldObservation
    source: Source
    span: SourceSpan | SourceTextSpan


class SourceBoundReview(BaseModel):
    """Independent reviewer input, never produced from a proposal by this module.

    ``request_sha256`` is SHA-256 of the exact request bytes; ``rubric_sha256``
    uses ``_digest(dict(rubric))`` (including status). ``snapshot_sha256`` uses
    ``frozen_snapshot_digest`` and therefore commits every Source hash/metadata,
    candidate, cutoff and generation. Spans are page or retained-text offsets.
    The controller authenticates the reviewer and supplies these records through
    a separate channel from the proposing/evaluating model.
    """

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )

    review_reference: Text
    request_sha256: ReviewDigest
    receipt_sha256: ReviewDigest
    candidate_id: Text
    subject: Text
    as_of: date
    snapshot_sha256: ReviewDigest
    rubric_sha256: ReviewDigest
    decision: Literal["accepted", "rejected", "unresolved"]
    spans: tuple[SourceSpan | SourceTextSpan, ...]
    rationale: Text

    @property
    def review_sha256(self) -> str:
        """Audit commitment to the entire independently supplied review record."""
        return _digest(self.model_dump(mode="json"))


def review_receipt_digest(receipt: ReviewReceipt) -> str:
    """Commit an existing domain receipt, including its exact type and values."""
    if type(receipt) not in (
        ReviewedFounderAnchor,
        ReviewedTechnologyAnchor,
        ReviewedMoatAnchor,
        ReviewedFinancialFact,
        FieldObservation,
        CriterionAssessment,
    ):
        raise SourceFactError("REVIEW_RECEIPT_REJECTED")
    try:
        if type(receipt) is CriterionAssessment:
            receipt = _checked_model(receipt, CriterionAssessment)
        payload = TypeAdapter(ReviewReceipt).dump_python(
            receipt, mode="json", warnings="error"
        )
    except (PydanticSerializationError, ValueError):
        raise SourceFactError("REVIEW_RECEIPT_REJECTED") from None
    return _digest([type(receipt).__name__, payload])


class SourceBoundReviewResolver:
    """Local integrity plus independently supplied semantic review, not inference.

    No default source authority or reviewer callback exists. Public claims alone
    remain unreviewed. PDF extraction and captured HTML/text stay separate;
    unknown source IDs return None, while changed known sources raise.
    Domain validators still own anchor/financial/eligibility contract checks.
    Four constructor inputs are separate trust commitments, not criterion knobs.
    """

    def __init__(
        self,
        snapshot: EvaluationSnapshot,
        rubric: Mapping[str, JsonValue],
        *,
        sources: Mapping[str, TrustedSource | TrustedCapture],
        reviews: tuple[SourceBoundReview, ...],
        approval_registry: "PinnedApprovalRegistry | None" = None,
    ) -> None:
        self._snapshot = checked_snapshot(snapshot)
        self._snapshot_sha256 = frozen_snapshot_digest(self._snapshot)
        self._rubric_sha256 = _digest(dict(rubric))
        rules = rubric.get("common_rules")
        dimensions = (
            rules.get("industry_evidence_dimensions")
            if isinstance(rules, dict)
            else None
        )
        pinned_core = False
        if approval_registry is not None:
            from skala_rag.scoring.approval_registry import PinnedApprovalRegistry

            if type(approval_registry) is not PinnedApprovalRegistry:
                raise SourceFactError("ARTIFACT_BINDING_REJECTED")
            approval = approval_registry.core_approval()
            pinned_core = (
                approval_registry.verify_core(approval) is True
                and core_artifact_digest(rubric) == approval.content_sha256
            )
            if not pinned_core:
                raise SourceFactError("ARTIFACT_BINDING_REJECTED")
        self._market_industry_allowed = (
            (rubric.get("status") == "approved" or pinned_core)
            and type(dimensions) is list
            and all(type(dimension) is str for dimension in dimensions)
            and "market" in dimensions
        )
        self._sources = {key: deepcopy(value) for key, value in sources.items()}
        if type(reviews) is not tuple or any(
            type(review) is not SourceBoundReview for review in reviews
        ):
            raise SourceFactError("REVIEW_REJECTED")
        self._reviews = tuple(
            SourceBoundReview.model_validate(deepcopy(review)) for review in reviews
        )
        keys = [(r.request_sha256, r.receipt_sha256) for r in self._reviews]
        if len(set(keys)) != len(keys):
            raise SourceFactError("AMBIGUOUS_REVIEW")
        for review in self._reviews:
            for span in review.spans:
                match span:
                    case SourceSpan():
                        _check_span(span)
                    case SourceTextSpan():
                        _text(span.source_id)
                        _text(span.quote)
                    case unreachable:
                        assert_never(unreachable)
            if (
                review.snapshot_sha256 != self._snapshot_sha256
                or review.rubric_sha256 != self._rubric_sha256
                or review.candidate_id != self._snapshot.candidate_id
                or review.as_of != self._snapshot.as_of
            ):
                raise SourceFactError("REVIEW_BINDING_REJECTED")

    def verify_snapshot(
        self, snapshot: EvaluationSnapshot, rubric: Mapping[str, JsonValue]
    ) -> None:
        """Graph admission check: reject any change to the captured context."""
        if (
            frozen_snapshot_digest(checked_snapshot(snapshot)) != self._snapshot_sha256
            or _digest(dict(rubric)) != self._rubric_sha256
        ):
            raise SourceFactError("REVIEW_BINDING_REJECTED")
        for source_id in self._sources:
            self.verify_source(source_id)

    def verify_source(self, source_id: str) -> SourceProof | CaptureProof | None:
        """Re-read known original bytes on every use; never trust a cached PASS."""
        trusted = self._sources.get(source_id)
        if trusted is None:
            return None
        match trusted:
            case TrustedSource():
                proof = verify_original_source(trusted)
            case TrustedCapture():
                proof = verify_original_capture(trusted)
            case unreachable:
                assert_never(unreachable)
        source = self._snapshot.sources.get(source_id)
        if (
            source is None
            or proof.source.model_dump(mode="json") != source.model_dump(mode="json")
            or proof.corpus_version != self._snapshot.corpus_version
            or not check_as_of(proof.source, self._snapshot.as_of).admitted
            or any(
                chunk_id not in self._snapshot.chunks
                or chunk.model_dump(mode="json")
                != self._snapshot.chunks[chunk_id].model_dump(mode="json")
                for chunk_id, chunk in proof.chunks.items()
            )
        ):
            raise SourceFactError("SNAPSHOT_SOURCE_REJECTED")
        return proof

    def resolve_review(
        self, request: bytes, receipt: ReviewReceipt, *, subject: str
    ) -> SourceBoundReview | None:
        """Return exact review/audit, or None for unreviewed/unresolved sources.

        Only ``decision == "accepted"`` can support downstream use; rejected and
        unresolved decisions never mean a false fact, zero value or N/A. A
        wrapper must still run its existing domain validator. No receipt is
        minted here, and no review is added to TrustedReviewRegistry.
        """
        if type(request) is not bytes or not request:
            raise SourceFactError("REVIEW_REQUEST_REJECTED")
        _text(subject)
        request_sha = content_hash(request).removeprefix("sha256:")
        receipt_sha = review_receipt_digest(receipt)
        review = next(
            (
                r
                for r in self._reviews
                if r.request_sha256 == request_sha and r.receipt_sha256 == receipt_sha
            ),
            None,
        )
        if review is None:
            return None
        if review.subject != subject:
            raise SourceFactError("REVIEW_BINDING_REJECTED")
        if not review.spans:
            match review.decision:
                case "accepted":
                    return None
                case "rejected" | "unresolved":
                    return review.model_copy(deep=True)
                case unreachable:
                    assert_never(unreachable)
        for span in review.spans:
            proof = self.verify_source(span.source_id)
            if proof is None:
                return None
            match (proof, span):
                case (SourceProof(), SourceSpan()):
                    verify_span(proof, span)
                case (CaptureProof(), SourceTextSpan()):
                    if (
                        type(span.start) is not int
                        or type(span.end) is not int
                        or not 0 <= span.start < span.end <= len(proof.text)
                        or proof.text[span.start : span.end] != span.quote
                        or proof.text_sha256 != span.text_sha256
                        or (
                            span.chunk_id is not None
                            and span.chunk_id not in proof.chunks
                        )
                    ):
                        raise SourceFactError("SOURCE_SPAN_REJECTED")
                case (SourceProof(), SourceTextSpan()) | (CaptureProof(), SourceSpan()):
                    raise SourceFactError("SOURCE_SPAN_REJECTED")
                case unreachable:
                    assert_never(unreachable)
        match review.decision:
            case "rejected" | "unresolved":
                return review.model_copy(deep=True)
            case "accepted":
                pass
            case unreachable:
                assert_never(unreachable)
        match receipt:
            case FieldObservation(value=StageObservation(normalized_round="unknown")):
                return None
            case FieldObservation():
                try:
                    observation_context = (
                        CompanyObservationReviewRequest.model_validate_json(
                            request, context={"execution_mode": "fixture"}
                        )
                    )
                except ValidationError:
                    return None
                if (
                    len(review.spans) != 1
                    or observation_context.field != receipt.field
                    or observation_context.candidate_id != self._snapshot.candidate_id
                    or observation_context.as_of != self._snapshot.as_of
                    or observation_context.subject != subject
                    or review_receipt_digest(observation_context.observation)
                    != receipt_sha
                    or observation_context.span != review.spans[0]
                    or receipt.source_id != review.spans[0].source_id
                    or receipt.excerpt != review.spans[0].quote
                    or observation_context.source.model_dump(mode="json")
                    != self._snapshot.sources[receipt.source_id].model_dump(mode="json")
                    or receipt.identity_basis == "name_only"
                    or (
                        receipt.event_date is not None
                        and receipt.event_date > self._snapshot.as_of
                    )
                    or any(
                        evidence.conflicts_with
                        for evidence in self._snapshot.evidence.values()
                        if evidence.source_id == receipt.source_id
                        and evidence.excerpt == receipt.excerpt
                    )
                ):
                    raise SourceFactError("EVIDENCE_BINDING_REJECTED")
                return review.model_copy(deep=True)
            case ReviewedFinancialFact():
                evidence_ids = (receipt.evidence.evidence_id,)
            case CriterionAssessment():
                if not receipt.criterion_id.startswith("market."):
                    raise SourceFactError("REVIEW_RECEIPT_REJECTED")
                evidence_ids = tuple(receipt.evidence_ids)
            case (
                ReviewedFounderAnchor()
                | ReviewedTechnologyAnchor()
                | ReviewedMoatAnchor()
            ):
                evidence_ids = receipt.evidence_ids
            case unreachable:
                assert_never(unreachable)
        if not evidence_ids:
            return None
        market_request_bound = False
        market_excerpts: dict[str, JsonValue] = {}
        if type(receipt) is CriterionAssessment:
            try:
                context = TypeAdapter(JsonValue).validate_json(request)
            except ValueError:
                raise SourceFactError("REVIEW_REQUEST_REJECTED") from None
            if isinstance(context, dict):
                target, links = context.get("target"), context.get("links")
                excerpts = context.get("excerpts")
                if isinstance(excerpts, dict):
                    market_excerpts = excerpts
                elif len(evidence_ids) == 1:
                    market_excerpts = {evidence_ids[0]: context.get("excerpt")}
                market_request_bound = (
                    isinstance(target, dict)
                    and isinstance(target.get("segment_id"), str)
                    and bool(target["segment_id"].strip())
                    and isinstance(target.get("geographies"), list)
                    and bool(target["geographies"])
                    and all(
                        isinstance(geography, str) and bool(geography.strip())
                        for geography in target["geographies"]
                    )
                    and isinstance(links, dict)
                    and context.get("criterion") == receipt.criterion_id
                    and all(
                        isinstance(links.get(eid), dict)
                        and links[eid].get("segment_id") == target["segment_id"]
                        for eid in evidence_ids
                    )
                )
            if not market_request_bound:
                raise SourceFactError("EVIDENCE_BINDING_REJECTED")
        covered: set[SourceSpan | SourceTextSpan] = set()
        for eid in evidence_ids:
            evidence = self._snapshot.evidence.get(eid)
            if (
                evidence is None
                or (
                    type(receipt) is CriterionAssessment
                    and market_excerpts.get(eid) != evidence.excerpt
                )
                or (
                    type(receipt) is CriterionAssessment
                    and receipt.criterion_id not in evidence.criterion_ids
                )
                or not (
                    (
                        evidence.scope == "company"
                        and evidence.candidate_id == self._snapshot.candidate_id
                    )
                    or (
                        type(receipt) is CriterionAssessment
                        and self._market_industry_allowed
                        and market_request_bound
                        and evidence.scope == "industry"
                        and evidence.candidate_id is None
                    )
                )
                or evidence.conflicts_with
                or any(
                    when is not None and when > self._snapshot.as_of
                    for when in (evidence.event_date, evidence.value_as_of)
                )
            ):
                raise SourceFactError("EVIDENCE_BINDING_REJECTED")
            spans = {
                s
                for s in review.spans
                if s.source_id == evidence.source_id
                and s.quote == evidence.excerpt
                and any(
                    p.chunk_id == s.chunk_id
                    and (s.chunk_id is not None or p.method in ("web", "api", "manual"))
                    for p in evidence.provenance
                )
            }
            if not spans:
                raise SourceFactError("EVIDENCE_BINDING_REJECTED")
            covered.update(spans)
        if covered != set(review.spans):
            raise SourceFactError("EVIDENCE_BINDING_REJECTED")
        return review.model_copy(deep=True)
