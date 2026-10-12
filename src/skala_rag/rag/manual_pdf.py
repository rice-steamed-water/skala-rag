"""기업 폴더의 PDF를 변환·보존하고 현재 가져오기 세대를 고정한다.

폴더 배치는 운영자 입력일 뿐 사실 검토나 법적 기업 식별이 아니다.
원본 PDF와 추출 Markdown을 별도로 보존하며 보고서는 ready 세대만 사용한다.
"""

import fcntl
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import uuid4

from markitdown import FileConversionException, MarkItDown, UnsupportedFormatException
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from skala_rag.contracts import (
    Chunk,
    Evidence,
    EvidenceProvenance,
    RetrievalRecord,
    Source,
)
from skala_rag.contracts.company_report import (
    CompanyReportConfig,
    ImportFile,
    ImportSnapshot,
    ImportState,
    ImportTarget,
    OperatorFolderBinding,
    OperatorFolderConfig,
    load_company_report_config,
)
from skala_rag.rag.company_store import (
    CompanyStore,
    RetainedSource,
    StoreSnapshot,
)
from skala_rag.rag.corpus import ManifestDocument, MarkdownExtraction
from skala_rag.rag.index_v3 import Encoder, IndexSettings


class ManualImportError(ValueError):
    """현재 입력을 사용할 수 없는 이유를 기계 코드로 전달한다."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ImportResult:
    """가져오기 완료 상태와 실행 가능한 config 위치를 반환한다."""

    state: ImportState
    config_path: Path | None
    reused: bool


@dataclass(frozen=True, slots=True)
class ReadyImport:
    """보고서 시작 시 검증한 불변 snapshot과 폴더 binding이다."""

    state: ImportState
    snapshot: StoreSnapshot
    binding: OperatorFolderBinding


@dataclass(frozen=True, slots=True)
class _InputPDF:
    path: Path
    content: bytes | None
    pdf_hash: str | None


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _identity(parts: list[str | int]) -> str:
    return _digest(
        json.dumps(parts, separators=(",", ":"), ensure_ascii=False).encode()
    )


def manual_index_settings(
    receipt_path: Path, *, max_characters: int = 1200, overlap: int = 150
) -> IndexSettings:
    """모델 영수증의 자산 설정은 그대로 두고 Markdown 청크 설정만 분리한다."""
    if (
        type(max_characters) is not int
        or type(overlap) is not int
        or not 0 <= overlap < max_characters
    ):
        raise ManualImportError("INVALID_MARKDOWN_CHUNK_SETTINGS")
    receipt = json.loads(receipt_path.read_bytes())
    values = json.loads(receipt["metadata"]["settings_snapshot"])
    values["chunk_settings"] = {
        "boundary": "markdown-character-span-1",
        "max_characters": max_characters,
        "overlap": overlap,
        "parser_version": version("markitdown"),
        "enable_plugins": False,
    }
    return IndexSettings(**values)


def _inventory(raw_dir: Path) -> tuple[_InputPDF, ...]:
    """정렬된 직접 자식 PDF만 읽는다. 읽기 실패도 inventory에서 제거하지 않는다."""
    paths = sorted(
        (p for p in raw_dir.iterdir() if p.suffix.lower() == ".pdf"),
        key=lambda p: p.name,
    )
    inputs = []
    for path in paths:
        try:
            # 외부 symlink와 PDF 이름의 디렉터리는 다른 기업 자료로 병합하지 않는다.
            if path.is_symlink() or not path.is_file():
                raise ManualImportError("INVALID_PDF_CHILD")
            content = path.read_bytes()
            inputs.append(_InputPDF(path, content, "sha256:" + _digest(content)))
        except (OSError, ManualImportError):
            inputs.append(_InputPDF(path, None, None))
    return tuple(inputs)


def _fingerprint(inputs: tuple[_InputPDF, ...]) -> str:
    """변환 실패를 포함한 전체 원본 목록으로 입력 identity를 만든다."""
    if any(item.pdf_hash is None for item in inputs):
        raise ManualImportError("PDF_INVENTORY_HASH_FAILED")
    return _digest(
        json.dumps(
            [[item.path.name, item.pdf_hash] for item in inputs],
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    )


def current_input_fingerprint(raw_dir: Path) -> str:
    """보고서 시작 시 현재 파일 목록·해시를 다시 읽어 변경·삭제를 탐지한다."""
    return _fingerprint(_inventory(raw_dir))


def _atomic_write(path: Path, payload: bytes) -> None:
    """완성된 파일만 교체하고 실패한 임시 파일은 제거한다."""
    pending = path.with_name(path.name + "." + uuid4().hex + ".pending")
    try:
        with pending.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


@contextmanager
def _company_lock(root: Path) -> Iterator[None]:
    """회사별 한 writer만 변환·config·ready 발행 전체를 수행한다."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".import.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ManualImportError("IMPORT_WRITER_CONFLICT") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _convert_pdf(content: bytes) -> str:
    """PDF extra만 사용한다. 플러그인·LLM·OCR 호출은 구성하지 않는다."""
    # MarkItDown은 손상 PDF를 일반 텍스트로 fallback할 수 있으므로 PDF를 먼저 검사한다.
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise ManualImportError("PDF_ENCRYPTED")
        len(reader.pages)
    except PdfReadError as exc:
        raise ManualImportError("PDF_INVALID") from exc
    # inventory에서 읽은 같은 bytes를 변환하여 변환 중 raw 변경과 섞이지 않는다.
    return (
        MarkItDown(enable_plugins=False)
        .convert_stream(BytesIO(content), file_extension=".pdf")
        .markdown
    )


