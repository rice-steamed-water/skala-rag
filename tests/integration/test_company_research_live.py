"""opt-in live Company Research smoke (#51). 기본 실행에서는 skip한다.

실행 조건(M2 승인 B의 smoke 범위: 이 테스트는 외부 요청 최대 3회):

    SKALA_LIVE_COMPANY_RESEARCH=1
    SKALA_LIVE_COMPANY_NAME=<후보 이름>
    SKALA_LIVE_HOMEPAGE_URL=<공식 홈페이지 URL>
    SKALA_LIVE_LEGAL_IDENTIFIERS='{"dart": "..."}'   # 선택, JSON
    OPENDART_API_KEY=<key>                           # 선택, 없으면 미설정 기록

추출기(LLM)는 연결하지 않는다(#47·#50). 따라서 홈페이지는 Source만 남기고,
적격성 사실은 OpenDART 식별자 일치 시의 식별·상장 근거뿐이다. 결과는 요약 JSON으로
출력한다(``pytest -s``). 원문·key는 출력하지 않는다.
"""

import json
import os
from datetime import UTC, datetime

import pytest

from skala_rag.agents.eligibility import check_eligibility
from skala_rag.contracts.candidates import Candidate
from skala_rag.contracts.tools import ToolBudget
from skala_rag.tools.company_research import LiveResearchCompany
from skala_rag.tools.official_homepage import OfficialHomepage
from skala_rag.tools.opendart import HOST as DART_HOST
from skala_rag.tools.opendart import OpenDartCompany
from skala_rag.tools.source_fetch import FetchPolicy, SafeFetcher

pytestmark = pytest.mark.skipif(
    os.environ.get("SKALA_LIVE_COMPANY_RESEARCH") != "1",
    reason="live Company Research smoke는 SKALA_LIVE_COMPANY_RESEARCH=1일 때만 실행",
)

SCHEMA = "live-smoke-1"
# smoke 전용 값. 승인된 코드 기본값이 아니다. timeout 30초는 D08 시도별 한도.
SMOKE_MAX_CALLS = 3
TIMEOUT_SECONDS = 30.0
MAX_BYTES = 5_000_000
MAX_REDIRECTS = 3


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def test_live_company_research_smoke():
    name = os.environ.get("SKALA_LIVE_COMPANY_NAME")
    homepage = os.environ.get("SKALA_LIVE_HOMEPAGE_URL")
    if not name or not homepage:
        pytest.skip("SKALA_LIVE_COMPANY_NAME·SKALA_LIVE_HOMEPAGE_URL 미설정")
    identifiers = json.loads(os.environ.get("SKALA_LIVE_LEGAL_IDENTIFIERS") or "{}")
    clock = SystemClock()
    now = clock.now()
    run_id = f"live-smoke-{now:%Y%m%dT%H%M%SZ}"

    def fetcher(hosts):
        return SafeFetcher(
            FetchPolicy(
                allowed_schemes=frozenset({"https"}),
                allowed_hosts=hosts,
                max_bytes=MAX_BYTES,
                timeout_seconds=TIMEOUT_SECONDS,
                max_redirects=MAX_REDIRECTS,
            ),
            clock=clock,
        )

    tool = LiveResearchCompany(
        [
            OfficialHomepage(
                fetcher(None), schema_version=SCHEMA, clock=clock, extractor=None
            ),
            OpenDartCompany(
                fetcher(frozenset({DART_HOST})),
                api_key=os.environ.get("OPENDART_API_KEY"),
                schema_version=SCHEMA,
                clock=clock,
                max_name_matches=1,
                max_index_bytes=100_000_000,
            ),
        ],
        run_id=run_id,
        schema_version=SCHEMA,
        as_of=now.date(),
        clock=clock,
    )
    candidate = Candidate(
        schema_version=SCHEMA,
        candidate_id="live-smoke-candidate",
        canonical_name=name,
        aliases=[],
        country="KR",
        homepage_url=homepage,
        legal_identifiers=identifiers,
        discovery_source_ids=["live-smoke-manual-input"],
    )
    result = tool(
        candidate,
        ToolBudget(
            schema_version=SCHEMA,
            max_calls=SMOKE_MAX_CALLS,
            max_retries=0,
            timeout_seconds=TIMEOUT_SECONDS,
        ),
    )

    report = {
        "run_id": run_id,
        "status": result.status,
        "errors": [(e.error_code, e.message_redacted) for e in result.errors],
        "records": [
            {
                "tool": r.tool_name,
                "status": r.status,
                "arguments": r.arguments_without_secrets,
                "source_ids": r.source_ids,
            }
            for r in result.retrieval_records
        ],
    }
    if result.data is not None:
        bundle = result.data
        report["sources"] = {
            sid: {"url": s.url, "content_hash": s.content_hash, "title": s.title}
            for sid, s in bundle.sources.items()
        }
        report["profile"] = bundle.profile.model_dump(mode="json")
        report["eligibility"] = check_eligibility(
            bundle.profile,
            bundle.evidence,
            {"policy_version": "live-smoke-unapproved"},
            run_id=run_id,
            evidence_revision=1,
        ).model_dump(mode="json", include={"status", "reason_codes"})
    print(json.dumps(report, ensure_ascii=False, indent=2))

    key = os.environ.get("OPENDART_API_KEY")
    if key:
        assert key not in result.model_dump_json()
    # 결과가 무엇이든 unknown을 eligible로 바꾸지 않는다.
    if result.data is not None and "eligibility" in report:
        assert report["eligibility"]["status"] != "eligible" or result.data.evidence
