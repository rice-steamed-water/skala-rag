"""T18 fetch 부분: 금지 대상·redirect·크기·시간·경로 이탈·기준일. 네트워크 없이 실행.

모든 응답은 ``httpx.MockTransport``, 이름 확인은 가짜 resolver다. 실제 사이트·기업
자료가 아니다.
"""

import hashlib
from datetime import UTC, date, datetime

import httpx
import pytest

from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.fakes import FakeClock
from skala_rag.tools.source_fetch import (
    AsOfExclusion,
    FetchError,
    FetchPolicy,
    FetchRejection,
    SafeFetcher,
    check_as_of,
    read_local,
    to_source,
)

SCHEMA = "synthetic-1"
NOW = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
PUBLIC_IP = "93.184.216.34"
DNS = {
    "public.test": [PUBLIC_IP],
    "other.test": [PUBLIC_IP],
    "internal.test": ["10.0.0.5"],
    "localhost": ["127.0.0.1", "::1"],
    "mixed.test": [PUBLIC_IP, "192.168.1.10"],
}


def resolve(host):
    try:
        return DNS[host]
    except KeyError:
        raise OSError("not found") from None


def policy(**changes):
    data = dict(
        allowed_schemes=frozenset({"https"}),
        allowed_hosts=None,
        max_bytes=64,
        timeout_seconds=30.0,
        max_redirects=3,
    )
    data.update(changes)
    return FetchPolicy(**data)


class Site:
    """URL별 응답을 돌려주고 실제로 요청된 URL을 기록한다."""

    def __init__(self, routes):
        self.routes = routes
        self.requested = []

    def __call__(self, request):
        url = str(request.url)
        self.requested.append(url)
        route = self.routes[url]
        return route(request) if callable(route) else route


def fetcher(routes, *, monotonic=None, **policy_changes):
    site = Site(routes)
    kwargs = {"monotonic": monotonic} if monotonic else {}
    return (
        SafeFetcher(
            policy(**policy_changes),
            clock=FakeClock(NOW),
            transport=httpx.MockTransport(site),
            resolve=resolve,
            **kwargs,
        ),
        site,
    )


def redirect(location, status=302):
    return httpx.Response(status, headers={"location": location})


def rejected(excinfo, reason, code=ErrorCode.TOOL_FAILED):
    assert excinfo.value.reason == reason
    assert excinfo.value.error_code == code


BODY = b"<html><script>alert('x')</script><p>synthetic</p></html>"


def test_allowed_fetch_keeps_raw_content_and_provenance():
    f, site = fetcher(
        {
            "https://public.test/a?id=1": redirect("/b"),
            "https://public.test/b": redirect("https://other.test/final", 301),
            "https://other.test/final": httpx.Response(
                200, content=BODY, headers={"content-type": "text/html"}
            ),
        }
    )
    raw = f.fetch("https://public.test/a?id=1")
    assert raw.content == BODY  # script는 실행·제거하지 않고 bytes로 보관
    assert raw.content_hash == f"sha256:{hashlib.sha256(BODY).hexdigest()}"
    assert raw.locator == "https://other.test/final"
    assert raw.redirects == ("https://public.test/b", "https://other.test/final")
    assert raw.retrieved_at == NOW
    assert raw.content_type == "text/html"
    assert len(site.requested) == 3

    source = to_source(
        raw,
        schema_version=SCHEMA,
        title="Synthetic page",
        source_kind="web",
        language="en",
        access_notes="Synthetic only",
        published_at=date(2026, 9, 1),
    )
    assert source.url == "https://other.test/final"
    assert source.local_path is None
    assert source.content_hash == raw.content_hash
    assert source.retrieved_at == NOW


