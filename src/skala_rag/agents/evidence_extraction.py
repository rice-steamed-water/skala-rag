"""출처 구간 → 구조화 Evidence 추출과 provenance 검증 — #50, contracts §3, T05·T13·T18.

흐름:

1. **검증된 입력 구간** ``SourceSegment``만 추출에 쓴다.
   - Web/API: ``web_segment``가 실제 받은 ``RawSnapshot`` bytes와 Source snapshot의
     content_hash·source_id·위치, 그 fetch의 ``RetrievalRecord``를 대조한다. 검색
     결과 URL·제목만으로는 구간을 만들 수 없다.
   - RAG: ``rag_segment``가 실제 ``RetrievalRecord.chunk_ids``에 있고 같은 검색
     bundle로 반환된 Chunk인지, Chunk의 Source·page가 locator와 맞는지 확인한다.
   provenance(retrieval_id·method·chunk_id)는 이때 검증된 경로로만 만든다.
2. ``extract_evidence``가 versioned prompt로 ``StructuredLLM``을 호출하고, 모델 출력을
   원문과 대조한다. 발췌가 원문에 그대로 없음, 발췌에 없는 수치, 단위·금액 맥락 누락,
   다른 기업 귀속(기업 근거는 발췌에 대상 기업명·별칭이 있어야 함), 기준일 이후
   날짜, 원문 속 지시문은 Evidence로 만들지 않고
   ``RejectedClaim``으로 남긴다(원문·모델 출력 문자열은 담지 않는다). 발췌가 원문과
   공백만 다르면 ``locate_excerpt``로 원문 구간을 찾아 그 구간으로 검증·저장한다(#161).
   기업 자체 발행 Source는 발췌의 기업명을 요구하지 않는다. 긴 구간은 호출자가
   ``split_segment``로 나눠 넘긴다(#163).
3. Evidence ID는 ``contracts.ids.evidence_id``(식별 core, 경로 제외)로 만든다. 같은
   구간의 같은 주장을 Web·RAG로 다시 얻으면 같은 ID가 되고 #15 reducer
   (``merge_evidence``)가 provenance만 합친다.
4. ``verify_provenance``는 State에 들어가기 전 Evidence의 provenance가 실제 검색 이력·
   Chunk와 맞는지 다시 확인한다. 기존 Web 근거의 method만 rag로 바꾼 경우를 막는다.

정정은 새 Source snapshot에서 추출하면서 호출자가 ``supersedes``를 넘긴다(새 ID).
상충(``conflicts_with``)은 호출자가 넘기며 reducer가 합집합으로 보존한다. 파생값은
이 모듈이 만들지 않는다. LLM provider·재시도·예산은 #45·#47 범위이며 여기서는
``StructuredLLM`` 주입으로만 호출한다.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import ValidationError

from skala_rag.contracts.bundles import RetrievalBundle
from skala_rag.contracts.candidates import Candidate
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.evidence import Evidence, EvidenceProvenance
from skala_rag.contracts.ids import evidence_id, normalize_claim
from skala_rag.contracts.interfaces import LLMError, StructuredLLM
from skala_rag.contracts.retrieval import RetrievalRecord
from skala_rag.contracts.sources import Chunk, Source
from skala_rag.graph.reducers import merge_evidence
from skala_rag.prompt.evidence_extraction import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    ClaimDraft,
    ExtractionOutput,
    build_user_prompt,
)
from skala_rag.tools.source_fetch import RawSnapshot, check_as_of, snapshot_source_id

Scope = Literal["company", "industry"]


class SegmentError(ValueError):
    """구간이 Source snapshot·검색 이력과 대응하지 않는다. 추출하지 않는다."""


@dataclass(frozen=True)
class SourceSegment:
    """추출 입력. 검증 함수(``web_segment``·``rag_segment``)로만 만든다."""

    source: Source
    locator: str
    text: str
    scope: Scope
    candidate_ids: tuple[str, ...]
    provenance: EvidenceProvenance


def _base(source: Source) -> str:
    return source.url if source.url is not None else source.local_path  # type: ignore[return-value]


def _check_locator(source: Source, locator: str) -> None:
    base = _base(source)
    if locator != base and not locator.startswith(f"{base}#"):
        raise SegmentError("locator가 Source 위치를 가리키지 않음")


def _check_record(record: RetrievalRecord, source_id: str) -> None:
    if record.status != "ok":
        raise SegmentError("성공하지 않은 검색 이력")
    if source_id not in record.source_ids:
        raise SegmentError("검색 이력에 없는 Source")


def web_segment(
    raw: RawSnapshot,
    source: Source,
    record: RetrievalRecord,
    *,
    locator: str,
    scope: Scope,
    schema_version: str,
    method: Literal["web", "api"] = "web",
    text: str | None = None,
) -> SourceSegment:
    """실제 받은 원문 snapshot의 구간. ``text``를 주면 원문 안의 부분이어야 한다.

    본문은 UTF-8로 읽는다. HTML 태그 제거 등 본문 추출은 호출자 책임이며, 추출한
    ``text``가 받은 원문에 그대로 없으면 거절한다.
    """
    if source.content_hash != raw.content_hash:
        raise SegmentError("Source content_hash와 받은 원문 불일치")
    if source.source_id != snapshot_source_id(raw.locator, raw.content_hash):
        raise SegmentError("Source snapshot ID와 받은 원문 불일치")
    if _base(source) != raw.locator:
        raise SegmentError("Source 위치와 받은 원문 위치 불일치")
    _check_record(record, source.source_id)
    if record.chunk_ids:
        raise SegmentError("Web/API 경로에 Chunk 검색 이력을 쓸 수 없음")
    if scope == "company" and record.candidate_id is None:
        raise SegmentError("기업 근거에는 검색 이력의 candidate_id가 필요")
    _check_locator(source, locator)
    try:
        document = raw.content.decode("utf-8")
    except UnicodeDecodeError:
        raise SegmentError("UTF-8 본문이 아님") from None
    if text is None:
        text = document
    elif not text.strip() or text not in document:
        raise SegmentError("구간이 받은 원문에 없음")
    return SourceSegment(
        source=source,
        locator=locator,
        text=text,
        scope=scope,
        candidate_ids=(record.candidate_id,) if scope == "company" else (),
        provenance=EvidenceProvenance(
            schema_version=schema_version,
            retrieval_id=record.retrieval_id,
            method=method,
            chunk_id=None,
        ),
    )


_PAGE = re.compile(r"#page=(\d+)$")


def rag_segment(
    chunk: Chunk,
    bundle: RetrievalBundle,
    record: RetrievalRecord,
    *,
    schema_version: str,
) -> SourceSegment:
    """실제 검색 결과로 반환된 Chunk만 RAG 구간이 된다."""
    if chunk.chunk_id not in record.chunk_ids:
        raise SegmentError("검색 이력에 없는 Chunk")
    returned = [c for c in bundle.chunks if c.chunk_id == chunk.chunk_id]
    if returned != [chunk]:
        raise SegmentError("검색 bundle이 반환하지 않았거나 내용이 다른 Chunk")
    source = bundle.sources.get(chunk.source_id)
    if source is None:
        raise SegmentError("Chunk Source payload 없음")
    _check_record(record, chunk.source_id)
    _check_locator(source, chunk.locator)
    page = _PAGE.search(chunk.locator)
    if page is not None and chunk.page_start is not None:
        last = chunk.page_end if chunk.page_end is not None else chunk.page_start
        if not chunk.page_start <= int(page.group(1)) <= last:
            raise SegmentError("Chunk locator page와 page 범위 불일치")
    if chunk.scope == "company" and record.candidate_id not in chunk.candidate_ids:
        raise SegmentError("검색 대상 기업과 Chunk 기업 불일치")
    return SourceSegment(
        source=source,
        locator=chunk.locator,
        text=chunk.text,
        scope=chunk.scope,
        candidate_ids=tuple(chunk.candidate_ids) if chunk.scope == "company" else (),
        provenance=EvidenceProvenance(
            schema_version=schema_version,
            retrieval_id=record.retrieval_id,
            method="rag",
            chunk_id=chunk.chunk_id,
        ),
    )


# --- 모델 출력 검증 ------------------------------------------------------------


class ClaimRejection(StrEnum):
    EXCERPT_NOT_IN_SOURCE = "EXCERPT_NOT_IN_SOURCE"
    VALUE_NOT_IN_EXCERPT = "VALUE_NOT_IN_EXCERPT"
    CLAIM_NUMBER_NOT_IN_EXCERPT = "CLAIM_NUMBER_NOT_IN_EXCERPT"
    UNIT_MISSING = "UNIT_MISSING"
    UNIT_WITHOUT_VALUE = "UNIT_WITHOUT_VALUE"
    UNIT_NOT_IN_EXCERPT = "UNIT_NOT_IN_EXCERPT"
    MONEY_CONTEXT_MISSING = "MONEY_CONTEXT_MISSING"
    CURRENCY_NOT_IN_EXCERPT = "CURRENCY_NOT_IN_EXCERPT"
    SUBJECT_MISSING = "SUBJECT_MISSING"
    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
    SUBJECT_NOT_IN_EXCERPT = "SUBJECT_NOT_IN_EXCERPT"
    DATE_AFTER_AS_OF = "DATE_AFTER_AS_OF"
    DATE_NOT_IN_SOURCE = "DATE_NOT_IN_SOURCE"
    INSTRUCTION_IN_OUTPUT = "INSTRUCTION_IN_OUTPUT"
    BLANK_FIELD = "BLANK_FIELD"


@dataclass(frozen=True)
class RejectedClaim:
    """거절 기록. 원문·모델 출력 문자열은 담지 않는다(로그 누출 방지)."""

    index: int
    reason: ClaimRejection


@dataclass(frozen=True)
class ExtractionResult:
    evidence: dict[str, Evidence]
    rejected: list[RejectedClaim] = field(default_factory=list)
    prompt_version: str = PROMPT_VERSION


_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")

_INSTRUCTION = re.compile(
    r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+"
    r"(?:instructions?|prompts?|rules?)"
    r"|disregard\s+(?:all\s+|the\s+)?(?:previous\s+|above\s+)?instructions?"
    r"|system\s+prompt|developer\s+message|you\s+are\s+now"
    r"|reveal\s+(?:the\s+)?(?:api[\s_-]?key|secret|password|token)"
    r"|(?:api[\s_-]?key|secret[\s_-]?key|password)\s*[:=]"
    r"|(?:이전|위의?|앞의?)\s*(?:모든\s*)?(?:지시|명령|규칙)"
    r"|지시(?:사항)?(?:을|를)?\s*무시|시스템\s*프롬프트|(?:api|API)\s*키"
    r"|비밀\s*(?:번호|키)",
    re.IGNORECASE,
)

# 금액 코드별 원문 표기. 목록에 없는 코드는 코드 문자열 자체가 발췌에 있어야 한다.
_CURRENCY_MARKERS = {
    "KRW": ("KRW", "원", "₩"),
    "USD": ("USD", "US$", "$", "달러"),
    "EUR": ("EUR", "€", "유로"),
    "JPY": ("JPY", "¥", "엔"),
    "CNY": ("CNY", "위안", "元"),
}


def _numbers(text: str) -> set[float]:
    return {float(m.replace(",", "")) for m in _NUMBER.findall(text)}


def _name_key(name: str) -> str:
    return "".join(unicodedata.normalize("NFKC", name).casefold().split())


def locate_excerpt(excerpt: str, text: str) -> str | None:
    """발췌에 대응하는 원문 구간. 공백(띄어쓰기·줄바꿈)만 다를 때까지 허용한다.

    PDF 추출 텍스트는 공백이 여러 칸이거나 줄이 바뀌어 있어 모델이 정리한 발췌와
    글자 그대로 맞지 않는다(#161). 공백을 뺀 문자열로 첫 위치를 찾고, **원문에 그대로
    있는 구간**을 돌려준다. 공백 외 문자가 하나라도 다르면 None이다.
    """
    needle = "".join(excerpt.split())
    if not needle:
        return None
    if excerpt in text:
        return excerpt
    positions = [i for i, ch in enumerate(text) if not ch.isspace()]
    compact = "".join(text[i] for i in positions)
    start = compact.find(needle)
    if start < 0:
        return None
    return text[positions[start] : positions[start + len(needle) - 1] + 1]


def _own_publication(source: Source, names: set[str]) -> bool:
    """발행처 또는 저자에 대상 기업명·별칭이 있으면 기업 자체 발행 문서다(#163)."""
    fields = [_name_key(f) for f in (source.publisher, source.author) if f]
    return any(name in field for field in fields for name in names)


def split_segment(segment: SourceSegment, max_bytes: int) -> list[SourceSegment]:
    """검증된 구간을 UTF-8 ``max_bytes`` 이하 조각으로 나눈다(#163).

    줄 경계에서 나누고, 한 줄이 상한을 넘으면 글자 단위로 자른다. 조각은 원문의
    연속 부분 문자열이라 Source·locator·provenance를 그대로 쓴다. 공백뿐인 조각은
    버린다. 조각 경계를 걸치는 주장은 복원하지 않는다.
    """
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    pieces: list[str] = []
    current = ""
    for line in segment.text.splitlines(keepends=True):
        while len(line.encode()) > max_bytes:
            cut = len(line)
            while len(line[:cut].encode()) > max_bytes:
                cut -= 1
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:cut])
            line = line[cut:]
        if len((current + line).encode()) > max_bytes:
            pieces.append(current)
            current = ""
        current += line
    pieces.append(current)
    return [replace(segment, text=piece) for piece in pieces if piece.strip()]


