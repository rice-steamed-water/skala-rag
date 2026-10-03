"""opt-in live Market 평가 smoke (#59). 기본 실행에서는 skip한다.

실행 조건:

    SKALA_LIVE_MARKET_EVAL=1
    SKALA_LIVE_MARKET_INPUT=data/local/<파일>.json   # Git 제외 경로
    OPENAI_API_KEY=<key>

입력 JSON은 ``{"snapshot": EvaluationSnapshot, "target_market": MarketTarget,
"market_links": {evidence_id: MarketLink}}``이다. snapshot은 동결된 실제 근거여야 하며
시장 귀속은 상위 조사에서 사람이 확인한 값이어야 한다. 이 smoke가 그것을 검증하지
않는다.

호출은 #45 runtime(live)을 거치며 평가 1회 + 구조 수정 1회, 요청당 입력 8,000/출력
2,000 token, USD 1.00 상한이다(M2 승인 B). rubric은 D14 core 승인(core-0.1.0)이지만
정책 파일은 draft이므로 결과는 연결 smoke이지 추천 근거가 아니다.
model·prompt·rubric version과 인용 근거 ID를 요약 JSON으로
출력한다(``pytest -s``). 근거 원문·key는 출력하지 않는다.
"""

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from skala_rag.agents.market import MarketLink, MarketTarget, evaluate_market
from skala_rag.contracts.evaluation import EvaluationSnapshot
from skala_rag.contracts.tools import ToolBudget
from skala_rag.prompts.market_evaluation import PROMPT_VERSION
from skala_rag.scoring.catalog import load_policy
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt, byte_bound_allowance
from skala_rag.tools.runtime import (
    AdapterRuntime,
    BudgetLedger,
    CallContext,
    Readiness,
    RuntimeLimits,
    RuntimePolicy,
)
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM

