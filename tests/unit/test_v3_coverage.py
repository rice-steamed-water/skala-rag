"""Synthetic common-fixture coverage; no live policy or factual approval."""

from decimal import Decimal
from pathlib import Path

import pytest
from tests.fixtures.loader import load_common_fixtures

from skala_rag.contracts.v3 import ApplicabilityAssessment
from skala_rag.scoring.catalog import load_policy

ROOT = Path(__file__).resolve().parents[2]


def inputs():
    catalog = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
    common = load_common_fixtures(catalog)
    return catalog, common, common.cases["eligible"]


def calculate(*, missing=(), na=(), **updates):
    from skala_rag.scoring.coverage_v3 import check_coverage_v3

    catalog, common, candidate = inputs()
    evidence = [e for e in common.evidence.values() if e.candidate_id == candidate]
    assessments = {
        cid: ApplicabilityAssessment(
            schema_version=common.schema_version,
            applicability_reason="Synthetic business model excludes this metric",
            applicability_rule_id="fixture-approved-rule",
            evidence_ids=[
                next(e.evidence_id for e in evidence if cid in e.criterion_ids)
            ],
        )
        for cid in na
    }
    options = dict(
        schema_version=common.schema_version,
        evidence_revision=1,
        execution_mode="fixture",
        policy_version="synthetic-v3",
        support_check=lambda criterion, evidence: criterion.criterion_id not in missing,
        applicability_assessments=assessments,
        applicability_check=lambda candidate, criterion, assessment, evidence: True,
        unresolved_conflict_ids=[],
    )
    options.update(updates)
    return check_coverage_v3(candidate, evidence, catalog, **options)


@pytest.mark.parametrize(
    "extra, expected",
    [
        ([], 29),
        (["traction.burn"], 30),
        (["traction.burn", "traction.concentration"], 31),
    ],
)
def test_boundary(extra, expected):
    missing = {
        "market.size",
        "market.growth",
        "technology.reliability",
        "founder.expertise",
        "founder.industry",
    } | set(extra)
    result = calculate(missing=missing)
    assert result.weighted_missing_pct == Decimal(expected)
    assert result.coverage_pct == Decimal(100) - Decimal(expected)
    assert result.research_ready is (expected < 30)


def test_na_removed_missing_retained_and_decimal_ratio():
    result = calculate(missing=["market.size"], na=["market.growth"])
    assert result.not_applicable_weight == Decimal(10)
    assert result.applicable_weight == Decimal(90)
    assert result.missing_weight == Decimal(10)
    assert result.weighted_missing_pct == Decimal(10) / Decimal(90) * Decimal(100)
    assert result.coverage_pct == Decimal(100) - result.weighted_missing_pct
    assert result.not_applicable_criterion_ids == ["market.growth"]
    assert set(result.applicability_assessments) == {"market.growth"}


def test_zero_applicable_is_candidate_scoped_error():
    from skala_rag.scoring.coverage_v3 import NoApplicableCriteria

    catalog, common, candidate = inputs()
    with pytest.raises(NoApplicableCriteria) as exc:
        calculate(na=[c.criterion_id for c in catalog.criteria])
    assert exc.value.candidate_id == candidate
    assert exc.value.policy_version == "synthetic-v3"


def test_gaps_only_missing_preserve_templates_stable_order():
    from skala_rag.contracts import ResearchGap
    from skala_rag.scoring.coverage_v3 import build_research_gaps_v3

    catalog, common, candidate = inputs()
    result = calculate(missing=["market.size", "market.demand"], na=["market.growth"])
    templates = [
        ResearchGap(
            schema_version=common.schema_version,
            gap_id=f"caller-{cid}",
            candidate_id=candidate,
            criterion_id=cid,
            missing_fields=["context"],
            reason="caller rationale",
            priority_weight=999,
            suggested_queries=["caller query"],
            attempted_retrieval_ids=["caller-retrieval"],
            status="open",
        )
        for cid in reversed(result.missing_criterion_ids)
    ]
    before = [t.model_dump() for t in templates]
    gaps = build_research_gaps_v3(
        result, catalog, templates, policy_version="synthetic-v3"
    )
    assert [g.criterion_id for g in gaps] == result.missing_criterion_ids
    assert [g.priority_weight for g in gaps] == [10, 10]
    assert [t.model_dump() for t in templates] == before
    assert all(
        g.reason == "caller rationale"
        and g.suggested_queries == ["caller query"]
        and g.attempted_retrieval_ids == ["caller-retrieval"]
        for g in gaps
    )