def _check_claim(
    draft: ClaimDraft,
    segment: SourceSegment,
    candidate: Candidate | None,
    as_of: date,
) -> ClaimRejection | None:
    texts = [
        draft.claim,
        draft.excerpt,
        draft.subject,
        draft.unit,
        draft.currency,
        draft.period,
        draft.geography,
        *draft.limitations,
    ]
    if any(t is not None and not t.strip() for t in texts):
        return ClaimRejection.BLANK_FIELD
    if any(t is not None and _INSTRUCTION.search(t) for t in texts):
        return ClaimRejection.INSTRUCTION_IN_OUTPUT
    if draft.excerpt not in segment.text:
        return ClaimRejection.EXCERPT_NOT_IN_SOURCE
    excerpt_numbers = _numbers(draft.excerpt)
    if not _numbers(draft.claim) <= excerpt_numbers:
        return ClaimRejection.CLAIM_NUMBER_NOT_IN_EXCERPT
    if draft.value is None:
        if draft.unit is not None or draft.currency is not None:
            return ClaimRejection.UNIT_WITHOUT_VALUE
    else:
        if abs(float(draft.value)) not in excerpt_numbers:
            return ClaimRejection.VALUE_NOT_IN_EXCERPT
        if draft.unit is None:
            return ClaimRejection.UNIT_MISSING
        if draft.unit.casefold() not in draft.excerpt.casefold():
            return ClaimRejection.UNIT_NOT_IN_EXCERPT
    if draft.currency is not None:
        if draft.value_as_of is None:
            return ClaimRejection.MONEY_CONTEXT_MISSING
        markers = _CURRENCY_MARKERS.get(draft.currency.upper(), (draft.currency,))
        if not any(m.casefold() in draft.excerpt.casefold() for m in markers):
            return ClaimRejection.CURRENCY_NOT_IN_EXCERPT
    elif draft.value_as_of is not None:
        return ClaimRejection.MONEY_CONTEXT_MISSING
    for day in (draft.event_date, draft.value_as_of):
        if day is None:
            continue
        if day > as_of:
            return ClaimRejection.DATE_AFTER_AS_OF
        if str(day.year) not in segment.text:
            return ClaimRejection.DATE_NOT_IN_SOURCE
    if segment.scope == "company":
        assert candidate is not None
        if draft.subject is None:
            return ClaimRejection.SUBJECT_MISSING
        names = {_name_key(n) for n in [candidate.canonical_name, *candidate.aliases]}
        if _name_key(draft.subject) not in names:
            return ClaimRejection.SUBJECT_MISMATCH
        # 다른 기업 발췌에 대상 기업을 subject로 붙이는 경우를 막는다. 기업이 직접
        # 펴낸 Source(발행처·저자에 기업명)만 발췌의 기업명을 요구하지 않는다(#163).
        # 제3자 문서의 기업명 없는 발췌("당사는 …")는 계속 거절한다.
        excerpt_key = _name_key(draft.excerpt)
        if not _own_publication(segment.source, names) and not any(
            name in excerpt_key for name in names
        ):
            return ClaimRejection.SUBJECT_NOT_IN_EXCERPT
    return None


