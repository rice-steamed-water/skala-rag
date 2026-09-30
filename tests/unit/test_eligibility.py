"""T04: 동명 기업 분리, TIPS/unknown 자동 적격 금지, Exit 제외 (scoring §1, D06)."""

from datetime import UTC, datetime

import pytest
from tests.fixtures.eligibility import (
    ALL_FIELDS,
    POLICY,
    SCHEMA,
    candidate,
    evidence_payload,
    research_bundle,
)

from skala_rag.agents.eligibility import (
    ELIGIBILITY_CONFIRMED,
    FIELD_BUSINESS,
    FIELD_DOMAIN,
    FIELD_EXIT,
    FIELD_IDENTITY,
    FIELD_LISTING,
    FIELD_STAGE,
    EligibilityInputError,
    check_eligibility,
)
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.ids import eligibility_result_id
from skala_rag.contracts.interfaces import CheckEligibility, ResearchCompany
from skala_rag.contracts.tools import ToolBudget
from skala_rag.fakes import FakeClock
from skala_rag.tools.fixture_research import FixtureResearchCompany

RUN = "run-t04"


def judge(bundle, evidence=None, policy=POLICY):
    return check_eligibility(
        bundle.profile,
        bundle.evidence if evidence is None else evidence,
        policy,
        run_id=RUN,
        evidence_revision=1,
    )


def without(field):
    return tuple(f for f in ALL_FIELDS if f != field)


def test_all_conditions_confirmed_is_eligible():
    result = judge(research_bundle("co-a"))
    assert result.status == "eligible"
    assert result.reason_codes == [ELIGIBILITY_CONFIRMED]
    assert {c["status"] for c in result.checks.values()} == {"pass"}
    assert set(result.evidence_ids) == {f"ev-co-a-{f}" for f in ALL_FIELDS}
    assert result.eligibility_result_id == eligibility_result_id(
        RUN, "co-a", 1, POLICY["policy_version"]
    )
    assert result.checks["exit"]["as_of"] == "2026-09-30"


@pytest.mark.parametrize("round_", ["seed", "series_a", "series_b", "series_c"])
def test_explicit_seed_to_series_c_pass_stage(round_):
    bundle = research_bundle("co-a", raw_label=round_, normalized_round=round_)
    assert judge(bundle).checks["stage"]["status"] == "pass"


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"is_listed": True}, "LISTED"),
        ({"exit_completed": True}, "EXIT_COMPLETED"),
        ({"domain_match": False}, "DOMAIN_MISMATCH"),
        (
            {"raw_label": "Series D", "normalized_round": "out_of_scope"},
            "STAGE_OUT_OF_SCOPE",
        ),
        (
            {"raw_label": "프리시드", "normalized_round": "out_of_scope"},
            "STAGE_OUT_OF_SCOPE",
        ),
    ],
)
def test_confirmed_disqualifier_is_ineligible(kwargs, code):
    result = judge(research_bundle("co-a", **kwargs))
    assert result.status == "ineligible"
    assert result.reason_codes == [code]


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        # TIPS 선정만으로 seed 확정 금지
        ({"raw_label": "TIPS 선정", "normalized_round": "seed"}, "STAGE_TIPS_ONLY"),
        # 추정 단계는 explicit로 승격하지 않는다
        ({"normalized_round": "series_a", "method": "estimated"}, "STAGE_ESTIMATED"),
        ({"normalized_round": "unknown", "method": "unknown"}, "STAGE_UNKNOWN"),
        # 직전 완료 라운드 근거 없는 브릿지
        (
            {"raw_label": "브릿지", "normalized_round": "unknown", "method": "unknown"},
            "STAGE_UNKNOWN",
        ),
        # 프리시드 표기를 seed로 올린 입력
        ({"raw_label": "pre-seed", "normalized_round": "seed"}, "STAGE_LABEL_MISMATCH"),
        # 명시적이지 않은 out_of_scope는 부적격 확정이 아니다
        (
            {"normalized_round": "out_of_scope", "method": "estimated"},
            "STAGE_ESTIMATED",
        ),
        ({"domain_match": None}, "DOMAIN_UNKNOWN"),
        ({"is_listed": None}, "LISTING_UNKNOWN"),
        # 모름을 false로 바꾸지 않는다
        ({"exit_completed": None}, "EXIT_UNKNOWN"),
    ],
)
def test_unresolved_condition_is_unknown(kwargs, code):
    result = judge(research_bundle("co-a", **kwargs))
    assert result.status == "unknown"
    assert result.reason_codes == [code]


def test_bridge_with_confirmed_previous_round_passes():
    bundle = research_bundle(
        "co-a", raw_label="시리즈A 브릿지", normalized_round="series_a"
    )
    assert judge(bundle).status == "eligible"


@pytest.mark.parametrize(
    ("field", "code"),
    [
        # 상장 검색 0건(근거 없음)을 비상장으로 처리하지 않는다
        (FIELD_LISTING, "LISTING_UNVERIFIED"),
        (FIELD_EXIT, "EXIT_UNVERIFIED"),
        (FIELD_DOMAIN, "DOMAIN_UNVERIFIED"),
        (FIELD_STAGE, "STAGE_UNVERIFIED"),
        (FIELD_IDENTITY, "IDENTITY_UNVERIFIED"),
        (FIELD_BUSINESS, "BUSINESS_EVIDENCE_MISSING"),
    ],
)
def test_value_without_evidence_is_unknown(field, code):
    result = judge(research_bundle("co-a", fields=without(field)))
    assert result.status == "unknown"
    assert result.reason_codes == [code]


