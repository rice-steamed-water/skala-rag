"""공식 원문 → 적격성 관측(``FieldObservation``) 추출과 검증 — #51, T04, D06.

``LLMEligibilityExtractor``는 ``tools.official_homepage.SourceFactExtractor``다.
``StructuredLLM``(#47 adapter, live는 #45 runtime 경유)을 주입받아 한 Source 원문에서
사실 후보를 받고, 원문과 대조해 통과한 것만 관측으로 만든다. 식별(``identity``)은
만들지 않는다. 법인 식별은 등기·공시 식별자 경로(OpenDART 등)의 몫이다.

거절 규칙(``FactRejection``, 원문·모델 출력 문자열은 기록하지 않는다):
- 발췌가 원문(태그 제거 텍스트)에 그대로 없음, 모델 출력 속 지시문(#50 규칙 재사용).
- 주체가 대상 기업명·별칭이 아님. 상장·Exit·단계는 발췌에도 대상 기업명 또는
  1인칭 표현("당사", "we" 등)이 있어야 한다(고객·파트너의 사건을 붙이지 않음).
- 상장·Exit 발췌에 해당 주제어가 없음. 언급 없음을 false로 만들지 않는다.
- 단계 표기가 발췌에 없음. 표기는 D06 규칙으로 코드가 정규화한다: 프리시드·엔젤·
  Series D 이상은 out_of_scope, 프리A/B/C·브릿지·TIPS 단독은 단계 근거로 쓰지 않음.
- 사건일이 기준일 이후이거나 그 연도가 발췌에 없음.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from html.parser import HTMLParser
from urllib.parse import quote

from skala_rag.agents.eligibility import (
    FIELD_BUSINESS,
    FIELD_DOMAIN,
    FIELD_EXIT,
    FIELD_LISTING,
    FIELD_STAGE,
)
from skala_rag.agents.evidence_extraction import _INSTRUCTION, _name_key
from skala_rag.contracts.candidates import Candidate
from skala_rag.contracts.interfaces import StructuredLLM
from skala_rag.contracts.sources import Source
from skala_rag.prompts.eligibility_facts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    EligibilityFactsOutput,
    FactDraft,
    build_user_prompt,
)
from skala_rag.tools.company_research import FieldObservation, StageObservation
from skala_rag.tools.official_homepage import ExtractedFacts

SELF_LIMITATION = "기업 공식 홈페이지의 자기 진술이며 독립 검증 아님"


class FactRejection(StrEnum):
    BLANK_FIELD = "BLANK_FIELD"
    INSTRUCTION_IN_OUTPUT = "INSTRUCTION_IN_OUTPUT"
    EXCERPT_NOT_IN_SOURCE = "EXCERPT_NOT_IN_SOURCE"
    VALUE_SHAPE = "VALUE_SHAPE"
    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
    SUBJECT_NOT_IN_EXCERPT = "SUBJECT_NOT_IN_EXCERPT"
    TOPIC_NOT_IN_EXCERPT = "TOPIC_NOT_IN_EXCERPT"
    STAGE_LABEL_NOT_IN_EXCERPT = "STAGE_LABEL_NOT_IN_EXCERPT"
    STAGE_HINT_ONLY = "STAGE_HINT_ONLY"
    STAGE_UNRECOGNIZED = "STAGE_UNRECOGNIZED"
    DATE_AFTER_AS_OF = "DATE_AFTER_AS_OF"
    DATE_NOT_IN_EXCERPT = "DATE_NOT_IN_EXCERPT"


# ---------------------------------------------------------------- 원문 텍스트


class _Text(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template", "svg"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _squash(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def visible_text(content: bytes, content_type: str | None = None) -> str:
    """HTML이면 script·style을 뺀 표시 텍스트, 아니면 본문. 공백은 한 칸으로 줄인다.

    실행·렌더링하지 않는다. 디코딩할 수 없는 byte는 대체 문자로 둔다.
    """
    charset = "utf-8"
    if content_type and "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=")[-1].split(";")[0].strip()
    try:
        text = content.decode(charset, errors="replace")
    except LookupError:
        text = content.decode("utf-8", errors="replace")
    looks_html = (
        "html" in (content_type or "").lower() or "<html" in text[:2000].lower()
    )
    if looks_html:
        parser = _Text()
        parser.feed(text)
        parser.close()
        text = " ".join(parser.parts)
    return _squash(text)


# ---------------------------------------------------------------- 단계 정규화


def _stage_key(label: str) -> str:
    return re.sub(r"[\s_\-.·]", "", unicodedata.normalize("NFKC", label).casefold())


_PRE_SEED = re.compile(r"preseed|프리시드|angel|엔젤")
_BRIDGE = re.compile(r"pre(?:series)?[abc]|프리(?:시리즈)?[abc]|bridge|브릿지|브리지")
_LATE = re.compile(r"(?:series|시리즈)[d-z]")
_SERIES = re.compile(r"(?:series|시리즈)([abc])")
_SEED = re.compile(r"seed|시드")
_TIPS = re.compile(r"tips")


def normalize_round(label: str) -> tuple[str | None, FactRejection | None]:
    """원문 표기 → StageInfo.normalized_round. D06 규칙만 쓴다.

    반환 ``(round, None)`` 또는 ``(None, 사유)``. 라벨에 TIPS가 함께 있으면 round는
    유지하고 판정 단계(``check_eligibility``)의 STAGE_TIPS_ONLY 규칙에 맡긴다.
    """
    key = _stage_key(label)
    if _PRE_SEED.search(key):
        return "out_of_scope", None
    if _BRIDGE.search(key):
        # 명칭만으로 다음 라운드로 올리지 않는다. 직전 완료 라운드 근거가 따로 필요.
        return None, FactRejection.STAGE_HINT_ONLY
    if _LATE.search(key):
        return "out_of_scope", None
    if match := _SERIES.search(key):
        return f"series_{match.group(1)}", None
    if _SEED.search(key):
        return "seed", None
    if _TIPS.search(key):
        return None, FactRejection.STAGE_HINT_ONLY
    return None, FactRejection.STAGE_UNRECOGNIZED


# ---------------------------------------------------------------- 검증


_FIRST_PERSON = re.compile(r"당사|저희|우리|\bwe\b|\bour\b|\bus\b", re.IGNORECASE)
_TOPIC = {
    FIELD_LISTING: re.compile(
        r"상장|코스닥|코스피|코넥스|kosdaq|kospi|konex|nasdaq|nyse|ipo|listed|"
        r"stock exchange|증권거래소",
        re.IGNORECASE,
    ),
    FIELD_EXIT: re.compile(
        r"인수|합병|매각|엑싯|엑시트|exit|acquir|merger|m&a|상장|ipo",
        re.IGNORECASE,
    ),
}
_BOOLEAN = frozenset({FIELD_DOMAIN, FIELD_LISTING, FIELD_EXIT})
_EVENT_FIELDS = frozenset({FIELD_LISTING, FIELD_EXIT, FIELD_STAGE})


@dataclass(frozen=True)
class _Checked:
    observation: FieldObservation | None
    rejection: FactRejection | None


class LLMEligibilityExtractor:
    """``SourceFactExtractor``. ``domain_definition``·``max_input_chars``는 승인
    설정에서 주입한다(기본값 없음). 원문이 길면 앞부분만 보내고 한계를 기록한다.

    ``StructuredLLM``의 ``LLMError``는 그대로 올린다. provider가 도구 결과를 실패로
    바꾸지 않고 추출 실패로 기록한다.
    """

    version = PROMPT_VERSION

    def __init__(
        self,
        llm: StructuredLLM,
        *,
        as_of: date,
        domain_definition: str,
        max_input_chars: int,
    ) -> None:
        if not domain_definition.strip():
            raise ValueError("domain_definition is required")
        if max_input_chars < 1:
            raise ValueError("max_input_chars must be positive")
        self._llm = llm
        self._as_of = as_of
        self._domain = domain_definition
        self._max_chars = max_input_chars

    def __call__(
        self,
        candidate: Candidate,
        source: Source,
        content: bytes,
        content_type: str | None = None,
    ) -> ExtractedFacts:
        text = visible_text(content, content_type)
        notes: list[str] = []
        if not text:
            return ExtractedFacts(observations=(), notes=("SOURCE_TEXT_EMPTY",))
        sent = text[: self._max_chars]
        limitations = [SELF_LIMITATION]
        if len(sent) < len(text):
            notes.append("SOURCE_TEXT_TRUNCATED")
            limitations.append(f"원문 앞 {len(sent)}자만 검토")
        output = self._llm.generate(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(
                source_text=sent,
                target_names=[candidate.canonical_name, *candidate.aliases],
                domain_definition=self._domain,
                as_of=self._as_of,
            ),
            output_schema=EligibilityFactsOutput,
        )
        observations: list[FieldObservation] = []
        for draft in output.facts:
            checked = self._check(draft, candidate, source, sent, limitations)
            if checked.rejection is not None:
                notes.append(f"FACT_REJECTED:{draft.field}:{checked.rejection.value}")
            elif checked.observation is not None:
                observations.append(checked.observation)
        return ExtractedFacts(observations=tuple(observations), notes=tuple(notes))

    def _check(
        self,
        draft: FactDraft,
        candidate: Candidate,
        source: Source,
        text: str,
        limitations: list[str],
    ) -> _Checked:
        def reject(reason: FactRejection) -> _Checked:
            return _Checked(None, reason)

        strings = [draft.subject, draft.claim, draft.excerpt, draft.stage_label]
        if any(s is not None and not s.strip() for s in strings):
            return reject(FactRejection.BLANK_FIELD)
        if any(s is not None and _INSTRUCTION.search(s) for s in strings):
            return reject(FactRejection.INSTRUCTION_IN_OUTPUT)
        excerpt = _squash(draft.excerpt)
        if excerpt not in text:
            return reject(FactRejection.EXCERPT_NOT_IN_SOURCE)

        field = draft.field
        if (field in _BOOLEAN) != (draft.value is not None):
            return reject(FactRejection.VALUE_SHAPE)
        if (field == FIELD_STAGE) != (draft.stage_label is not None):
            return reject(FactRejection.VALUE_SHAPE)

        names = {_name_key(n) for n in [candidate.canonical_name, *candidate.aliases]}
        if _name_key(draft.subject) not in names:
            return reject(FactRejection.SUBJECT_MISMATCH)
        if field in _EVENT_FIELDS:
            key = _name_key(excerpt)
            if not any(n in key for n in names) and not _FIRST_PERSON.search(excerpt):
                return reject(FactRejection.SUBJECT_NOT_IN_EXCERPT)
        if field in _TOPIC and not _TOPIC[field].search(excerpt):
            return reject(FactRejection.TOPIC_NOT_IN_EXCERPT)

        event_date = draft.event_date if field in _EVENT_FIELDS else None
        if event_date is not None:
            if event_date > self._as_of:
                return reject(FactRejection.DATE_AFTER_AS_OF)
            if str(event_date.year) not in excerpt:
                return reject(FactRejection.DATE_NOT_IN_EXCERPT)

        value: bool | StageObservation | None = draft.value
        if field == FIELD_STAGE:
            label = _squash(draft.stage_label or "")
            if _stage_key(label) not in _stage_key(excerpt):
                return reject(FactRejection.STAGE_LABEL_NOT_IN_EXCERPT)
            normalized, why = normalize_round(label)
            if normalized is None:
                return reject(why)
            value = StageObservation(label, normalized, "explicit")
        if field == FIELD_BUSINESS:
            value = None

        return _Checked(
            FieldObservation(
                field=field,
                value=value,
                source_id=source.source_id,
                locator=f"{source.url}#:~:text={quote(excerpt[:200])}",
                claim=draft.claim,
                excerpt=excerpt,
                identity_basis="official_domain",
                event_date=event_date,
                evidence_kind="reported",
                confidence=draft.confidence,
                limitations=tuple(limitations),
            ),
            None,
        )
