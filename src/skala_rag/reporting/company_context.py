"""Original-only company context; retained reports are retrieval, not authority."""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from types import MappingProxyType
from typing import Final
from urllib.parse import quote

from pydantic import JsonValue

from skala_rag.contracts import Evidence
from skala_rag.contracts.common import JSONMap
from skala_rag.contracts.company_report import (
    EvidenceView,
    InformationResult,
    NarrativeQuestion,
    NarrativeTarget,
    OperatorFolderBinding,
    ReviewedIssuerReceipt,
    WriterContext,
    _reject_nonfinite,
    _unique_object,
)
from skala_rag.contracts.evaluation import EvaluationSnapshot
from skala_rag.rag.company_store import (
    CompanyStoreError,
    StoreManifest,
    StoreSnapshot,
    _evidence_closure,
    _has_ancestor,
    evidence_fingerprint,
)
from skala_rag.rag.corpus import verify_markdown_chunks
from skala_rag.rag.retrieval import source_date
from skala_rag.reporting.validator import TOKEN


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class CompanyContextError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CompanyEvidence:
    evidence: tuple[Evidence, ...]
    prior_interpretations: tuple[JSONMap, ...]


@dataclass(frozen=True, slots=True)
class IssuerAssociation:
    """Controller-reviewed issuer mapping, not legal identity or eligibility.

    Fingerprints must come from reviewed originals, never model output or a name
    match. This permits narrowly reviewed issuer excerpts without a CompanyProfile.
    """

    subject_id: str
    name: str
    evidence_hashes: Mapping[str, str]
    receipt_hash: str | None = None


def load_issuer_association(
    receipt_path: Path | None,
    *,
    retained: StoreSnapshot,
    company_name: str,
    as_of: date,
) -> IssuerAssociation | None:
    """Load trusted local review input and verify every original before dispatch.

    Pass the effective config's resolved issuer_receipt_path. Absence preserves
    stored identity resolution; configured invalid input fails closed. The caller
    owns review trust: a well-formed file is not authentication or authorization.
    """
    if receipt_path is None:
        return None
    content = receipt_path.read_bytes()
    json.loads(
        content, object_pairs_hook=_unique_object, parse_constant=_reject_nonfinite
    )
    receipt = ReviewedIssuerReceipt.model_validate_json(content)
    if receipt.name != company_name:
        raise CompanyContextError("ISSUER_ASSOCIATION_MISMATCH")
    association = IssuerAssociation(
        subject_id=receipt.subject_id,
        name=receipt.name,
        evidence_hashes=MappingProxyType(dict(receipt.evidence_hashes)),
        receipt_hash="sha256:" + hashlib.sha256(content).hexdigest(),
    )
    manifest = retained.manifest
    for eid, fingerprint in association.evidence_hashes.items():
        item = manifest.evidence.get(eid)
        if item is None or item.evidence_id != eid:
            raise CompanyContextError("UNRESOLVED_ORIGINAL_LINEAGE")
        if item.scope != "company" or item.candidate_id != association.subject_id:
            raise CompanyContextError("FOREIGN_ISSUER_EVIDENCE")
        citation = _original_citation(retained, manifest, item, as_of)
        if (
            item.evidence_kind != "reported"
            or citation["evidence_fingerprint"] != fingerprint
        ):
            raise CompanyContextError("ISSUER_ASSOCIATION_MISMATCH")
    return association


@dataclass(frozen=True, slots=True)
class CompanyWriterContext:
    """Detached writer bytes and private citation bytes; send only writer_context."""

    writer_payload: str
    citation_payload: str

    @property
    def writer_context(self) -> WriterContext:
        return WriterContext.model_validate_json(self.writer_payload)

    @property
    def citation_registry(self) -> JSONMap:
        return json.loads(self.citation_payload)

    @property
    def context_id(self) -> str:
        return self.writer_context.context_id

    def verify_binding(self) -> None:
        """Reject changed projections or citation receipts before downstream use."""
        data = self.writer_context.model_dump(mode="json")
        expected = data.pop("context_id")
        if expected != _context_digest(data, self.citation_payload):
            raise CompanyContextError("WRITER_CONTEXT_BINDING_MISMATCH")