def _material(
    target: ImportTarget,
    item: _InputPDF,
    markdown: str,
    settings: IndexSettings,
    recipe_hash: str,
) -> RetainedSource:
    """추출 원문 그대로 claim·excerpt·chunk를 만들고 미상 값을 유지한다."""
    if item.content is None or item.pdf_hash is None:
        raise ManualImportError("PDF_INVENTORY_HASH_FAILED")
    now = datetime.now(timezone.utc)
    sid = "src-operator-" + _identity([target.subject_id, item.pdf_hash])
    markdown_bytes = markdown.encode("utf-8")
    markdown_hash = "sha256:" + _digest(markdown_bytes)
    path = f"data/local/objects/{item.pdf_hash[7:]}.pdf"
    extraction = MarkdownExtraction(
        schema_version="markitdown-pdf-1",
        markdown_path=f"data/local/objects/{markdown_hash[7:]}.markdown",
        markdown_hash=markdown_hash,
        parser_version=version("markitdown"),
        recipe_hash=recipe_hash,
        converted_at=now,
    )
    source = Source(
        schema_version="manual-pdf-1",
        source_id=sid,
        title=item.path.name,
        source_kind="report",
        local_path=path,
        retrieved_at=now,
        content_hash=item.pdf_hash,
        language="unknown",
        access_notes="운영자가 기업 폴더에 제공한 자료이며 사실 검토 전이다.",
        bibliographic_metadata={"admission_kind": "operator_folder"},
    )
    document = ManifestDocument(
        schema_version="manual-pdf-1",
        document_id="doc-" + sid,
        source_id=sid,
        local_path=path,
        content_hash=item.pdf_hash,
        title=source.title,
        language=source.language,
        permission_note="운영자 제공 자료; 재배포 권한은 별도 확인 필요.",
        candidate_ids=(target.subject_id,),
        scope="company",
        extraction_status="ok",
        approved=False,
        reviewer=None,
        admission_kind="operator_folder",
        markdown_extraction=extraction,
    )
    chunks, evidence = [], []
    rid = "ret-" + _identity([sid, recipe_hash, markdown_hash])
    size = settings.chunk_settings["max_characters"]
    overlap = settings.chunk_settings["overlap"]
    for start in range(0, len(markdown), size - overlap):
        end = min(start + size, len(markdown))
        text = markdown[start:end]
        if not text.strip():
            continue
        cid = "chunk-markdown-" + _identity(
            [sid, markdown_hash, extraction.parser_version, recipe_hash, start, end]
        )
        locator = f"{extraction.markdown_path}#char={start}:{end}"
        chunk = Chunk(
            schema_version="manual-pdf-1",
            chunk_id=cid,
            source_id=sid,
            corpus_version="manual-pdf-1",
            text=text,
            locator=locator,
            candidate_ids=[target.subject_id],
            scope="company",
            language=source.language,
            embedding_model=settings.model_id,
            embedding_revision=settings.model_revision,
        )
        chunks.append(chunk)
        evidence.append(
            Evidence(
                schema_version="manual-pdf-1",
                evidence_id="ev-" + cid,
                candidate_id=target.subject_id,
                scope="company",
                criterion_ids=[],
                claim=text,
                excerpt=text,
                source_id=sid,
                locator=locator,
                provenance=[
                    EvidenceProvenance(
                        schema_version="manual-pdf-1",
                        retrieval_id=rid,
                        method="rag",
                        chunk_id=cid,
                    )
                ],
                evidence_kind="reported",
                confidence="unknown",
                limitations=["운영자 제공 원문이며 사실·기업 귀속은 검토되지 않았다."],
                supporting_evidence_ids=[],
                conflicts_with=[],
            )
        )
        if end == len(markdown):
            break
    record = RetrievalRecord(
        schema_version="manual-pdf-1",
        retrieval_id=rid,
        run_id="manual-" + recipe_hash,
        candidate_id=target.subject_id,
        tool_name="markitdown-pdf",
        arguments_without_secrets={"enable_plugins": False},
        started_at=now,
        finished_at=now,
        status="ok",
        source_ids=[sid],
        chunk_ids=[c.chunk_id for c in chunks],
        evidence_ids=[e.evidence_id for e in evidence],
        cache_hit=False,
    )
    return RetainedSource(
        document,
        source,
        item.content,
        tuple(chunks),
        tuple(evidence),
        (record,),
        markdown_content=markdown_bytes,
    )


