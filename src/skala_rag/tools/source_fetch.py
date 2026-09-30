"""안전한 외부 fetch와 Source snapshot·기준일 판정 — #46, data-rag §6, T18.

- Web: 허용 scheme·host 검증, private/loopback/link-local 등 비공개 주소 차단,
  redirect마다 대상 재검증, 응답 크기(압축 해제 후)·총 시간 제한. redirect는 직접
  따라가며 쿠키·인증 헤더·환경 proxy를 쓰지 않는다.
- 로컬: 승인 corpus 경로(``data/local/``) 안의 파일만 읽는다. symlink 이탈도 거절한다.
- 받은 내용은 bytes 그대로 보관한다. HTML·script를 렌더링하거나 실행하지 않는다.
- ``content_hash``는 실제 받은 bytes에서 계산하고 ``source_id``는 최종 위치와
  content_hash에서 만든다. 내용이 바뀌면(정정본) 새 Source snapshot이 된다.
- ``check_as_of``는 기준일 이후 공개 자료, 기준일 이후 확보한 날짜 미상 자료·최신
  편집본을 사유와 함께 제외한다.

크기·시간·redirect 한도와 허용 scheme·host는 ``FetchPolicy``로 주입하며 기본값이
없다(승인 전 코드 기본값으로 정하지 않음). 재시도·예산은 #45 공통 runtime 범위다.
DNS 확인 뒤 연결 시점에 주소가 바뀌는 경우(DNS rebinding)는 막지 못한다.
"""

import hashlib
import ipaddress
import socket
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from skala_rag.contracts.common import JSONMap
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import Clock
from skala_rag.contracts.sources import Source
from skala_rag.rag.corpus import LOCAL_CORPUS_ROOT, validate_local_path