def _context_digest(projection: JSONMap, registry: str) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            canonical(
                {
                    "writer": projection,
                    "citation_registry_hash": "sha256:"
                    + hashlib.sha256(registry.encode()).hexdigest(),
                }
            ).encode()
        ).hexdigest()
    )


_PRIVATE_TEXT: Final = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://|www\.|(?:^|\s)/(?:\w|Users/)|"
    r"\b(?:data/local|outputs/|source_id|chunk_id|retrieval_id|local_path|"
    r"corpus_version|index_version|generation_id|input_fingerprint|store_version|"
    r"page_start|page_end)\b"
    r'|\b(?:provider|publisher|author|page|url|path)\b["\s]*[:=]'
    r"|\bpage\s+\d+)",
    re.IGNORECASE,
)


def _check_neutral_text(value: JsonValue, private_tokens: set[str]) -> None:
    """Check every nested text leaf, including JSON encoded inside feedback."""
    match value:
        case str():
            folded = value.casefold()
            if _PRIVATE_TEXT.search(value) or any(
                token.casefold() in folded for token in private_tokens
            ):
                raise CompanyContextError("SOURCE_METADATA_IN_WRITER_TEXT")
            if value.lstrip().startswith(("{", "[", '"')):
                try:
                    nested = json.loads(value)
                except json.JSONDecodeError:
                    return
                _check_neutral_text(nested, private_tokens)
        case list():
            for item in value:
                _check_neutral_text(item, private_tokens)
        case dict():
            for key, item in value.items():
                _check_neutral_text(key + ":", private_tokens)
                _check_neutral_text(item, private_tokens)
        case None | bool() | int() | float():
            return