def _cached_material(snapshot: StoreSnapshot, source_id: str) -> RetainedSource:
    """검증된 불변 snapshot에서 성공 자료만 재사용해 변환 시각을 보존한다."""
    manifest = snapshot.manifest
    document = manifest.documents[source_id]
    extraction = document.markdown_extraction
    if extraction is None:
        raise ManualImportError("CACHED_MARKDOWN_REQUIRED")
    source = manifest.sources[source_id]
    return RetainedSource(
        document,
        source,
        (snapshot.root / document.local_path).read_bytes(),
        tuple(c for c in manifest.chunks.values() if c.source_id == source_id),
        tuple(e for e in manifest.evidence.values() if e.source_id == source_id),
        tuple(
            r for r in manifest.retrieval_records.values() if source_id in r.source_ids
        ),
        markdown_content=(snapshot.root / extraction.markdown_path).read_bytes(),
    )


def _publish_config(
    base: CompanyReportConfig, state: ImportState, state_path: Path
) -> Path:
    """ready보다 먼저 실행 config를 저장한다. 이전 세대 config는 무효다."""
    snapshot = state.snapshot
    if snapshot is None:
        raise ManualImportError("READY_SNAPSHOT_REQUIRED")
    values = base.model_dump()
    values.update(
        store_dir=snapshot.store_dir,
        issuer_receipt_path=None,
        acquisition_receipt_path=None,
        operator_folder=OperatorFolderConfig(
            imports_path=state_path,
            generation_id=state.generation_id,
            store_version=snapshot.store_version,
        ),
    )
    config = CompanyReportConfig.model_validate(values)
    path = state_path.parent / "company-report.json"
    _atomic_write(path, config.model_dump_json(indent=2).encode())
    return path


