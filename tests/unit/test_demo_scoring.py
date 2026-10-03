"""Offline scoring checks; synthetic observations are not live evaluations."""

from pathlib import Path

import pytest

from skala_rag import demo_scoring as scoring

ROOT = Path(__file__).resolve().parents[2]


def missing_review(role, rubric):
    return {
        "criteria": [
            {
                "criterion_id": cid,
                "status": "missing",
                "rating": None,
                "rationale": "No criterion-specific evidence in supplied excerpts.",
                "missing_reason": "not_disclosed",
                "supports": [],
            }
            for cid in rubric["roles"][role]
        ]
    }


def test_all_missing_roles_are_blank_not_zero():
    rubric = scoring.load_demo_rubric(ROOT)
    reviews = {role: missing_review(role, rubric) for role in rubric["roles"]}
    scores = scoring.score_reviews(reviews, evidence={}, rubric=rubric)
    assert set(scores) == {"founder", "market", "technology", "moat", "business_deal"}
    assert all(row["score"] is None for row in scores.values())
    assert all(row["evidence_ids"] == [] for row in scores.values())
    assert all(row["scored_count"] == 0 for row in scores.values())
    assert scores["business_deal"]["missing_count"] == 9
    assert scores["business_deal"]["applicable_weight"] == "20"
    assert scores["technology"]["missing_weight"] == "25"
    assert all(row["method"] for row in scores.values())


def observe(review, cid, rubric, *, rating=3, evidence_id="ev-test"):
    assessment = next(c for c in review["criteria"] if c["criterion_id"] == cid)
    assessment.update(
        status="observed",
        rating=rating,
        missing_reason=None,
        rationale="Synthetic direct observation corresponds to the rubric anchor.",
        supports=[
            {
                "criterion_id": cid,
                "evidence_id": evidence_id,
                "quote": "Synthetic direct observation.",
                "requirement": requirement,
            }
            for requirement in rubric["criteria"][cid]["minimum_evidence"]
        ],
    )


def evidence_for(*criteria):
    return {
        "ev-test": {
            "evidence_id": "ev-test",
            "candidate_id": "co-physical-intelligence",
            "scope": "company",
            "criterion_ids": list(criteria),
            "excerpt": "Synthetic direct observation.",
            "conflicts_with": [],
            "source_id": "src-test",
            "evidence_kind": "reported",
        }
    }


def test_partial_role_retains_missing_weights_and_combines_business_deal():
    rubric = scoring.load_demo_rubric(ROOT)
    reviews = {role: missing_review(role, rubric) for role in rubric["roles"]}
    observe(reviews["technology"], "technology.maturity", rubric, rating=3)
    observe(reviews["business_deal"], "traction.revenue_growth", rubric, rating=4)
    observe(reviews["business_deal"], "deal_terms.stage", rubric, rating=3)
    evidence = evidence_for(
        "technology.maturity", "traction.revenue_growth", "deal_terms.stage"
    )
    scores = scoring.score_reviews(reviews, evidence=evidence, rubric=rubric)
    assert scores["technology"]["score"] == "24"
    assert scores["technology"]["scored_count"] == 1
    assert scores["technology"]["missing_count"] == 3
    assert scores["technology"]["missing_weight"] == "15"
    assert scores["technology"]["evidence_ids"] == ["ev-test"]
    assert scores["technology"]["criteria"]["technology.maturity"]["points"] == "6"
    assert scores["technology"]["criteria"]["technology.integration"]["points"] is None
    assert scores["business_deal"]["score"] == "18"
    assert scores["business_deal"]["missing_weight"] == "15"
    assert scores["founder"]["score"] is None


@pytest.mark.parametrize(
    "broken",
    [
        "absent_id",
        "empty_support",
        "wrong_criterion",
        "wrong_quote",
        "wrong_company",
        "unmet_requirement",
        "unknown_criterion",
        "duplicate_criterion",
        "omitted_criterion",
        "bool_rating",
        "zero_rating",
        "missing_with_rating",
        "na",
        "conflict",
        "industry",
        "blank_rationale",
        "wrong_map_id",
    ],
)
def test_invalid_criterion_observations_fail_closed(broken):
    rubric = scoring.load_demo_rubric(ROOT)
    review = missing_review("technology", rubric)
    observe(review, "technology.maturity", rubric)
    evidence = evidence_for("technology.maturity")
    a = review["criteria"][0]
    if broken == "absent_id":
        evidence = {}
    elif broken == "empty_support":
        a["supports"] = []
    elif broken == "wrong_criterion":
        a["supports"][0]["criterion_id"] = "technology.integration"
    elif broken == "wrong_quote":
        a["supports"][0]["quote"] = "Not in source"
    elif broken == "wrong_company":
        evidence["ev-test"]["candidate_id"] = "other"
    elif broken == "unmet_requirement":
        a["supports"][0]["requirement"] = "invented requirement"
    elif broken == "unknown_criterion":
        a["criterion_id"] = "technology.novelty"
    elif broken == "duplicate_criterion":
        review["criteria"].append(a.copy())
    elif broken == "omitted_criterion":
        review["criteria"].pop()
    elif broken == "bool_rating":
        a["rating"] = True
    elif broken == "zero_rating":
        a["rating"] = 0
    elif broken == "missing_with_rating":
        a["status"] = "missing"
    elif broken == "na":
        a.update(status="not_applicable", rating=None)
    elif broken == "conflict":
        evidence["ev-test"]["conflicts_with"] = ["ev-other"]
    elif broken == "industry":
        evidence["ev-test"].update(scope="industry", candidate_id=None)
    elif broken == "blank_rationale":
        a["rationale"] = " "
    elif broken == "wrong_map_id":
        evidence["ev-test"]["evidence_id"] = "another-id"
    with pytest.raises(ValueError):
        scoring.score_reviews({"technology": review}, evidence=evidence, rubric=rubric)


