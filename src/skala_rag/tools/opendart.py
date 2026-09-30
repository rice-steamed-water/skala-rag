"""OpenDART 기업개황 provider — #51, M2 경로 A의 KR 적격성 보강(optional).

기업개황(``company.json``)의 법인구분(``corp_cls``)으로 국내 상장 여부를,
법인등록번호·사업자등록번호·고유번호로 법인 식별을 확인한다. 상장·Exit·단계를
이름만으로 붙이지 않는다.

후보 ``legal_identifiers`` scheme(소문자): ``dart``(고유번호 8자리),
``brn``(사업자등록번호), ``crn``(법인등록번호). 비교 전에 하이픈·공백을 뺀다.

- KR 후보가 아니면 미조회(skipped=UNSUPPORTED_COUNTRY).
- key가 없으면 요청 없이 ``unavailable``/TOOL_NOT_CONFIGURED.
- 식별자가 하나도 없으면 미조회(skipped=NO_LEGAL_IDENTIFIER). 이름 검색 결과를
  붙이지 않는다.
- ``dart``가 없고 ``brn``/``crn``만 있으면 고유번호 목록(``corpCode.xml``)에서 이름이
  같은 법인을 찾고, 기업개황의 번호가 후보와 일치하는 법인만 쓴다. 불일치는 동명
  기업이다.
- 조회 결과 없음(status 013)·이름 불일치·동명 기업만 발견은 ``empty``다. 비상장이
  아니다.
- ``corp_cls`` Y/K/N은 상장, E(기타)는 조회 시점 국내 3개 시장 비상장. 해외 상장·과거
  기준일 상태는 이 근거로 확인하지 않는다(limitations에 남김).

key는 요청 URL에만 넣고 Source·locator·기록에는 key를 뺀 URL만 남긴다.
"""

import dataclasses
import io
import json
import zipfile
from urllib.parse import urlencode
from xml.etree import ElementTree

from skala_rag.agents.eligibility import FIELD_IDENTITY, FIELD_LISTING
from skala_rag.contracts.candidates import Candidate
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode
from skala_rag.contracts.interfaces import Clock
from skala_rag.tools.company_research import (
    CallBudget,
    FieldObservation,
    ProviderCall,
    ProviderOutcome,
    candidate_identifiers,
    failed_outcome,
    normalize_identifier,
)
from skala_rag.tools.source_fetch import FetchError, RawSnapshot, SafeFetcher, to_source

NAME = "opendart-company"
HOST = "opendart.fss.or.kr"
BASE = f"https://{HOST}/api"
KEY_PARAM = "crtfc_key"

LISTED_CLASSES = {"Y": "유가증권시장", "K": "코스닥", "N": "코넥스"}
UNLISTED_CLASS = "E"

# OpenDART 응답 status → 도구 오류. 013은 오류가 아니라 조회 결과 없음이다.
STATUS_NO_DATA = "013"
_STATUS_ERRORS = {
    "010": ErrorCode.TOOL_AUTH_FAILED,  # 등록되지 않은 키
    "011": ErrorCode.TOOL_AUTH_FAILED,  # 사용할 수 없는 키
    "012": ErrorCode.TOOL_AUTH_FAILED,  # 접근할 수 없는 IP
    "901": ErrorCode.TOOL_AUTH_FAILED,  # 개인정보 보유기간 만료 키
    "020": ErrorCode.TOOL_RATE_LIMITED,  # 요청 제한 초과
    "800": ErrorCode.TOOL_UNAVAILABLE,  # 시스템 점검
}

_NAME_NOISE = ("주식회사", "(주)", "㈜", "(유)", "유한회사")


def normalize_name(name: str) -> str:
    text = name.lower()
    for noise in _NAME_NOISE:
        text = text.replace(noise, "")
    return "".join(ch for ch in text if ch.isalnum())


def public_url(endpoint: str, **params: str) -> str:
    """key를 뺀 URL. Source·locator·기록용."""
    query = urlencode(sorted(params.items()))
    return f"{BASE}/{endpoint}" + (f"?{query}" if query else "")


class _DartError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _status_error(status: str | None) -> _DartError:
    code = _STATUS_ERRORS.get(status or "", ErrorCode.TOOL_RESPONSE_INVALID)
    return _DartError(code, f"OpenDART status {status or 'missing'}")


