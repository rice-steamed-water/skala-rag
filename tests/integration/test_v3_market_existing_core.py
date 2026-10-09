"""Real reused Market code; all facts, approval controls and siblings synthetic."""

from collections import Counter
from copy import deepcopy

import pytest
from tests.unit.test_market import CLOCK, RUBRIC, TARGET, Case

from skala_rag.agents.evaluation_v3_adapter import bind_baseline_evaluator_v3
from skala_rag.agents.market import evaluate_market
from skala_rag.contracts.error_codes import ErrorCode
from skala_rag.contracts.interfaces import LLMError
from skala_rag.contracts.v3 import BRANCH_DIMENSIONS
from skala_rag.fakes import FakeLLM
from skala_rag.graph.evaluation_v3 import build_evaluation_graph_v3

pytest_plugins = ("tests.integration.test_evaluation_v3_adapter",)


@pytest.mark.parametrize("fault", [None, "timeout", "schema", "live"])
def test_original_graph_real_market_callback(case, fault):
    _, policy, baseline, synthetic_v3, state = case
    market = Case()
    snapshot = market.snapshot
    frozen = snapshot.model_dump(mode="json")
    state["snapshot_v3"] = frozen
    state["snapshots"] = {snapshot.snapshot_id: deepcopy(frozen)}
    if fault == "live":
        state["run_input"]["execution_mode"] = "live"
    before = deepcopy(state)
    calls = Counter()
    originals = []
    llm = FakeLLM(
        [
            LLMError(ErrorCode.LLM_TIMEOUT, "synthetic timeout")
            if fault == "timeout"
            else market.output()
        ]
    )

    def real_market(received):
        calls["market"] += 1
        assert received == snapshot and received is not snapshot
        result = evaluate_market(
            received,
            target_market=TARGET,
            market_links=market.links,
            rubric=RUBRIC,
            llm=llm,
            policy=policy,
            clock=CLOCK,
            schema_version="synthetic-wrong"
            if fault == "schema"
            else snapshot.schema_version,
        )
        originals.append(result)
        return result

    callbacks = {}
    for branch in BRANCH_DIMENSIONS:

        def synthetic(received, b=branch):
            calls[b] += 1
            assert received == snapshot
            return baseline(b) if b in ("founder", "technology") else synthetic_v3(b)

        callbacks[branch] = (
            bind_baseline_evaluator_v3(
                branch,
                real_market if branch == "market" else synthetic,
                criteria=policy.criteria,
                industry_evidence_dimensions={"market"},
            )
            if branch in ("founder", "market", "technology")
            else synthetic
        )
    graph = build_evaluation_graph_v3(
        callbacks,
        criteria=policy.criteria,
        policy_version=policy.policy_version,
        run_id=snapshot.run_id,
        schema_version=snapshot.schema_version,
        industry_evidence_dimensions={"market"},
        applicability_validator=None,
        clock=CLOCK.now,
    ).compile()
    events = list(graph.stream(state, stream_mode=["updates", "values"]))
    values = [value for mode, value in events if mode == "values"]
    out = values[-1]
    assert state == before
    assert all(len(value.get("evaluations_v3", {})) in (0, 6) for value in values)
    if fault == "live":
        assert calls == {} and llm.calls == []
    else:
        assert calls == Counter({branch: 1 for branch in BRANCH_DIMENSIONS})
        assert len(llm.calls) == 1
        assert (
            sum("join_v3" in value for mode, value in events if mode == "updates") == 1
        )
    if fault is None:
        assert out["evaluation_status_v3"] == "success"
        stored = next(
            result
            for result in out["branch_results_v3"].values()
            if result["branch_id"] == "market"
        )
        original = originals[0].evaluation.model_dump(mode="json")
        promoted = stored["evaluations"]["market"]
        for key, value in original.items():
            if key == "criteria":
                for old, new in zip(value, promoted[key], strict=True):
                    assert all(new[field] == item for field, item in old.items())
            else:
                assert promoted[key] == value
    else:
        assert out["evaluations_v3"] == {}
        assert out["candidate_index"] == 1
        if fault == "timeout":
            assert out["errors"] == [
                error.model_dump(mode="json") for error in originals[0].errors
            ]