def _convert_inputs(
    target: ImportTarget,
    inputs: tuple[_InputPDF, ...],
    settings: IndexSettings,
    recipe_hash: str,
    previous: ImportState | None,
    cached: StoreSnapshot | None,
) -> tuple[tuple[ImportFile, ...], tuple[RetainedSource, ...]]:
    """성공한 원문을 재사용하고 실패 PDF만 다시 변환해 파일별 결과를 반환한다."""
    previous_files = (
        {f.relative_path: f for f in previous.files}
        if cached is not None and previous is not None
        else {}
    )
    materials: dict[str, RetainedSource] = {}
    by_hash: dict[str, RetainedSource] = {}
    results = []
    for item in inputs:
        if item.pdf_hash is None:
            raise ManualImportError("PDF_INVENTORY_HASH_FAILED")
        prior = previous_files.get(item.path.name)
        try:
            if item.pdf_hash in by_hash:
                material = by_hash[item.pdf_hash]
            elif (
                prior is not None
                and prior.status == "ok"
                and prior.pdf_hash == item.pdf_hash
                and prior.source_id is not None
                and cached is not None
            ):
                material = _cached_material(cached, prior.source_id)
            else:
                if item.content is None:
                    raise ManualImportError("PDF_INVENTORY_HASH_FAILED")
                markdown = _convert_pdf(item.content)
                if not markdown.strip():
                    results.append(
                        ImportFile(
                            relative_path=item.path.name,
                            pdf_hash=item.pdf_hash,
                            status="empty",
                            source_id=None,
                            error_code="PDF_TEXT_EMPTY",
                        )
                    )
                    continue
                material = _material(target, item, markdown, settings, recipe_hash)
            sid = material.source.source_id
            # 동일 회사·동일 PDF bytes는 파일명이 달라도 한 번만 인덱싱한다.
            materials.setdefault(sid, material)
            by_hash[item.pdf_hash] = material
            results.append(
                ImportFile(
                    relative_path=item.path.name,
                    pdf_hash=item.pdf_hash,
                    status="ok",
                    source_id=sid,
                    error_code=None,
                )
            )
        except (
            FileConversionException,
            UnsupportedFormatException,
            OSError,
            ManualImportError,
        ) as exc:
            results.append(
                ImportFile(
                    relative_path=item.path.name,
                    pdf_hash=item.pdf_hash,
                    status="failed",
                    source_id=None,
                    error_code=(
                        exc.code
                        if isinstance(exc, ManualImportError)
                        else "PDF_CONVERSION_FAILED"
                    ),
                )
            )
    return tuple(results), tuple(materials.values())