def _original_citation(
    retained: StoreSnapshot,
    manifest: StoreManifest,
    item: Evidence,
    as_of: date,
    *,
    operator_folder: bool = False,
) -> JSONMap:
    """Close exact original bytes, subject, locator and every retrieval proof."""
    source = manifest.sources.get(item.source_id)
    doc = manifest.documents.get(item.source_id)
    if (
        source is None
        or doc is None
        or source.source_id != item.source_id
        or doc.source_id != item.source_id
        or source.bibliographic_metadata.get("generated_report")
        or any(item.source_id in r.source_ids for r in manifest.reports.values())
        or (
            not doc.approved
            and not (operator_folder and doc.admission_kind == "operator_folder")
        )
        or (doc.admission_kind == "operator_folder" and not operator_folder)
        or doc.extraction_status not in ("ok", "partial")
        or (doc.extraction_status == "partial" and doc.text_index_review is None)
        or source.content_hash != doc.content_hash
        or source.local_path != doc.local_path
        or doc.scope != item.scope
        or (item.scope == "company" and set(doc.candidate_ids) != {item.candidate_id})
        or (item.scope == "industry" and (doc.candidate_ids or item.candidate_id))
    ):
        raise CompanyContextError("ORIGINAL_SOURCE_MISMATCH")
    if (
        source_date(source) > as_of
        or source.retrieved_at.date() > as_of
        or (doc.publication_date is not None and doc.publication_date > as_of)
        or any(d is not None and d > as_of for d in (item.event_date, item.value_as_of))
    ):
        raise CompanyContextError("EVIDENCE_AFTER_CUTOFF")
    path = (retained.root / doc.local_path).resolve()
    if not path.is_relative_to((retained.root / "data/local").resolve()):
        raise CompanyContextError("ORIGINAL_PATH_ESCAPE")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CompanyContextError("ORIGINAL_BYTES_UNAVAILABLE") from exc
    if "sha256:" + hashlib.sha256(raw).hexdigest() != source.content_hash:
        raise CompanyContextError("ORIGINAL_BYTES_MISMATCH")
    markdown = None
    if doc.admission_kind == "operator_folder":
        # 원본 PDF 확인 뒤 추출본을 읽고, 아래 각 청크의 정확한 span을 검증한다.
        extraction = doc.markdown_extraction
        if extraction is None or path.suffix.lower() != ".pdf":
            raise CompanyContextError("ORIGINAL_EXTRACTION_REQUIRED")
        markdown_path = (retained.root / extraction.markdown_path).resolve()
        if not markdown_path.is_relative_to((retained.root / "data/local").resolve()):
            raise CompanyContextError("ORIGINAL_PATH_ESCAPE")
        try:
            markdown = markdown_path.read_bytes()
        except OSError as exc:
            raise CompanyContextError("ORIGINAL_BYTES_UNAVAILABLE") from exc
    if not item.provenance:
        raise CompanyContextError("EXACT_LOCATOR_LINEAGE_REQUIRED")
    chunks = {}
    records = {}
    for provenance in item.provenance:
        record = manifest.retrieval_records.get(provenance.retrieval_id)
        chunk = manifest.chunks.get(provenance.chunk_id)
        if (
            record is None
            or record.retrieval_id != provenance.retrieval_id
            or record.status != "ok"
            or record.candidate_id not in (None, item.candidate_id)
            or item.source_id not in record.source_ids
            or item.evidence_id not in record.evidence_ids
            or record.finished_at.date() > as_of
        ):
            raise CompanyContextError("EXACT_LOCATOR_LINEAGE_REQUIRED")
        if provenance.method == "web" and provenance.chunk_id is None:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CompanyContextError("ORIGINAL_TEXT_MISMATCH") from exc
            if (
                source.url is None
                or doc.origin_url != source.url
                or item.locator != f"{source.url}#:~:text={quote(item.excerpt[:200])}"
                or item.excerpt not in text
            ):
                raise CompanyContextError("EXACT_LOCATOR_LINEAGE_REQUIRED")
            records[record.retrieval_id] = record.model_dump(mode="json")
            continue
        if (
            chunk is None
            or chunk.chunk_id != provenance.chunk_id
            or chunk.chunk_id not in record.chunk_ids
            or chunk.source_id != item.source_id
            or chunk.locator != item.locator
            or chunk.scope != item.scope
            or set(chunk.candidate_ids) != set(doc.candidate_ids)
            or chunk.corpus_version != manifest.version
            or item.excerpt not in chunk.text
        ):
            raise CompanyContextError("EXACT_LOCATOR_LINEAGE_REQUIRED")
        if markdown is not None:
            try:
                verify_markdown_chunks(doc, markdown, (chunk,))
            except ValueError as exc:
                raise CompanyContextError("ORIGINAL_MARKDOWN_MISMATCH") from exc
            if (
                item.claim != chunk.text
                or item.excerpt != chunk.text
                or item.evidence_kind != "reported"
            ):
                raise CompanyContextError("ORIGINAL_TEXT_MISMATCH")
        elif path.suffix.lower() != ".pdf":
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CompanyContextError("ORIGINAL_TEXT_MISMATCH") from exc
            if chunk.text not in text:
                raise CompanyContextError("ORIGINAL_TEXT_MISMATCH")
        chunks[chunk.chunk_id] = chunk.model_dump(mode="json")
        records[record.retrieval_id] = record.model_dump(mode="json")
    return {
        "original_evidence": item.model_dump(mode="json"),
        "evidence_fingerprint": evidence_fingerprint(item, source),
        "source": source.model_dump(mode="json"),
        "document": doc.model_dump(mode="json"),
        "locator": item.locator,
        "chunks": chunks,
        "retrieval_records": records,
    }


