"""Offline controller loop; the evaluator fanout remains the real LangGraph."""

from copy import deepcopy
from dataclasses import replace

import pytest
from tests.integration.test_v3_candidates import scenario

import skala_rag.graph.candidates_v3 as controller


def test_empty_responses_charge_two_before_callback_then_evaluate_once():
    assert hasattr(controller, "ResearchResponseV3"), "explicit response API required"
    requests = []
    trace = []

    def retry(request):
        requests.append(request)
        assert request.attempt in (1, 2)
        assert request.remaining_requests == 2 - request.attempt
        assert request.coverage.missing_criterion_ids
        return controller.ResearchResponseV3(
            request.candidate["candidate_id"], request.coverage.evidence_revision, ()
        )

    result, calls = scenario(
        run_options={"support_check": lambda c, e: False, "trace_events": trace},
        stages_transform=lambda stages: replace(stages, additional_research=retry),
    )
    assert [(r.candidate["candidate_id"], r.attempt) for r in requests] == [
        ("company-0", 1),
        ("company-0", 2),
        ("company-1", 1),
        ("company-1", 2),
    ]
    assert len(calls) == 10
    assert result.candidate_index == 2
    assert result.research_retry_count == {"company-0": 2, "company-1": 2}
    assert result.research_stop_reasons == {
        "company-0": "exhausted",
        "company-1": "exhausted",
    }
    assert not result.errors
    for cid in result.outcomes:
        steps = [e["step"] for e in trace if e["candidate_id"] == cid]
        assert steps.count("coverage") == 3
        assert steps.count("additional_research") == 2
        assert steps.index("research_exhausted") < steps.index("evaluation_join")
        assert steps.index("research_exhausted") < steps.index("freeze")
        assert steps.index("freeze") < steps.index("evaluation_join")
        assert steps[-1] == "advance"


@pytest.mark.parametrize("ready_on", [1, 2])
def test_new_evidence_recomputed_and_frozen_at_new_revision(ready_on):
    requests = []
    frozen = []

    def retry(request):
        requests.append(request)
        item = request.evidence[0].model_dump(mode="json")
        item["evidence_id"] = (
            f"extra-{request.candidate['candidate_id']}-{request.attempt}"
        )
        item["claim"] = "ready" if request.attempt == ready_on else "not ready"
        return controller.ResearchResponseV3(
            request.candidate["candidate_id"],
            request.coverage.evidence_revision,
            [item],
        )

    def transform(stages):
        def freeze(candidate, eligible, coverage, evidence):
            frozen.append(
                (
                    candidate["candidate_id"],
                    eligible.evidence_revision,
                    deepcopy(coverage),
                )
            )
            snap = stages.freeze(candidate, eligible, coverage)
            snap["evidence"] = {
                e.evidence_id: e.model_dump(mode="json") for e in evidence
            }
            snap["evidence_ids"] = list(snap["evidence"])
            for record in snap["retrieval_records"].values():
                record["evidence_ids"] = list(snap["evidence"])
            for chunk in snap["chunks"].values():
                chunk["text"] = "\n".join(e.excerpt for e in evidence)
            return snap

        return replace(stages, additional_research=retry, freeze_with_evidence=freeze)

    result, calls = scenario(
        stages_transform=transform,
        run_options={"support_check": lambda c, e: any(x.claim == "ready" for x in e)},
    )
    assert len(requests) == ready_on * 2
    assert len(calls) == 10
    assert result.research_retry_count == {"company-0": ready_on, "company-1": ready_on}
    assert all(x[1] == ready_on + 1 and x[2].research_ready for x in frozen)
    assert all(s.evidence_revision == ready_on + 1 for s in result.scores.values())
    assert all(reason == "ready" for reason in result.research_stop_reasons.values())


