"""Offline end-to-end synthetic all-candidate v3 controller through #20/#24."""

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from skala_rag.contracts.v3 import (
    BRANCH_DIMENSIONS,
    ApplicabilityAssessment,
    EvaluationBranchResult,
)
from skala_rag.graph.candidates_v3 import CandidateStagesV3, run_candidates_v3
from skala_rag.scoring.catalog import load_policy
from skala_rag.scoring.v3_policy import load_v3_policy


def scenario(
    statuses=("eligible", "eligible"),
    ratings=(5, 4),
    broken=(),
    stale=(),
    na=(),
    discovery_failure=False,
    freeze_mutation=None,
    eligibility_schema=None,
    catalog_mutation=None,
    schema_version=None,
):
    policy = load_v3_policy("configs/scoring.v3.json", execution_mode="fixture")
    catalog = load_policy("configs/scoring.draft.json", execution_mode="fixture")
    if catalog_mutation:
        catalog = catalog_mutation(catalog)
    raw = json.loads((Path(__file__).parents[1] / "fixtures/common.json").read_text())
    template = deepcopy(next(iter(raw["snapshots"].values())))
    candidate_template = next(iter(raw["candidates"].values()))
    ids = [f"company-{i}" for i in range(len(statuses))]
    candidates = [
        {**candidate_template, "candidate_id": cid, "discovery_source_ids": []}
        for cid in ids
    ]
    calls = []

    def freeze(candidate, eligibility, coverage, *, tamper=True):
        snap = deepcopy(template)
        snap.update(
            schema_version=schema_version or template["schema_version"],
            run_id="run",
            candidate_id=candidate["candidate_id"],
            policy_version=policy.policy_version,
            evaluation_round=1,
            evidence_revision=coverage.evidence_revision
            + (candidate["candidate_id"] in stale),
            snapshot_id=f"snapshot-{candidate['candidate_id']}",
        )
        snap["evidence_ids"] = list(snap["evidence"])
        for evidence in snap["evidence"].values():
            evidence["candidate_id"] = candidate["candidate_id"]
            evidence["criterion_ids"] = [c.criterion_id for c in policy.criteria]
            evidence["locator"] = snap["chunks"]["chunk-fixture-eligible"]["locator"]
        for record in snap["retrieval_records"].values():
            record["run_id"] = "run"
            record["candidate_id"] = candidate["candidate_id"]
        for chunk in snap["chunks"].values():
            chunk["candidate_ids"] = [candidate["candidate_id"]]
            chunk["text"] = "\n".join(e["excerpt"] for e in snap["evidence"].values())
        if tamper and freeze_mutation and candidate["candidate_id"] == "company-0":
            freeze_mutation(snap)
        return snap

    def evaluate(branch, snap):
        cid = snap.candidate_id
        calls.append((cid, branch))
        if cid in broken and branch == "business_deal":
            raise RuntimeError("secret synthetic provider error")
        index = ids.index(cid)
        identity = {
            k: getattr(snap, k)
            for k in (
                "schema_version",
                "run_id",
                "candidate_id",
                "evaluation_round",
                "snapshot_id",
                "evidence_revision",
                "policy_version",
            )
        }
        evaluations = {}
        for dimension in BRANCH_DIMENSIONS[branch]:
            evaluations[dimension] = dict(
                **identity,
                dimension=dimension,
                rubric_version="synthetic",
                criteria=[
                    dict(
                        schema_version=snap.schema_version,
                        criterion_id=c.criterion_id,
                        status="not_applicable" if c.criterion_id in na else "observed",
                        rating=None if c.criterion_id in na else ratings[index],
                        evidence_ids=[]
                        if c.criterion_id in na
                        else [next(iter(snap.evidence))],
                        rationale="synthetic",
                        applicability_reason="synthetic applicability"
                        if c.criterion_id in na
                        else None,
                        applicability_rule_id="approved-external-rule"
                        if c.criterion_id in na
                        else None,
                        applicability_evidence_ids=[next(iter(snap.evidence))]
                        if c.criterion_id in na
                        else None,
                    )
                    for c in policy.criteria
                    if c.dimension == dimension
                ],
                research_gaps=[],
                caveats=[],
            )
        return EvaluationBranchResult.model_validate(
            dict(
                **identity,
                branch_id=branch,
                status="success",
                evaluations=evaluations,
                errors=[],
            )
        )

    stages = CandidateStagesV3(
        discover=lambda: (
            (_ for _ in ()).throw(RuntimeError("source failed"))
            if discovery_failure
            else deepcopy(candidates)
        ),
        normalize=lambda items: items,
        research=lambda c: c["candidate_id"],
        eligibility=lambda c, research: dict(
            schema_version=eligibility_schema
            or schema_version
            or template["schema_version"],
            eligibility_result_id=f"elig-{c['candidate_id']}",
            run_id="run",
            candidate_id=c["candidate_id"],
            evidence_revision=1,
            policy_version=policy.policy_version,
            as_of=template["as_of"],
            status=statuses[ids.index(c["candidate_id"])],
            checks={},
            reason_codes=["synthetic"],
            evidence_ids=[],
        ),
        collect=lambda c, research: list(
            freeze(c, None, type("C", (), {"evidence_revision": 1})(), tamper=False)[
                "evidence"
            ].values()
        ),
        freeze=freeze,
    )
    result = run_candidates_v3(
        stages,
        {b: lambda snap, b=b: evaluate(b, snap) for b in BRANCH_DIMENSIONS},
        policy=policy,
        catalog=catalog,
        catalog_policy_version="main-draft-0.1.0",
        run_id="run",
        schema_version=schema_version or template["schema_version"],
        support_check=lambda criterion, evidence: bool(evidence),
        applicability_assessments=lambda cid: {
            criterion_id: ApplicabilityAssessment(
                schema_version=schema_version or template["schema_version"],
                applicability_reason="synthetic applicability",
                applicability_rule_id="approved-external-rule",
                evidence_ids=[next(iter(template["evidence"]))],
            )
            for criterion_id in na
        },
        applicability_check=lambda cid, criterion, assessment, evidence: (
            assessment.applicability_rule_id == "approved-external-rule"
        ),
        applicability_verifier=lambda assessment, snap: (
            assessment.applicability_rule_id == "approved-external-rule"
        ),
        industry_evidence_dimensions=set(),
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    return result, calls


def test_all_candidates_evaluated_priority_not_first_stop():
    result, calls = scenario()
    assert result.selection.selected_candidate_id == "company-0"
    assert len(calls) == 10
    assert result.candidate_index == 2
    assert len(result.outcomes) == 2
    assert result.status == "ready_for_v3_reporting"
    assert result.baseline_report_input is None


def test_failure_then_success_advances_once_and_no_partial_promotion():
    result, calls = scenario(broken={"company-0"})
    assert result.candidate_index == 2
    assert result.outcomes["company-0"].status == "failed"
    assert "company-0" not in result.scores
    assert result.selection.selected_candidate_id == "company-1"
    assert len(calls) == 10


def test_unknown_ineligible_and_no_candidates_distinct():
    result, calls = scenario(("unknown", "ineligible"))
    assert not calls
    assert result.selection.selected_candidate_id is None
    assert result.status == "no_eligible_candidates"
    empty, _ = scenario(())
    assert empty.status == "no_candidates"


def test_all_failed_distinct_from_no_candidates():
    result, _ = scenario(broken={"company-0", "company-1"})
    assert result.status == "all_eligible_failed"
    assert result.selection.selected_candidate_id is None


def test_all_watchlist_has_comparison_not_selection():
    result, _ = scenario(ratings=(3, 3))
    assert result.selection.reason == "NO_RECOMMENDATION"
    assert result.status == "ready_for_v3_reporting"
    assert len(result.selection.compared_score_summary_ids) == 2


def test_stale_generation_rejects_one_candidate_and_advances_once():
    result, calls = scenario(stale={"company-0"})
    assert result.candidate_index == 2
    assert result.outcomes["company-0"].status == "failed"
    assert result.selection.selected_candidate_id == "company-1"
    assert len(calls) == 5


def test_zero_dimension_denominator_archives_not_recommends():
    result, calls = scenario(na={"market.size", "market.growth", "market.demand"})
    assert len(calls) == 10
    assert result.status == "all_eligible_failed"
    assert not result.scores
    assert result.candidate_index == 2
    assert {e.error_code for e in result.errors} == {"ZERO_APPLICABLE_DENOMINATOR"}
    assert all(
        "Zero applicable denominator: market" in e.message_redacted
        for e in result.errors
    )


def test_na_adjusts_denominator_after_external_verification():
    result, _ = scenario(
        na={"traction.revenue_growth", "traction.gross_margin", "traction.rule_of_40"}
    )
    assert result.scores["company-0"].applicable_weight == 94
    assert result.scores["company-0"].not_applicable_weight == 6
    assert result.selection.selected_candidate_id == "company-0"


def test_discovery_failure_not_empty_discovery():
    result, calls = scenario(discovery_failure=True)
    assert not calls
    assert result.status == "discovery_failed"
    assert result.errors[0].candidate_id is None


@pytest.mark.parametrize("mutation", ["claim", "source", "criterion", "inject"])
def test_freeze_cannot_change_or_inject_collected_evidence(mutation):
    def tamper(snapshot):
        evidence = next(iter(snapshot["evidence"].values()))
        if mutation == "inject":
            injected = deepcopy(evidence)
            injected["evidence_id"] = "injected-secret-id"
            snapshot["evidence"]["injected-secret-id"] = injected
            snapshot["evidence_ids"].append("injected-secret-id")
        elif mutation == "criterion":
            evidence["criterion_ids"] = ["market.size"]
        elif mutation == "claim":
            evidence["claim"] = "secret synthetic claim"
        else:
            evidence["source_id"] = "secret synthetic source"

    result, calls = scenario(freeze_mutation=tamper)
    assert result.outcomes["company-0"].status == "failed"
    assert "company-0" not in result.scores
    assert result.selection.selected_candidate_id == "company-1"
    assert len(calls) == 5
    assert result.errors[0].error_code == "SNAPSHOT_INVALID"
    assert "secret" not in result.errors[0].message_redacted


def test_eligibility_schema_mismatch_archives_and_advances():
    result, calls = scenario(eligibility_schema="different-schema")
    assert not calls
    assert not result.scores
    assert all(o.status == "failed" for o in result.outcomes.values())
    assert all(e.node == "eligibility" for e in result.errors)


def test_selection_schema_is_caller_schema_on_success_and_empty():
    result, _ = scenario(schema_version="caller-schema")
    assert result.schema_version == result.selection.schema_version == "caller-schema"
    empty, _ = scenario((), schema_version="caller-schema")
    assert empty.selection.schema_version == "caller-schema"


def test_catalog_dimension_identity_mismatch_rejected():
    def mutate(catalog):
        criteria = list(catalog.criteria)
        criteria[0] = criteria[0].model_copy(update={"dimension": "market"})
        return catalog.model_copy(update={"criteria": tuple(criteria)})

    with pytest.raises(ValueError, match="coverage catalog differs"):
        scenario(catalog_mutation=mutate)


def test_catalog_policy_identity_mismatch_rejected():
    with pytest.raises(ValueError, match="coverage catalog differs"):
        scenario(
            catalog_mutation=lambda c: c.model_copy(update={"policy_version": "other"})
        )


def test_catalog_dimension_weights_mismatch_rejected():
    with pytest.raises(ValueError, match="coverage catalog differs"):
        scenario(
            catalog_mutation=lambda c: c.model_copy(update={"dimension_weights": {}})
        )


def test_freeze_may_omit_collected_evidence():
    def omit(snapshot):
        unused = snapshot["evidence_ids"].pop()
        del snapshot["evidence"][unused]

    result, calls = scenario(freeze_mutation=omit)
    assert result.outcomes["company-0"].status == "recommend"
    assert result.selection.selected_candidate_id == "company-0"
    assert len(calls) == 10


@pytest.mark.parametrize("mutation", ["source", "record", "chunk", "excerpt", "corpus"])
def test_freeze_rejects_broken_reference_closure(mutation):
    def tamper(snapshot):
        if mutation == "source":
            snapshot["sources"].clear()
        elif mutation == "record":
            snapshot["retrieval_records"].clear()
        elif mutation == "chunk":
            snapshot["chunks"].clear()
        elif mutation == "excerpt":
            next(iter(snapshot["chunks"].values()))["text"] = "unrelated"
        else:
            next(iter(snapshot["chunks"].values()))["corpus_version"] = "wrong"

    result, calls = scenario(freeze_mutation=tamper)
    assert result.outcomes["company-0"].status == "failed"
    assert "company-0" not in result.scores
    assert result.selection.selected_candidate_id == "company-1"
    assert len(calls) == 5
    assert result.errors[0].error_code == "SNAPSHOT_INVALID"