def test_corrected_content_becomes_new_snapshot():
    versions = iter([b"v1", b"v1", b"v2 corrected"])
    f, _ = fetcher(
        {
            "https://public.test/doc": lambda _: httpx.Response(
                200, content=next(versions)
            )
        }
    )
    meta = dict(
        schema_version=SCHEMA,
        title="Synthetic",
        source_kind="web",
        language="en",
        access_notes="Synthetic only",
    )
    first, again, corrected = (
        to_source(f.fetch("https://public.test/doc"), **meta) for _ in range(3)
    )
    assert first.source_id == again.source_id
    assert corrected.source_id != first.source_id
    assert corrected.content_hash != first.content_hash


@pytest.mark.parametrize(
    "url",
    [
        "http://public.test/",
        "file:///etc/passwd",
        "ftp://public.test/x",
        "fixture://robotics",
        "javascript:alert(1)",
    ],
)
def test_disallowed_scheme_is_rejected_before_request(url):
    f, site = fetcher({})
    with pytest.raises(FetchError) as excinfo:
        f.fetch(url)
    rejected(excinfo, FetchRejection.SCHEME_NOT_ALLOWED)
    assert site.requested == []


def test_policy_cannot_allow_non_http_schemes():
    with pytest.raises(ValueError):
        policy(allowed_schemes=frozenset({"https", "file"}))
    with pytest.raises(ValueError):
        policy(max_bytes=0)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/",
        "https://10.1.2.3/",
        "https://172.16.0.1/",
        "https://192.168.0.1/",
        "https://169.254.169.254/latest/meta-data/",
        "https://100.64.0.1/",
        "https://0.0.0.0/",
        "https://[::1]/",
        "https://[fe80::1]/",
        "https://[fc00::1]/",
        "https://[::ffff:127.0.0.1]/",
        "https://224.0.0.1/",
        "https://localhost/",
        "https://internal.test/",
        "https://mixed.test/",
    ],
)
def test_private_loopback_link_local_targets_are_blocked(url):
    f, site = fetcher({})
    with pytest.raises(FetchError) as excinfo:
        f.fetch(url)
    rejected(excinfo, FetchRejection.ADDRESS_BLOCKED)
    assert site.requested == []


def test_unresolvable_host_and_credentials_are_rejected():
    f, site = fetcher({})
    with pytest.raises(FetchError) as excinfo:
        f.fetch("https://missing.test/")
    rejected(excinfo, FetchRejection.RESOLUTION_FAILED)
    with pytest.raises(FetchError) as excinfo:
        f.fetch("https://user:secret@public.test/")
    rejected(excinfo, FetchRejection.CREDENTIALS_IN_URL)
    assert "secret" not in str(excinfo.value)
    assert site.requested == []


def test_host_allowlist_applies_to_every_hop():
    f, site = fetcher(
        {"https://public.test/": redirect("https://other.test/")},
        allowed_hosts=frozenset({"public.test"}),
    )
    with pytest.raises(FetchError) as excinfo:
        f.fetch("https://public.test/")
    rejected(excinfo, FetchRejection.HOST_NOT_ALLOWED)
    assert site.requested == ["https://public.test/"]


@pytest.mark.parametrize(
    "location",
    [
        "https://169.254.169.254/latest/meta-data/",
        "https://internal.test/admin",
        "https://localhost/",
        "https://[::1]/",
    ],
)
def test_redirect_to_internal_address_is_rejected(location):
    f, site = fetcher({"https://public.test/": redirect(location)})
    with pytest.raises(FetchError) as excinfo:
        f.fetch("https://public.test/")
    rejected(excinfo, FetchRejection.ADDRESS_BLOCKED)
    assert site.requested == ["https://public.test/"]


def test_redirect_downgrade_to_disallowed_scheme_is_rejected():
    f, site = fetcher({"https://public.test/": redirect("http://public.test/")})
    with pytest.raises(FetchError) as excinfo:
        f.fetch("https://public.test/")
    rejected(excinfo, FetchRejection.SCHEME_NOT_ALLOWED)
    assert site.requested == ["https://public.test/"]