def extract_evidence(
    segment: SourceSegment,
    *,
    llm: StructuredLLM,
    candidate: Candidate | None,
    criterion_ids: Sequence[str],
    as_of: date,
    schema_version: str,
    execution_mode: Literal["fixture", "live"],
    supersedes: str | None = None,
    conflicts_with: Sequence[str] = (),
) -> ExtractionResult:
    """구간 하나에서 검증을 통과한 ``reported`` Evidence만 만든다.

    - 구간이 대상 기업·기준일과 맞지 않으면 ``SegmentError``(LLM 호출 안 함).
    - 모델 출력 schema 오류는 ``LLMError(LLM_OUTPUT_INVALID)``, 그 밖의 LLM 오류는
      그대로 전파한다. 재시도·예산은 호출 wrapper 책임이다.
    - criterion_ids·supersedes·conflicts_with는 호출자(수집 controller)가 정한다.
    """
    if segment.scope == "company":
        if candidate is None or candidate.candidate_id not in segment.candidate_ids:
            raise SegmentError("구간과 대상 기업 불일치")
    decision = check_as_of(segment.source, as_of)
    if not decision.admitted:
        raise SegmentError(f"기준일 정책으로 제외된 Source: {decision.reason}")

    names = [] if candidate is None else [candidate.canonical_name, *candidate.aliases]
    prompt = build_user_prompt(
        source_text=segment.text,
        scope=segment.scope,
        target_names=names,
        as_of=as_of,
        criterion_ids=list(criterion_ids),
    )
    try:
        output = llm.generate(
            system=SYSTEM_PROMPT, user=prompt, output_schema=ExtractionOutput
        )
        output = ExtractionOutput.model_validate(output.model_dump())
    except ValidationError as err:
        raise LLMError(
            ErrorCode.LLM_OUTPUT_INVALID, f"추출 schema 오류 {err.error_count()}건"
        ) from None

    owner = candidate.candidate_id if segment.scope == "company" else None
    context = {"execution_mode": execution_mode}
    evidence: dict = {}
    rejected: list[RejectedClaim] = []
    for index, draft in enumerate(output.claims):
        span = locate_excerpt(draft.excerpt, segment.text)
        if span is not None:
            # 저장·검증은 모델 출력이 아니라 원문 구간으로 한다.
            draft = draft.model_copy(update={"excerpt": span})
        reason = _check_claim(draft, segment, candidate, as_of)
        if reason is not None:
            rejected.append(RejectedClaim(index, reason))
            continue
        core = dict(
            source_id=segment.source.source_id,
            locator=segment.locator,
            claim=normalize_claim(draft.claim),
            candidate_id=owner,
            scope=segment.scope,
            value=draft.value,
            unit=draft.unit,
            currency=None if draft.currency is None else draft.currency.upper(),
            value_as_of=draft.value_as_of,
            period=draft.period,
            geography=draft.geography,
            event_date=draft.event_date,
            evidence_kind="reported",
            supporting_evidence_ids=[],
            derivation=None,
            supersedes=supersedes,
        )
        item = Evidence.model_validate(
            {
                "schema_version": schema_version,
                "evidence_id": evidence_id(**core),
                **core,
                "criterion_ids": list(criterion_ids),
                "excerpt": draft.excerpt,
                "provenance": [segment.provenance.model_dump(mode="json")],
                "confidence": draft.confidence,
                "limitations": draft.limitations,
                "conflicts_with": list(conflicts_with),
            },
            context=context,
        )
        evidence = merge_evidence(
            evidence, {item.evidence_id: item.model_dump(mode="json")}
        )
    return ExtractionResult(
        evidence={
            key: Evidence.model_validate(payload, context=context)
            for key, payload in evidence.items()
        },
        rejected=rejected,
    )