pytestmark = pytest.mark.skipif(
    os.environ.get("SKALA_LIVE_MARKET_EVAL") != "1",
    reason="live Market 평가 smoke는 SKALA_LIVE_MARKET_EVAL=1일 때만 실행",
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "live-smoke-1"
LLM_TOOL = "openai-market-evaluation"
# 평가 1회 + #22 구조 수정 1회. M2 승인 B의 LLM 8회 안.
LLM_MAX_CALLS = 2
TIMEOUT_SECONDS = 30.0
LLM_MAX_INPUT_TOKENS = 8_000
LLM_MAX_OUTPUT_TOKENS = 2_000
LLM_MAX_COST_USD = "1.00"
# 공식 요금(developers.openai.com/api/docs/models/gpt-4.1-mini, 2026-09-30 확인):
# 입력 $0.40 / 1M, 출력 $1.60 / 1M token.
USD_PER_INPUT_TOKEN = Decimal("0.40") / 1_000_000
USD_PER_OUTPUT_TOKEN = Decimal("1.60") / 1_000_000


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def test_live_market_evaluation_smoke():
    path = os.environ.get("SKALA_LIVE_MARKET_INPUT")
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not path or not api_key:
        pytest.skip("SKALA_LIVE_MARKET_INPUT·OPENAI_API_KEY 미설정")
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    snapshot = EvaluationSnapshot.model_validate(raw["snapshot"])
    target = MarketTarget.model_validate_json(json.dumps(raw["target_market"]))
    links = {
        eid: MarketLink.model_validate_json(json.dumps(link))
        for eid, link in raw["market_links"].items()
    }
    rubric = yaml.safe_load((ROOT / "configs/rubrics/core.yaml").read_text())
    # 정책 파일은 draft다(D14 core 승인은 정책 승인이 아님). live 추천 정책이 아니다.
    policy = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
    if snapshot.policy_version != policy.policy_version:
        pytest.fail("snapshot은 draft 정책 버전으로 동결되어야 한다(덮어쓰지 않음)")

    clock = SystemClock()
    run_id = f"live-market-smoke-{clock.now():%Y%m%dT%H%M%SZ}"
    transport = OpenAIResponsesAttempt(
        api_key=api_key,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA,
        clock=clock,
    )
    llm = _runtime_llm(transport, clock, run_id, snapshot.candidate_id)
    recorder = _AttemptRecorder(llm)
    result = evaluate_market(
        snapshot,
        target_market=target,
        market_links=links,
        rubric=rubric,
        llm=recorder,
        policy=policy,
        clock=clock,
        schema_version=SCHEMA,
    )

    report = {
        "run_id": run_id,
        "snapshot_id": snapshot.snapshot_id,
        "prompt_version": PROMPT_VERSION,
        "rubric_version": rubric["rubric_version"],
        "rubric_status": rubric["status"],
        "status": result.status,
        "errors": [(e.error_code, e.message_redacted) for e in result.errors],
        "llm_calls": [
            {
                "model": c.model,
                "prompt_version": c.prompt_version,
                "schema_hash": c.schema_hash,
                "input_tokens": c.input_tokens,
                "output_tokens": c.output_tokens,
                "status": c.status,
                "error_code": c.error_code,
            }
            for c in transport.llm_calls
        ],
        "attempts": recorder.attempts,
        "ledger": llm.runtime.ledger.snapshot(),
        "runtime_errors": [e.error_code for e in llm.runtime.error_history.values()],
    }
    if result.evaluation is not None:
        report["criteria"] = [
            {
                "criterion_id": c.criterion_id,
                "status": c.status,
                "rating": c.rating,
                "evidence_ids": c.evidence_ids,
                "missing_reason": c.missing_reason,
            }
            for c in result.evaluation.criteria
        ]
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    assert api_key not in json.dumps(report, default=str)
    assert api_key not in result.model_dump_json()
    # 성공이면 인용 근거는 모두 동결 snapshot 안에 있다(wrapper가 보장한 계약 재확인).
    if result.evaluation is not None:
        cited = {e for c in result.evaluation.criteria for e in c.evidence_ids}
        assert cited <= set(snapshot.evidence)


class _AttemptRecorder:
    """시도별 구조화 판단(rating·인용 ID)만 기록한다. 모델이 쓴 문장은 남기지 않는다."""

    def __init__(self, llm: RuntimeStructuredLLM) -> None:
        self.llm = llm
        self.attempts: list[list[dict]] = []

    def generate(self, *, system, user, output_schema):
        output = self.llm.generate(
            system=system, user=user, output_schema=output_schema
        )
        self.attempts.append(
            [
                {
                    "criterion_id": c.criterion_id,
                    "status": c.status,
                    "rating": c.rating,
                    "evidence_ids": c.evidence_ids,
                    "missing_reason": c.missing_reason,
                }
                for c in output.criteria
            ]
        )
        return output


def _runtime_llm(
    transport: OpenAIResponsesAttempt,
    clock: SystemClock,
    run_id: str,
    candidate_id: str,
) -> RuntimeStructuredLLM:
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
                max_calls=LLM_MAX_CALLS,
                tool_max_calls={LLM_TOOL: LLM_MAX_CALLS},
                max_input_tokens=LLM_MAX_INPUT_TOKENS * LLM_MAX_CALLS,
                max_output_tokens=LLM_MAX_OUTPUT_TOKENS * LLM_MAX_CALLS,
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
            candidate_id=candidate_id,
            tool_name=LLM_TOOL,
            node="market_evaluation",
        ),
        budget=ToolBudget(
            schema_version=SCHEMA,
            max_calls=LLM_MAX_CALLS,
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
        transport=transport,
        allowance_for=_per_request_allowance,
    )


def _per_request_allowance(system, user, schema):
    """요청당 입력 8,000 token 상한(M2 승인 B). 넘으면 호출 전에 거절한다."""
    allowance = byte_bound_allowance(
        system,
        user,
        schema,
        schema_version=SCHEMA,
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        usd_per_input_token=USD_PER_INPUT_TOKEN,
        usd_per_output_token=USD_PER_OUTPUT_TOKEN,
    )
    if allowance.input_tokens > LLM_MAX_INPUT_TOKENS:
        raise ValueError("request exceeds per-request input token limit")
    return allowance