class OpenDartCompany:
    name = NAME
    required = False

    def __init__(
        self,
        fetcher: SafeFetcher,
        *,
        api_key: str | None,
        schema_version: str,
        clock: Clock,
        max_name_matches: int,
        max_index_bytes: int,
    ) -> None:
        """``max_name_matches``: 이름 검색 시 기업개황을 확인할 동명 법인 수 상한.
        ``max_index_bytes``: ``corpCode.xml`` 압축 해제 크기 상한.
        둘 다 승인 설정에서 주입한다.
        """
        if max_name_matches < 1 or max_index_bytes < 1:
            raise ValueError("OpenDART limits must be positive")
        self._fetcher = fetcher
        self._key = api_key.strip() if api_key and api_key.strip() else None
        self._schema_version = schema_version
        self._clock = clock
        self._max_name_matches = max_name_matches
        self._max_index_bytes = max_index_bytes

    # ------------------------------------------------------------ public

    def __call__(self, candidate: Candidate, calls: CallBudget) -> ProviderOutcome:
        if candidate.country.strip().upper() != "KR":
            return ProviderOutcome(status="empty", skipped="UNSUPPORTED_COUNTRY")
        if self._key is None:
            return failed_outcome(
                ErrorCode.TOOL_NOT_CONFIGURED, "OpenDART API key not configured"
            )
        known = candidate_identifiers(candidate)
        if not ({"dart", "brn", "crn"} & known.keys()):
            return ProviderOutcome(status="empty", skipped="NO_LEGAL_IDENTIFIER")

        made: list[ProviderCall] = []
        notes: list[str] = []
        try:
            if "dart" in known:
                codes = [known["dart"]]
            else:
                codes = self._search_codes(candidate, calls, made)
                if len(codes) > self._max_name_matches:
                    notes.append(f"NAME_MATCHES_TRUNCATED:{len(codes)}")
                    codes = codes[: self._max_name_matches]
                if not codes:
                    notes.append("NAME_NOT_IN_DART_INDEX")
            for code in codes:
                found = self._company(candidate, code, known, calls, made, notes)
                if found is not None:
                    return dataclasses.replace(
                        found, calls=tuple(made), notes=tuple(notes)
                    )
        except _DartError as exc:
            return failed_outcome(exc.code, exc.message, made)
        return ProviderOutcome(status="empty", calls=tuple(made), notes=tuple(notes))

    # ------------------------------------------------------------ requests

    def _get(
        self,
        endpoint: str,
        params: dict[str, str],
        calls: CallBudget,
        made: list[ProviderCall],
        query: str | None,
    ) -> tuple[RawSnapshot, ProviderCall]:
        if not calls.take():
            raise _DartError(
                ErrorCode.BUDGET_EXHAUSTED, "no request budget for OpenDART"
            )
        url = f"{BASE}/{endpoint}?" + urlencode({KEY_PARAM: self._key, **params})
        shown = public_url(endpoint, **params)
        started = self._clock.now()
        try:
            raw = self._fetcher.fetch(url)
        except FetchError as exc:
            made.append(
                ProviderCall(
                    status=ERROR_SPECS[exc.error_code].tool_status,
                    method="api",
                    query=query,
                    arguments={
                        "endpoint": endpoint,
                        **params,
                        "rejection": exc.reason.value,
                    },
                    started_at=started,
                    finished_at=self._clock.now(),
                    error_code=exc.error_code,
                )
            )
            raise _DartError(exc.error_code, exc.message_redacted) from exc
        # 최종 위치에 key가 남지 않도록 공개 URL로 바꾼다.
        raw = dataclasses.replace(raw, requested=shown, locator=shown, redirects=())
        call = ProviderCall(
            status="ok",
            method="api",
            query=query,
            arguments={"endpoint": endpoint, **params},
            started_at=started,
            finished_at=self._clock.now(),
        )
        made.append(call)
        return raw, call

    def _mark_last(self, made: list[ProviderCall], **changes) -> None:
        made[-1] = dataclasses.replace(made[-1], **changes)

    def _search_codes(
        self, candidate: Candidate, calls: CallBudget, made: list[ProviderCall]
    ) -> list[str]:
        raw, _ = self._get("corpCode.xml", {}, calls, made, candidate.canonical_name)
        try:
            names = self._index(raw.content)
        except _DartError as exc:
            self._mark_last(
                made,
                status=ERROR_SPECS[exc.code].tool_status,
                error_code=exc.code,
            )
            raise
        wanted = {
            normalize_name(n) for n in [candidate.canonical_name, *candidate.aliases]
        }
        wanted.discard("")
        codes = sorted({code for name, code in names if name in wanted})
        self._mark_last(
            made,
            status="ok" if codes else "empty",
            arguments={**made[-1].arguments, "name_matches": len(codes)},
        )
        return codes

    def _index(self, content: bytes) -> list[tuple[str, str]]:
        if not content.startswith(b"PK"):
            raise _status_error(_error_status(content))
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
            members = archive.infolist()
            if len(members) != 1 or members[0].file_size > self._max_index_bytes:
                raise _DartError(
                    ErrorCode.TOOL_RESPONSE_INVALID, "unexpected corpCode archive"
                )
            with archive.open(members[0]) as handle:
                data = handle.read(self._max_index_bytes + 1)
            if len(data) > self._max_index_bytes:
                raise _DartError(
                    ErrorCode.TOOL_RESPONSE_INVALID, "corpCode index too large"
                )
            root = ElementTree.fromstring(data)
        except (zipfile.BadZipFile, ElementTree.ParseError) as exc:
            raise _DartError(
                ErrorCode.TOOL_RESPONSE_INVALID, "corpCode archive unreadable"
            ) from exc
        rows = []
        for item in root.iter("list"):
            code = (item.findtext("corp_code") or "").strip()
            name = normalize_name(item.findtext("corp_name") or "")
            if code and name:
                rows.append((name, code))
        return rows

    def _company(
        self,
        candidate: Candidate,
        code: str,
        known: dict[str, str],
        calls: CallBudget,
        made: list[ProviderCall],
        notes: list[str],
    ) -> ProviderOutcome | None:
        raw, _ = self._get(
            "company.json", {"corp_code": code}, calls, made, candidate.canonical_name
        )
        try:
            body = json.loads(raw.content)
            if not isinstance(body, dict):
                raise ValueError
        except ValueError as exc:
            self._mark_last(
                made, status="failed", error_code=ErrorCode.TOOL_RESPONSE_INVALID
            )
            raise _DartError(
                ErrorCode.TOOL_RESPONSE_INVALID, "company.json is not a JSON object"
            ) from exc
        status = str(body.get("status", ""))
        if status == STATUS_NO_DATA:
            self._mark_last(made, status="empty")
            notes.append(f"NO_DATA:{code}")
            return None
        if status != "000":
            error = _status_error(status)
            self._mark_last(
                made,
                status=ERROR_SPECS[error.code].tool_status,
                error_code=error.code,
            )
            raise error

        reported = {
            "dart": normalize_identifier(str(body.get("corp_code") or "")),
            "brn": normalize_identifier(str(body.get("bizr_no") or "")),
            "crn": normalize_identifier(str(body.get("jurir_no") or "")),
        }
        comparable = {s: v for s, v in reported.items() if v and s in known}
        if not comparable or any(known[s] != v for s, v in comparable.items()):
            # 이름·코드가 같아도 번호가 다르면 다른 법인이다(동명 기업).
            notes.append(f"IDENTIFIER_MISMATCH:{code}")
            return None

        corp_name = str(body.get("corp_name") or code)
        source = to_source(
            raw,
            schema_version=self._schema_version,
            title=f"OpenDART 기업개황 {corp_name}",
            publisher="금융감독원 OpenDART",
            source_kind="filing",
            language="ko",
            access_notes="OpenDART 기업개황 API 응답 snapshot. 조회 시점의 현재 정보",
            bibliographic_metadata={"corp_code": code, "endpoint": "company.json"},
        )
        self._mark_last(made, source_ids=(source.source_id,))
        observations = [
            FieldObservation(
                field=FIELD_IDENTITY,
                value=None,
                source_id=source.source_id,
                locator=f"{source.url}#identifiers",
                claim=(
                    f"OpenDART 기업개황의 {', '.join(sorted(comparable))}가 "
                    "후보 식별자와 일치"
                ),
                excerpt=f"corp_code={code}; corp_name={corp_name}",
                identity_basis="legal_identifier",
                matched_identifiers=comparable,
                confidence="high",
            )
        ]
        corp_cls = str(body.get("corp_cls") or "").strip().upper()
        limitations = (
            "조회 시점 현재 법인구분이며 과거 기준일 상태가 아님",
            "국내 유가증권·코스닥·코넥스 기준이며 해외 상장은 확인하지 않음",
        )
        if corp_cls in LISTED_CLASSES:
            listing = True
            claim = f"OpenDART 법인구분 {corp_cls}: {LISTED_CLASSES[corp_cls]} 상장법인"
        elif corp_cls == UNLISTED_CLASS:
            listing = False
            claim = "OpenDART 법인구분 E(기타): 조회 시점 국내 3개 시장 비상장"
        else:
            listing = None
            notes.append(f"CORP_CLS_UNRECOGNIZED:{corp_cls or 'missing'}")
        if listing is not None:
            observations.append(
                FieldObservation(
                    field=FIELD_LISTING,
                    value=listing,
                    source_id=source.source_id,
                    locator=f"{source.url}#corp_cls",
                    claim=claim,
                    excerpt=f"corp_cls={corp_cls}",
                    identity_basis="legal_identifier",
                    matched_identifiers=comparable,
                    confidence="high" if listing else "medium",
                    limitations=limitations,
                )
            )
        return ProviderOutcome(
            status="ok", sources=(source,), observations=tuple(observations)
        )


def _error_status(content: bytes) -> str | None:
    """오류 시 corpCode.xml은 zip 대신 XML/JSON status를 돌려준다."""
    try:
        return str(json.loads(content).get("status"))
    except (ValueError, AttributeError):
        pass
    try:
        return (
            ElementTree.fromstring(content).findtext("status") or ""
        ).strip() or None
    except ElementTree.ParseError:
        return None
