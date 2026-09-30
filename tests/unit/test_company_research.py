"""T04 live Company Research 조립·실패 계약 (#51). 네트워크 없이 실행.

모든 HTTP 응답은 ``httpx.MockTransport``, 이름 확인은 가짜 resolver다. 회사명·번호·
URL은 가상이며 실제 기업 자료가 아니다. 적격성은 #18의 ``check_eligibility``로 본다.
"""

import io
import json
import zipfile
from datetime import UTC, date, datetime

import httpx
import pytest

from skala_rag.agents.eligibility import (
    FIELD_BUSINESS,
    FIELD_DOMAIN,
    FIELD_EXIT,
    FIELD_IDENTITY,
    FIELD_LISTING,
    FIELD_STAGE,
    check_eligibility,
)
from skala_rag.contracts.candidates import Candidate
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import ResearchCompany
from skala_rag.contracts.tools import ToolBudget
from skala_rag.fakes import FakeClock
from skala_rag.tools.company_research import (
    FieldObservation,
    LiveResearchCompany,
    StageObservation,
)
from skala_rag.tools.official_homepage import OfficialHomepage
from skala_rag.tools.opendart import OpenDartCompany
from skala_rag.tools.source_fetch import FetchPolicy, SafeFetcher

SCHEMA = "synthetic-research-1"
RUN = "run-t04-live"
NOW = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
AS_OF = date(2026, 9, 30)
POLICY = {"policy_version": "synthetic-policy"}
KEY = "SECRET-DART-KEY-123"
HOME = "https://robot.example/"
DART = "https://opendart.fss.or.kr/api"
PUBLIC_IP = "93.184.216.34"


def resolve(host):
    return [PUBLIC_IP]


def candidate(**changes) -> Candidate:
    data = dict(
        schema_version=SCHEMA,
        candidate_id="co-alpha",
        canonical_name="가상로봇",
        aliases=["Gasang Robot"],
        country="KR",
        homepage_url=HOME,
        legal_identifiers={"brn": "123-45-67890"},
        discovery_source_ids=["src-discovery"],
    )
    data.update(changes)
    return Candidate(**data)


def budget(max_calls=5) -> ToolBudget:
    return ToolBudget(
        schema_version=SCHEMA, max_calls=max_calls, max_retries=0, timeout_seconds=30
    )


class Site:
    def __init__(self, routes):
        self.routes = routes
        self.requested: list[str] = []

    def __call__(self, request):
        url = str(request.url)
        self.requested.append(url)
        key = url.split("?")[0] if url.startswith(DART) else url
        if key.endswith("company.json"):
            key = f"{key}:{request.url.params['corp_code']}"
        route = self.routes.get(key)
        if route is None:
            return httpx.Response(404)
        return route(request) if callable(route) else route


def fetcher(site: Site, clock) -> SafeFetcher:
    return SafeFetcher(
        FetchPolicy(
            allowed_schemes=frozenset({"https"}),
            allowed_hosts=None,
            max_bytes=1_000_000,
            timeout_seconds=30.0,
            max_redirects=2,
        ),
        clock=clock,
        transport=httpx.MockTransport(site),
        resolve=resolve,
    )


def html(text="<html>가상로봇 공식 홈페이지</html>"):
    return httpx.Response(
        200, content=text.encode(), headers={"content-type": "text/html"}
    )


def dart_company(corp_code, *, bizr_no="1234567890", corp_cls="E", status="000"):
    return httpx.Response(
        200,
        json={
            "status": status,
            "message": "가상",
            "corp_code": corp_code,
            "corp_name": "가상로봇",
            "corp_cls": corp_cls,
            "bizr_no": bizr_no,
            "jurir_no": "",
        },
    )


