"""승인 PDF의 페이지 원문 보존 추출. OCR·표 의미 추론·embedding은 하지 않는다."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from typing import Literal
from urllib.parse import urldefrag

from pypdf import PdfReader

from skala_rag.contracts import Chunk, Source
from skala_rag.rag.corpus import ManifestDocument
from skala_rag.tools.source_fetch import content_hash


@dataclass(frozen=True)
class PageChunkSettings:
    """페이지 atomic 분할 설정. 임의의 tokenizer·overlap 기본값을 두지 않는다."""

    max_characters: int
    overlap: int
    tokenizer: Literal["none-page-atomic"]
    document_kind: Literal["technical_whitepaper", "product_document"]
    version: str

    def __post_init__(self):
        if type(self.max_characters) is not int or self.max_characters <= 0:
            raise ValueError("max_characters는 양의 정수여야 합니다")
        if type(self.overlap) is not int or self.overlap != 0:
            raise ValueError("페이지 atomic 경로는 overlap=0만 지원합니다")
        if self.tokenizer != "none-page-atomic":
            raise ValueError("tokenizer 분할은 지원하지 않습니다")
        if self.document_kind not in ("technical_whitepaper", "product_document"):
            raise ValueError("기술 백서·제품 문서 PDF만 지원합니다")
        if not self.version.strip():
            raise ValueError("chunk 설정 버전이 필요합니다")


@dataclass(frozen=True)
class ExtractionIssue:
    code: str
    page: int | None


@dataclass(frozen=True)
class ExtractionResult:
    status: Literal["ok", "partial", "failed"]
    chunks: tuple[Chunk, ...]
    page_count: int | None
    issues: tuple[ExtractionIssue, ...]
    settings: PageChunkSettings
    document_id: str
    source_id: str
    content_hash: str
    corpus_version: str


def extract_pdf(
    content: bytes,
    document: ManifestDocument,
    source: Source,
    *,
    corpus_version: str,
    schema_version: str,
    settings: PageChunkSettings,
    sections_by_page: Mapping[int, str],
    embedding_model: str,
    embedding_revision: str,
    execution_mode: Literal["fixture", "live"],
) -> ExtractionResult:
    """승인 snapshot bytes를 PDF page 기반 Chunk로 변환한다.

    extraction_status=pending도 추출 입력으로 허용한다. 인덱싱 승인은 추출 결과를
    검토해 새 manifest에 기록한 뒤 #44 gate에서 별도로 확인해야 한다.
    표 구조를 확정하지 않고 페이지 전체를 보존하므로 제목·단위·주석을 자르지 않는다.
    이미지나 읽을 수 없는 페이지는 성공으로 숨기지 않으며 수치를 생성하지 않는다.
    """
    if execution_mode not in ("fixture", "live"):
        raise ValueError("명시적 실행 모드가 필요합니다")
    if execution_mode == "live" and (
        document.local_path.startswith("fixture://")
        or (source.url or "").startswith("fixture://")
    ):
        raise ValueError("가상 문서는 live 추출에 사용할 수 없습니다")
    if not document.approved or not document.reviewer:
        raise ValueError("승인·검토자가 있는 문서만 추출할 수 있습니다")
    if document.source_id != source.source_id:
        raise ValueError("manifest와 Source ID가 다릅니다")
    if document.language != source.language:
        raise ValueError("manifest와 Source 언어가 다릅니다")
    digest = content_hash(content)
    if digest != document.content_hash or digest != source.content_hash:
        raise ValueError("원문 bytes와 manifest/Source hash가 다릅니다")
    if document.local_path != source.local_path:
        raise ValueError("manifest와 Source snapshot 경로가 다릅니다")
    chunks, issues = [], []
    page_count = None
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise ValueError("암호화 PDF 미지원")
        page_count = len(reader.pages)
        if any(
            type(page) is not int
            or not 1 <= page <= page_count
            or not isinstance(section, str)
            or not section.strip()
            for page, section in sections_by_page.items()
        ):
            raise ValueError("section metadata 페이지·제목 오류")
        for number, page in enumerate(reader.pages, start=1):
            if "/Contents" not in page:
                issues.append(ExtractionIssue("NO_EXTRACTABLE_TEXT", number))
                continue
            try:
                text = page.extract_text(extraction_mode="layout")
            except Exception:
                issues.append(ExtractionIssue("TEXT_EXTRACTION_FAILED", number))
                continue
            if not text or not text.strip():
                issues.append(ExtractionIssue("NO_EXTRACTABLE_TEXT", number))
                continue
            resources = page.get("/Resources", {})
            resources = (
                resources.get_object()
                if hasattr(resources, "get_object")
                else resources
            )
            xobjects = resources.get("/XObject", {})
            xobjects = (
                xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
            )
            if xobjects:
                # Form 내부 이미지 가능성도 시각 payload 누락으로 표시한다.
                issues.append(ExtractionIssue("VISUAL_CONTENT_NOT_EXTRACTED", number))
            if len(text) > settings.max_characters:
                issues.append(ExtractionIssue("PAGE_EXCEEDS_CHARACTER_LIMIT", number))
            section = sections_by_page.get(number)
            key = json.dumps(
                [
                    "page-chunk-v1",
                    document.source_id,
                    digest,
                    corpus_version,
                    settings.__dict__,
                    number,
                    section,
                    text,
                    embedding_model,
                    embedding_revision,
                ],
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            identifier = "chunk-page-v1-" + hashlib.sha256(key).hexdigest()
            locator = urldefrag(source.url or source.local_path)[0] + f"#page={number}"
            chunk = Chunk.model_validate(
                dict(
                    schema_version=schema_version,
                    chunk_id=identifier,
                    source_id=source.source_id,
                    corpus_version=corpus_version,
                    text=text,
                    page_start=number,
                    page_end=number,
                    section=section,
                    locator=locator,
                    candidate_ids=list(document.candidate_ids),
                    scope=document.scope,
                    language=document.language,
                    embedding_model=embedding_model,
                    embedding_revision=embedding_revision,
                ),
                context={"execution_mode": execution_mode},
            )
            chunks.append(chunk)
    except Exception:
        issues.append(ExtractionIssue("PDF_UNREADABLE", None))
        chunks = []
    status = "failed" if not chunks else "partial" if issues else "ok"
    return ExtractionResult(
        status,
        tuple(chunks),
        page_count,
        tuple(issues),
        settings,
        document.document_id,
        source.source_id,
        digest,
        corpus_version,
    )
