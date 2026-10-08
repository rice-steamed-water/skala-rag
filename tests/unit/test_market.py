"""#59 Market market-context boundary; all values are synthetic fixtures."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml
from tests.fixtures.loader import load_common_fixtures

from skala_rag.agents.market import MarketLink, MarketTarget, evaluate_market
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import LLMError
from skala_rag.fakes import FakeClock, FakeLLM
from skala_rag.scoring.catalog import load_policy

ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
SOURCE_RUBRIC = yaml.safe_load((ROOT / "configs/rubrics/core.yaml").read_text())
RUBRIC = {**SOURCE_RUBRIC, "status": "approved"}
CLOCK = FakeClock(datetime(2026, 9, 30, tzinfo=UTC))
SEGMENT = "kr-logistics-amr"
TARGET = MarketTarget(segment_id=SEGMENT, geographies=("KR", "GLOBAL"))


def _sam(value=8e8, *, year=2025, geography="KR", basis="actual", metric="sam"):
    return {
        "criterion_ids": ["market.size"],
        "value": value,
        "unit": "USD",
        "currency": "USD",
        "value_as_of": date(year, 12, 31),
        "geography": geography,
    }, MarketLink(segment_id=SEGMENT, metric=metric, basis=basis, reference_year=year)


def _cagr(value=18.2, *, start=2025, end=2030, geography="KR"):
    return {
        "criterion_ids": ["market.growth"],
        "value": value,
        "unit": "%",
        "geography": geography,
    }, MarketLink(
        segment_id=SEGMENT,
        metric="cagr",
        basis="forecast",
        reference_year=start,
        end_year=end,
    )


class Case:
    def __init__(self):
        fixtures = load_common_fixtures(POLICY)
        self.snapshot = next(iter(fixtures.snapshots.values()))
        self.base = next(
            e
            for e in self.snapshot.evidence.values()
            if "market.size" in e.criterion_ids
        )
        market = [
            eid
            for eid, evidence in self.snapshot.evidence.items()
            if any(cid.startswith("market.") for cid in evidence.criterion_ids)
        ]
        self.snapshot = self._replace(market, {})
        self.links: dict[str, MarketLink] = {}
        self.add(
            "ev-demand",
            {"criterion_ids": ["market.demand"]},
            MarketLink(segment_id=SEGMENT),
        )
        self.add("ev-sam", *_sam())
        self.add("ev-cagr", *_cagr())

    def _replace(self, drop, add):
        evidence = {
            key: value
            for key, value in self.snapshot.evidence.items()
            if key not in drop
        }
        evidence.update(add)
        return self.snapshot.model_copy(
            update={"evidence_ids": list(evidence), "evidence": evidence}, deep=True
        )

    def add(self, eid, fields, link, *, scope="industry", candidate_id=None):
        evidence = self.base.model_copy(
            update={
                "evidence_id": eid,
                "scope": scope,
                "candidate_id": candidate_id,
                "claim": f"가상 시장 근거 {eid}",
                "excerpt": f"가상 시장 근거 {eid}. 실측 아님.",
                **fields,
            }
        )
        self.snapshot = self._replace([], {eid: evidence})
        if link is not None:
            self.links[eid] = link

    def output(self, *, size=("ev-sam",), size_rating=3, growth=("ev-cagr",), gr=4):
        def observed(cid, ids, rating):
            return {
                "criterion_id": cid,
                "status": "observed",
                "rating": rating,
                "evidence_ids": list(ids),
                "rationale": f"가상 {cid} 판단",
            }

        return {
            "criteria": [
                observed("market.size", size, size_rating),
                observed("market.growth", growth, gr),
                observed("market.demand", ["ev-demand"], 3),
            ],
            "research_gaps": [],
            "caveats": [],
        }

    def run(self, *outputs, links=None, rubric=RUBRIC):
        llm = FakeLLM(list(outputs))
        result = evaluate_market(
            self.snapshot,
            target_market=TARGET,
            market_links=self.links if links is None else links,
            rubric=rubric,
            llm=llm,
            policy=POLICY,
            clock=CLOCK,
            schema_version="synthetic-1",
        )
        return result, llm


@pytest.fixture
def case():
    return Case()


def _payload(llm, call=0):
    return json.loads(llm.calls[call].user)


def test_approval_does_not_transfer_to_unknown_core_version(case):
    llm = FakeLLM([])
    with pytest.raises(ValueError, match="core-0.1.0"):
        evaluate_market(
            case.snapshot,
            target_market=TARGET,
            market_links=case.links,
            rubric={**RUBRIC, "rubric_version": "core-unapproved"},
            llm=llm,
            policy=POLICY,
            clock=CLOCK,
            schema_version="synthetic-1",
        )
    assert llm.calls == []


def test_matching_market_evidence_reaches_wrapper_with_context(case):
    result, llm = case.run(case.output())
    assert result.status == "success"
    assert result.evaluation.dimension == "market"
    assert result.evaluation.rubric_version == RUBRIC["rubric_version"]
    context = _payload(llm)["context"]
    assert context["target_market"]["segment_id"] == SEGMENT
    assert context["market_figures"]["ev-sam"] == {
        "metric": "sam",
        "basis": "actual",
        "reference_year": 2025,
        "end_year": None,
        "geography": "KR",
        "rubric_band": 3,
    }
    assert context["market_figures"]["ev-cagr"]["end_year"] == 2030


@pytest.mark.parametrize("metric", ["tam", "sam"])
@pytest.mark.parametrize(
    "value_as_of", [date(2019, 12, 31), None], ids=["year-mismatch", "null-date"]
)
def test_size_source_date_contract_rejected_before_llm(case, metric, value_as_of):
    fields, link = _sam(year=2025, metric=metric)
    case.add("ev-date-mismatch", {**fields, "value_as_of": value_as_of}, link)
    llm = FakeLLM([case.output(size=["ev-date-mismatch"], size_rating=3)])
    with pytest.raises(ValueError, match="value_as_of"):
        evaluate_market(
            case.snapshot,
            target_market=TARGET,
            market_links=case.links,
            rubric=RUBRIC,
            llm=llm,
            policy=POLICY,
            clock=CLOCK,
            schema_version="synthetic-1",
        )
    assert llm.calls == []


@pytest.mark.parametrize("metric", ["tam", "sam"])
def test_size_matching_source_year_reaches_llm(case, metric):
    fields, link = _sam(year=2025, metric=metric)
    case.add("ev-matching-date", {**fields, "value_as_of": date(2025, 1, 1)}, link)
    result, llm = case.run(case.output(size=["ev-matching-date"], size_rating=3))
    assert result.status == "success"
    assert len(llm.calls) == 1
    assert (
        _payload(llm)["context"]["market_figures"]["ev-matching-date"][
            "reference_year"
        ]
        == 2025
    )


@pytest.mark.parametrize("metric", ["tam", "sam"])
def test_size_outside_rubric_bands_rejected_before_llm(case, metric):
    case.add("ev-out-of-band", *_sam(float("nan"), metric=metric))
    llm = FakeLLM([case.output(size=["ev-out-of-band"], size_rating=1)])
    with pytest.raises(ValueError, match="value outside rubric bands"):
        evaluate_market(
            case.snapshot,
            target_market=TARGET,
            market_links=case.links,
            rubric=RUBRIC,
            llm=llm,
            policy=POLICY,
            clock=CLOCK,
            schema_version="synthetic-1",
        )
    assert llm.calls == []


def test_other_segment_and_currency_excluded_from_prompt(case):
    fields, link = _sam(5e10)
    case.add("ev-global-robot", fields, link.model_copy(update={"segment_id": "robot"}))
    fields, link = _sam(1_000_000_000_000)
    case.add("ev-krw", {**fields, "currency": "KRW", "unit": "KRW"}, link)
    fields, link = _sam()
    case.add("ev-us", {**fields, "geography": "US"}, link)
    result, llm = case.run(case.output())
    assert result.status == "success"
    for eid in ("ev-global-robot", "ev-krw", "ev-us"):
        assert eid not in llm.calls[0].user
    assert _payload(llm)["context"]["excluded_evidence_reasons"] == {
        "currency_mismatch": 1,
        "segment_mismatch": 2,
    }


def test_citing_excluded_segment_evidence_fails(case):
    fields, link = _sam(5e10)
    case.add("ev-global-robot", fields, link.model_copy(update={"segment_id": "robot"}))
    bad = case.output(size=["ev-global-robot"], size_rating=5)
    result, llm = case.run(bad, bad)
    assert result.status == "failure"
    assert len(llm.calls) == 2
    assert "EVIDENCE_NOT_IN_SNAPSHOT" in result.errors[0].message_redacted


def test_unlinked_and_other_company_evidence_excluded(case):
    fields, _ = _sam()
    case.add("ev-unlinked", fields, None)
    fields, link = _sam()
    case.add(
        "ev-other-co", fields, link, scope="company", candidate_id="another-company"
    )
    case.add("ev-industry-tagged", fields, link, candidate_id="another-company")
    result, llm = case.run(case.output())
    assert result.status == "success"
    for eid in ("ev-unlinked", "ev-other-co", "ev-industry-tagged"):
        assert eid not in llm.calls[0].user
    assert _payload(llm)["context"]["excluded_evidence_reasons"] == {
        "attribution_unverified": 3
    }


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        (_sam(9e8, geography="GLOBAL"), "MARKET_CONTEXT_MIXED"),
        (_sam(9e8, year=2024), "MARKET_CONTEXT_MIXED"),
        (_sam(9e8, metric="tam"), "MARKET_CONTEXT_MIXED"),
        (_sam(9e8, year=2030, basis="forecast"), "MARKET_FORECAST_AS_ACTUAL"),
        (_sam(3e9), "MARKET_CONFLICT_UNRESOLVED"),
    ],
    ids=["region", "year", "tam-sam", "forecast", "band-conflict"],
)
def test_incomparable_size_figures_rejected(case, extra, code):
    case.add("ev-extra", *extra)
    bad = case.output(size=["ev-sam", "ev-extra"])
    result, llm = case.run(bad, bad)
    assert result.status == "failure"
    assert code in result.errors[0].message_redacted
    assert code in llm.calls[1].user
    assert "가상 market.size 판단" not in llm.calls[1].user


def test_growth_period_mix_rejected(case):
    case.add("ev-cagr-b", *_cagr(19.0, start=2023, end=2028))
    bad = case.output(growth=["ev-cagr", "ev-cagr-b"])
    result, _ = case.run(bad, bad)
    assert result.status == "failure"
    assert "MARKET_CONTEXT_MIXED" in result.errors[0].message_redacted


def test_rating_must_match_rubric_band(case):
    bad = case.output(size_rating=5)
    result, _ = case.run(bad, bad)
    assert result.status == "failure"
    assert "MARKET_RATING_BAND_MISMATCH" in result.errors[0].message_redacted


def test_tam_only_is_capped(case):
    case.add("ev-tam", *_sam(5e10, metric="tam"))
    over = case.output(size=["ev-tam"], size_rating=5)
    result, _ = case.run(over, over)
    assert result.status == "failure"
    assert "MARKET_TAM_CAP_EXCEEDED_SIZE" in result.errors[0].message_redacted
    capped, _ = case.run(case.output(size=["ev-tam"], size_rating=3))
    assert capped.status == "success"


def test_growth_one_band_conflict_takes_lower(case):
    case.add("ev-cagr-b", *_cagr(26.0))
    both = ["ev-cagr", "ev-cagr-b"]
    higher = case.output(growth=both, gr=5)
    result, _ = case.run(higher, higher)
    assert result.status == "failure"
    lower, _ = case.run(case.output(growth=both, gr=4))
    assert lower.status == "success"


def test_growth_two_band_conflict_must_be_missing(case):
    case.add("ev-cagr-b", *_cagr(3.0))
    both = ["ev-cagr", "ev-cagr-b"]
    bad = case.output(growth=both, gr=2)
    result, _ = case.run(bad, bad)
    assert "MARKET_CONFLICT_UNRESOLVED" in result.errors[0].message_redacted
    missing = case.output()
    missing["criteria"][1].update(
        status="missing",
        rating=None,
        evidence_ids=both,
        missing_reason="unresolved_conflict",
    )
    ok, _ = case.run(missing)
    assert ok.status == "success"
    assert ok.evaluation.criteria[1].missing_reason == "unresolved_conflict"


def test_size_figure_cited_for_growth_rejected(case):
    fields, link = _sam()
    case.add(
        "ev-sam-both",
        {**fields, "criterion_ids": ["market.size", "market.growth"]},
        link,
    )
    bad = case.output(growth=["ev-sam-both"])
    result, _ = case.run(bad, bad)
    assert "MARKET_METRIC_MISMATCH" in result.errors[0].message_redacted


def test_size_rating_without_figure_rejected(case):
    bad = case.output(size=["ev-demand"])
    bad["criteria"][0]["evidence_ids"] = []
    result, _ = case.run(bad, bad)
    assert result.status == "failure"


def test_repair_feedback_names_criterion_not_model_text(case):
    bad = case.output(gr=5)
    _, llm = case.run(bad, bad)
    assert "MARKET_RATING_BAND_MISMATCH_GROWTH" in llm.calls[1].user
    assert "SIZE" not in llm.calls[1].user.rpartition("\n")[2]


def test_free_text_missing_reason_rejected(case):
    bad = case.output()
    bad["criteria"][2].update(
        status="missing", rating=None, evidence_ids=[], missing_reason="자료 없음"
    )
    result, llm = case.run(bad, bad)
    assert "MARKET_MISSING_REASON_INVALID_DEMAND" in result.errors[0].message_redacted
    assert "not_disclosed" in _payload(llm)["context"]["missing_reasons"]


def test_prompt_figures_carry_rubric_band_and_tam_cap(case):
    case.add("ev-tam", *_sam(5e10, metric="tam"))
    _, llm = case.run(case.output())
    figures = _payload(llm)["context"]["market_figures"]
    assert figures["ev-sam"]["rubric_band"] == 3
    assert "rubric_max_rating" not in figures["ev-sam"]
    assert figures["ev-tam"]["rubric_band"] == 5
    assert figures["ev-tam"]["rubric_max_rating"] == 3
    assert figures["ev-cagr"]["rubric_band"] == 4


def test_repair_then_success(case):
    result, llm = case.run(case.output(size_rating=5), case.output())
    assert result.status == "success"
    assert len(llm.calls) == 2


def test_missing_market_evidence_stays_missing(case):
    missing = case.output()
    for criterion in missing["criteria"]:
        criterion.update(
            status="missing",
            rating=None,
            evidence_ids=[],
            missing_reason="segment_mismatch",
        )
    result, llm = case.run(missing, links={})
    assert result.status == "success"
    assert '"evidence": []' in llm.calls[0].user
    assert all(
        criterion.status == "missing" for criterion in result.evaluation.criteria
    )


def test_llm_failure_is_not_missing(case):
    result, _ = case.run(LLMError(ErrorCode.LLM_TIMEOUT, "timeout"))
    assert result.status == "failure"
    assert result.evaluation is None
    assert result.errors[0].error_code == ErrorCode.LLM_TIMEOUT.value


def test_unknown_link_id_rejected_before_llm(case):
    with pytest.raises(ValueError, match="outside snapshot"):
        case.run(
            links={
                **case.links,
                "not-in-snapshot": MarketLink(segment_id=SEGMENT),
            }
        )


def test_numeric_evidence_without_figure_link_rejected(case):
    fields, _ = _sam()
    case.add("ev-bare-number", fields, MarketLink(segment_id=SEGMENT))
    with pytest.raises(ValueError, match="figure link"):
        case.run()


def test_unapproved_rubric_rejected(case):
    assert RUBRIC["status"] == "approved"
    with pytest.raises(ValueError, match="approved rubric"):
        case.run(rubric={**RUBRIC, "status": "proposed"})


def test_link_requires_complete_figure_context():
    with pytest.raises(ValueError):
        MarketLink(
            segment_id=SEGMENT, metric="cagr", basis="forecast", reference_year=2025
        )
    with pytest.raises(ValueError):
        MarketLink(segment_id=SEGMENT, metric="sam", basis="actual")
    with pytest.raises(ValueError):
        MarketLink(segment_id=SEGMENT, reference_year=2025)


@pytest.mark.parametrize("rubric", [{}, SOURCE_RUBRIC])
def test_no_approved_rubric_means_no_call(case, rubric):
    llm = FakeLLM([])
    with pytest.raises(ValueError):
        evaluate_market(
            case.snapshot,
            target_market=TARGET,
            market_links=case.links,
            rubric=rubric,
            llm=llm,
            policy=POLICY,
            clock=CLOCK,
            schema_version=case.snapshot.schema_version,
        )
    assert llm.calls == []


def test_non_draft_policy_rejected_before_call(case):
    llm = FakeLLM([])
    with pytest.raises(ValueError, match="draft policy"):
        evaluate_market(
            case.snapshot,
            target_market=TARGET,
            market_links=case.links,
            rubric=RUBRIC,
            llm=llm,
            policy=POLICY.model_copy(update={"status": "approved"}),
            clock=CLOCK,
            schema_version=case.snapshot.schema_version,
        )
    assert llm.calls == []