def corp_index(*rows):
    xml = (
        "<result>"
        + "".join(
            f"<list><corp_code>{c}</corp_code><corp_name>{n}</corp_name></list>"
            for c, n in rows
        )
        + "</result>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("CORPCODE.xml", xml)
    return httpx.Response(200, content=buffer.getvalue())


def obs(field, value=None, *, basis="official_domain", **extra):
    """추출기가 돌려줄 관측. source_id는 추출기가 채운다."""
    return dict(field=field, value=value, identity_basis=basis, **extra)


class Extractor:
    """source_id만 채워 미리 정한 관측을 돌려준다(가짜 LLM 추출 대신)."""

    def __init__(self, specs):
        self.specs = specs

    def __call__(self, candidate, source, content):
        out = []
        for i, spec in enumerate(self.specs):
            spec = dict(spec)
            out.append(
                FieldObservation(
                    source_id=spec.pop("source_id", source.source_id),
                    locator=spec.pop("locator", f"{source.url}#obs-{i}"),
                    claim=spec.pop("claim", f"가상 {spec['field']} 관측 {i}"),
                    excerpt=spec.pop("excerpt", "가상 발췌"),
                    **spec,
                )
            )
        return out


FULL = [
    obs(FIELD_DOMAIN, True),
    obs(FIELD_LISTING, False),
    obs(FIELD_EXIT, False),
    obs(FIELD_STAGE, StageObservation("Series A", "series_a", "explicit")),
    obs(FIELD_IDENTITY),
    obs(FIELD_BUSINESS),
]


def research(routes, *, specs=None, key=KEY, dart=True, as_of=AS_OF, **dart_kw):
    clock = FakeClock(NOW)
    site = Site(routes)
    providers = [
        OfficialHomepage(
            fetcher(site, clock),
            schema_version=SCHEMA,
            clock=clock,
            extractor=Extractor(specs) if specs is not None else None,
        )
    ]
    if dart:
        providers.append(
            OpenDartCompany(
                fetcher(site, clock),
                api_key=key,
                schema_version=SCHEMA,
                clock=clock,
                max_name_matches=dart_kw.pop("max_name_matches", 3),
                max_index_bytes=1_000_000,
            )
        )
    tool = LiveResearchCompany(
        providers, run_id=RUN, schema_version=SCHEMA, as_of=as_of, clock=clock
    )
    return tool, site


def judge(bundle):
    return check_eligibility(
        bundle.profile, bundle.evidence, POLICY, run_id=RUN, evidence_revision=1
    )


def summary(result):
    return result.retrieval_records[-1].arguments_without_secrets


# ---------------------------------------------------------------- 정상 경로


def test_satisfies_research_company_boundary():
    tool, _ = research({HOME: html()}, dart=False)
    assert isinstance(tool, ResearchCompany)


def test_every_profile_fact_traces_to_source_and_evidence():
    tool, _ = research({HOME: html()}, specs=FULL, dart=False)
    result = tool(candidate(), budget())
    assert result.status == "ok"
    bundle = result.data
    for field, ids in bundle.profile.field_evidence_ids.items():
        for evidence_id in ids:
            ev = bundle.evidence[evidence_id]
            assert ev.source_id in bundle.sources
            assert ev.candidate_id == "co-alpha" and ev.scope == "company"
            record_ids = {r.retrieval_id for r in result.retrieval_records}
            assert {p.retrieval_id for p in ev.provenance} <= record_ids
            assert ev.provenance[0].method == "web"
    outcome = judge(bundle)
    assert outcome.status == "eligible"
    assert bundle.profile.stage.normalized_round == "series_a"


def test_dart_identifier_match_adds_identity_and_listing():
    routes = {
        HOME: html(),
        f"{DART}/corpCode.xml": corp_index(("00000001", "가상로봇")),
        f"{DART}/company.json:00000001": dart_company("00000001", corp_cls="K"),
    }
    tool, _ = research(routes)
    result = tool(candidate(), budget())
    bundle = result.data
    assert bundle.profile.is_listed is True
    listing = [
        bundle.evidence[i] for i in bundle.profile.field_evidence_ids[FIELD_LISTING]
    ]
    assert listing[0].provenance[0].method == "api"
    assert "코스닥" in listing[0].claim
    assert judge(bundle).checks["listing"]["reason_code"] == "LISTED"
    assert judge(bundle).status == "ineligible"


def test_dart_unlisted_class_keeps_scope_limitations():
    routes = {
        HOME: html(),
        f"{DART}/company.json:00000009": dart_company("00000009", corp_cls="E"),
    }
    tool, _ = research(routes)
    result = tool(candidate(legal_identifiers={"dart": "00000009"}), budget())
    bundle = result.data
    assert bundle.profile.is_listed is False
    (ev,) = [
        bundle.evidence[i] for i in bundle.profile.field_evidence_ids[FIELD_LISTING]
    ]
    assert any("해외 상장" in note for note in ev.limitations)
    assert any("과거 기준일" in note for note in ev.limitations)


# ---------------------------------------------------------------- 자료 없음·미조회


def test_no_data_is_empty_and_never_eligible():
    tool, site = research({}, dart=True)
    result = tool(candidate(homepage_url=None, legal_identifiers={}), budget())
    assert result.status == "empty"
    assert site.requested == []
    profile = result.data.profile
    assert (profile.is_listed, profile.exit_completed, profile.domain_match) == (
        None,
        None,
        None,
    )
    assert profile.stage.normalized_round == "unknown"
    assert profile.field_evidence_ids == {}
    providers = summary(result)["providers"]
    assert providers["official-homepage"]["skipped"] == "NO_HOMEPAGE"
    assert providers["opendart-company"]["skipped"] == "NO_LEGAL_IDENTIFIER"
    outcome = judge(result.data)
    assert outcome.status == "unknown"
    assert outcome.checks["listing"]["reason_code"] == "LISTING_UNKNOWN"


def test_dart_no_data_is_not_unlisted():
    routes = {
        HOME: html(),
        f"{DART}/company.json:00000002": dart_company("00000002", status="013"),
    }
    tool, _ = research(routes)
    result = tool(candidate(legal_identifiers={"dart": "00000002"}), budget())
    assert result.status == "ok"
    assert result.data.profile.is_listed is None
    dart = summary(result)["providers"]["opendart-company"]
    assert dart["status"] == "empty"
    assert "NO_DATA:00000002" in dart["notes"]


def test_homepage_without_extractor_keeps_source_but_no_facts():
    tool, _ = research({HOME: html()}, dart=False)
    result = tool(candidate(), budget())
    assert result.status == "ok"
    assert len(result.data.sources) == 1 and result.data.evidence == {}
    notes = summary(result)["providers"]["official-homepage"]["notes"]
    assert notes == ["EXTRACTOR_NOT_CONFIGURED"]
    assert judge(result.data).status == "unknown"


# ---------------------------------------------------------------- 동명 기업·식별


def test_same_name_company_with_other_number_is_not_attached():
    routes = {
        HOME: html(),
        f"{DART}/corpCode.xml": corp_index(
            ("00000003", "가상로봇"),
            ("00000004", "(주)가상로봇"),
            ("00000005", "다른회사"),
        ),
        f"{DART}/company.json:00000003": dart_company(
            "00000003", bizr_no="9999999999", corp_cls="Y"
        ),
        f"{DART}/company.json:00000004": dart_company("00000004", corp_cls="E"),
    }
    tool, site = research(routes)
    result = tool(candidate(), budget())
    bundle = result.data
    # 상장된 동명 기업(00000003)의 상장 사실을 붙이지 않는다.
    assert bundle.profile.is_listed is False
    assert all("00000003" not in s.url for s in bundle.sources.values())
    notes = summary(result)["providers"]["opendart-company"]["notes"]
    assert "IDENTIFIER_MISMATCH:00000003" in notes
    assert not any("00000005" in url for url in site.requested)


def test_only_same_name_companies_found_stays_unknown():
    routes = {
        HOME: html(),
        f"{DART}/corpCode.xml": corp_index(("00000003", "가상로봇")),
        f"{DART}/company.json:00000003": dart_company(
            "00000003", bizr_no="9999999999", corp_cls="Y"
        ),
    }
    tool, _ = research(routes)
    result = tool(candidate(), budget())
    assert result.data.profile.is_listed is None
    assert summary(result)["providers"]["opendart-company"]["status"] == "empty"


def test_name_only_observation_is_rejected():
    specs = [obs(FIELD_LISTING, False, basis="name_only"), obs(FIELD_EXIT, False)]
    tool, _ = research({HOME: html()}, specs=specs, dart=False)
    result = tool(candidate(), budget())
    assert result.data.profile.is_listed is None
    assert result.data.profile.exit_completed is False
    rejected = summary(result)["rejected_observations"]
    assert rejected == [
        {
            "field": FIELD_LISTING,
            "source_id": next(iter(result.data.sources)),
            "reason": "NAME_ONLY",
        }
    ]


def test_redirect_to_other_domain_is_not_official():
    routes = {
        HOME: httpx.Response(302, headers={"location": "https://news.example/a"}),
        "https://news.example/a": html(),
    }
    tool, _ = research(routes, specs=[obs(FIELD_EXIT, False)], dart=False)
    result = tool(candidate(), budget())
    assert result.data.profile.exit_completed is None
    assert summary(result)["rejected_observations"][0]["reason"] == "DOMAIN_MISMATCH"


@pytest.mark.parametrize(
    ("matched", "reason"),
    [
        ({"brn": "000-00-00000"}, "IDENTIFIER_MISMATCH"),
        ({"crn": "1101110000000"}, "IDENTIFIER_UNVERIFIABLE"),
        ({}, "IDENTIFIER_MISSING"),
    ],
)
def test_legal_identifier_observation_must_match_candidate(matched, reason):
    specs = [
        obs(FIELD_LISTING, True, basis="legal_identifier", matched_identifiers=matched)
    ]
    tool, _ = research({HOME: html()}, specs=specs, dart=False)
    result = tool(candidate(), budget())
    assert result.data.profile.is_listed is None
    assert summary(result)["rejected_observations"][0]["reason"] == reason


# ---------------------------------------------------------------- 상충·사건일·기준일


def test_conflicting_sources_without_event_dates_stay_unknown():
    specs = [obs(FIELD_LISTING, False), obs(FIELD_LISTING, True)]
    tool, _ = research({HOME: html()}, specs=specs, dart=False)
    bundle = tool(candidate(), budget()).data
    assert bundle.profile.is_listed is None
    ids = bundle.profile.field_evidence_ids[FIELD_LISTING]
    assert len(ids) == 2
    a, b = (bundle.evidence[i] for i in ids)
    assert a.conflicts_with == [b.evidence_id] and b.conflicts_with == [a.evidence_id]
    assert judge(bundle).checks["listing"]["status"] == "unknown"


def test_dart_and_homepage_listing_conflict_is_unknown():
    routes = {
        HOME: html(),
        f"{DART}/company.json:00000006": dart_company("00000006", corp_cls="E"),
    }
    specs = [obs(FIELD_LISTING, True)]
    tool, _ = research(routes, specs=specs)
    result = tool(candidate(legal_identifiers={"dart": "00000006"}), budget())
    assert result.data.profile.is_listed is None
    assert judge(result.data).checks["listing"]["status"] == "unknown"


def test_old_round_article_is_superseded_by_later_event():
    """기사 발행일이 아니라 라운드 사건일로 순서를 정한다."""
    specs = [
        # 늦게 발행된 회고 기사가 과거 Series A를 다시 언급
        obs(
            FIELD_STAGE,
            StageObservation("Series A", "series_a", "explicit"),
            event_date=date(2022, 3, 1),
            claim="2025년 기사: 2022년 Series A 유치",
        ),
        obs(
            FIELD_STAGE,
            StageObservation("Series B", "series_b", "explicit"),
            event_date=date(2024, 6, 1),
            claim="2024년 Series B 유치 보도자료",
        ),
    ]
    tool, _ = research({HOME: html()}, specs=specs, dart=False)
    bundle = tool(candidate(), budget()).data
    stage = bundle.profile.stage
    assert (stage.normalized_round, stage.last_round_date) == (
        "series_b",
        date(2024, 6, 1),
    )
    ids = bundle.profile.field_evidence_ids[FIELD_STAGE]
    newer = next(
        bundle.evidence[i] for i in ids if bundle.evidence[i].event_date.year == 2024
    )
    older = next(
        bundle.evidence[i] for i in ids if bundle.evidence[i].event_date.year == 2022
    )
    assert newer.supersedes == older.evidence_id
    check = judge(bundle).checks["stage"]
    assert check["status"] == "pass" and check["evidence_ids"] == [newer.evidence_id]


def test_one_page_with_old_and_new_rounds_uses_latest_event():
    """한 원문(같은 Source)에 과거·최신 라운드가 함께 있어도 최신 사건일을 쓴다."""
    specs = [
        obs(
            FIELD_STAGE,
            StageObservation("Series B", "series_b", "explicit"),
            event_date=date(2024, 6, 1),
        ),
        obs(
            FIELD_STAGE,
            StageObservation("Series A", "series_a", "explicit"),
            event_date=date(2022, 3, 1),
        ),
    ]
    tool, _ = research({HOME: html()}, specs=specs, dart=False)
    stage = tool(candidate(), budget()).data.profile.stage
    assert (stage.raw_label, stage.last_round_date) == ("Series B", date(2024, 6, 1))


def test_same_event_date_with_different_rounds_conflicts():
    specs = [
        obs(
            FIELD_STAGE,
            StageObservation("Series A", "series_a", "explicit"),
            event_date=date(2024, 6, 1),
        ),
        obs(
            FIELD_STAGE,
            StageObservation("Series B", "series_b", "explicit"),
            event_date=date(2024, 6, 1),
        ),
    ]
    tool, _ = research({HOME: html()}, specs=specs, dart=False)
    bundle = tool(candidate(), budget()).data
    assert bundle.profile.stage.normalized_round == "unknown"
    assert judge(bundle).checks["stage"]["status"] == "unknown"


def test_event_after_as_of_is_rejected():
    specs = [
        obs(FIELD_EXIT, True, event_date=date(2027, 1, 1)),
        obs(FIELD_EXIT, False),
    ]
    tool, _ = research({HOME: html()}, specs=specs, dart=False)
    result = tool(candidate(), budget())
    assert result.data.profile.exit_completed is False
    assert summary(result)["rejected_observations"][0]["reason"] == "EVENT_AFTER_AS_OF"


def test_source_retrieved_after_historical_as_of_is_excluded():
    tool, _ = research({HOME: html()}, specs=FULL, dart=False, as_of=date(2025, 1, 1))
    result = tool(candidate(), budget())
    bundle = result.data
    assert bundle.sources == {} and bundle.evidence == {}
    assert result.status == "empty"
    reasons = set(summary(result)["excluded_sources"].values())
    assert reasons == {"UNDATED_RETRIEVED_AFTER_AS_OF"}
    assert {r["reason"] for r in summary(result)["rejected_observations"]} == reasons


# ---------------------------------------------------------------- 단계 정책 재사용


def test_tips_label_is_not_auto_eligible():
    specs = [s for s in FULL if s["field"] != FIELD_STAGE] + [
        obs(FIELD_STAGE, StageObservation("TIPS 선정", "seed", "explicit"))
    ]
    tool, _ = research({HOME: html()}, specs=specs, dart=False)
    outcome = judge(tool(candidate(), budget()).data)
    assert outcome.status == "unknown"
    assert outcome.checks["stage"]["reason_code"] == "STAGE_TIPS_ONLY"


def test_estimated_stage_is_not_eligible():
    specs = [s for s in FULL if s["field"] != FIELD_STAGE] + [
        obs(
            FIELD_STAGE,
            StageObservation(None, "seed", "estimated"),
            evidence_kind="estimated",
        )
    ]
    tool, _ = research({HOME: html()}, specs=specs, dart=False)
    outcome = judge(tool(candidate(), budget()).data)
    assert outcome.status == "unknown"
    assert outcome.checks["stage"]["reason_code"] == "STAGE_ESTIMATED"


# ---------------------------------------------------------------- API·도구 실패


@pytest.mark.parametrize(
    ("response", "status", "code"),
    [
        (httpx.Response(503), "unavailable", ErrorCode.TOOL_UNAVAILABLE),
        (httpx.Response(403), "unavailable", ErrorCode.TOOL_AUTH_FAILED),
        (httpx.Response(404), "failed", ErrorCode.TOOL_FAILED),
    ],
)
def test_required_homepage_failure_fails_the_tool(response, status, code):
    tool, _ = research({HOME: response}, dart=False)
    result = tool(candidate(), budget())
    assert result.status == status and result.data is None
    assert result.errors[0].error_code == code.value
    assert result.errors[0].node == "company_research"
    assert result.retrieval_records[-1].error_id == result.errors[0].error_id


def test_homepage_timeout_is_failed_not_empty():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    tool, _ = research({HOME: slow}, dart=False)
    result = tool(candidate(), budget())
    assert result.status == "failed"
    assert result.errors[0].error_code == ErrorCode.TOOL_TIMEOUT.value


def test_missing_dart_key_is_recorded_without_request():
    tool, site = research({HOME: html()}, key=None)
    result = tool(candidate(), budget())
    assert result.status == "ok"
    assert all(not url.startswith(DART) for url in site.requested)
    dart = summary(result)["providers"]["opendart-company"]
    assert (dart["status"], dart["error_code"]) == (
        "unavailable",
        ErrorCode.TOOL_NOT_CONFIGURED.value,
    )
    assert result.data.profile.is_listed is None


@pytest.mark.parametrize(
    ("dart_status", "tool_status", "code"),
    [
        ("010", "unavailable", ErrorCode.TOOL_AUTH_FAILED),
        ("020", "unavailable", ErrorCode.TOOL_RATE_LIMITED),
        ("800", "unavailable", ErrorCode.TOOL_UNAVAILABLE),
        ("100", "failed", ErrorCode.TOOL_RESPONSE_INVALID),
    ],
)
def test_dart_api_errors_are_distinguished_from_no_data(dart_status, tool_status, code):
    routes = {
        HOME: html(),
        f"{DART}/company.json:00000007": dart_company("00000007", status=dart_status),
    }
    tool, _ = research(routes)
    result = tool(candidate(legal_identifiers={"dart": "00000007"}), budget())
    assert result.status == "ok"
    dart = summary(result)["providers"]["opendart-company"]
    assert (dart["status"], dart["error_code"]) == (tool_status, code.value)
    call = next(
        r for r in result.retrieval_records if r.tool_name.endswith("opendart-company")
    )
    assert call.status == tool_status
    assert result.data.profile.is_listed is None


def test_dart_http_failure_is_recorded_and_optional():
    routes = {HOME: html(), f"{DART}/corpCode.xml": httpx.Response(500)}
    tool, _ = research(routes)
    result = tool(candidate(), budget())
    assert result.status == "ok"
    dart = summary(result)["providers"]["opendart-company"]
    assert dart["error_code"] == ErrorCode.TOOL_UNAVAILABLE.value


def test_corrupt_corp_index_is_response_invalid():
    routes = {
        HOME: html(),
        f"{DART}/corpCode.xml": httpx.Response(200, content=b"PKbad"),
    }
    tool, _ = research(routes)
    dart = summary(tool(candidate(), budget()))["providers"]["opendart-company"]
    assert dart["error_code"] == ErrorCode.TOOL_RESPONSE_INVALID.value


def test_api_key_never_appears_in_result():
    routes = {
        HOME: html(),
        f"{DART}/corpCode.xml": corp_index(("00000001", "가상로봇")),
        f"{DART}/company.json:00000001": dart_company("00000001"),
    }
    tool, site = research(routes)
    result = tool(candidate(), budget())
    assert any(KEY in url for url in site.requested)
    assert KEY not in result.model_dump_json()


# ---------------------------------------------------------------- 예산


def test_zero_budget_fails_before_any_request():
    tool, site = research({HOME: html()})
    result = tool(candidate(), budget(0))
    assert result.status == "failed"
    assert result.errors[0].error_code == ErrorCode.BUDGET_EXHAUSTED.value
    assert site.requested == []


def test_budget_exhaustion_stops_optional_provider():
    routes = {
        HOME: html(),
        f"{DART}/corpCode.xml": corp_index(("00000001", "가상로봇")),
    }
    tool, site = research(routes)
    result = tool(candidate(), budget(1))
    assert site.requested == [HOME]
    assert summary(result)["requests_used"] == 1
    dart = summary(result)["providers"]["opendart-company"]
    assert dart["error_code"] == ErrorCode.BUDGET_EXHAUSTED.value


def test_same_name_lookups_are_capped():
    routes = {
        HOME: html(),
        f"{DART}/corpCode.xml": corp_index(
            ("00000011", "가상로봇"), ("00000012", "가상로봇"), ("00000013", "가상로봇")
        ),
        f"{DART}/company.json:00000011": dart_company("00000011", bizr_no="1"),
    }
    tool, site = research(routes, max_name_matches=1)
    result = tool(candidate(), budget())
    assert sum("company.json" in url for url in site.requested) == 1
    notes = summary(result)["providers"]["opendart-company"]["notes"]
    assert "NAME_MATCHES_TRUNCATED:3" in notes
    assert result.data.profile.is_listed is None


def test_summary_record_is_json_serializable():
    tool, _ = research({HOME: html()}, specs=FULL, dart=False)
    result = tool(candidate(), budget())
    json.dumps(summary(result))
    assert result.retrieval_records[-1].evidence_ids == sorted(result.data.evidence)