@pytest.mark.parametrize("recover", [True, False])
def test_recoverable_failure_is_saved_and_only_recovered_response_can_evaluate(recover):
    assert hasattr(controller, "RecoverableResearchFailure")
    requests = []

    def retry(request):
        requests.append(request)
        if request.attempt == 1 or not recover:
            raise controller.RecoverableResearchFailure("secret provider payload")
        return controller.ResearchResponseV3(
            request.candidate["candidate_id"], request.coverage.evidence_revision, ()
        )

    result, calls = scenario(
        stages_transform=lambda stages: replace(stages, additional_research=retry),
        run_options={"support_check": lambda c, e: False},
    )
    assert len(requests) == 4
    assert len(calls) == (10 if recover else 0)
    assert result.candidate_index == 2
    assert not any("secret" in e.message_redacted for e in result.errors)
    assert len({e.error_id for e in result.errors}) == len(result.errors)
    failures = [
        e for e in result.errors if e.error_code == "RESEARCH_RECOVERABLE_FAILURE"
    ]
    assert len(failures) == (2 if recover else 4)
    assert all(e.retryable for e in failures)
    if not recover:
        assert not result.scores and not result.decisions
        assert all(o.status == "failed" for o in result.outcomes.values())
        assert "RESEARCH_FAILED_EXHAUSTED" in {e.error_code for e in result.errors}


@pytest.mark.parametrize("exception", [RuntimeError, TimeoutError])
def test_terminal_or_budget_failure_never_becomes_missing(exception):
    requests = []

    def retry(request):
        requests.append(request)
        raise exception("secret provider budget failure")

    result, calls = scenario(
        stages_transform=lambda stages: replace(stages, additional_research=retry),
        run_options={"support_check": lambda c, e: False},
    )
    assert len(requests) == 2
    assert calls == []
    assert not result.scores and not result.decisions
    assert result.research_retry_count == {"company-0": 1, "company-1": 1}
    assert {e.error_code for e in result.errors} == {"RESEARCH_TERMINAL_FAILURE"}
    assert not any("secret" in e.message_redacted for e in result.errors)


def test_real_conflicts_reach_coverage_and_research_request():
    requests = []
    conflicts_seen = []

    def conflicts(candidate, evidence):
        conflicts_seen.append(candidate["candidate_id"])
        return [evidence[0].evidence_id]

    def retry(request):
        requests.append(request)
        assert request.coverage.unresolved_conflicts == [
            request.evidence[0].evidence_id
        ]
        return controller.ResearchResponseV3(
            request.candidate["candidate_id"], request.coverage.evidence_revision, ()
        )

    result, calls = scenario(
        stages_transform=lambda stages: replace(
            stages, additional_research=retry, unresolved_conflicts=conflicts
        )
    )
    assert len(requests) == 4
    assert len(conflicts_seen) == 6
    assert len(calls) == 10
    assert all(
        reason == "exhausted" for reason in result.research_stop_reasons.values()
    )


def test_ready_coverage_never_calls_research_and_unknown_is_not_promoted():
    def forbidden(request):
        pytest.fail("research called after ready coverage or for unknown eligibility")

    result, calls = scenario(
        statuses=("eligible", "unknown"),
        stages_transform=lambda stages: replace(stages, additional_research=forbidden),
    )
    assert len(calls) == 5
    assert result.research_retry_count == {"company-0": 0, "company-1": 0}
    assert result.outcomes["company-1"].status == "eligibility_unknown"


