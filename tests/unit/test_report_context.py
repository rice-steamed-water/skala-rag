"""#26 / T23: ReportContext 조립과 참조 검증 (공통 가상 fixture)."""

import copy
from pathlib import Path

import pytest
from tests.fixtures.loader import load_common_fixtures

from skala_rag.contracts import CandidateOutcome, EvaluationResult, ReportInput
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import BuildReportContext
from skala_rag.reporting.context import (
    ReportContextError,
    build_report_context,
    context_id_for,
    permitted_evidence_ids,
)
from skala_rag.scoring import build_investment_decision, build_score_summary
from skala_rag.scoring.catalog import load_policy

ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
SV = "synthetic-common-1"
DIMS = ("founder", "market", "technology", "moat", "traction", "deal_terms")
STATUS = {"RECOMMEND": "recommend", "WATCHLIST": "watchlist", "PASS": "pass"}


def _dump(model):
    return model.model_dump(mode="json")


@pytest.fixture
def world():
    fx = load_common_fixtures(POLICY)
    snap = next(iter(fx.snapshots.values()))
    cid = snap.candidate_id
    evaluations = [fx.evaluations[f"{cid}:{snap.evaluation_round}:{d}"] for d in DIMS]
    results = [
        EvaluationResult(
            schema_version=SV,
            run_id=e.run_id,
            candidate_id=e.candidate_id,
            dimension=e.dimension,
            evaluation_round=e.evaluation_round,
            snapshot_id=e.snapshot_id,
            evidence_revision=e.evidence_revision,
            policy_version=e.policy_version,
            status="success",
            evaluation=e,
            errors=[],
        )
        for e in evaluations
    ]
    summary = build_score_summary(results, POLICY, schema_version=SV)
    decision = build_investment_decision(
        summary, POLICY, schema_version=SV, evidence_ids=[], rationale="가상 설명"
    )
    elig = {er.candidate_id: er for er in fx.eligibility_results.values()}
    state = {
        "run_input": {"execution_mode": "fixture"},
        "sources": {k: _dump(v) for k, v in fx.sources.items()},
        "chunks": {k: _dump(v) for k, v in fx.chunks.items()},
        "evidence": {k: _dump(v) for k, v in fx.evidence.items()},
        "retrieval_history": [_dump(r) for r in fx.retrieval_records.values()],
        "eligibility_results": {k: _dump(v) for k, v in elig.items()},
        "snapshots": {k: _dump(v) for k, v in fx.snapshots.items()},
        "evaluations": {k: _dump(v) for k, v in fx.evaluations.items()},
        "evaluation_rounds": {cid: snap.evaluation_round},
        "score_summaries": {cid: _dump(summary)},
        "investment_decisions": {cid: _dump(decision)},
        "errors": [],
    }
    status = STATUS[decision.label]
    outcomes = [
        CandidateOutcome(
            schema_version=SV,
            candidate_id=cid,
            status=status,
            eligibility_result_id=elig[cid].eligibility_result_id,
            decision_id=decision.decision_id,
            failure_ids=[],
            summary_reason="가상 평가 완료",
        ),
        CandidateOutcome(
            schema_version=SV,
            candidate_id="co-fixture-ineligible",
            status="ineligible",
            eligibility_result_id=elig["co-fixture-ineligible"].eligibility_result_id,
            failure_ids=[],
            summary_reason="가상 부적격",
        ),
        CandidateOutcome(
            schema_version=SV,
            candidate_id="co-fixture-unknown",
            status="eligibility_unknown",
            eligibility_result_id=elig["co-fixture-unknown"].eligibility_result_id,
            failure_ids=[],
            summary_reason="가상 적격성 미확정",
        ),
    ]
    return {
        "fx": fx,
        "snap": snap,
        "cid": cid,
        "state": state,
        "outcomes": outcomes,
        "decision": decision,
        "summary": summary,
    }


def _input(world, outcomes=None, **overrides):
    outcomes = outcomes if outcomes is not None else world["outcomes"]
    recommend = world["decision"].label == "RECOMMEND"
    payload = {
        "schema_version": SV,
        "run_id": world["snap"].run_id,
        "mode": "single_candidate" if recommend else "no_recommendation",
        "selected_candidate_id": world["cid"] if recommend else None,
        "candidate_outcomes": outcomes,
        "permitted_evidence_ids": permitted_evidence_ids(outcomes, world["state"]),
        "as_of": world["snap"].as_of,
        "corpus_version": world["snap"].corpus_version,
        "policy_version": POLICY.policy_version,
    }
    payload.update(overrides)
    return ReportInput(**payload)


def _code(world, state=None, report_input=None, policy=POLICY):
    with pytest.raises(ReportContextError) as err:
        build_report_context(
            report_input or _input(world), state or world["state"], policy=policy
        )
    return err.value.code


def test_protocol():
    assert isinstance(build_report_context, BuildReportContext)


def test_builds_resolved_payload_context(world):
    ri = _input(world)
    ctx = build_report_context(ri, world["state"], policy=POLICY)
    cid = world["cid"]
    assert ctx.context_id == context_id_for(ri)
    assert set(ctx.evaluations) == {f"{cid}:1:{d}" for d in DIMS}
    assert list(ctx.snapshots) == [world["snap"].snapshot_id]
    assert set(ctx.evidence) == set(ri.permitted_evidence_ids)
    for ev in ctx.evidence.values():
        assert ev.source_id in ctx.sources
    assert len(ctx.eligibility_results) == 3
    # 평가 안 된 후보는 평가·점수 없이 사유만
    assert all(e.candidate_id == cid for e in ctx.evaluations.values())
    assert len(ctx.score_summaries) == len(ctx.decisions) == 1