TOOL_NAME = "safe-fetch"
FETCHABLE_SCHEMES = frozenset({"http", "https"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

Resolver = Callable[[str], Sequence[str]]
"""host → IP 문자열 목록. 실패는 ``OSError``."""


class FetchRejection(StrEnum):
    URL_INVALID = "URL_INVALID"
    SCHEME_NOT_ALLOWED = "SCHEME_NOT_ALLOWED"
    CREDENTIALS_IN_URL = "CREDENTIALS_IN_URL"
    HOST_NOT_ALLOWED = "HOST_NOT_ALLOWED"
    ADDRESS_BLOCKED = "ADDRESS_BLOCKED"
    RESOLUTION_FAILED = "RESOLUTION_FAILED"
    REDIRECT_INVALID = "REDIRECT_INVALID"
    TOO_MANY_REDIRECTS = "TOO_MANY_REDIRECTS"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    TIMEOUT = "TIMEOUT"
    HTTP_STATUS = "HTTP_STATUS"
    TRANSPORT_FAILED = "TRANSPORT_FAILED"
    PATH_OUTSIDE_CORPUS = "PATH_OUTSIDE_CORPUS"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"


class FetchError(Exception):
    """fetch 거절·실패. 메시지에는 scheme·host까지만 넣고 query·원문은 넣지 않는다."""

    def __init__(
        self, reason: FetchRejection, error_code: ErrorCode, message_redacted: str
    ) -> None:
        super().__init__(message_redacted)
        self.reason = FetchRejection(reason)
        self.error_code = ErrorCode(error_code)
        self.message_redacted = message_redacted


def _rejected(reason: FetchRejection, message: str) -> FetchError:
    return FetchError(reason, ErrorCode.TOOL_FAILED, message)


@dataclass(frozen=True)
class FetchPolicy:
    """모든 값은 호출자가 승인된 설정에서 넘긴다.

    ``allowed_hosts``가 None이면 host 목록 제한 없이 공개 주소만 허용한다.
    """

    allowed_schemes: frozenset[str]
    allowed_hosts: frozenset[str] | None
    max_bytes: int
    timeout_seconds: float
    max_redirects: int

    def __post_init__(self) -> None:
        schemes = {s.lower() for s in self.allowed_schemes}
        if not schemes or not schemes <= FETCHABLE_SCHEMES:
            raise ValueError(f"allowed_schemes must be a subset of {FETCHABLE_SCHEMES}")
        object.__setattr__(self, "allowed_schemes", frozenset(schemes))
        if self.allowed_hosts is not None:
            hosts = frozenset(h.lower().rstrip(".") for h in self.allowed_hosts)
            object.__setattr__(self, "allowed_hosts", hosts)
        if self.max_bytes <= 0 or self.timeout_seconds <= 0 or self.max_redirects < 0:
            raise ValueError("fetch limits must be positive")


@dataclass(frozen=True)
class RawSnapshot:
    """실제로 받은 원문 한 벌. Source로 바꾸기 전 단계."""

    requested: str
    locator: str
    """최종 URL 또는 ``data/local/`` 상대 경로."""
    kind: str
    """``web`` 또는 ``local``."""
    redirects: tuple[str, ...]
    content: bytes
    content_type: str | None
    retrieved_at: datetime

    @property
    def content_hash(self) -> str:
        return content_hash(self.content)


def content_hash(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def snapshot_source_id(locator: str, digest: str) -> str:
    """같은 위치라도 내용이 다르면 다른 snapshot ID."""
    key = hashlib.sha256(f"{locator}\n{digest}".encode()).hexdigest()
    return f"src-{key[:24]}"


def default_resolve(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def is_blocked_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """공개 unicast가 아니면 차단: private·loopback·link-local·CGNAT·예약·multicast."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return not address.is_global or address.is_multicast


def _origin(url: str) -> str:
    """오류 메시지용. query·path·userinfo를 뺀다."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname or '?'}"


class SafeFetcher:
    """공통 Web fetch 경로. 한 번의 ``fetch``가 한 번의 시도다(재시도 없음)."""

    def __init__(
        self,
        policy: FetchPolicy,
        *,
        clock: Clock,
        transport: httpx.BaseTransport | None = None,
        resolve: Resolver = default_resolve,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._clock = clock
        self._transport = transport
        self._resolve = resolve
        self._monotonic = monotonic

    def check_url(self, url: str) -> None:
        """요청 전 대상 검증. 통과하지 못하면 ``FetchError``."""
        try:
            parts = urlsplit(url)
            _ = parts.port  # 잘못된 port면 ValueError
        except ValueError as exc:
            raise _rejected(FetchRejection.URL_INVALID, "malformed URL") from exc
        scheme = parts.scheme.lower()
        if scheme not in self._policy.allowed_schemes:
            raise _rejected(
                FetchRejection.SCHEME_NOT_ALLOWED, f"scheme {scheme!r} not allowed"
            )
        if parts.username is not None or parts.password is not None:
            raise _rejected(
                FetchRejection.CREDENTIALS_IN_URL, "credentials in URL not allowed"
            )
        host = (parts.hostname or "").rstrip(".")
        if not host:
            raise _rejected(FetchRejection.URL_INVALID, "URL has no host")
        origin = _origin(url)
        allowed = self._policy.allowed_hosts
        if allowed is not None and host not in allowed:
            raise _rejected(FetchRejection.HOST_NOT_ALLOWED, f"{origin} not allowed")
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            try:
                addresses = [ipaddress.ip_address(a) for a in self._resolve(host)]
            except (OSError, ValueError) as exc:
                raise _rejected(
                    FetchRejection.RESOLUTION_FAILED, f"{origin} did not resolve"
                ) from exc
        if not addresses:
            raise _rejected(FetchRejection.RESOLUTION_FAILED, f"{origin} no address")
        if any(is_blocked_address(a) for a in addresses):
            raise _rejected(
                FetchRejection.ADDRESS_BLOCKED, f"{origin} resolves to blocked address"
            )

    def fetch(self, url: str) -> RawSnapshot:
        policy = self._policy
        started = self._monotonic()
        current = url
        redirects: list[str] = []
        with httpx.Client(
            transport=self._transport,
            follow_redirects=False,
            timeout=policy.timeout_seconds,
            trust_env=False,
        ) as client:
            while True:
                self.check_url(current)
                try:
                    with client.stream("GET", current) as response:
                        if response.status_code in REDIRECT_STATUSES:
                            current = self._next_hop(current, response, redirects)
                            self._check_deadline(started, current)
                            continue
                        _raise_for_status(current, response.status_code)
                        body = self._read_body(current, response, started)
                        content_type = response.headers.get("content-type")
                except httpx.TimeoutException as exc:
                    raise FetchError(
                        FetchRejection.TIMEOUT,
                        ErrorCode.TOOL_TIMEOUT,
                        f"{_origin(current)} timed out",
                    ) from exc
                except httpx.HTTPError as exc:
                    raise FetchError(
                        FetchRejection.TRANSPORT_FAILED,
                        ErrorCode.TOOL_UNAVAILABLE,
                        f"{_origin(current)} transport failed",
                    ) from exc
                return RawSnapshot(
                    requested=url,
                    locator=current,
                    kind="web",
                    redirects=tuple(redirects),
                    content=body,
                    content_type=content_type,
                    retrieved_at=self._clock.now(),
                )

    def _next_hop(
        self, current: str, response: httpx.Response, redirects: list[str]
    ) -> str:
        location = response.headers.get("location")
        if not location:
            raise FetchError(
                FetchRejection.REDIRECT_INVALID,
                ErrorCode.TOOL_RESPONSE_INVALID,
                f"{_origin(current)} redirect without location",
            )
        if len(redirects) >= self._policy.max_redirects:
            raise _rejected(
                FetchRejection.TOO_MANY_REDIRECTS,
                f"more than {self._policy.max_redirects} redirects",
            )
        target = urljoin(current, location)
        redirects.append(target)
        return target

    def _check_deadline(self, started: float, url: str) -> None:
        if self._monotonic() - started > self._policy.timeout_seconds:
            raise FetchError(
                FetchRejection.TIMEOUT,
                ErrorCode.TOOL_TIMEOUT,
                f"{_origin(url)} exceeded {self._policy.timeout_seconds}s",
            )

    def _read_body(self, url: str, response: httpx.Response, started: float) -> bytes:
        limit = self._policy.max_bytes
        too_large = FetchError(
            FetchRejection.RESPONSE_TOO_LARGE,
            ErrorCode.TOOL_RESPONSE_INVALID,
            f"{_origin(url)} response exceeds {limit} bytes",
        )
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            raise too_large
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                raise too_large
            self._check_deadline(started, url)
        return bytes(body)


def _raise_for_status(url: str, status: int) -> None:
    if 200 <= status < 300:
        return
    if status in (401, 403):
        code = ErrorCode.TOOL_AUTH_FAILED
    elif status == 429:
        code = ErrorCode.TOOL_RATE_LIMITED
    elif status >= 500:
        code = ErrorCode.TOOL_UNAVAILABLE
    else:
        code = ErrorCode.TOOL_FAILED
    raise FetchError(
        FetchRejection.HTTP_STATUS, code, f"{_origin(url)} returned HTTP {status}"
    )


def read_local(
    base_dir: Path, local_path: str, *, max_bytes: int, clock: Clock
) -> RawSnapshot:
    """``base_dir/data/local/`` 아래 파일만 읽는다. 경로 이탈·symlink 이탈은 거절."""
    try:
        validate_local_path(local_path)
    except ValueError as exc:
        raise _rejected(FetchRejection.PATH_OUTSIDE_CORPUS, str(exc)) from exc
    if local_path.lower().startswith("fixture://"):
        raise _rejected(FetchRejection.PATH_OUTSIDE_CORPUS, "fixture:// is not a file")
    root = (base_dir / LOCAL_CORPUS_ROOT).resolve()
    target = (base_dir / local_path).resolve()
    if not target.is_relative_to(root):
        raise _rejected(
            FetchRejection.PATH_OUTSIDE_CORPUS, "path resolves outside corpus root"
        )
    if not target.is_file():
        raise _rejected(FetchRejection.FILE_NOT_FOUND, "corpus file not found")
    with target.open("rb") as handle:
        content = handle.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise FetchError(
            FetchRejection.RESPONSE_TOO_LARGE,
            ErrorCode.TOOL_RESPONSE_INVALID,
            f"corpus file exceeds {max_bytes} bytes",
        )
    return RawSnapshot(
        requested=local_path,
        locator=local_path,
        kind="local",
        redirects=(),
        content=content,
        content_type=None,
        retrieved_at=clock.now(),
    )


def to_source(
    raw: RawSnapshot,
    *,
    schema_version: str,
    title: str,
    source_kind: str,
    language: str,
    access_notes: str,
    published_at: date | datetime | None = None,
    publisher: str | None = None,
    author: str | None = None,
    bibliographic_metadata: JSONMap | None = None,
) -> Source:
    """받은 원문을 Source snapshot으로. 서지 값은 호출자가 확인한 것만 넘긴다."""
    digest = raw.content_hash
    return Source(
        schema_version=schema_version,
        source_id=snapshot_source_id(raw.locator, digest),
        title=title,
        publisher=publisher,
        author=author,
        source_kind=source_kind,
        url=raw.locator if raw.kind == "web" else None,
        local_path=raw.locator if raw.kind == "local" else None,
        published_at=published_at,
        retrieved_at=raw.retrieved_at,
        content_hash=digest,
        language=language,
        access_notes=access_notes,
        bibliographic_metadata=bibliographic_metadata or {},
    )


class AsOfExclusion(StrEnum):
    PUBLISHED_AFTER_AS_OF = "PUBLISHED_AFTER_AS_OF"
    UNDATED_RETRIEVED_AFTER_AS_OF = "UNDATED_RETRIEVED_AFTER_AS_OF"
    """발행일 미상이고 기준일 이전 확보도 확인할 수 없음."""
    EDITION_RETRIEVED_AFTER_AS_OF = "EDITION_RETRIEVED_AFTER_AS_OF"
    """과거 발행일이지만 받은 편집본은 기준일 이후 것일 수 있음."""


@dataclass(frozen=True)
class AsOfDecision:
    source_id: str
    as_of: date
    admitted: bool
    reason: AsOfExclusion | None


def _day(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def check_as_of(source: Source, as_of: date) -> AsOfDecision:
    """기준일 이전에 공개됐고, 받은 snapshot이 기준일 이전 확보분일 때만 허용."""
    reason: AsOfExclusion | None = None
    if source.published_at is not None and _day(source.published_at) > as_of:
        reason = AsOfExclusion.PUBLISHED_AFTER_AS_OF
    elif _day(source.retrieved_at) > as_of:
        reason = (
            AsOfExclusion.UNDATED_RETRIEVED_AFTER_AS_OF
            if source.published_at is None
            else AsOfExclusion.EDITION_RETRIEVED_AFTER_AS_OF
        )
    return AsOfDecision(source.source_id, as_of, reason is None, reason)
