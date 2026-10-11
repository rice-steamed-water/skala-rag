"""보고서 형식 규약 — reporting.md §1–§4 (D09 승인), #27 구현 규약.

Generator(#28)와 Structural Validator(#27)가 같은 함수를 써서 목차·표·인용·
REFERENCE 문자열을 만든다. 표 형식은 reporting.md의 "평가 결과 표"·"후보 결과
표"를 기계 검사할 수 있게 고정한 구현 규약이다.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

from skala_rag.contracts import (
    Chunk,
    Evidence,
    Source,
)
from skala_rag.contracts.company_report import CompositionReply

if TYPE_CHECKING:
    from skala_rag.reporting.company_context import CompanyWriterContext


def evidence_token(evidence_id: str) -> str:
    return f"[@evidence:{evidence_id}]"


def _year(value: date | datetime | None) -> str | None:
    return None if value is None else f"{value.year:04d}"


def _day(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return (
        value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    )


def _location(source: Source) -> str:
    if source.url:
        return source.url
    if source.local_path and not source.local_path.startswith(("/", "~")):
        return source.local_path  # 재배포 가능한 상대 locator만
    return "온라인 주소 없음"


def bibliography(source: Source) -> str:
    """Source metadata만으로 서지 문자열을 만든다. 미상 필드는 승인 표기."""
    meta = source.bibliographic_metadata
    loc = _location(source)
    if source.source_kind == "paper":
        author = source.author or "저자 미상"
        year = _year(source.published_at) or "발행연도 미상"
        journal = meta.get("journal") or "학술지 미상"
        volume = meta.get("volume_issue") or "권호 미상"
        pages = meta.get("pages") or "페이지 미상"
        return f"{author}({year}). {source.title}. {journal}, {volume}, {pages}."
    if source.source_kind in ("report", "filing"):
        org = source.publisher or "발행기관 미상"
        year = _year(source.published_at) or "발행연도 미상"
        return f"{org}({year}). {source.title}. {loc}"
    who = source.publisher or source.author or "발행기관 미상"
    day = _day(source.published_at) or "발행일 미상"
    site = meta.get("site_name") or "사이트명 미상"
    return f"{who}({day}). {source.title}. {site}, {loc}"


def reference_line(source: Source) -> str:
    return f"- [@source:{source.source_id}] {bibliography(source)}"


class NarrativeCitationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class NarrativeReference:
    """Exact private-registry locator, for renderer use only."""

    evidence_id: str
    source_id: str
    chunk_id: str
    locator: str
    page_start: int | None
    page_end: int | None
    url: str | None


@dataclass(frozen=True, slots=True)
class FinalizedNarrative:
    """Deterministic renderer/controller input; never send references to a model."""

    context_id: str
    markdown: str
    cited_evidence_ids: tuple[str, ...]
    reference_source_ids: tuple[str, ...]
    references: tuple[NarrativeReference, ...]


def finalize_narrative(
    composition: CompositionReply, context: "CompanyWriterContext"
) -> FinalizedNarrative:
    """Resolve only task 3 admitted originals; do not acquire or invent support.

    Supporting evidence must be explicitly cited in the body and metadata too.
    Composition validation is contextual, including previously parsed instances.
    This checks citation integrity, not semantic support or PDF acceptance.
    """
    # Local imports avoid the legacy validator -> format -> context import cycle.
    from skala_rag.reporting.validator import TOKEN

    context.verify_binding()
    writer = context.writer_context
    reply = CompositionReply.model_validate(
        composition, context={"writer_context": writer}
    )
    body = reply.markdown_without_reference
    if re.search(r"(?im)^\s*#{1,6}\s+REFERENCE\b", body) or "[@source:" in body:
        raise NarrativeCitationError("MODEL_REFERENCE_FORBIDDEN")
    if "[@evidence:" in TOKEN.sub("", body):
        raise NarrativeCitationError("MALFORMED_CITATION")
    cited = set(TOKEN.findall(body))
    if cited != set(reply.cited_evidence_ids):
        raise NarrativeCitationError("BODY_CITATION_MISMATCH")
    if any(
        not set(writer.evidence[eid].supporting_evidence_ids) <= cited for eid in cited
    ):
        raise NarrativeCitationError("UNCITED_SUPPORT_EVIDENCE")

    registry = context.citation_registry["citations"]
    sources: dict[str, Source] = {}
    references: list[NarrativeReference] = []
    for eid in sorted(cited):
        receipt = registry[eid]
        original = Evidence.model_validate(receipt["original_evidence"])
        source = Source.model_validate(receipt["source"])
        if source.bibliographic_metadata.get("generated_report"):
            raise NarrativeCitationError("GENERATED_SOURCE_FORBIDDEN")
        sources[source.source_id] = source
        for chunk_id in sorted(receipt["chunks"]):
            chunk = Chunk.model_validate(receipt["chunks"][chunk_id])
            if (
                original.evidence_id != eid
                or original.source_id != source.source_id
                or chunk.source_id != source.source_id
                or chunk.locator != original.locator
                or receipt["locator"] != original.locator
            ):
                raise NarrativeCitationError("ORIGINAL_LOCATOR_MISMATCH")
            references.append(
                NarrativeReference(
                    eid,
                    source.source_id,
                    chunk.chunk_id,
                    chunk.locator,
                    chunk.page_start,
                    chunk.page_end,
                    source.url,
                )
            )
    lines = []
    for sid, source in sorted(sources.items()):
        # The legacy paper bibliography omits URLs; narrative references never do.
        lines.append(f"{reference_line(source)} | 원문 URL: {source.url or '미상'}")
        for ref in references:
            if ref.source_id != sid:
                continue
            pages = (
                "페이지 미상"
                if ref.page_start is None and ref.page_end is None
                else f"pages {ref.page_start if ref.page_start is not None else '?'}"
                f"-{ref.page_end if ref.page_end is not None else '?'}"
            )
            lines.append(
                f"  - evidence={ref.evidence_id}; chunk={ref.chunk_id}; "
                f"locator={ref.locator}; {pages}"
            )
    return FinalizedNarrative(
        writer.context_id,
        body.rstrip() + "\n\n## REFERENCE\n\n" + "\n".join(lines) + "\n",
        tuple(sorted(cited)),
        tuple(sorted(sources)),
        tuple(references),
    )