def import_company_pdfs(
    *,
    company_name: str,
    raw_root: Path,
    output_root: Path,
    base_config_path: Path,
    subject_id: str | None = None,
    encoder: Encoder | None = None,
) -> ImportResult:
    """원본 inventory → 변환/재사용 → 새 저장소 → config → ready 순서로 발행한다."""
    # 기업 이름은 raw_root의 한 직접 자식 이름이어야 한다.
    if (
        not company_name.strip()
        or company_name in {".", ".."}
        or "/" in company_name
        or "\\" in company_name
    ):
        raise ManualImportError("INVALID_COMPANY_FOLDER")
    target = ImportTarget(
        subject_id=subject_id or "operator-" + _digest(company_name.encode())[:24],
        name=company_name,
        raw_dir=(raw_root / company_name).resolve(),
    )
    root = output_root.resolve() / _identity([target.subject_id, company_name])[:24]
    state_path = root / "import-state.json"
    with _company_lock(root):
        previous_error = None
        try:
            previous_bytes = state_path.read_bytes() if state_path.exists() else None
        except OSError as exc:
            previous_bytes = None
            previous_error = exc
        state = ImportState(
            schema_version="manual-import-1",
            generation_id=uuid4().hex,
            status="importing",
            target=target,
            input_fingerprint=None,
            recipe_hash=None,
            files=(),
            snapshot=None,
        )
        _atomic_write(state_path, state.model_dump_json(indent=2).encode())
        completed = False
        try:
            if previous_error is not None:
                raise previous_error
            inputs = _inventory(target.raw_dir)
            files = tuple(
                ImportFile(
                    relative_path=i.path.name,
                    pdf_hash=i.pdf_hash,
                    status="failed",
                    source_id=None,
                    error_code="PDF_INVENTORY_HASH_FAILED",
                )
                for i in inputs
            )
            state = state.model_copy(update={"files": files})
            fingerprint = _fingerprint(inputs)
            base_effective = load_company_report_config(base_config_path)
            base = CompanyReportConfig.model_validate(
                base_effective.model_dump(exclude={"runtime_document"})
            )
            settings = manual_index_settings(base.model_receipt_path)
            recipe_hash = _digest(settings._payload.encode())
            state = state.model_copy(
                update={
                    "input_fingerprint": fingerprint,
                    "recipe_hash": recipe_hash,
                }
            )
            previous = (
                ImportState.model_validate_json(previous_bytes)
                if previous_bytes is not None
                else None
            )
            cached = None
            if (
                previous is not None
                and previous.status == "ready"
                and previous.target == target
                and previous.recipe_hash == recipe_hash
                and previous.snapshot is not None
            ):
                store = (
                    CompanyStore(
                        previous.snapshot.store_dir, settings=settings, encoder=encoder
                    )
                    if encoder is not None
                    else CompanyStore.from_local(
                        previous.snapshot.store_dir,
                        model_path=base.model_path,
                        receipt_path=base.model_receipt_path,
                        index_settings=settings,
                    )
                )
                cached = store.open(version=previous.snapshot.store_version)
            results, materials = _convert_inputs(
                target, inputs, settings, recipe_hash, previous, cached
            )
            state = state.model_copy(update={"files": results})
            if not materials:
                status: Literal["failed", "empty"] = (
                    "failed" if any(f.status == "failed" for f in results) else "empty"
                )
                state = state.model_copy(update={"status": status})
                _atomic_write(state_path, state.model_dump_json(indent=2).encode())
                completed = True
                return ImportResult(state, None, False)
            reuse = (
                cached is not None
                and previous is not None
                and previous.input_fingerprint == fingerprint
                and {f.source_id for f in previous.files if f.status == "ok"}
                == {m.source.source_id for m in materials}
            )
            if reuse:
                snapshot = cached
            else:
                store_root = root / "generations" / state.generation_id / "store"
                store = (
                    CompanyStore(store_root, settings=settings, encoder=encoder)
                    if encoder is not None
                    else CompanyStore.from_local(
                        store_root,
                        model_path=base.model_path,
                        receipt_path=base.model_receipt_path,
                        index_settings=settings,
                    )
                )
                snapshot = store.ingest_sources(materials)
            if snapshot is None:
                raise ManualImportError("IMPORT_REOPEN_FAILED")
            state = state.model_copy(
                update={
                    "status": "ready",
                    "snapshot": ImportSnapshot(
                        store_dir=snapshot.root,
                        store_version=snapshot.manifest.version,
                    ),
                }
            )
            # ready 상태는 생성 config와 불변 store readback이 모두 끝난 뒤에만 보인다.
            config_path = _publish_config(base, state, state_path)
            _atomic_write(state_path, state.model_dump_json(indent=2).encode())
            completed = True
            return ImportResult(state, config_path, reuse)
        finally:
            if not completed:
                failed = state.model_copy(update={"status": "failed", "snapshot": None})
                _atomic_write(state_path, failed.model_dump_json(indent=2).encode())