def test_common_fixture_all_observed():
    from skala_rag.scoring.coverage_v3 import check_coverage_v3

    catalog, common, candidate = inputs()
    result = check_coverage_v3(
        candidate,
        list(common.evidence.values()),
        catalog,
        schema_version=common.schema_version,
        evidence_revision=1,
        execution_mode="fixture",
        policy_version="synthetic-v3",
        support_check=lambda criterion, evidence: True,
        applicability_assessments={},
        applicability_check=lambda candidate, criterion, assessment, evidence: False,
        unresolved_conflict_ids=[],
    )
    assert result.applicable_weight == Decimal(100)
    assert result.missing_weight == Decimal(0)
    assert result.coverage_pct == Decimal(100)
    assert result.research_ready is True
    assert result.policy_version == "synthetic-v3"


@pytest.mark.parametrize(
    "problem",
    [
        "unknown_evidence",
        "foreign",
        "wrong_criterion",
        "rule",
        "return_type",
        "conflict",
        "no_provenance",
    ],
)
def test_invalid_na_rejected(problem):
    catalog, common, candidate = inputs()
    evidence = list(common.evidence.values())
    target = next(
        e
        for e in evidence
        if e.candidate_id == candidate and e.criterion_ids == ["market.size"]
    )
    identifier = target.evidence_id
    updates = {}
    if problem == "unknown_evidence":
        identifier = "absent"
    elif problem == "foreign":
        identifier = next(
            e.evidence_id for e in evidence if e.candidate_id != candidate
        )
    elif problem == "wrong_criterion":
        identifier = next(
            e.evidence_id for e in evidence if e.criterion_ids == ["market.growth"]
        )
    elif problem == "rule":
        updates["applicability_check"] = lambda *args: False
    elif problem == "return_type":
        updates["applicability_check"] = lambda *args: "approved"
    elif problem == "conflict":
        updates["unresolved_conflict_ids"] = [identifier]
    else:
        target.provenance = []
    assessment = ApplicabilityAssessment(
        schema_version=common.schema_version,
        applicability_reason="fixture reason",
        applicability_rule_id="fixture rule",
        evidence_ids=[identifier],
    )
    from skala_rag.scoring.coverage_v3 import check_coverage_v3

    options = dict(
        schema_version=common.schema_version,
        evidence_revision=1,
        execution_mode="fixture",
        policy_version="synthetic-v3",
        support_check=lambda *args: True,
        applicability_assessments={"market.size": assessment},
        applicability_check=lambda *args: True,
        unresolved_conflict_ids=[],
    )
    options.update(updates)
    with pytest.raises((ValueError, TypeError)):
        check_coverage_v3(candidate, evidence, catalog, **options)


def test_no_evidence_never_na_and_unknown_support_is_missing():
    from skala_rag.scoring.coverage_v3 import check_coverage_v3

    catalog, common, candidate = inputs()
    result = check_coverage_v3(
        candidate,
        [],
        catalog,
        schema_version=common.schema_version,
        evidence_revision=0,
        execution_mode="fixture",
        policy_version="synthetic-v3",
        support_check=lambda *args: True,
        applicability_assessments={},
        applicability_check=lambda *args: True,
        unresolved_conflict_ids=[],
    )
    assert result.missing_weight == Decimal(100)
    assert result.not_applicable_criterion_ids == []
    assert result.research_ready is False
    assert calculate(support_check=lambda *args: False).missing_weight == Decimal(100)