def test_redirect_limit_and_missing_location():
    f, _ = fetcher(
        {f"https://public.test/{i}": redirect(f"/{i + 1}") for i in range(5)},
        max_redirects=2,
    )
    with pytest.raises(FetchError) as excinfo:
        f.fetch("https://public.test/0")
    rejected(excinfo, FetchRejection.TOO_MANY_REDIRECTS)

    f, _ = fetcher({"https://public.test/": httpx.Response(302)})
    with pytest.raises(FetchError) as excinfo:
        f.fetch("https://public.test/")
    rejected(excinfo, FetchRejection.REDIRECT_INVALID, ErrorCode.TOOL_RESPONSE_INVALID)


def test_declared_oversize_response_is_rejected():
    f, _ = fetcher(
        {
            "https://public.test/": httpx.Response(
                200, headers={"content-length": "1000"}, content=b"x" * 1000
            )
        }
    )
    with pytest.raises(FetchError) as excinfo:
        f.fetch("https://public.test/")
    rejected(
        excinfo, FetchRejection.RESPONSE_TOO_LARGE, ErrorCode.TOOL_RESPONSE_INVALID
    )


def test_streamed_oversize_response_is_rejected_without_length():
    pulled = []

    def chunks():
        for i in range(100):
            pulled.append(i)
            yield b"x" * 16

    f, _ = fetcher(
        {"https://public.test/": lambda _: httpx.Response(200, content=chunks())}
    )
    with pytest.raises(FetchError) as excinfo:
        f.fetch("https://public.test/")
    rejected(
        excinfo, FetchRejection.RESPONSE_TOO_LARGE, ErrorCode.TOOL_RESPONSE_INVALID
    )
    assert len(pulled) < 100  # 한도를 넘으면 더 읽지 않는다


def test_transport_timeout_and_total_deadline():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    f, _ = fetcher({"https://public.test/": slow})
    with pytest.raises(FetchError) as excinfo:
        f.fetch("https://public.test/")
    rejected(excinfo, FetchRejection.TIMEOUT, ErrorCode.TOOL_TIMEOUT)

    ticks = iter([0.0, 10.0, 20.0, 31.0])
    f, _ = fetcher(
        {"https://public.test/": httpx.Response(200, content=iter([b"a", b"b", b"c"]))},
        monotonic=lambda: next(ticks),
    )
    with pytest.raises(FetchError) as excinfo:
        f.fetch("https://public.test/")
    rejected(excinfo, FetchRejection.TIMEOUT, ErrorCode.TOOL_TIMEOUT)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, ErrorCode.TOOL_AUTH_FAILED),
        (403, ErrorCode.TOOL_AUTH_FAILED),
        (404, ErrorCode.TOOL_FAILED),
        (429, ErrorCode.TOOL_RATE_LIMITED),
        (503, ErrorCode.TOOL_UNAVAILABLE),
    ],
)
def test_http_status_is_classified(status, code):
    f, _ = fetcher({"https://public.test/?key=SECRET": httpx.Response(status)})
    with pytest.raises(FetchError) as excinfo:
        f.fetch("https://public.test/?key=SECRET")
    rejected(excinfo, FetchRejection.HTTP_STATUS, code)
    assert "SECRET" not in excinfo.value.message_redacted


def test_connection_failure_is_unavailable():
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    f, _ = fetcher({"https://public.test/": down})
    with pytest.raises(FetchError) as excinfo:
        f.fetch("https://public.test/")
    rejected(excinfo, FetchRejection.TRANSPORT_FAILED, ErrorCode.TOOL_UNAVAILABLE)


# --- 로컬 corpus ---


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "data" / "local" / "corpus"
    root.mkdir(parents=True)
    (root / "doc.txt").write_bytes(b"synthetic corpus text")
    (tmp_path / "secret.txt").write_bytes(b"outside")
    return tmp_path