def test_context_id_deterministic(world):
    a = build_report_context(_input(world), world["state"])
    b = build_report_context(_input(world), world["state"])
    assert a.context_id == b.context_id


def test_missing_source_is_context_invalid(world):
    state = copy.deepcopy(world["state"])
    snap = state["snapshots"][world["snap"].snapshot_id]
    victim = next(iter(snap["evidence"].values()))["source_id"]
    snap["sources"].pop(victim, None)
    state["sources"].pop(victim, None)
    assert _code(world, state=state) == ErrorCode.CONTEXT_INVALID


def test_other_generation_score_is_context_invalid(world):
    state = copy.deepcopy(world["state"])
    state["evaluation_rounds"][world["cid"]] = 2  # 최종 세대는 2인데 점수는 1
    assert _code(world, state=state) == ErrorCode.CONTEXT_INVALID


def test_unknown_decision_id_is_context_invalid(world):
    outcomes = [o.model_copy() for o in world["outcomes"]]
    outcomes[0] = outcomes[0].model_copy(update={"decision_id": "decision-v1-nope"})
    assert _code(world, report_input=_input(world, outcomes)) == (
        ErrorCode.CONTEXT_INVALID
    )


def test_tampered_score_is_upstream_invalid(world):
    state = copy.deepcopy(world["state"])
    state["score_summaries"][world["cid"]]["observed_score"] = "99"
    assert _code(world, state=state) == ErrorCode.UPSTREAM_INVALID


def test_tampered_label_is_upstream_invalid(world):
    state = copy.deepcopy(world["state"])
    d = state["investment_decisions"][world["cid"]]
    d["report_grade"] = "조작된 등급"
    assert _code(world, state=state) == ErrorCode.UPSTREAM_INVALID


def test_malformed_state_payload_is_upstream_invalid(world):
    state = copy.deepcopy(world["state"])
    state["score_summaries"][world["cid"]]["coverage_pct"] = "abc"
    assert _code(world, state=state) == ErrorCode.UPSTREAM_INVALID


@pytest.mark.parametrize(
    "field,bad", [("evaluation_rounds", []), ("sources", []), ("chunks", None)]
)
def test_explicit_falsey_state_map_is_upstream_invalid(world, field, bad):
    state = copy.deepcopy(world["state"])
    state[field] = bad
    assert _code(world, state=state) == ErrorCode.UPSTREAM_INVALID


def test_superseded_eligibility_evidence_is_context_invalid(world):
    state = copy.deepcopy(world["state"])
    target = state["eligibility_results"]["co-fixture-ineligible"]["evidence_ids"][0]
    fix = copy.deepcopy(state["evidence"][target])
    fix["evidence_id"] = "ev-correction"
    fix["supersedes"] = target
    state["evidence"]["ev-correction"] = fix
    assert _code(world, state=state) == ErrorCode.CONTEXT_INVALID


def test_permitted_ids_must_match(world):
    ri = _input(world)
    extra = ri.model_copy(
        update={"permitted_evidence_ids": [*ri.permitted_evidence_ids, "ev-x"]}
    )
    assert _code(world, report_input=extra) == ErrorCode.CONTEXT_INVALID


def test_failed_candidate_carries_errors_only(world):
    state = copy.deepcopy(world["state"])
    state["errors"] = [
        {
            "schema_version": SV,
            "error_id": "err-1",
            "run_id": world["snap"].run_id,
            "candidate_id": "co-fixture-same_name",
            "node": "technology_evaluation",
            "error_code": "LLM_OUTPUT_INVALID",
            "message_redacted": "가상 오류",
            "retryable": True,
            "attempt": 2,
            "timestamp": "2026-09-30T00:00:00+09:00",
        }
    ]
    failed = CandidateOutcome(
        schema_version=SV,
        candidate_id="co-fixture-same_name",
        status="failed",
        failure_ids=["err-1"],
        summary_reason="평가 실패",
    )
    outcomes = [*world["outcomes"], failed]
    ctx = build_report_context(_input(world, outcomes), state)
    assert "err-1" in ctx.errors
    assert not any(
        e.candidate_id == "co-fixture-same_name" for e in ctx.evaluations.values()
    )
    missing_error = failed.model_copy(update={"failure_ids": ["err-none"]})
    bad = _input(world, [*world["outcomes"], missing_error])
    assert _code(world, state=state, report_input=bad) == ErrorCode.CONTEXT_INVALID


def test_not_decided_candidate_must_not_carry_decision(world):
    outcomes = list(world["outcomes"])
    outcomes[1] = outcomes[1].model_copy(
        update={"decision_id": world["decision"].decision_id}
    )
    assert _code(world, report_input=_input(world, outcomes)) == (
        ErrorCode.CONTEXT_INVALID
    )


def test_live_mode_rejects_fixture_payloads(world):
    with pytest.raises(ReportContextError) as err:
        build_report_context(_input(world), world["state"], execution_mode="live")
    assert err.value.code == ErrorCode.UPSTREAM_INVALID
