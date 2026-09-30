"""가상 근거로 Coverage 경계·격리·gap 정렬을 검증한다."""

import json
from pathlib import Path

import pytest

from skala_rag.contracts import Evidence, ResearchGap
from skala_rag.scoring.catalog import load_policy
from skala_rag.scoring.coverage import build_research_gaps, check_coverage

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def policy():
    return load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")


@pytest.fixture
def evidence_factory():
    payload = json.loads((ROOT / "tests/fixtures/contracts.json").read_text())[
        "Evidence"
    ]

    def create(
        criterion_ids, identifier="ev-fixture", candidate="co-fixture", **updates
    ):
        data = dict(
            payload,
            criterion_ids=criterion_ids,
            evidence_id=identifier,
            candidate_id=candidate,
            locator="fixture://coverage#claim=1",
            claim="가상 fixture 주장. 실제 기업 자료 아님.",
            **updates,
        )
        return Evidence.model_validate(data, context={"execution_mode": "fixture"})

    return create


def calculate(
    policy, evidence, support_check=lambda criterion, matches: True, **updates
):
    options = dict(
        evidence_revision=1,
        schema_version="synthetic-1",
        execution_mode="fixture",
        support_check=support_check,
        unresolved_conflict_ids=[],
    )
    options.update(updates)
    return check_coverage("co-fixture", evidence, policy, **options)


@pytest.mark.parametrize(
    "extra,expected",
    [
        ([], 29),
        (["traction.burn"], 30),
        (["traction.burn", "traction.concentration"], 31),
    ],
)
def test_missing_boundary(policy, evidence_factory, extra, expected):
    missing = {
        "market.size",
        "market.growth",
        "technology.reliability",
        "founder.expertise",
        "founder.industry",
    } | set(extra)
    known = [c.criterion_id for c in policy.criteria if c.criterion_id not in missing]
    result = calculate(policy, [evidence_factory(known)])
    assert result.missing_weight == expected
    assert result.coverage_pct == 100 - expected
    assert result.research_ready is (expected < 30)
    assert set(result.covered_criterion_ids) == set(known)
    assert set(result.missing_criterion_ids) == missing
    assert result.policy_version == policy.policy_version
    assert result.evidence_revision == 1


def test_no_evidence_and_all_evidence(policy, evidence_factory):
    assert calculate(policy, []).missing_weight == 100
    ids = [c.criterion_id for c in policy.criteria]
    result = calculate(policy, [evidence_factory(ids)])
    assert result.missing_weight == 0
    assert result.coverage_pct == 100
    assert result.research_ready is True


def test_support_requires_explicit_context_check(policy, evidence_factory):
    item = evidence_factory(["technology.integration"])
    result = calculate(policy, [item], support_check=lambda c, matches: False)
    assert result.missing_weight == 100
    assert item.model_dump()["criterion_ids"] == ["technology.integration"]


def test_foreign_company_excluded_and_industry_explicit(policy, evidence_factory):
    foreign = evidence_factory(["market.size"], candidate="co-other")
    assert calculate(policy, [foreign]).missing_weight == 100
    industry = evidence_factory(["market.size"], candidate=None, scope="industry")
    assert calculate(policy, [industry]).missing_weight == 90


def test_unresolved_conflict_blocks_shared_criterion(policy, evidence_factory):
    item = evidence_factory(["market.size"])
    independent = evidence_factory(["market.size"], identifier="ev-second")
    result = calculate(
        policy, [item, independent], unresolved_conflict_ids=[item.evidence_id]
    )
    assert result.missing_weight == 100
    assert result.unresolved_conflicts == [item.evidence_id]


@pytest.mark.parametrize("mode", ["live", "unknown"])
def test_fixture_only(policy, mode):
    with pytest.raises(ValueError, match="fixture"):
        calculate(policy, [], execution_mode=mode)


def test_unknown_criterion_and_duplicate_evidence_rejected(policy, evidence_factory):
    with pytest.raises(ValueError, match="catalog"):
        calculate(policy, [evidence_factory(["unknown"])])
    item = evidence_factory(["market.size"])
    with pytest.raises(ValueError, match="중복"):
        calculate(policy, [item, item])
    with pytest.raises(ValueError, match="conflict"):
        calculate(policy, [], unresolved_conflict_ids=["absent"])


def test_checker_failure_and_wrong_type_not_converted_to_missing(
    policy, evidence_factory
):
    item = evidence_factory(["market.size"])
    with pytest.raises(TypeError):
        calculate(policy, [item], support_check=lambda c, e: "yes")

    def failed(c, e):
        raise RuntimeError("가상 검사 실패")

    with pytest.raises(RuntimeError):
        calculate(policy, [item], support_check=failed)


def make_templates(coverage):
    return [
        ResearchGap(
            schema_version="synthetic-1",
            gap_id=f"gap-fixture-{i}",
            candidate_id=coverage.candidate_id,
            criterion_id=criterion,
            missing_fields=["직접 근거와 맥락"],
            reason="가상 결측",
            priority_weight=999,
            suggested_queries=[f"가상 기업 {criterion}"],
            attempted_retrieval_ids=[],
            status="open",
        )
        for i, criterion in enumerate(coverage.missing_criterion_ids)
    ]


def test_gap_priority_uses_catalog_and_does_not_mutate_templates(policy):
    coverage = calculate(policy, [])
    templates = make_templates(coverage)
    before = [item.model_dump() for item in templates]
    gaps = build_research_gaps(coverage, policy, list(reversed(templates)))
    assert len(gaps) == 23
    assert gaps[0].criterion_id == "market.size"
    assert [g.priority_weight for g in gaps] == sorted(
        [c.weight for c in policy.criteria], reverse=True
    )
    assert [t.model_dump() for t in templates] == before
    assert all(g.suggested_queries and g.status == "open" for g in gaps)
    assert (
        build_research_gaps(
            calculate(policy, [], support_check=lambda c, e: True), policy, templates
        )
        == gaps
    )


@pytest.mark.parametrize(
    "problem", ["omitted", "duplicate", "foreign", "queries", "version"]
)
def test_invalid_gap_templates_rejected(policy, problem):
    coverage = calculate(policy, [])
    templates = make_templates(coverage)
    if problem == "omitted":
        templates.pop()
    elif problem == "duplicate":
        templates[-1] = templates[0]
    elif problem == "foreign":
        templates[0].candidate_id = "co-other"
    elif problem == "queries":
        templates[0].suggested_queries = []
    else:
        coverage.policy_version = "other"
    with pytest.raises(ValueError):
        build_research_gaps(coverage, policy, templates)