@pytest.mark.parametrize(
    "mutation",
    ["candidate", "revision", "foreign_evidence", "schema", "duplicate", "overwrite"],
)
def test_invalid_research_response_isolated_and_never_promoted(mutation):
    requests = []
    trace = []

    def retry(request):
        requests.append(request)
        cid = request.candidate["candidate_id"]
        item = request.evidence[0].model_dump(mode="json")
        if mutation == "foreign_evidence":
            item["candidate_id"] = "foreign"
        elif mutation == "schema":
            item["schema_version"] = "foreign-schema"
        elif mutation == "overwrite":
            item["claim"] = "secret replacement"
        return controller.ResearchResponseV3(
            "foreign" if mutation == "candidate" else cid,
            request.coverage.evidence_revision + (mutation == "revision"),
            [item, item] if mutation == "duplicate" else [item],
        )

    result, calls = scenario(
        stages_transform=lambda stages: replace(stages, additional_research=retry),
        run_options={"support_check": lambda c, e: False, "trace_events": trace},
    )
    assert calls == []
    assert len(requests) == 2
    assert not result.scores and not result.decisions
    assert result.candidate_index == 2
    assert {e.error_code for e in result.errors} == {"RESEARCH_RESPONSE_INVALID"}
    assert not any("secret" in e.message_redacted for e in result.errors)
    for cid in result.outcomes:
        steps = [e["step"] for e in trace if e["candidate_id"] == cid]
        assert steps.count("archive") == steps.count("advance") == 1


def test_identical_repeated_evidence_does_not_increase_revision():
    revisions = []

    def retry(request):
        revisions.append(request.coverage.evidence_revision)
        request.candidate["candidate_id"] = "mutated detached copy"
        # The coverage candidate still belongs to the detached original generation.
        return controller.ResearchResponseV3(
            request.coverage.candidate_id,
            request.coverage.evidence_revision,
            request.evidence,
        )

    result, calls = scenario(
        stages_transform=lambda stages: replace(stages, additional_research=retry),
        run_options={"support_check": lambda c, e: False},
    )
    assert revisions == [1, 1, 1, 1]
    assert len(calls) == 10
    assert all(s.evidence_revision == 1 for s in result.scores.values())


def test_caller_gap_payloads_survive_request_and_exhausted_state():
    from skala_rag.contracts.coverage import ResearchGap

    seen = []

    def gaps(candidate, coverage):
        return [
            ResearchGap(
                schema_version=coverage.schema_version,
                candidate_id=candidate["candidate_id"],
                gap_id=f"gap-{cid}",
                criterion_id=cid,
                missing_fields=["caller-field"],
                reason="caller reason",
                suggested_queries=["caller query"],
                attempted_retrieval_ids=["caller-attempt"],
                status="open",
            )
            for cid in coverage.missing_criterion_ids
        ]

    def retry(request):
        seen.append(request.research_gaps)
        assert all(
            g.suggested_queries == ["caller query"] for g in request.research_gaps
        )
        return controller.ResearchResponseV3(
            request.coverage.candidate_id, request.coverage.evidence_revision, ()
        )

    result, calls = scenario(
        stages_transform=lambda stages: replace(
            stages, additional_research=retry, gap_templates=gaps
        ),
        run_options={"support_check": lambda c, e: False},
    )
    assert len(seen) == 4
    assert len(calls) == 10
    assert len(result.coverage_results) == 2
    for cid, final_gaps in result.research_gaps.items():
        assert len(final_gaps) == 23
        assert all(g.status == "exhausted" for g in final_gaps)
        assert all(g.attempted_retrieval_ids == ["caller-attempt"] for g in final_gaps)
        assert not result.coverage_results[cid].research_ready


def test_research_evidence_cannot_be_omitted_from_final_snapshot():
    def retry(request):
        item = request.evidence[0].model_dump(mode="json")
        item.update(evidence_id="new-evidence", claim="ready")
        return controller.ResearchResponseV3(
            request.coverage.candidate_id, request.coverage.evidence_revision, [item]
        )

    def transform(stages):
        return replace(
            stages,
            additional_research=retry,
            freeze_with_evidence=(
                lambda candidate, eligible, coverage, evidence: stages.freeze(
                    candidate, eligible, coverage
                )
            ),
        )

    result, calls = scenario(
        stages_transform=transform,
        run_options={"support_check": lambda c, e: any(x.claim == "ready" for x in e)},
    )
    assert calls == []
    assert not result.scores
    assert {e.error_code for e in result.errors} == {"SNAPSHOT_INVALID"}
