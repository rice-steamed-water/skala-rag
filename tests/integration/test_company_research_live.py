"""opt-in live Company Research smoke (#51). 기본 실행에서는 skip한다.

실행 조건(M2 승인 B의 smoke 범위: 이 테스트는 외부 요청 최대 3회):

    SKALA_LIVE_COMPANY_RESEARCH=1
    SKALA_LIVE_COMPANY_NAME=<후보 이름>
    SKALA_LIVE_HOMEPAGE_URL=<공식 홈페이지 URL>
    SKALA_LIVE_LEGAL_IDENTIFIERS='{"dart": "..."}'   # 선택, JSON
    OPENDART_API_KEY=<key>                           # 선택, 없으면 미설정 기록
    SKALA_LIVE_LLM=1 + OPENAI_API_KEY=<key>          # 선택, 홈페이지 사실 추출

``SKALA_LIVE_LLM=1``이면 공식 홈페이지 원문을 #47 adapter로 추출한다. 호출은 #45
runtime(live)을 거치며 LLM 요청 1회·입력 8,000/출력 2,000 token·USD 1.00 상한이다
(M2 승인 B). 없으면 홈페이지는 Source만 남긴다. 결과는 요약 JSON으로 출력한다
(``pytest -s``). 원문·key는 출력하지 않는다.
"""

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from skala_rag.agents.eligibility import check_eligibility
from skala_rag.agents.eligibility_extraction import LLMEligibilityExtractor
from skala_rag.contracts.candidates import Candidate
from skala_rag.contracts.tools import ToolBudget
from skala_rag.prompts.eligibility_facts import PROMPT_VERSION
from skala_rag.tools.company_research import LiveResearchCompany
from skala_rag.tools.official_homepage import OfficialHomepage
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt, byte_bound_allowance
from skala_rag.tools.opendart import HOST as DART_HOST
from skala_rag.tools.opendart import OpenDartCompany
from skala_rag.tools.runtime import (
    AdapterRuntime,
    BudgetLedger,
    CallContext,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
)
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM
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

# LLM smoke 상한: M2 승인 B(docs/implementation/m2-live-approval-proposal.md §3).
LLM_TOOL = "openai-eligibility-facts"
LLM_MAX_INPUT_TOKENS = 8_000
LLM_MAX_OUTPUT_TOKENS = 2_000
LLM_MAX_COST_USD = "1.00"
# 한국어 1자 ≈ 3 byte. byte 기준 입력 상한 8,000 안에 prompt·schema와 함께 든다.
LLM_MAX_INPUT_CHARS = 1_200
# 공식 요금(developers.openai.com/api/docs/models/gpt-4.1-mini, 2026-09-30 확인):
# 입력 $0.40 / 1M, 출력 $1.60 / 1M token.
USD_PER_INPUT_TOKEN = Decimal("0.40") / 1_000_000
USD_PER_OUTPUT_TOKEN = Decimal("1.60") / 1_000_000
DOMAIN_DEFINITION = (
    "Physical AI / Robotics: 로봇·자율 이동체·로봇 팔 등 물리 세계에서 동작하는 "
    "하드웨어와 그 제어·인지 AI (과제 대상 도메인, smoke용 문구)"
)


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

    llm = None
    extractor = None
    if os.environ.get("SKALA_LIVE_LLM") == "1":
        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not openai_key:
            pytest.skip("SKALA_LIVE_LLM=1이지만 OPENAI_API_KEY 미설정")
        llm = _runtime_llm(openai_key, clock, run_id)
        extractor = LLMEligibilityExtractor(
            llm,
            as_of=now.date(),
            domain_definition=DOMAIN_DEFINITION,
            max_input_chars=LLM_MAX_INPUT_CHARS,
        )

    tool = LiveResearchCompany(
        [
            OfficialHomepage(
                fetcher(None), schema_version=SCHEMA, clock=clock, extractor=extractor
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
        report["evidence"] = {
            eid: {"claim": e.claim, "excerpt": e.excerpt, "event_date": e.event_date}
            for eid, e in bundle.evidence.items()
        }
    if llm is not None:
        report["llm"] = {
            "records": [
                {"status": r.status, **r.arguments_without_secrets}
                for r in llm.retrieval_records
            ],
            "ledger": llm.runtime.ledger.snapshot(),
            "errors": [e.error_code for e in llm.runtime.error_history.values()],
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    dumped = result.model_dump_json() + json.dumps(report, default=str)
    for secret in ("OPENDART_API_KEY", "OPENAI_API_KEY"):
        value = os.environ.get(secret, "").strip()
        if value:
            assert value not in dumped
    # 결과가 무엇이든 unknown을 eligible로 바꾸지 않는다.
    if result.data is not None and "eligibility" in report:
        assert report["eligibility"]["status"] != "eligible" or result.data.evidence


def _runtime_llm(api_key: str, clock: SystemClock, run_id: str) -> RuntimeStructuredLLM:
    runtime = AdapterRuntime(
        policy=RuntimePolicy(
            schema_version=SCHEMA,
            execution_mode="live",
            retry_delays_seconds=(),
            live_approval_reference="m2-live-approval-proposal.md §3 B (#43)",
            timing_approval_reference="D08 시도별 timeout 30초",
        ),
        ledger=BudgetLedger(
            RuntimeLimits(
                schema_version=SCHEMA,
                max_calls=1,
                tool_max_calls={LLM_TOOL: 1},
                max_input_tokens=LLM_MAX_INPUT_TOKENS,
                max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
                max_cost_usd=LLM_MAX_COST_USD,
            )
        ),
        clock=clock,
        sleep=lambda seconds: None,
    )
    return RuntimeStructuredLLM(
        runtime=runtime,
        call=CallContext(
            schema_version=SCHEMA,
            call_id=f"{run_id}-llm",
            run_id=run_id,
            candidate_id="live-smoke-candidate",
            tool_name=LLM_TOOL,
            node="company_research",
        ),
        budget=ToolBudget(
            schema_version=SCHEMA,
            max_calls=1,
            max_retries=0,
            timeout_seconds=TIMEOUT_SECONDS,
            deadline=clock.now() + timedelta(minutes=10),
        ),
        readiness=Readiness(
            schema_version=SCHEMA,
            required=True,
            configured=True,
            credential_required=True,
            credential_present=True,
            model_required=False,
            model_available=False,
            index_required=False,
            index_available=False,
        ),
        transport=OpenAIResponsesAttempt(
            api_key=api_key,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA,
            clock=clock,
        ),
        allowance_for=lambda system, user, schema: byte_bound_allowance(
            system,
            user,
            schema,
            schema_version=SCHEMA,
            max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
            usd_per_input_token=USD_PER_INPUT_TOKEN,
            usd_per_output_token=USD_PER_OUTPUT_TOKEN,
        ),
    )