def build_writer_context(
    retained: StoreSnapshot,
    *,
    company_name: str,
    as_of: date,
    questions: Sequence[NarrativeQuestion],
    evidence_ids: Sequence[str],
    issuer_association: IssuerAssociation | None = None,
    operator_folder: OperatorFolderBinding | None = None,
    may_request_information: bool = True,
    information_results: Sequence[InformationResult] = (),
) -> CompanyWriterContext:
    """Project admitted originals only; no eligibility, collection or name joins.

    A stored identity can resolve a unique canonical name/alias. Otherwise callers
    supply a reviewed fingerprint-bound issuer association. Explicitly selected
    foreign evidence, including foreign support/conflict/supersession, fails closed.
    Legacy scored assembly below remains solely for unmigrated v1 consumers.
    """
    manifest = retained.manifest
    # 폴더 연결은 사용자 입력의 대상·세대일 뿐 검토 영수증이나 법적 신원이 아니다.
    if operator_folder is not None:
        if issuer_association is not None:
            raise CompanyContextError("OPERATOR_FOLDER_REVIEWED_AUTHORITY_CONFLICT")
        if (
            operator_folder.name != company_name
            or operator_folder.store_version != manifest.version
        ):
            raise CompanyContextError("OPERATOR_FOLDER_BINDING_MISMATCH")
    matches = [
        identity
        for identity in manifest.companies.values()
        if company_name.casefold()
        in {
            name.casefold()
            for name in (identity.candidate.canonical_name, *identity.candidate.aliases)
        }
    ]
    if len(matches) > 1:
        raise CompanyContextError("AMBIGUOUS_ISSUER_SUBJECT")
    association = issuer_association
    if operator_folder is not None:
        cid, name = operator_folder.subject_id, operator_folder.name
        identity_ids = []
    elif matches:
        identity = matches[0]
        cid = identity.candidate.candidate_id
        name = identity.candidate.canonical_name
        field = (
            "canonical_name"
            if company_name.casefold() == name.casefold()
            else next(
                "alias:" + alias
                for alias in identity.candidate.aliases
                if company_name.casefold() == alias.casefold()
            )
        )
        identity_ids = identity.field_evidence_ids.get(field, [])
        if not identity_ids:
            raise CompanyContextError("ISSUER_ASSOCIATION_REQUIRED")
        if association is not None and (
            association.subject_id != cid or association.name != name
        ):
            raise CompanyContextError("AMBIGUOUS_ISSUER_SUBJECT")
    else:
        if association is None or association.name != company_name:
            raise CompanyContextError("ISSUER_ASSOCIATION_REQUIRED")
        cid, name = association.subject_id, association.name
        identity_ids = list(association.evidence_hashes)
        if not identity_ids:
            raise CompanyContextError("ISSUER_ASSOCIATION_REQUIRED")
    if association is not None:
        identity_ids = sorted(set(identity_ids) | set(association.evidence_hashes))
    selected = set(evidence_ids) | set(identity_ids)
    # Conflicts can legitimately be symmetric; only support edges must be acyclic.
    pending = list(selected)
    while pending:
        eid = pending.pop()
        item = manifest.evidence.get(eid)
        if item is None or item.evidence_id != eid:
            raise CompanyContextError("UNRESOLVED_ORIGINAL_LINEAGE")
        if (
            (item.scope == "company" and item.candidate_id != cid)
            or (item.scope == "industry" and item.candidate_id is not None)
            or (eid in identity_ids and item.scope != "company")
        ):
            raise CompanyContextError("FOREIGN_ISSUER_EVIDENCE")
        refs = (
            item.supporting_evidence_ids
            + item.conflicts_with
            + ([item.supersedes] if item.supersedes is not None else [])
        )
        if eid in refs:
            raise CompanyContextError("CIRCULAR_EVIDENCE_LINEAGE")
        for ref in refs:
            if ref not in selected:
                selected.add(ref)
                pending.append(ref)
    try:
        _evidence_closure(selected, manifest.evidence, manifest.sources)
        tuple(
            TopologicalSorter(
                {
                    eid: (
                        [manifest.evidence[eid].supersedes]
                        if manifest.evidence[eid].supersedes is not None
                        else []
                    )
                    for eid in selected
                }
            ).static_order()
        )
    except CompanyStoreError as exc:
        raise CompanyContextError(exc.code) from exc
    except CycleError as exc:
        raise CompanyContextError("CIRCULAR_SUPERSESSION_LINEAGE") from exc
    citations = {
        eid: _original_citation(
            retained,
            manifest,
            manifest.evidence[eid],
            as_of,
            operator_folder=operator_folder is not None,
        )
        for eid in sorted(selected)
    }
    # 검색 hit 수가 아니라 지원·충돌·정정 폐쇄 후의 정확한 원문 청크를 센다.
    chunk_pairs = {
        (manifest.evidence[eid].source_id, provenance.chunk_id)
        for eid in selected
        for provenance in manifest.evidence[eid].provenance
        if provenance.chunk_id is not None
    }
    if len(chunk_pairs) > 12:
        raise CompanyContextError("WRITER_CHUNK_LIMIT_EXCEEDED")
    if operator_folder is not None and any(
        manifest.documents[manifest.evidence[eid].source_id].admission_kind
        != "operator_folder"
        for eid in selected
    ):
        raise CompanyContextError("OPERATOR_FOLDER_SOURCE_MISMATCH")
    for eid in identity_ids:
        item = manifest.evidence[eid]
        if item.evidence_kind != "reported" or (
            association is not None
            and association.evidence_hashes.get(eid)
            != citations[eid]["evidence_fingerprint"]
        ):
            raise CompanyContextError("ISSUER_ASSOCIATION_MISMATCH")
    views = {
        eid: EvidenceView.model_validate(
            {
                "schema_version": "narrative-report-1",
                **{
                    key: value
                    for key, value in manifest.evidence[eid]
                    .model_dump(mode="json")
                    .items()
                    if key in EvidenceView.model_fields and key != "schema_version"
                },
            }
        )
        for eid in sorted(selected)
    }
    writer = WriterContext(
        schema_version="narrative-report-1",
        context_id="pending-controller-hash",
        target=NarrativeTarget(
            schema_version="narrative-report-1", subject_id=cid, name=name
        ),
        as_of=as_of,
        questions=list(questions),
        evidence=views,
        may_request_information=may_request_information,
        information_results=list(information_results),
    )
    private_tokens = {manifest.version, manifest.index_metadata.index_version}
    if operator_folder is not None:
        # 회사명·공개 subject ID와 달리 가져오기 세대·해시는 원문 밖에서 비공개다.
        private_tokens.update(
            (
                operator_folder.generation_id,
                operator_folder.input_fingerprint,
                operator_folder.store_version,
            )
        )
    private_tokens.update(manifest.chunks)
    private_tokens.update(manifest.retrieval_records)
    for source in manifest.sources.values():
        private_tokens.update(
            token
            for token in (
                source.url,
                source.local_path,
                source.content_hash,
                source.title,
                source.publisher,
                source.author,
            )
            if token and token.casefold() not in name.casefold()
        )
        # Original evidence IDs can contain source IDs; IDs are deliberately public.
        private_tokens.add(source.source_id)
        for key in ("capture", "capture_path", "provider", "channel", "index_version"):
            match source.bibliographic_metadata.get(key):
                case str() as token:
                    if token:
                        private_tokens.add(token)
    for record in manifest.retrieval_records.values():
        private_tokens.update((record.tool_name, record.run_id))
    for chunk in manifest.chunks.values():
        private_tokens.update((chunk.embedding_model, chunk.embedding_revision))
    data = writer.model_dump(mode="json")
    # Check prose, not public evidence/subject IDs or factual reference edges.
    _check_neutral_text(name, private_tokens)
    for question in writer.questions:
        _check_neutral_text(question.question, private_tokens)
    for view in views.values():
        # PDF·Markdown·정확한 span 검사를 통과한 원문 두 필드만 문자열 예외다.
        original_fields = {"claim", "excerpt"} if operator_folder is not None else set()
        _check_neutral_text(
            view.model_dump(
                mode="json",
                exclude={"supporting_evidence_ids", "conflicts_with", "supersedes"}
                | original_fields,
            ),
            private_tokens,
        )
    for result in writer.information_results:
        _check_neutral_text(result.detail, private_tokens)
    registry = canonical(
        {
            "target": writer.target.model_dump(mode="json"),
            "as_of": as_of.isoformat(),
            "issuer_evidence_ids": sorted(identity_ids),
            "operator_folder": (
                operator_folder.model_dump(mode="json")
                if operator_folder is not None
                else None
            ),
            "issuer_association": (
                {
                    "subject_id": association.subject_id,
                    "name": association.name,
                    "evidence_hashes": dict(association.evidence_hashes),
                    "receipt_hash": association.receipt_hash,
                }
                if association is not None
                else None
            ),
            "citations": citations,
        }
    )
    data.pop("context_id")
    data["context_id"] = _context_digest(data, registry)
    return CompanyWriterContext(canonical(data), registry)