@pytest.mark.parametrize(
    "updates",
    [
        {"execution_mode": "live"},
        {"policy_version": ""},
        {"policy_version": None},
        {"unresolved_conflict_ids": ["absent"]},
        {"applicability_assessments": {"unknown": None}},
        {"support_check": lambda *args: "yes"},
    ],
)
def test_invalid_inputs_rejected(updates):
    with pytest.raises((ValueError, TypeError)):
        calculate(**updates)


def test_validator_failures_not_hidden():
    def failed(*args):
        raise RuntimeError("fixture validator failure")

    with pytest.raises(RuntimeError):
        calculate(support_check=failed)
    with pytest.raises(RuntimeError):
        calculate(na=["market.size"], applicability_check=failed)


def test_conflict_blocks_criterion():
    catalog, common, candidate = inputs()
    eid = next(
        e.evidence_id
        for e in common.evidence.values()
        if e.candidate_id == candidate and e.criterion_ids == ["market.size"]
    )
    result = calculate(unresolved_conflict_ids=[eid])
    assert result.missing_criterion_ids == ["market.size"]
    assert result.unresolved_conflicts == [eid]


@pytest.mark.parametrize(
    "problem",
    ["missing", "overlap", "unknown", "duplicate", "foreign", "queries", "policy"],
)
def test_gap_invalid_inputs_rejected(problem):
    from skala_rag.contracts import ResearchGap
    from skala_rag.scoring.coverage_v3 import build_research_gaps_v3

    catalog, common, candidate = inputs()
    result = calculate(missing=["market.size"])
    template = ResearchGap(
        schema_version=common.schema_version,
        gap_id="caller-id",
        candidate_id=candidate,
        criterion_id="market.size",
        missing_fields=["context"],
        reason="caller reason",
        priority_weight=0,
        suggested_queries=["caller query"],
        attempted_retrieval_ids=[],
        status="open",
    )
    templates = [template]
    version = "synthetic-v3"
    if problem == "missing":
        result.covered_criterion_ids.pop()
    elif problem == "overlap":
        result.not_applicable_criterion_ids.append("market.size")
    elif problem == "unknown":
        template.criterion_id = "unknown"
    elif problem == "duplicate":
        templates.append(template)
    elif problem == "foreign":
        template.candidate_id = "foreign"
    elif problem == "queries":
        template.suggested_queries = []
    else:
        version = "wrong-policy"
    with pytest.raises(ValueError):
        build_research_gaps_v3(result, catalog, templates, policy_version=version)


@pytest.mark.parametrize(
    "problem",
    [
        "duplicate_evidence",
        "unknown_criterion",
        "duplicate_criterion",
        "industry_attribution",
        "company_attribution",
        "missing_catalog",
        "duplicate_conflict",
        "schema",
    ],
)
def test_catalog_and_evidence_identity_validation(problem):
    from skala_rag.scoring.coverage_v3 import check_coverage_v3

    catalog, common, candidate = inputs()
    evidence = list(common.evidence.values())
    options = dict(
        schema_version=common.schema_version,
        evidence_revision=1,
        execution_mode="fixture",
        policy_version="synthetic-v3",
        support_check=lambda *args: True,
        applicability_assessments={},
        applicability_check=lambda *args: True,
        unresolved_conflict_ids=[],
    )
    target = evidence[0]
    if problem == "duplicate_evidence":
        evidence.append(target)
    elif problem == "unknown_criterion":
        target.criterion_ids = ["unknown"]
    elif problem == "duplicate_criterion":
        target.criterion_ids *= 2
    elif problem == "industry_attribution":
        target.scope = "industry"
    elif problem == "company_attribution":
        target.candidate_id = None
    elif problem == "missing_catalog":
        catalog = catalog.model_copy(update={"criteria": catalog.criteria[:-1]})
    elif problem == "duplicate_conflict":
        options["unresolved_conflict_ids"] = [target.evidence_id, target.evidence_id]
    else:
        options["applicability_assessments"] = {
            target.criterion_ids[0]: ApplicabilityAssessment(
                schema_version="other",
                applicability_reason="fixture",
                applicability_rule_id="fixture",
                evidence_ids=[target.evidence_id],
            )
        }
    with pytest.raises(ValueError):
        check_coverage_v3(candidate, evidence, catalog, **options)