@pytest.mark.parametrize(
    "role,cid", [("founder", "founder.expertise"), ("market", "market.demand")]
)
def test_technology_only_evidence_cannot_support_founder_or_market(role, cid):
    rubric = scoring.load_demo_rubric(ROOT)
    review = missing_review(role, rubric)
    observe(review, cid, rubric)
    with pytest.raises(ValueError, match="REVIEW_EVIDENCE_INVALID"):
        scoring.score_reviews(
            {role: review}, evidence=evidence_for("technology.maturity"), rubric=rubric
        )


def test_self_reported_core_claim_cannot_receive_five():
    rubric = scoring.load_demo_rubric(ROOT)
    review = missing_review("technology", rubric)
    observe(review, "technology.maturity", rubric, rating=5)
    with pytest.raises(ValueError, match="REVIEW_RATING_INVALID"):
        scoring.score_reviews(
            {"technology": review},
            evidence=evidence_for("technology.maturity"),
            rubric=rubric,
        )


def test_scored_context_exposes_trace_separately_from_investment_decisions(tmp_path):
    from tests.unit.test_local_demo import retrieval_fixture

    from skala_rag.demo_context import build_research_context, research_material

    bundle, records = retrieval_fixture(tmp_path)
    material = research_material(
        root=tmp_path, bundle=bundle, records=records, run_id="test-run"
    )
    rubric = scoring.load_demo_rubric(ROOT)
    review = missing_review("technology", rubric)
    eid = next(iter(material["evidence"]))
    observe(review, "technology.maturity", rubric, evidence_id=eid)
    review["criteria"][0]["supports"][0]["quote"] = bundle.chunks[0].text
    review.update(observations=[], interpretations=[], missing=[])
    context = build_research_context(
        run_id="test-run",
        material=material,
        reviews={"technology": review},
        rubric=rubric,
    ).snapshot()
    assert context["role_scores"]["technology"]["score"] == "24"
    assert context["role_scores"]["founder"]["score"] is None
    assert context["role_scores"]["technology"]["evidence_ids"] == [eid]
    assert context["scoring"]["rubric"]["artifacts"] == rubric["artifacts"]
    assert context["scores"] == context["decisions"] == context["outcomes"] == {}
    assert context["policy_version"] == "unscored-research-only-1"
    assert context["eligibility_checked"] is False
    assert context["publication_allowed"] is False
    assert context["live_reviews"]["technology"] == review


@pytest.mark.parametrize(
    "rating,expected", [(1, "20"), (2, "40"), (3, "60"), (4, "80")]
)
def test_each_fully_observed_role_is_normalized_to_one_hundred(rating, expected):
    rubric = scoring.load_demo_rubric(ROOT)
    reviews = {role: missing_review(role, rubric) for role in rubric["roles"]}
    for role, ids in rubric["roles"].items():
        for cid in ids:
            observe(reviews[role], cid, rubric, rating=rating)
    evidence = evidence_for(*rubric["criteria"])
    scores = scoring.score_reviews(reviews, evidence=evidence, rubric=rubric)
    assert all(row["score"] == expected for row in scores.values())
    assert all(row["missing_count"] == 0 for row in scores.values())
    # Duplicate observations/sources/confidence cannot increase a rating score.
    evidence["ev-test"]["confidence"] = "high"
    evidence["unused"] = {**evidence["ev-test"], "evidence_id": "unused"}
    assert scoring.score_reviews(reviews, evidence=evidence, rubric=rubric) == scores


def test_unknown_role_is_not_silently_discarded():
    with pytest.raises(ValueError, match="REVIEW_CRITERIA_INVALID"):
        scoring.score_reviews(
            {"unknown": {"criteria": []}},
            evidence={},
            rubric=scoring.load_demo_rubric(ROOT),
        )


@pytest.mark.parametrize("cid", ["traction.burn", "traction.runway"])
def test_finance_alternative_minimum_evidence_does_not_require_both(cid):
    rubric = scoring.load_demo_rubric(ROOT)
    review = missing_review("business_deal", rubric)
    observe(review, cid, rubric, rating=5)
    a = next(a for a in review["criteria"] if a["criterion_id"] == cid)
    a["supports"] = [a["supports"][1]]
    result = scoring.score_reviews(
        {"business_deal": review}, evidence=evidence_for(cid), rubric=rubric
    )
    assert result["business_deal"]["scored_count"] == 1