def validate_company_snapshot(
    snapshot: EvaluationSnapshot, retained: StoreSnapshot
) -> None:
    """Accept unchanged originals or a current local reread, never rewritten facts.

    A current reread replaces only provenance and commits the retained original
    Evidence fingerprints. Historic receipts remain in the immutable store.
    Exact snapshot-bound operator reviews still apply to the new reread.
    """
    manifest = retained.manifest
    originals = _evidence_closure(
        set(snapshot.evidence), manifest.evidence, manifest.sources
    )
    if set(originals) != set(snapshot.evidence) or any(
        item.model_copy(update={"provenance": manifest.evidence[eid].provenance})
        != manifest.evidence[eid]
        or snapshot.sources.get(item.source_id) != manifest.sources[item.source_id]
        for eid, item in snapshot.evidence.items()
    ):
        raise CompanyContextError("EVALUATED_ORIGINALS_MISMATCH")
    source_ids = {e.source_id for e in snapshot.evidence.values()}
    if set(snapshot.sources) != source_ids or any(
        c.source_id not in source_ids
        or c.chunk_id not in manifest.chunks
        or c.model_copy(update={"corpus_version": manifest.version})
        != manifest.chunks[c.chunk_id]
        for c in snapshot.chunks.values()
    ):
        raise CompanyContextError("EVALUATED_PROVENANCE_MISMATCH")
    fingerprints = {
        eid: evidence_fingerprint(e, manifest.sources[e.source_id])
        for eid, e in manifest.evidence.items()
        if eid in snapshot.evidence
    }
    for record in snapshot.retrieval_records.values():
        if (
            not set(record.source_ids) <= source_ids
            or not set(record.evidence_ids) <= set(snapshot.evidence)
            or not set(record.chunk_ids) <= set(snapshot.chunks)
        ):
            raise CompanyContextError("EVALUATED_PROVENANCE_MISMATCH")
        if record == manifest.retrieval_records.get(record.retrieval_id):
            continue
        if (
            record.tool_name != "company-store-retrieval"
            or record.run_id != snapshot.run_id
            or record.candidate_id != snapshot.candidate_id
            or record.status != "ok"
            or not record.cache_hit
            or snapshot.corpus_version != manifest.version
            or snapshot.index_version != manifest.index_metadata.index_version
            or record.arguments_without_secrets
            != {
                "corpus_version": manifest.version,
                "index_version": manifest.index_metadata.index_version,
                "as_of": snapshot.as_of.isoformat(),
                "original_evidence_hashes": fingerprints,
            }
            or set(record.evidence_ids) != set(snapshot.evidence)
            or set(record.source_ids) != source_ids
            or set(record.chunk_ids) != set(snapshot.chunks)
        ):
            raise CompanyContextError("EVALUATED_PROVENANCE_MISMATCH")
    for eid, item in snapshot.evidence.items():
        if item.provenance == manifest.evidence[eid].provenance:
            continue
        if not item.provenance or any(
            p.retrieval_id not in snapshot.retrieval_records
            or snapshot.retrieval_records[p.retrieval_id].tool_name
            != "company-store-retrieval"
            or p.method != "rag"
            or p.chunk_id not in snapshot.chunks
            or snapshot.chunks[p.chunk_id].source_id != item.source_id
            or item.excerpt not in snapshot.chunks[p.chunk_id].text
            for p in item.provenance
        ):
            raise CompanyContextError("EVALUATED_PROVENANCE_MISMATCH")


