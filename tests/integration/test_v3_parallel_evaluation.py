"""Synthetic T06/T22 against installed LangGraph, no providers."""

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier

import pytest

from skala_rag.contracts.evaluation import EvaluationSnapshot
from skala_rag.contracts.v3 import BRANCH_DIMENSIONS, EvaluationBranchResult
from skala_rag.graph.evaluation_v3 import build_evaluation_graph_v3
from skala_rag.scoring.catalog import load_policy


@pytest.fixture
def case():
    policy = load_policy("configs/scoring.draft.json", execution_mode="fixture")
    common = json.loads(
        (Path(__file__).parents[1] / "fixtures/common.json").read_text()
    )
    raw = deepcopy(next(iter(common["snapshots"].values())))
    raw["policy_version"] = policy.policy_version
    for ev in raw["evidence"].values():
        ev["criterion_ids"] = [c.criterion_id for c in policy.criteria]
    snapshot = EvaluationSnapshot.model_validate(
        raw, context={"execution_mode": "fixture"}
    )
    state = dict(
        snapshot_v3=snapshot.model_dump(mode="json"),
        current_candidate_id=snapshot.candidate_id,
        evaluation_rounds={snapshot.candidate_id: snapshot.evaluation_round},
        evidence_revisions={snapshot.candidate_id: snapshot.evidence_revision},
        run_input={
            "policy_version": snapshot.policy_version,
            "execution_mode": "fixture",
        },
        snapshots={snapshot.snapshot_id: snapshot.model_dump(mode="json")},
        candidates=[{"candidate_id": snapshot.candidate_id}],
        candidate_index=0,
        candidate_outcomes={},
        candidate_status={},
        errors=[],
    )

    def result(branch, snapshot):
        identity = {
            k: getattr(snapshot, k)
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
                        schema_version=snapshot.schema_version,
                        criterion_id=c.criterion_id,
                        status="observed",
                        rating=4,
                        evidence_ids=[next(iter(snapshot.evidence))],
                        rationale="Synthetic",
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

    return snapshot, policy, state, result


@pytest.mark.parametrize(
    "kind", ["round", "revision", "stored_snapshot", "run", "live"]
)
def test_preflight_corruption_fails_before_callbacks_and_advances(case, kind):
    snapshot, _, state, _ = case
    state["snapshots"] = {snapshot.snapshot_id: deepcopy(state["snapshot_v3"])}
    calls = []
    if kind == "round":
        state["evaluation_rounds"][snapshot.candidate_id] += 1
    elif kind == "revision":
        state["evidence_revisions"][snapshot.candidate_id] += 1
    elif kind == "stored_snapshot":
        state["snapshots"][snapshot.snapshot_id]["evidence_ids"] = []
    elif kind == "run":
        state["snapshot_v3"]["run_id"] = "other-run"
    else:
        state["run_input"]["execution_mode"] = "live"
    callbacks = {b: lambda s: calls.append(s) for b in BRANCH_DIMENSIONS}
    out = build(case, callbacks).invoke(state)
    assert not calls
    assert out["evaluation_status_v3"] == "failure"
    assert out["candidate_index"] == 1
    assert not out["evaluations_v3"]


def build(case, callbacks):
    snapshot, policy, _, _ = case
    return build_evaluation_graph_v3(
        callbacks,
        criteria=policy.criteria,
        policy_version=policy.policy_version,
        run_id=snapshot.run_id,
        schema_version=snapshot.schema_version,
        industry_evidence_dimensions=set(),
        applicability_validator=None,
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    ).compile()


def test_t06_actual_five_parallel_barrier_six_atomic_dimensions(case):
    _, _, state, result = case
    barrier = Barrier(5, timeout=5)

    def evaluator(branch):
        def call(snapshot):
            barrier.wait()
            return result(branch, snapshot)

        return call

    graph = build(case, {b: evaluator(b) for b in BRANCH_DIMENSIONS})
    events = list(graph.stream(state, stream_mode=["updates", "values"]))
    out = [value for mode, value in events if mode == "values"][-1]
    joins = [
        value for mode, value in events if mode == "updates" and "join_v3" in value
    ]
    assert len(joins) == 1
    assert out["evaluation_status_v3"] == "success"
    assert len(out["evaluations_v3"]) == 6
    assert len(out["branch_results_v3"]) == 5
    assert out["candidate_index"] == 0


@pytest.mark.parametrize(
    "kind",
    ["exception", "half", "mutated_dto_half", "wrong_identity", "criteria", "evidence"],
)
def test_t22_terminal_failure_no_partial_promotion_archive_advance_once(case, kind):
    _, _, state, result = case

    def bad(snapshot):
        if kind == "exception":
            raise RuntimeError("secret provider content")
        if kind == "mutated_dto_half":
            dto = result("business_deal", snapshot)
            dto.evaluations.pop("deal_terms")
            return dto
        payload = result("business_deal", snapshot).model_dump(mode="json")
        if kind == "half":
            del payload["evaluations"]["deal_terms"]
        elif kind == "wrong_identity":
            payload["run_id"] = "other"
        elif kind == "criteria":
            payload["evaluations"]["traction"]["criteria"] = []
        else:
            payload["evaluations"]["traction"]["criteria"][0]["evidence_ids"] = [
                "outside"
            ]
        return payload

    callbacks = {b: lambda s, b=b: result(b, s) for b in BRANCH_DIMENSIONS}
    callbacks["business_deal"] = bad
    out = build(case, callbacks).invoke(state)
    assert out["evaluation_status_v3"] == "failure"
    assert not out["evaluations_v3"]
    assert out["candidate_index"] == 1
    assert out["current_candidate_id"] is None
    assert (
        out["candidate_outcomes"][state["current_candidate_id"]]["status"] == "failed"
    )
    assert out["errors"]
    assert "secret provider content" not in json.dumps(out)
    assert "score_summaries" not in out


def test_real_freeze_controller_snapshot_enters_v3_graph(case):
    from skala_rag.contracts.inputs import RunInput
    from skala_rag.graph.snapshot import freeze_snapshot

    snapshot, policy, state, result = case
    root = Path(__file__).parents[1] / "fixtures"
    base = json.loads((root / "contracts.json").read_text())
    common = json.loads((root / "common.json").read_text())
    run_raw = base["RunInput"]
    run_raw.update(
        policy_version=policy.policy_version,
        corpus_version=snapshot.corpus_version,
        as_of=snapshot.as_of.isoformat(),
    )
    run = RunInput.model_validate(run_raw)
    cid = snapshot.candidate_id
    eligible = next(
        e for e in common["eligibility_results"].values() if e["candidate_id"] == cid
    )
    state.update(
        run_input=run.model_dump(mode="json"),
        eligibility_results={cid: eligible},
        evidence=deepcopy(state["snapshot_v3"]["evidence"]),
        sources=deepcopy(state["snapshot_v3"]["sources"]),
        chunks=deepcopy(state["snapshot_v3"]["chunks"]),
        retrieval_history=list(state["snapshot_v3"]["retrieval_records"].values()),
        snapshots={},
        evaluation_rounds={cid: 0},
    )
    # Common DTO fixture is not a freeze-valid excerpt/locator fixture.
    # Align this synthetic test's Evidence to the actual returned Chunk.
    for evidence in state["evidence"].values():
        chunk = state["chunks"][evidence["provenance"][0]["chunk_id"]]
        evidence["locator"] = chunk["locator"]
        chunk["text"] += "\n" + evidence["excerpt"]
    frozen = freeze_snapshot(
        cid,
        state,
        run,
        run_id=snapshot.run_id,
        index_version=snapshot.index_version,
        schema_version=snapshot.schema_version,
        allowed_source_ids=set(state["sources"]),
        industry_evidence_ids=set(),
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    state["snapshot_v3"] = frozen.model_dump(mode="json")
    out = build(
        (frozen, policy, state, result),
        {b: lambda s, b=b: result(b, s) for b in BRANCH_DIMENSIONS},
    ).invoke(state)
    assert out["evaluation_status_v3"] == "success"
    assert len(out["evaluations_v3"]) == 6
    assert state["snapshots"][frozen.snapshot_id] == frozen.model_dump(mode="json")


@pytest.mark.parametrize("conflict", [False, True])
def test_actual_graph_replay_identical_or_conflicting(case, conflict):
    from skala_rag.graph.reducers_v3 import branch_key

    snapshot, _, state, result = case
    stored = result("founder", snapshot).model_dump(mode="json")
    if conflict:
        stored["evaluations"]["founder"]["caveats"] = ["different"]
    state["branch_results_v3"] = {branch_key(result("founder", snapshot)): stored}
    out = build(
        case, {b: lambda s, b=b: result(b, s) for b in BRANCH_DIMENSIONS}
    ).invoke(state)
    assert out["evaluation_status_v3"] == ("failure" if conflict else "success")
    assert out["candidate_index"] == (1 if conflict else 0)
    assert len(out["evaluations_v3"]) == (0 if conflict else 6)


@pytest.mark.parametrize("conflict", [False, True])
def test_failed_branch_preserves_original_terminal_errors(case, conflict):
    from skala_rag.contracts.errors import WorkflowError

    _, _, state, result = case

    def failed(snapshot):
        raw = result("market", snapshot).model_dump(mode="json")
        error = WorkflowError(
            schema_version=snapshot.schema_version,
            error_id="supplied-timeout",
            run_id=snapshot.run_id,
            candidate_id=snapshot.candidate_id,
            node="market",
            error_code="TOOL_TIMEOUT",
            message_redacted="Synthetic timeout",
            retryable=True,
            attempt=1,
            timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        raw.update(
            status="failure", evaluations=None, errors=[error.model_dump(mode="json")]
        )
        return EvaluationBranchResult.model_validate(raw)

    callbacks = {b: lambda s, b=b: result(b, s) for b in BRANCH_DIMENSIONS}
    callbacks["market"] = failed
    if conflict:
        from skala_rag.graph.reducers_v3 import branch_key

        snapshot = case[0]
        original = result("founder", snapshot)
        raw = original.model_dump(mode="json")
        raw["evaluations"]["founder"]["caveats"] = ["Conflict"]
        state["branch_results_v3"] = {branch_key(original): raw}
    out = build(case, callbacks).invoke(state)
    ids = [e["error_id"] for e in out["errors"]]
    assert "supplied-timeout" in ids
    assert len(ids) == (2 if conflict else 1)
    assert (
        out["candidate_outcomes"][state["current_candidate_id"]]["failure_ids"] == ids
    )
    assert out["candidate_index"] == 1
    assert not out["evaluations_v3"]


def test_failure_archive_excludes_prior_errors_for_same_candidate(case):
    snapshot, _, state, result = case
    prior = {
        "schema_version": "historical-schema",
        "error_id": "prior-generation-error",
        "run_id": snapshot.run_id,
        "candidate_id": snapshot.candidate_id,
        "node": "old-evaluation",
        "error_code": "UPSTREAM_INVALID",
        "message_redacted": "Previous attempt",
        "retryable": False,
        "attempt": 1,
        "timestamp": "2026-09-01T00:00:00Z",
    }
    state["errors"] = [prior]

    def bad(_snapshot):
        raise RuntimeError("synthetic failure")

    callbacks = {b: lambda s, b=b: result(b, s) for b in BRANCH_DIMENSIONS}
    callbacks["market"] = bad
    out = build(case, callbacks).invoke(state)
    assert prior in out["errors"]
    assert out["candidate_outcomes"][snapshot.candidate_id]["failure_ids"] == [
        f"v3:{snapshot.snapshot_id}:market"
    ]
    assert out["candidate_outcomes"][snapshot.candidate_id]["schema_version"] == (
        snapshot.schema_version
    )
