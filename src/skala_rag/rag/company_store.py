"""Retained originals and derived report retrieval, not an approval authority.

Callers supply admitted source material and exact report validation results.
All writes rebuild one immutable SQLite index; CURRENT moves only after readback.
Report hits expose original evidence separately from labeled prior interpretation.
"""

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field

from skala_rag.contracts import (
    Candidate,
    Chunk,
    Evidence,
    ReportDraft,
    ReportJudgement,
    RetrievalRecord,
    Source,
    ValidationResult,
)
from skala_rag.contracts.common import Contract, ISODate, Text
from skala_rag.rag.corpus import CorpusManifest, ManifestDocument, manifest_hash
from skala_rag.rag.extraction import PageChunkSettings, extract_pdf
from skala_rag.rag.index_v3 import (
    EmbeddingVector,
    Encoder,
    IndexMetadata,
    IndexSettings,
    build_index_plan,
    write_index,
)
from skala_rag.rag.local_bge_validation import load_local_encoder
from skala_rag.rag.query_local import LocalQueryEncoder
from skala_rag.rag.reviewed_extraction import verify_text_review
from skala_rag.rag.sqlite_index import SQLiteIndexStore
from skala_rag.reporting.v3_pipeline import ReportRunV3
from skala_rag.reporting.validator import TOKEN, artifact_hash

SCHEMA = "company-store-1"