def test_industry_support_and_same_name_isolation():
    from skala_rag.scoring.coverage_v3 import check_coverage_v3

    catalog, common, candidate = inputs()
    item = next(
        e
        for e in common.evidence.values()
        if e.candidate_id == candidate and e.criterion_ids == ["market.size"]
    ).model_copy(deep=True)
    item.scope = "industry"
    item.candidate_id = None
    evidence = [item] + [
        e
        for e in common.evidence.values()
        if e.candidate_id == common.cases["same_name"]
    ]
    result = check_coverage_v3(
        candidate,
        evidence,
        catalog,
        schema_version=common.schema_version,
        evidence_revision=1,
        execution_mode="fixture",
        policy_version="synthetic-v3",
        support_check=lambda *args: True,
        applicability_assessments={},
        applicability_check=lambda *args: True,
        unresolved_conflict_ids=[],
    )
    assert result.covered_criterion_ids == ["market.size"]
    assert result.missing_weight == Decimal(90)


@pytest.mark.parametrize("use_for_applicability", [False, True])
def test_evidence_schema_must_match_coverage_schema(use_for_applicability):
    from skala_rag.scoring.coverage_v3 import check_coverage_v3

    catalog, common, candidate = inputs()
    item = next(
        e
        for e in common.evidence.values()
        if e.candidate_id == candidate and e.criterion_ids == ["market.size"]
    ).model_copy(deep=True)
    item.schema_version = "other"
    assessments = (
        {
            "market.size": ApplicabilityAssessment(
                schema_version=common.schema_version,
                applicability_reason="synthetic reason",
                applicability_rule_id="fixture-approved-rule",
                evidence_ids=[item.evidence_id],
            )
        }
        if use_for_applicability
        else {}
    )
    with pytest.raises(ValueError, match="Evidence schema_version mismatch"):
        check_coverage_v3(
            candidate,
            [item],
            catalog,
            schema_version=common.schema_version,
            evidence_revision=1,
            execution_mode="fixture",
            policy_version="synthetic-v3",
            support_check=lambda *args: True,
            applicability_assessments=assessments,
            applicability_check=lambda *args: True,
            unresolved_conflict_ids=[],
        )


def test_gap_rejects_incomplete_catalog_even_matching_partition():
    from skala_rag.scoring.coverage_v3 import build_research_gaps_v3

    catalog, common, candidate = inputs()
    result = calculate()
    result.covered_criterion_ids.pop()
    catalog = catalog.model_copy(update={"criteria": catalog.criteria[:-1]})
    with pytest.raises(ValueError):
        build_research_gaps_v3(result, catalog, [], policy_version="synthetic-v3")


@pytest.mark.parametrize(
    "missing, expected, ready",
    [
        (["founder.expertise", "founder.industry", "technology.maturity"], 28, True),
        (["technology.maturity", "technology.reliability"], 30, False),
        (
            ["founder.execution", "technology.maturity", "technology.reliability"],
            32,
            False,
        ),
    ],
)
def test_normalized_boundary_after_na(missing, expected, ready):
    applicable = {
        "founder.expertise",
        "founder.industry",
        "founder.execution",
        "market.size",
        "market.growth",
        "market.demand",
        "technology.maturity",
        "technology.reliability",
    }
    catalog, common, candidate = inputs()
    result = calculate(
        missing=missing,
        na=[
            c.criterion_id for c in catalog.criteria if c.criterion_id not in applicable
        ],
    )
    assert result.applicable_weight == Decimal(50)
    assert result.weighted_missing_pct == Decimal(expected)
    assert result.research_ready is ready