def validate_operator_folder_input(
    config: CompanyReportConfig, *, company_name: str
) -> OperatorFolderBinding:
    """모델 적재 전에 현재 폴더 입력의 단일 authority 경계를 검증한다."""
    folder = config.operator_folder
    if folder is None:
        raise ManualImportError("OPERATOR_FOLDER_REQUIRED")
    state = ImportState.model_validate_json(folder.imports_path.read_bytes())
    snapshot_info = state.snapshot
    if state.status != "ready" or snapshot_info is None:
        raise ManualImportError("IMPORT_NOT_READY")
    if (
        state.target.name != company_name
        or state.generation_id != folder.generation_id
        or snapshot_info.store_version != folder.store_version
        or snapshot_info.store_dir.resolve() != config.store_dir.resolve()
    ):
        raise ManualImportError("IMPORT_BINDING_MISMATCH")
    settings = manual_index_settings(config.model_receipt_path)
    if state.recipe_hash != _digest(settings._payload.encode()):
        raise ManualImportError("IMPORT_RECIPE_MISMATCH")
    if state.input_fingerprint != current_input_fingerprint(state.target.raw_dir):
        raise ManualImportError("IMPORT_INPUT_CHANGED")
    recorded_fingerprint = _digest(
        json.dumps(
            [[f.relative_path, f.pdf_hash] for f in state.files],
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    )
    if (
        not any(f.status == "ok" for f in state.files)
        or recorded_fingerprint != state.input_fingerprint
    ):
        raise ManualImportError("IMPORT_SNAPSHOT_MISMATCH")
    binding = OperatorFolderBinding(
        subject_id=state.target.subject_id,
        name=company_name,
        generation_id=state.generation_id,
        input_fingerprint=state.input_fingerprint,
        store_version=snapshot_info.store_version,
    )
    if ImportState.model_validate_json(folder.imports_path.read_bytes()) != state:
        raise ManualImportError("IMPORT_GENERATION_CHANGED")
    return binding


def validate_operator_import(
    config: CompanyReportConfig, *, subject_id: str, name: str, store: CompanyStore
) -> ReadyImport:
    """공통 입력 authority 검사 뒤 실제 저장소의 원본·Markdown 무결성을 검증한다."""
    binding = validate_operator_folder_input(config, company_name=name)
    folder = config.operator_folder
    assert folder is not None
    state = ImportState.model_validate_json(folder.imports_path.read_bytes())
    snapshot_info = state.snapshot
    # 공개 precheck 이후 가져오기가 바뀌었다면 저장소를 열기 전에 거절한다.
    if (
        snapshot_info is None
        or state.generation_id != binding.generation_id
        or state.target.subject_id != binding.subject_id
        or state.target.name != binding.name
        or state.input_fingerprint != binding.input_fingerprint
        or snapshot_info.store_version != binding.store_version
        or snapshot_info.store_dir.resolve() != config.store_dir.resolve()
    ):
        raise ManualImportError("IMPORT_GENERATION_CHANGED")
    if (
        binding.subject_id != subject_id
        or store.root != snapshot_info.store_dir.resolve()
    ):
        raise ManualImportError("IMPORT_BINDING_MISMATCH")
    if state.recipe_hash != _digest(store.settings._payload.encode()):
        raise ManualImportError("IMPORT_RECIPE_MISMATCH")
    snapshot = store.open(version=snapshot_info.store_version)
    if snapshot is None:
        raise ManualImportError("IMPORT_REOPEN_FAILED")
    manifest = snapshot.manifest
    admitted = {f.source_id for f in state.files if f.status == "ok"}
    if (
        set(manifest.sources) != admitted
        or manifest.companies
        or any(
            f.source_id not in manifest.sources
            or manifest.sources[f.source_id].content_hash != f.pdf_hash
            for f in state.files
            if f.status == "ok"
        )
        or any(
            doc.admission_kind != "operator_folder"
            or doc.candidate_ids != (subject_id,)
            or doc.markdown_extraction is None
            or doc.markdown_extraction.recipe_hash != state.recipe_hash
            for doc in manifest.documents.values()
        )
    ):
        raise ManualImportError("IMPORT_SNAPSHOT_MISMATCH")
    # 동시에 새 가져오기가 시작됐다면 admission을 완료하지 않는다.
    current = ImportState.model_validate_json(folder.imports_path.read_bytes())
    if current != state:
        raise ManualImportError("IMPORT_GENERATION_CHANGED")
    return ReadyImport(state, snapshot, binding)