class CompanyStoreError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _hash(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _json(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def evidence_fingerprint(evidence: Evidence, source: Source) -> str:
    """Bind a report's evaluated Evidence to its exact original source bytes.

    Retention changes the local file path, not this fingerprint. The trusted
    caller captures it from the evaluated context, never from generated prose.
    """
    if evidence.source_id != source.source_id:
        raise CompanyStoreError("EVIDENCE_SOURCE_MISMATCH")
    return _hash(
        _json(
            {
                "evidence": evidence.model_dump(mode="json"),
                "source_content_hash": source.content_hash,
            }
        )
    )


class CompanyIdentity(Contract):
    """Every stored identity field is explicitly linked to original evidence."""

    candidate: Candidate
    field_evidence_ids: dict[Text, list[Text]]


@dataclass(frozen=True, slots=True)
class RetainedSource:
    document: ManifestDocument
    source: Source
    content: bytes
    chunks: tuple[Chunk, ...]
    evidence: tuple[Evidence, ...]
    retrieval_records: tuple[RetrievalRecord, ...]
    page_settings: PageChunkSettings | None = None


@dataclass(frozen=True, slots=True)
class ValidatedReport:
    """Trusted caller descriptor; results are observations, never new authority."""

    run_id: str
    candidate_id: str
    as_of: date
    retrieved_at: datetime
    generation_model: str
    generation_revision: str
    reviewer: str
    permission_note: str
    result: ReportRunV3
    evidence_hashes: Mapping[str, str]
    parent_report_ids: tuple[str, ...] = ()


class ReportProvenance(Contract):
    run_id: Text
    candidate_id: Text
    as_of: ISODate
    generation_model: Text
    generation_revision: Text
    draft: ReportDraft
    validation: ValidationResult
    judgement: ReportJudgement
    pdf_validation: ValidationResult
    validation_receipt_hashes: tuple[Text, ...]
    validated_artifact_hash: Text
    content_hash: Text
    source_ids: tuple[Text, ...]
    original_evidence_ids: tuple[Text, ...]
    original_evidence_hashes: dict[Text, Text]
    original_source_ids: tuple[Text, ...]
    parent_report_ids: tuple[Text, ...]


class StoreManifest(Contract):
    model_config = ConfigDict(frozen=True)
    schema_version: Literal["company-store-1"]
    version: Text
    settings_snapshot: Text
    documents: dict[Text, ManifestDocument]
    sources: dict[Text, Source]
    chunks: dict[Text, Chunk]
    evidence: dict[Text, Evidence]
    retrieval_records: dict[Text, RetrievalRecord]
    companies: dict[Text, CompanyIdentity]
    reports: dict[Text, ReportProvenance]
    index_metadata: IndexMetadata
    index_hash: Text
    indexing_seconds: float = Field(ge=0)
    document_count: int = Field(ge=1)


@dataclass(frozen=True, slots=True)
class StoreHit:
    chunk: Chunk
    source: Source
    similarity: float
    original_evidence: tuple[Evidence, ...]
    prior_report: ReportProvenance | None


@dataclass(frozen=True, slots=True)
class StoreSnapshot:
    """Serialized ownership prevents later callers from mutating a frozen view."""

    root: Path
    payload: bytes

    @property
    def manifest(self) -> StoreManifest:
        return StoreManifest.model_validate_json(self.payload)

    def search(
        self,
        query: EmbeddingVector,
        *,
        top_k: int,
        current_report_id: str | None = None,
    ) -> tuple[StoreHit, ...]:
        manifest = self.manifest
        store = SQLiteIndexStore(
            self.root / "versions" / manifest.version / "index.sqlite"
        )
        hits = store.search(query, expected=manifest.index_metadata, top_k=top_k)
        results = []
        for hit in hits:
            report = next(
                (
                    r
                    for r in manifest.reports.values()
                    if hit.source.source_id in r.source_ids
                ),
                None,
            )
            if report is not None:
                if current_report_id is not None and _has_ancestor(
                    report.draft.report_id, current_report_id, manifest.reports
                ):
                    continue
                ids = set(TOKEN.findall(hit.chunk.text))
            else:
                ids = {
                    e.evidence_id
                    for e in manifest.evidence.values()
                    if e.source_id == hit.source.source_id
                    and e.excerpt in hit.chunk.text
                }
            originals = _evidence_closure(ids, manifest.evidence, manifest.sources)
            results.append(
                StoreHit(
                    hit.chunk,
                    hit.source,
                    hit.similarity,
                    tuple(manifest.evidence[e] for e in originals),
                    report,
                )
            )
        return tuple(results)


def _has_ancestor(
    report_id: str,
    target: str,
    reports: dict[str, ReportProvenance],
    visiting: frozenset[str] = frozenset(),
) -> bool:
    if report_id in visiting:
        raise CompanyStoreError("CIRCULAR_REPORT_LINEAGE")
    if report_id == target:
        return True
    if report_id not in reports:
        raise CompanyStoreError("UNRESOLVED_REPORT_LINEAGE")
    return any(
        _has_ancestor(parent, target, reports, visiting | {report_id})
        for parent in reports[report_id].parent_report_ids
    )


def _evidence_closure(
    ids: set[str],
    evidence: dict[str, Evidence],
    sources: dict[str, Source],
) -> tuple[str, ...]:
    resolved: set[str] = set()

    def visit(eid: str, visiting: frozenset[str]) -> None:
        if eid in visiting:
            raise CompanyStoreError("CIRCULAR_EVIDENCE_LINEAGE")
        if eid in resolved:
            return
        item = evidence.get(eid)
        if item is None or item.source_id not in sources:
            raise CompanyStoreError("UNRESOLVED_ORIGINAL_LINEAGE")
        source = sources[item.source_id]
        if source.bibliographic_metadata.get("generated_report"):
            raise CompanyStoreError("REPORT_CANNOT_CREATE_EVIDENCE")
        if item.evidence_kind != "reported" and not item.supporting_evidence_ids:
            raise CompanyStoreError("UNRESOLVED_ORIGINAL_LINEAGE")
        for support in item.supporting_evidence_ids:
            visit(support, visiting | {eid})
        resolved.add(eid)

    for eid in sorted(ids):
        visit(eid, frozenset())
    return tuple(sorted(resolved))


def _put(values, key: str, value) -> None:
    if key in values and values[key] != value:
        raise CompanyStoreError("IMMUTABLE_ID_CONFLICT")
    values[key] = value


class CompanyStore:
    def __init__(
        self,
        root: Path,
        *,
        settings: IndexSettings,
        encoder: Encoder,
    ) -> None:
        self.root = root.resolve()
        self.settings = IndexSettings(**settings.snapshot())
        self.encoder = encoder

    @classmethod
    def from_local(
        cls,
        root: Path,
        *,
        model_path: Path,
        receipt_path: Path,
    ) -> "CompanyStore":
        """Read existing local assets only. Missing assets never trigger download."""
        receipt = json.loads(receipt_path.read_bytes())
        settings = IndexSettings(**json.loads(receipt["metadata"]["settings_snapshot"]))
        encoder = load_local_encoder(
            model_path,
            device=settings.embedding_settings["device"],
            receipt_path=receipt_path,
        )
        return cls(root, settings=settings, encoder=encoder)

    def search_local(
        self,
        query: str,
        *,
        top_k: int,
        timeout_seconds: float,
        snapshot: StoreSnapshot | None = None,
        current_report_id: str | None = None,
    ) -> tuple[StoreHit, ...]:
        """Use the existing local query bridge; no hosted embedding fallback."""
        from skala_rag.rag.local_bge_validation import LocalEncoder

        if not isinstance(self.encoder, LocalEncoder):
            raise CompanyStoreError("LOCAL_QUERY_ENCODER_REQUIRED")
        selected = snapshot if snapshot is not None else self.open()
        if selected is None:
            return ()
        vector = LocalQueryEncoder(self.encoder, settings=self.settings).encode_once(
            query,
            settings=self.settings,
            timeout_seconds=timeout_seconds,
        )
        return selected.search(
            EmbeddingVector(
                "query", vector.model_id, vector.model_revision, vector.values
            ),
            top_k=top_k,
            current_report_id=current_report_id,
        )

    @contextmanager
    def _writer(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        # Keep the inode: unlinking a lock file permits two independently locked
        # inodes. flock is released by the OS even on process interruption.
        with (self.root / ".writer.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CompanyStoreError("WRITER_CONFLICT") from exc
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def open(self, *, version: str | None = None) -> StoreSnapshot | None:
        """Reopen an immutable version, checking retained bytes and SQLite hash."""
        pointer = self.root / "CURRENT"
        if version is None:
            if not pointer.exists():
                return None
            version = pointer.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{64}", version) is None:
            raise CompanyStoreError("INVALID_STORE_VERSION")
        folder = self.root / "versions" / version
        payload = (folder / "manifest.json").read_bytes()
        manifest = StoreManifest.model_validate_json(payload)
        if manifest.version != version:
            raise CompanyStoreError("MANIFEST_VERSION_MISMATCH")
        state = {
            name: dict(getattr(manifest, name))
            for name in (
                "documents",
                "sources",
                "chunks",
                "evidence",
                "retrieval_records",
                "companies",
                "reports",
            )
        }
        if self._version(state) != version:
            raise CompanyStoreError("MANIFEST_HASH_MISMATCH")
        if manifest.settings_snapshot != self.settings._payload:
            raise CompanyStoreError("STORE_SETTINGS_MISMATCH")
        if _hash((folder / "index.sqlite").read_bytes()) != manifest.index_hash:
            raise CompanyStoreError("INDEX_HASH_MISMATCH")
        store = SQLiteIndexStore(folder / "index.sqlite")
        if store.read_metadata(manifest.index_metadata.index_version) != (
            manifest.index_metadata
        ):
            raise CompanyStoreError("INDEX_READBACK_MISMATCH")
        self._validate_closure(state)
        for source in manifest.sources.values():
            if source.local_path is None:
                raise CompanyStoreError("MISSING_RETAINED_SOURCE")
            path = (self.root / source.local_path).resolve()
            if not path.is_relative_to(self.root / "data/local"):
                raise CompanyStoreError("RETAINED_PATH_ESCAPE")
            if _hash(path.read_bytes()) != source.content_hash:
                raise CompanyStoreError("SOURCE_HASH_MISMATCH")
        _evidence_closure(set(manifest.evidence), manifest.evidence, manifest.sources)
        for rid, report in manifest.reports.items():
            for parent in report.parent_report_ids:
                if _has_ancestor(parent, rid, manifest.reports):
                    raise CompanyStoreError("CIRCULAR_REPORT_LINEAGE")
        return StoreSnapshot(self.root, payload)

    def _state(self):
        previous = self.open()
        if previous is None:
            return {
                name: {}
                for name in (
                    "documents",
                    "sources",
                    "chunks",
                    "evidence",
                    "retrieval_records",
                    "companies",
                    "reports",
                )
            }
        manifest = previous.manifest
        state = {
            name: dict(getattr(manifest, name))
            for name in (
                "documents",
                "sources",
                "chunks",
                "evidence",
                "retrieval_records",
                "companies",
                "reports",
            )
        }
        state["chunks"] = {
            key: chunk.model_copy(update={"corpus_version": SCHEMA})
            for key, chunk in state["chunks"].items()
        }
        return state

    def ingest_sources(
        self,
        materials: Sequence[RetainedSource],
        *,
        companies: Sequence[CompanyIdentity] = (),
    ) -> StoreSnapshot:
        """Batch already-admitted originals; no fetching or automatic approvals."""
        with self._writer():
            state = self._state()
            contents: dict[str, bytes] = {}
            for material in materials:
                source = Source.model_validate_json(material.source.model_dump_json())
                doc = ManifestDocument.model_validate_json(
                    material.document.model_dump_json()
                )
                if (
                    source.bibliographic_metadata.get("generated_report")
                    or ("company_report_provenance" in source.bibliographic_metadata)
                    or any(
                        r.content_hash == source.content_hash
                        for r in state["reports"].values()
                    )
                ):
                    raise CompanyStoreError("REPORT_REQUIRES_VALIDATED_INGESTION")
                if (
                    source.source_id != doc.source_id
                    or source.content_hash != _hash(material.content)
                    or source.content_hash != doc.content_hash
                    or source.local_path != doc.local_path
                    or not doc.approved
                    or not doc.reviewer
                    or not (
                        doc.extraction_status == "ok"
                        or (
                            doc.extraction_status == "partial"
                            and doc.text_index_review is not None
                        )
                    )
                ):
                    raise CompanyStoreError("SOURCE_ACCEPTANCE_MISMATCH")
                chunks = tuple(
                    Chunk.model_validate_json(c.model_dump_json())
                    for c in material.chunks
                )
                if not chunks:
                    raise CompanyStoreError("SOURCE_CHUNKS_REQUIRED")
                suffix = Path(doc.local_path).suffix.lower()
                if suffix == ".pdf":
                    if material.page_settings is None:
                        raise CompanyStoreError("PDF_EXTRACTION_SETTINGS_REQUIRED")
                    extracted = extract_pdf(
                        material.content,
                        doc,
                        source,
                        corpus_version=chunks[0].corpus_version,
                        schema_version=chunks[0].schema_version,
                        settings=material.page_settings,
                        sections_by_page={
                            c.page_start: c.section
                            for c in chunks
                            if c.page_start is not None and c.section is not None
                        },
                        embedding_model=self.settings.model_id,
                        embedding_revision=self.settings.model_revision,
                        execution_mode="live",
                    )
                    if doc.text_index_review is not None:
                        verify_text_review(extracted, doc.text_index_review)
                    if (
                        extracted.status != doc.extraction_status
                        or extracted.chunks != chunks
                        or asdict(material.page_settings)
                        != self.settings.chunk_settings
                    ):
                        raise CompanyStoreError("EXTRACTION_MISMATCH")
                else:
                    if doc.text_index_review is not None:
                        raise CompanyStoreError("PDF_TEXT_REVIEW_REQUIRED")
                    text = material.content.decode("utf-8")
                    if any(c.text not in text for c in chunks):
                        raise CompanyStoreError("CHUNK_TEXT_MISMATCH")
                path = f"data/local/objects/{source.content_hash[7:]}{suffix}"
                source = source.model_copy(update={"local_path": path})
                doc = doc.model_copy(update={"local_path": path})
                _put(state["sources"], source.source_id, source)
                _put(state["documents"], source.source_id, doc)
                contents[path] = material.content
                for chunk in chunks:
                    if (
                        chunk.source_id != source.source_id
                        or chunk.scope != doc.scope
                        or set(chunk.candidate_ids) != set(doc.candidate_ids)
                        or chunk.embedding_model != self.settings.model_id
                        or chunk.embedding_revision != self.settings.model_revision
                    ):
                        raise CompanyStoreError("CHUNK_IDENTITY_MISMATCH")
                    # Corpus versions are owned by the complete immutable index.
                    normalized = chunk.model_copy(update={"corpus_version": SCHEMA})
                    _put(state["chunks"], chunk.chunk_id, normalized)
                for evidence in material.evidence:
                    item = Evidence.model_validate_json(evidence.model_dump_json())
                    if (
                        item.source_id != source.source_id
                        or item.scope != doc.scope
                        or (
                            item.scope == "company"
                            and item.candidate_id not in doc.candidate_ids
                        )
                        or (item.scope == "industry" and item.candidate_id is not None)
                        or not any(item.excerpt in c.text for c in chunks)
                    ):
                        raise CompanyStoreError("EVIDENCE_SOURCE_MISMATCH")
                    _put(state["evidence"], item.evidence_id, item)
                for record in material.retrieval_records:
                    item = RetrievalRecord.model_validate_json(record.model_dump_json())
                    _put(state["retrieval_records"], item.retrieval_id, item)
            for identity in companies:
                item = CompanyIdentity.model_validate_json(identity.model_dump_json())
                _put(state["companies"], item.candidate.candidate_id, item)
            return self._publish(state, contents)

    def ingest_report(self, report: ValidatedReport) -> StoreSnapshot:
        """Retain validated prose as retrieval only; never mint report Evidence."""
        result = report.result
        if (
            result.status != "completed"
            or result.warning
            or result.error_code is not None
            or result.draft is None
            or result.validation is None
            or result.judgement is None
            or result.pdf_validation is None
        ):
            raise CompanyStoreError("REPORT_NOT_ACCEPTED")
        draft = ReportDraft.model_validate_json(result.draft.model_dump_json())
        validation = ValidationResult.model_validate_json(
            result.validation.model_dump_json()
        )
        judgement = ReportJudgement.model_validate_json(
            result.judgement.model_dump_json()
        )
        pdf = ValidationResult.model_validate_json(
            result.pdf_validation.model_dump_json()
        )
        digest = artifact_hash(draft)
        if (
            not validation.valid
            or not pdf.valid
            or judgement.verdict != "pass"
            or any(f.severity != "stub" for f in judgement.findings)
            or judgement.revision_instructions
            or any(
                r.context_id != draft.context_id for r in (validation, judgement, pdf)
            )
            or result.context_id != draft.context_id
            or result.revisions != draft.revision
            or validation.artifact_hash != digest
            or pdf.artifact_hash != digest
            or judgement.judged_artifact_hash != digest
            or set(TOKEN.findall(draft.markdown)) != set(draft.cited_evidence_ids)
        ):
            raise CompanyStoreError("REPORT_VALIDATION_MISMATCH")
        with self._writer():
            state = self._state()
            if report.candidate_id not in state["companies"]:
                raise CompanyStoreError("REPORT_COMPANY_UNRESOLVED")
            for parent in report.parent_report_ids:
                if parent == draft.report_id or _has_ancestor(
                    parent, draft.report_id, state["reports"]
                ):
                    raise CompanyStoreError("CIRCULAR_REPORT_LINEAGE")
            originals = _evidence_closure(
                set(draft.cited_evidence_ids), state["evidence"], state["sources"]
            )
            evidence_hashes = {
                eid: evidence_fingerprint(
                    state["evidence"][eid],
                    state["sources"][state["evidence"][eid].source_id],
                )
                for eid in originals
            }
            if dict(report.evidence_hashes) != evidence_hashes:
                raise CompanyStoreError("REPORT_EVIDENCE_HASH_MISMATCH")
            source_ids = {
                state["evidence"][eid].source_id for eid in draft.cited_evidence_ids
            }
            if not originals or source_ids != set(draft.reference_source_ids):
                raise CompanyStoreError("REPORT_REFERENCE_MISMATCH")
            content = draft.markdown.encode("utf-8")
            content_hash = _hash(content)
            path = f"data/local/objects/{content_hash[7:]}.md"
            # Citation-terminated claims preserve tokens; a claim with several
            # scopes is rejected, not guessed into company scope. Each scope has
            # its own Source view of the same immutable full Markdown bytes.
            claims = re.findall(
                r"[^\n]*?\[@evidence:[^\]\s]+\]"
                r"(?:[ \t]*\[@evidence:[^\]\s]+\])*",
                draft.markdown,
            )
            groups: dict[tuple[str, str | None], list[str]] = {}
            for claim in claims:
                refs = [state["evidence"][eid] for eid in TOKEN.findall(claim)]
                scopes = {(e.scope, e.candidate_id) for e in refs}
                if len(scopes) != 1:
                    raise CompanyStoreError("MIXED_CLAIM_SCOPE")
                groups.setdefault(next(iter(scopes)), []).append(claim)
            generated_sources = []
            receipts = tuple(
                _hash(_json(r.model_dump(mode="json")))
                for r in (validation, judgement, pdf)
            )
            for (scope, candidate), texts in sorted(
                groups.items(), key=lambda pair: str(pair[0])
            ):
                sid = (
                    "report-"
                    + hashlib.sha256(
                        _json([draft.report_id, content_hash, scope, candidate])
                    ).hexdigest()
                )
                generated_sources.append(sid)
                metadata = {
                    "schema_version": SCHEMA,
                    "generated_report": True,
                    "run_id": report.run_id,
                    "candidate_id": report.candidate_id,
                    "as_of": report.as_of.isoformat(),
                    "cited_evidence_ids": draft.cited_evidence_ids,
                    "reference_source_ids": draft.reference_source_ids,
                    "validation_receipt_hashes": list(receipts),
                    "original_evidence_ids": list(originals),
                    "original_evidence_hashes": evidence_hashes,
                    "original_source_ids": sorted(
                        {state["evidence"][e].source_id for e in originals}
                    ),
                    "report_id": draft.report_id,
                }
                source = Source(
                    schema_version=SCHEMA,
                    source_id=sid,
                    title=draft.report_id,
                    source_kind="report",
                    local_path=path,
                    retrieved_at=report.retrieved_at,
                    content_hash=content_hash,
                    language=state["sources"][sorted(source_ids)[0]].language,
                    access_notes=report.permission_note,
                    bibliographic_metadata={
                        "generated_report": True,
                        "company_report_provenance": metadata,
                    },
                )
                doc = ManifestDocument(
                    schema_version=SCHEMA,
                    document_id=sid,
                    source_id=sid,
                    local_path=path,
                    content_hash=content_hash,
                    title=source.title,
                    language=source.language,
                    permission_note=report.permission_note,
                    candidate_ids=() if candidate is None else (candidate,),
                    scope=scope,
                    extraction_status="ok",
                    reviewer=report.reviewer,
                    approved=True,
                )
                _put(state["sources"], sid, source)
                _put(state["documents"], sid, doc)
                for number, text in enumerate(texts):
                    cid = f"{sid}-claim-{number}"
                    chunk = Chunk(
                        schema_version=SCHEMA,
                        chunk_id=cid,
                        source_id=sid,
                        corpus_version=SCHEMA,
                        text=text,
                        locator=f"{path}#claim={number}",
                        candidate_ids=list(doc.candidate_ids),
                        scope=scope,
                        language=source.language,
                        embedding_model=self.settings.model_id,
                        embedding_revision=self.settings.model_revision,
                    )
                    _put(state["chunks"], cid, chunk)
            provenance = ReportProvenance(
                schema_version=SCHEMA,
                run_id=report.run_id,
                candidate_id=report.candidate_id,
                as_of=report.as_of,
                generation_model=report.generation_model,
                generation_revision=report.generation_revision,
                draft=draft,
                validation=validation,
                judgement=judgement,
                pdf_validation=pdf,
                validation_receipt_hashes=receipts,
                validated_artifact_hash=digest,
                content_hash=content_hash,
                source_ids=tuple(generated_sources),
                original_evidence_ids=originals,
                original_evidence_hashes=evidence_hashes,
                original_source_ids=tuple(
                    sorted({state["evidence"][e].source_id for e in originals})
                ),
                parent_report_ids=report.parent_report_ids,
            )
            _put(state["reports"], draft.report_id, provenance)
            return self._publish(state, {path: content})

    def _validate_closure(self, state) -> None:
        _evidence_closure(set(state["evidence"]), state["evidence"], state["sources"])
        for evidence in state["evidence"].values():
            if not evidence.provenance:
                raise CompanyStoreError("EVIDENCE_PROVENANCE_REQUIRED")
            for provenance in evidence.provenance:
                record = state["retrieval_records"].get(provenance.retrieval_id)
                if (
                    record is None
                    or record.status != "ok"
                    or evidence.source_id not in record.source_ids
                    or evidence.evidence_id not in record.evidence_ids
                ):
                    raise CompanyStoreError("RETRIEVAL_LINEAGE_MISMATCH")
                if provenance.chunk_id is not None:
                    chunk = state["chunks"].get(provenance.chunk_id)
                    if (
                        chunk is None
                        or chunk.chunk_id not in record.chunk_ids
                        or chunk.source_id != evidence.source_id
                        or evidence.excerpt not in chunk.text
                        or evidence.locator != chunk.locator
                    ):
                        raise CompanyStoreError("RAG_LINEAGE_MISMATCH")
        for record in state["retrieval_records"].values():
            if (
                not set(record.source_ids) <= set(state["sources"])
                or not set(record.chunk_ids) <= set(state["chunks"])
                or not set(record.evidence_ids) <= set(state["evidence"])
            ):
                raise CompanyStoreError("RETRIEVAL_CLOSURE_MISMATCH")
        for identity in state["companies"].values():
            candidate = identity.candidate
            fields = {"canonical_name", "country"}
            fields.update("alias:" + alias for alias in candidate.aliases)
            fields.update("legal:" + key for key in candidate.legal_identifiers)
            if candidate.homepage_url is not None:
                fields.add("homepage_url")
            if set(identity.field_evidence_ids) != fields or not set(
                candidate.discovery_source_ids
            ) <= set(state["sources"]):
                raise CompanyStoreError("IDENTITY_LINEAGE_MISMATCH")
            for ids in identity.field_evidence_ids.values():
                if not ids:
                    raise CompanyStoreError("IDENTITY_LINEAGE_MISMATCH")
                for eid in ids:
                    evidence = state["evidence"].get(eid)
                    if (
                        evidence is None
                        or evidence.evidence_kind != "reported"
                        or evidence.candidate_id != candidate.candidate_id
                        or evidence.scope != "company"
                    ):
                        raise CompanyStoreError("IDENTITY_LINEAGE_MISMATCH")

    def _version(self, state) -> str:
        encoded = _json(
            {
                name: {
                    key: (
                        item.model_copy(update={"corpus_version": SCHEMA})
                        if name == "chunks"
                        else item
                    ).model_dump(mode="json")
                    for key, item in values.items()
                }
                for name, values in state.items()
            }
        )
        return hashlib.sha256(
            encoded + self.settings._payload.encode("utf-8")
        ).hexdigest()

    def _publish(self, state, contents: dict[str, bytes]) -> StoreSnapshot:
        self._validate_closure(state)
        version = self._version(state)
        previous = self.open()
        if previous is not None and previous.manifest.version == version:
            return previous
        state["chunks"] = {
            key: chunk.model_copy(update={"corpus_version": version})
            for key, chunk in state["chunks"].items()
        }
        corpus = CorpusManifest(
            schema_version=SCHEMA,
            corpus_version=version,
            documents=tuple(state["documents"][s] for s in sorted(state["documents"])),
        )
        extractions = {}
        for doc in corpus.documents:
            if doc.text_index_review is None:
                continue
            content = contents.get(doc.local_path)
            if content is None:
                content = (self.root / doc.local_path).read_bytes()
            chunks = [
                c for c in state["chunks"].values() if c.source_id == doc.source_id
            ]
            extractions[doc.document_id] = extract_pdf(
                content,
                doc,
                state["sources"][doc.source_id],
                corpus_version=version,
                schema_version=chunks[0].schema_version,
                settings=PageChunkSettings(**doc.text_index_review.extraction_settings),
                sections_by_page={
                    c.page_start: c.section
                    for c in chunks
                    if c.page_start is not None and c.section is not None
                },
                embedding_model=self.settings.model_id,
                embedding_revision=self.settings.model_revision,
                execution_mode="live",
            )
        plan = build_index_plan(
            manifest=corpus,
            expected_corpus_hash=manifest_hash(corpus),
            sources=state["sources"],
            chunks=tuple(state["chunks"].values()),
            settings=self.settings,
            extraction_results=extractions,
        )
        versions = self.root / "versions"
        versions.mkdir(exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".pending-", dir=versions))
        destination = versions / version
        created: list[Path] = []
        moved = published = False
        started = time.monotonic()
        try:
            for name, content in contents.items():
                path = self.root / name
                if not path.resolve().is_relative_to(self.root / "data/local"):
                    raise CompanyStoreError("RETAINED_PATH_ESCAPE")
                if path.exists():
                    if path.read_bytes() != content:
                        raise CompanyStoreError("RETAINED_CONTENT_CONFLICT")
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as handle:
                    created.append(path)
                    handle.write(content)
            database = staging / "index.sqlite"
            write_index(
                plan,
                embedder=self.encoder,
                store=SQLiteIndexStore(database, plan=plan),
            )
            reopened = SQLiteIndexStore(database)
            if reopened.read_metadata(plan.metadata.index_version) != plan.metadata:
                raise CompanyStoreError("INDEX_READBACK_MISMATCH")
            # Read real rows through cosine search and compare their full DTOs.
            query = EmbeddingVector(
                "readback",
                self.settings.model_id,
                self.settings.model_revision,
                (1.0,) + (0.0,) * (self.settings.dimension - 1),
            )
            hits = reopened.search(
                query, expected=plan.metadata, top_k=len(plan.chunk_snapshots)
            )
            if {h.chunk.chunk_id: h.chunk for h in hits} != state["chunks"] or {
                h.source.source_id: h.source for h in hits
            } != {
                s.source_id: s
                for s in (
                    Source.model_validate_json(raw) for raw in plan.source_snapshots
                )
            }:
                raise CompanyStoreError("INDEX_CONTENT_READBACK_MISMATCH")
            manifest = StoreManifest(
                schema_version=SCHEMA,
                version=version,
                settings_snapshot=self.settings._payload,
                **state,
                index_metadata=plan.metadata,
                index_hash=_hash(database.read_bytes()),
                indexing_seconds=time.monotonic() - started,
                document_count=len(state["documents"]),
            )
            (staging / "manifest.json").write_bytes(
                _json(manifest.model_dump(mode="json"))
            )
            if destination.exists():
                raise CompanyStoreError("UNPUBLISHED_VERSION_EXISTS")
            staging.rename(destination)
            moved = True
            snapshot = self.open(version=version)
            if snapshot is None:
                raise CompanyStoreError("INDEX_READBACK_MISMATCH")
            pointer = destination / "CURRENT.pending"
            with pointer.open("x", encoding="utf-8") as handle:
                handle.write(version + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(pointer, self.root / "CURRENT")
            published = True
            return snapshot
        finally:
            if not published:
                if moved:
                    shutil.rmtree(destination)
                else:
                    shutil.rmtree(staging)
                for path in created:
                    path.unlink()