def resolve_company_evidence(
    stored: StoreSnapshot,
    chunk_ids: Sequence[str],
    *,
    candidate_id: str,
    as_of: date,
    current_report_id: str,
) -> CompanyEvidence:
    """Resolve hit IDs against the frozen store before evaluating originals.

    The store owns payloads; caller-supplied hit text cannot introduce facts.
    Missing/circular lineage excludes the affected hit, not unrelated originals.
    Independent keyword inputs bind subject, cutoff and forbidden self-ancestry.
    """
    manifest = stored.manifest
    evidence: dict[str, Evidence] = {}
    interpretations: list[JSONMap] = []
    for chunk_id in sorted(set(chunk_ids)):
        chunk = manifest.chunks.get(chunk_id)
        if chunk is None or (
            chunk.scope == "company" and candidate_id not in chunk.candidate_ids
        ):
            continue
        source = manifest.sources.get(chunk.source_id)
        if source is None:
            continue
        report = next(
            (r for r in manifest.reports.values() if source.source_id in r.source_ids),
            None,
        )
        try:
            if report is not None:
                if report.as_of > as_of or _has_ancestor(
                    report.draft.report_id, current_report_id, manifest.reports
                ):
                    continue
                ids = set(TOKEN.findall(chunk.text))
            else:
                ids = {
                    e.evidence_id
                    for e in manifest.evidence.values()
                    if e.source_id == source.source_id and e.excerpt in chunk.text
                }
            originals = _evidence_closure(ids, manifest.evidence, manifest.sources)
        except CompanyStoreError:
            continue
        items = [manifest.evidence[eid] for eid in originals]
        if not items or any(
            (e.scope == "company" and e.candidate_id != candidate_id)
            or (e.scope == "industry" and e.candidate_id is not None)
            or source_date(manifest.sources[e.source_id]) > as_of
            or any(d is not None and d > as_of for d in (e.event_date, e.value_as_of))
            for e in items
        ):
            continue
        if report is not None:
            if any(
                report.original_evidence_hashes.get(e.evidence_id)
                != evidence_fingerprint(e, manifest.sources[e.source_id])
                for e in items
            ):
                continue
            interpretations.append(
                {
                    "kind": "prior_report_interpretation",
                    "report_id": report.draft.report_id,
                    "run_id": report.run_id,
                    "as_of": report.as_of.isoformat(),
                    "generation_model": report.generation_model,
                    "generation_revision": report.generation_revision,
                    "validated_artifact_hash": report.validated_artifact_hash,
                    "validation_receipt_hashes": list(report.validation_receipt_hashes),
                    "chunk_id": chunk_id,
                    "text": chunk.text,
                    "original_evidence_ids": list(originals),
                    "original_source_ids": sorted({e.source_id for e in items}),
                    "independent_support": False,
                }
            )
        evidence.update((e.evidence_id, e) for e in items)
    return CompanyEvidence(
        tuple(evidence[eid] for eid in sorted(evidence)), tuple(interpretations)
    )