def test_listed_without_evidence_is_not_ineligible():
    result = judge(
        research_bundle("co-a", is_listed=True, fields=without(FIELD_LISTING))
    )
    assert result.status == "unknown"
    assert result.reason_codes == ["LISTING_UNVERIFIED"]


def test_estimated_evidence_does_not_confirm():
    bundle = research_bundle(
        "co-a", evidence_overrides={FIELD_LISTING: {"evidence_kind": "estimated"}}
    )
    result = judge(bundle)
    assert result.status == "unknown"
    assert result.reason_codes == ["LISTING_ESTIMATED"]
    assert "ev-co-a-is_listed" not in result.evidence_ids


def test_superseded_evidence_does_not_confirm():
    correction = evidence_payload(
        "co-a", "correction", supersedes="ev-co-a-exit_completed"
    )
    bundle = research_bundle("co-a", extra_evidence=[correction])
    result = judge(bundle)
    assert result.status == "unknown"
    assert result.reason_codes == ["EXIT_SUPERSEDED"]


def test_conflicting_evidence_does_not_confirm():
    other = evidence_payload("co-a", "other-stage-report")
    bundle = research_bundle(
        "co-a",
        evidence_overrides={FIELD_STAGE: {"conflicts_with": [other["evidence_id"]]}},
        extra_evidence=[other],
    )
    result = judge(bundle)
    assert result.status == "unknown"
    assert result.reason_codes == ["STAGE_CONFLICT"]


def test_ineligible_takes_precedence_and_keeps_unknown_reasons():
    bundle = research_bundle(
        "co-a", is_listed=True, normalized_round="unknown", method="unknown"
    )
    result = judge(bundle)
    assert result.status == "ineligible"
    assert result.reason_codes == ["LISTED", "STAGE_UNKNOWN"]
    assert result.checks["stage"]["status"] == "unknown"


def test_same_name_company_evidence_is_rejected():
    alpha = research_bundle("co-alpha-kr")
    namesake = research_bundle("co-alpha-us", is_listed=True)
    mixed = alpha.profile.model_copy(
        update={
            "field_evidence_ids": {
                **alpha.profile.field_evidence_ids,
                FIELD_LISTING: ["ev-co-alpha-us-is_listed"],
            }
        }
    )
    with pytest.raises(EligibilityInputError):
        check_eligibility(
            mixed,
            {**alpha.evidence, **namesake.evidence},
            POLICY,
            run_id=RUN,
            evidence_revision=1,
        )
    assert judge(alpha).status == "eligible"
    assert judge(namesake).status == "ineligible"


def test_missing_evidence_reference_is_rejected():
    bundle = research_bundle("co-a")
    with pytest.raises(EligibilityInputError):
        judge(bundle, evidence={})


def test_policy_version_required():
    with pytest.raises(EligibilityInputError):
        judge(research_bundle("co-a"), policy={})


def test_check_eligibility_satisfies_protocol_shape():
    assert isinstance(check_eligibility, CheckEligibility)


def _adapter(bundles, **kwargs):
    clock = FakeClock(datetime(2026, 9, 30, tzinfo=UTC))
    return FixtureResearchCompany(
        bundles, run_id=RUN, schema_version=SCHEMA, clock=clock, **kwargs
    )


BUDGET = ToolBudget(
    schema_version=SCHEMA, max_calls=1, max_retries=0, timeout_seconds=30
)


def test_fixture_research_returns_independent_bundle_per_same_name_candidate():
    bundles = {
        "co-alpha-kr": research_bundle("co-alpha-kr"),
        "co-alpha-us": research_bundle("co-alpha-us", is_listed=True),
    }
    research = _adapter(bundles)
    assert isinstance(research, ResearchCompany)

    kr = research(candidate("co-alpha-kr"), BUDGET)
    us = research(candidate("co-alpha-us", country="US"), BUDGET)
    assert kr.status == us.status == "ok"
    assert kr.data.profile.candidate_id == "co-alpha-kr"
    assert us.data.profile.candidate_id == "co-alpha-us"
    assert kr.retrieval_records[0].candidate_id == "co-alpha-kr"
    assert kr.retrieval_records[0].evidence_ids == list(kr.data.evidence)
    assert kr.data is not bundles["co-alpha-kr"]

    assert judge(kr.data).status == "eligible"
    assert judge(us.data).status == "ineligible"


def test_fixture_research_unknown_candidate_fails_without_profile():
    result = _adapter({})(candidate("co-missing"), BUDGET)
    assert result.status == "failed"
    assert result.data is None
    assert result.errors[0].error_code == ErrorCode.TOOL_FAILED


def test_fixture_research_budget_and_injected_error():
    bundles = {"co-a": research_bundle("co-a")}
    exhausted = ToolBudget(
        schema_version=SCHEMA, max_calls=0, max_retries=0, timeout_seconds=30
    )
    result = _adapter(bundles)(candidate("co-a"), exhausted)
    assert result.errors[0].error_code == ErrorCode.BUDGET_EXHAUSTED

    result = _adapter(bundles, error_code=ErrorCode.TOOL_AUTH_FAILED)(
        candidate("co-a"), BUDGET
    )
    assert result.status == "unavailable"
    assert result.errors[0].retryable is False


def test_fixture_research_rejects_mismatched_bundle():
    with pytest.raises(ValueError):
        _adapter({"co-b": research_bundle("co-a")})
