"""후보 공식 홈페이지 provider — #51, M2 경로 A의 "공식 회사 원문 확인"(required).

``Candidate.homepage_url``을 ``SafeFetcher``로 한 번 받아 Source snapshot으로 만든다.
원문에서 적격성 사실을 읽는 일은 주입한 ``SourceFactExtractor``가 한다(LLM 구현은
``agents.eligibility_extraction``). 추출기가 없으면 Source만 남기고 관측을 만들지
않는다. 추출기의 ``LLMError``는 도구 실패가 아니라 추출 실패
note(``EXTRACTOR_FAILED:<code>``)다.
받은 Source는 남기고 사실은 만들지 않는다(모름을 false로 바꾸지 않음).

- homepage가 없으면 요청 없이 ``empty``(skipped=NO_HOMEPAGE). 자료 부재이지 실패가
  아니다.
- redirect 뒤 다른 도메인이면 Source는 남기되 관측은 조립 단계에서 DOMAIN_MISMATCH로
  버려진다.
- fetch 실패는 ``FetchError.error_code``를 그대로 도구 상태로 옮긴다.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from skala_rag.contracts.candidates import Candidate
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode
from skala_rag.contracts.interfaces import Clock, LLMError
from skala_rag.contracts.sources import Source
from skala_rag.tools.company_research import (
    CallBudget,
    FieldObservation,
    ProviderCall,
    ProviderOutcome,
    failed_outcome,
    url_host,
)
from skala_rag.tools.source_fetch import FetchError, SafeFetcher, to_source

NAME = "official-homepage"


@dataclass(frozen=True)
class ExtractedFacts:
    """추출 결과. ``notes``는 거절·절단 등 사유 코드(원문·모델 출력 없음)."""

    observations: tuple[FieldObservation, ...]
    notes: tuple[str, ...] = ()


@runtime_checkable
class SourceFactExtractor(Protocol):
    """받은 원문에서 적격성 관측을 만든다. 원문에 없는 사실을 만들지 않는다.

    반환 관측의 ``source_id``는 ``source.source_id``여야 한다. ``version``은
    prompt·규칙 버전이며 기록에 남는다.
    """

    version: str

    def __call__(
        self,
        candidate: Candidate,
        source: Source,
        content: bytes,
        content_type: str | None = None,
    ) -> ExtractedFacts: ...


class OfficialHomepage:
    name = NAME
    required = True

    def __init__(
        self,
        fetcher: SafeFetcher,
        *,
        schema_version: str,
        clock: Clock,
        extractor: SourceFactExtractor | None,
    ) -> None:
        self._fetcher = fetcher
        self._schema_version = schema_version
        self._clock = clock
        self._extractor = extractor

    def __call__(self, candidate: Candidate, calls: CallBudget) -> ProviderOutcome:
        url = candidate.homepage_url
        if not url:
            return ProviderOutcome(status="empty", skipped="NO_HOMEPAGE")
        if not calls.take():
            return failed_outcome(
                ErrorCode.BUDGET_EXHAUSTED, "no request budget for official homepage"
            )
        arguments = {"host": url_host(url)}
        started = self._clock.now()
        try:
            raw = self._fetcher.fetch(url)
        except FetchError as exc:
            call = ProviderCall(
                status=ERROR_SPECS[exc.error_code].tool_status,
                method="web",
                query=None,
                arguments={**arguments, "rejection": exc.reason.value},
                started_at=started,
                finished_at=self._clock.now(),
                error_code=exc.error_code,
            )
            return failed_outcome(exc.error_code, exc.message_redacted, [call])

        source = to_source(
            raw,
            schema_version=self._schema_version,
            title=f"{candidate.canonical_name} 공식 홈페이지 ({url_host(raw.locator)})",
            source_kind="web",
            # 언어를 추정하지 않는다(BCP 47 undetermined).
            language="und",
            access_notes=(
                "후보 homepage_url의 live snapshot. 원문 bytes는 번들에 싣지 않음"
            ),
            bibliographic_metadata={
                "requested_url": raw.requested,
                "redirects": list(raw.redirects),
                "content_type": raw.content_type,
            },
        )
        observations: tuple[FieldObservation, ...] = ()
        notes: list[str] = []
        if self._extractor is None:
            notes.append("EXTRACTOR_NOT_CONFIGURED")
        else:
            try:
                facts = self._extractor(
                    candidate, source, raw.content, raw.content_type
                )
            except LLMError as exc:
                notes.append(f"EXTRACTOR_FAILED:{exc.error_code.value}")
            else:
                observations, notes = facts.observations, [*notes, *facts.notes]
            if any(o.source_id != source.source_id for o in observations):
                raise ValueError("extractor returned observation for another Source")
        call = ProviderCall(
            status="ok",
            method="web",
            query=None,
            arguments={
                **arguments,
                "final_host": url_host(raw.locator),
                "redirect_count": len(raw.redirects),
                "extractor": self._extractor.version
                if self._extractor
                else "not_configured",
            },
            started_at=started,
            finished_at=self._clock.now(),
            source_ids=(source.source_id,),
        )
        return ProviderOutcome(
            status="ok",
            sources=(source,),
            observations=tuple(observations),
            calls=(call,),
            notes=tuple(notes),
        )