def test_local_file_inside_corpus_is_read(corpus):
    raw = read_local(
        corpus, "data/local/corpus/doc.txt", max_bytes=64, clock=FakeClock(NOW)
    )
    assert raw.content == b"synthetic corpus text"
    source = to_source(
        raw,
        schema_version=SCHEMA,
        title="Synthetic doc",
        source_kind="report",
        language="en",
        access_notes="Synthetic only",
    )
    assert source.local_path == "data/local/corpus/doc.txt"
    assert source.url is None


@pytest.mark.parametrize(
    "path",
    [
        "data/local/../../secret.txt",
        "../secret.txt",
        "secret.txt",
        "/etc/passwd",
        "data/local",
        "data\\local\\corpus\\doc.txt",
        "fixture://robotics",
    ],
)
def test_local_path_escape_is_rejected(corpus, path):
    with pytest.raises(FetchError) as excinfo:
        read_local(corpus, path, max_bytes=64, clock=FakeClock(NOW))
    rejected(excinfo, FetchRejection.PATH_OUTSIDE_CORPUS)


def test_local_symlink_escape_is_rejected(corpus):
    link = corpus / "data" / "local" / "corpus" / "link.txt"
    link.symlink_to(corpus / "secret.txt")
    with pytest.raises(FetchError) as excinfo:
        read_local(
            corpus, "data/local/corpus/link.txt", max_bytes=64, clock=FakeClock(NOW)
        )
    rejected(excinfo, FetchRejection.PATH_OUTSIDE_CORPUS)


def test_local_missing_and_oversize_files(corpus):
    clock = FakeClock(NOW)
    with pytest.raises(FetchError) as excinfo:
        read_local(corpus, "data/local/corpus/none.txt", max_bytes=64, clock=clock)
    rejected(excinfo, FetchRejection.FILE_NOT_FOUND)
    with pytest.raises(FetchError) as excinfo:
        read_local(corpus, "data/local/corpus/doc.txt", max_bytes=4, clock=clock)
    rejected(
        excinfo, FetchRejection.RESPONSE_TOO_LARGE, ErrorCode.TOOL_RESPONSE_INVALID
    )


# --- 기준일 ---


def source_at(retrieved_at, published_at=None):
    return to_source(
        _raw(retrieved_at),
        schema_version=SCHEMA,
        title="Synthetic",
        source_kind="web",
        language="en",
        access_notes="Synthetic only",
        published_at=published_at,
    )


def _raw(retrieved_at):
    f = SafeFetcher(
        policy(),
        clock=FakeClock(retrieved_at),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x")),
        resolve=resolve,
    )
    return f.fetch("https://public.test/")


AS_OF = date(2026, 6, 30)
BEFORE = datetime(2026, 6, 1, tzinfo=UTC)
AFTER = datetime(2026, 9, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("retrieved_at", "published_at", "reason"),
    [
        (BEFORE, date(2026, 5, 1), None),
        (BEFORE, None, None),
        (datetime(2026, 6, 30, 23, tzinfo=UTC), date(2026, 5, 1), None),  # 당일 확보
        (AFTER, date(2026, 7, 1), AsOfExclusion.PUBLISHED_AFTER_AS_OF),
        (BEFORE, date(2026, 7, 1), AsOfExclusion.PUBLISHED_AFTER_AS_OF),
        (AFTER, None, AsOfExclusion.UNDATED_RETRIEVED_AFTER_AS_OF),
        (AFTER, date(2026, 5, 1), AsOfExclusion.EDITION_RETRIEVED_AFTER_AS_OF),
    ],
)
def test_as_of_admission_records_exclusion_reason(retrieved_at, published_at, reason):
    source = source_at(retrieved_at, published_at)
    decision = check_as_of(source, AS_OF)
    assert decision.reason == reason
    assert decision.admitted is (reason is None)
    assert decision.source_id == source.source_id
    assert decision.as_of == AS_OF