def link_record(
    record: RetrievalRecord, result: ExtractionResult, segment: SourceSegment
) -> RetrievalRecord:
    """구간의 검색 이력 복사본에 이번 추출 Evidence ID를 순서대로 더한다."""
    if record.retrieval_id != segment.provenance.retrieval_id:
        raise SegmentError("구간과 다른 검색 이력")
    ids = list(record.evidence_ids)
    ids += [key for key in result.evidence if key not in ids]
    return record.model_copy(update={"evidence_ids": ids}, deep=True)


# --- provenance 사후 검증 ------------------------------------------------------


def recompute_evidence_id(item: Evidence) -> str:
    return evidence_id(
        source_id=item.source_id,
        locator=item.locator,
        claim=item.claim,
        candidate_id=item.candidate_id,
        scope=item.scope,
        value=item.value,
        unit=item.unit,
        currency=item.currency,
        value_as_of=item.value_as_of,
        period=item.period,
        geography=item.geography,
        event_date=item.event_date,
        evidence_kind=item.evidence_kind,
        supporting_evidence_ids=item.supporting_evidence_ids,
        derivation=item.derivation,
        supersedes=item.supersedes,
    )


def verify_provenance(
    item: Evidence,
    *,
    records: Mapping[str, RetrievalRecord],
    chunks: Mapping[str, Chunk],
) -> list[str]:
    """위반 사유 목록(빈 목록이면 통과). 메시지에 원문·URL을 넣지 않는다.

    - ID가 식별 core에서 다시 계산한 값과 같아야 한다.
    - 모든 경로는 실제 성공 검색 이력이 있고 그 이력에 Evidence Source가 있어야 한다.
    - rag: 이력의 chunk_ids에 Chunk가 있고, Chunk의 Source·locator가 Evidence와 같으며
      발췌가 Chunk 원문에 있어야 한다. web/api/manual은 chunk_id가 없어야 한다.
    """
    problems = []
    if item.claim != normalize_claim(item.claim):
        problems.append("claim이 정규화되지 않음")
    if item.evidence_id != recompute_evidence_id(item):
        problems.append("evidence_id가 식별 core와 불일치")
    for path in item.provenance:
        record = records.get(path.retrieval_id)
        if record is None or record.status != "ok":
            problems.append(f"{path.method}: 성공 검색 이력 없음")
            continue
        if item.source_id not in record.source_ids:
            problems.append(f"{path.method}: 검색 이력에 Source 없음")
        if item.evidence_id not in record.evidence_ids:
            problems.append(f"{path.method}: 검색 이력이 Evidence를 기록하지 않음")
        if path.method != "rag":
            if path.chunk_id is not None or record.chunk_ids:
                problems.append(f"{path.method}: Chunk 검색 이력과 섞임")
            continue
        chunk = chunks.get(path.chunk_id)  # type: ignore[arg-type]
        if path.chunk_id not in record.chunk_ids or chunk is None:
            problems.append("rag: 실제 반환 Chunk 아님")
        elif (
            chunk.source_id != item.source_id
            or chunk.locator != item.locator
            or item.excerpt not in chunk.text
        ):
            problems.append("rag: Chunk Source·locator·발췌 불일치")
    return problems
