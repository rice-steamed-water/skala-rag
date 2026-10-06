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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from skala_rag.agents.moat_verification import (
    _digest,
    core_artifact_digest,
    frozen_snapshot_digest,
)
from skala_rag.agents.technology_verification import _exact_tree, checked_snapshot
from skala_rag.contracts import Chunk, Source
from skala_rag.contracts.evaluation import EvaluationSnapshot
from skala_rag.rag.corpus import ManifestDocument
from skala_rag.rag.extraction import PageChunkSettings, extract_pdf
from skala_rag.rag.reviewed_extraction import verify_text_review
from skala_rag.rag.text_review import text_hash
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


def _texts(values: object, container: type = tuple) -> None:
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
        spec = rubric["dimensions"]["technology"]["criteria"][CRITERION]
        if not isinstance(spec, Mapping) or not isinstance(spec["anchors"], Mapping):
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
