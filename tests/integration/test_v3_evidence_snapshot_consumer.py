"""#211 public consumers, real research/freeze/Technology; offline synthetic data."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
import yaml
from tests.fixtures.evidence_research import (
    SCHEMA,
    LineLLM,
    chunk,
    indexed_retriever,
    utc,
)
from tests.integration.test_evidence_research_graph import (
    BASE,
    BUDGET,
    CORPUS,
    INDEX,
    _gap,
    _research_node,
)
from tests.unit import test_actual_admission_v3 as actual_shared
from tests.unit.test_approved_policy import gates_payload, runtime_binding
from tests.unit.test_openai_attempt import body

import skala_rag.graph.candidates_v3 as oracle
import skala_rag.graph.snapshot as snapshot_module
from skala_rag.agents.eligibility import check_eligibility
from skala_rag.agents.evaluation import output_from_evaluation
from skala_rag.agents.evaluation_v3_adapter import bind_baseline_evaluator_v3
from skala_rag.agents.evidence_research import EvidenceResearch
from skala_rag.agents.technology import evaluate_technology
from skala_rag.contracts import EligibilityResult, RetrievalBundle
from skala_rag.contracts.candidates import Candidate, CompanyProfile
from skala_rag.contracts.evaluation import Evaluation as BaselineEvaluation
from skala_rag.contracts.evidence import Evidence
from skala_rag.contracts.inputs import RunInput
from skala_rag.contracts.sources import Chunk, Source
from skala_rag.contracts.v3 import BRANCH_DIMENSIONS, EvaluationBranchResult
from skala_rag.fakes import FakeClock, FakeLLM
from skala_rag.graph.candidate_workflow_v3 import run_candidate_workflow_v3
from skala_rag.graph.research_artifacts_v3 import (
    CompanyResearchArtifactsV3,
    EvidenceResearchBindingV3,
)
from skala_rag.prompts.evidence_extraction import ClaimDraft
from skala_rag.rag.adapter import IndexedRetriever, IndexSnapshot
from skala_rag.scoring.catalog import load_policy
from skala_rag.scoring.v3_policy import load_v3_policy
from skala_rag.tools.runtime import Allowance

actual_admission_fixture = actual_shared.configured


def setup_case(*, gap=False, count=1) -> dict[str, Any]:
    assert hasattr(oracle, "EvidenceResearchBindingV3"), (
        "explicit full-outcome binding required"
    )
    assert hasattr(oracle, "CompanyResearchArtifactsV3"), (
        "explicit CompanyResearch artifact seed required"
    )
    policy = load_v3_policy("configs/scoring.v3.json", execution_mode="fixture")
    catalog = load_policy("configs/scoring.draft.json", execution_mode="fixture")
    source = Source.model_validate(
        BASE["Source"], context={"execution_mode": "fixture"}
    )
    clock = FakeClock(utc(2026, 9, 1))
    candidates = [
        Candidate.model_validate(
            dict(
                BASE["Candidate"],
                candidate_id=f"co-{i}",
                canonical_name=f"Synthetic {i}",
            ),
            context={"execution_mode": "fixture"},
        )
        for i in range(count)
    ]
    initial = [
        c.criterion_id
        for c in policy.criteria
        if not gap or c.dimension == "technology"
    ]
    chunks = [
        chunk(
            f"chunk-{i}-{criterion}",
            source,
            f"Synthetic {i} {criterion} observed",
            candidate_ids=[f"co-{i}"],
            corpus=CORPUS,
        )
        for i in range(count)
        for criterion in [c.criterion_id for c in policy.criteria]
    ]
    retrieve, backend = indexed_retriever(
        chunks, {source.source_id: source}, corpus=CORPUS, index=INDEX, clock=clock
    )
    # This mock backend has no paid request; avoid unknown-cost fixture reservations.
    retrieve._allowance = retrieve._allowance.model_copy(
        update={"max_cost_usd": Decimal(0)}
    )
    producer = EvidenceResearch(
        retrieve=retrieve,
        rag_required=True,
        llm=LineLLM(),
        initial_plan=lambda c: [_gap(c.candidate_id, x) for x in initial],
        run_id="run-synthetic",
        corpus_version=CORPUS,
        index_version=INDEX,
        as_of=date(2026, 9, 1),
        top_k=5,
        allowed_source_ids=[source.source_id],
        clock=clock,
        schema_version=SCHEMA,
        execution_mode="fixture",
    )
    run = RunInput.model_validate(
        dict(
            BASE["RunInput"],
            policy_version=policy.policy_version,
            corpus_version=CORPUS,
        )
    )
    # One explicit batch budget: initial all-criteria plan is not a controller retry.
    budget = BUDGET.model_copy(update={"max_calls": 23})
    binding = oracle.EvidenceResearchBindingV3(
        research=producer,
        budget=budget,
        run_input=run,
        run_id="run-synthetic",
        schema_version=SCHEMA,
        index_version=INDEX,
        allowed_source_ids=frozenset([source.source_id]),
        industry_evidence_ids=frozenset(),
    )
    seeds = {}
    profiles = {}
    for candidate in candidates:
        cid = candidate.candidate_id
        delta = _research_node(dict(current_candidate_id=cid, retrieval_history=[]))
        profiles[cid] = CompanyProfile.model_validate(
            dict(
                BASE["CompanyProfile"],
                candidate_id=cid,
                domain_match=True,
                is_listed=False,
                exit_completed=False,
                stage=dict(
                    BASE["StageInfo"],
                    raw_label="Series A",
                    normalized_round="series_a",
                    method="explicit",
                    source_ids=[source.source_id],
                ),
                field_evidence_ids={
                    k: [f"ev-{cid}"]
                    for k in [
                        "domain_match",
                        "is_listed",
                        "stage",
                        "exit_completed",
                        "identity",
                        "business",
                    ]
                },
            )
        )
        seeds[cid] = oracle.CompanyResearchArtifactsV3(
            candidate_id=cid,
            run_id="run-synthetic",
            schema_version=SCHEMA,
            evidence_revision=1,
            sources={source.source_id: source},
            chunks={},
            records=delta["retrieval_history"],
            evidence=delta["evidence"],
        )

    def eligible(c, seed):
        return check_eligibility(
            profiles[c["candidate_id"]],
            {
                k: Evidence.model_validate(v, context={"execution_mode": "fixture"})
                for k, v in seed.evidence.items()
            },
            {"policy_version": policy.policy_version},
            run_id="run-synthetic",
            evidence_revision=seed.evidence_revision,
        )

    def forbidden(*args):
        pytest.fail("artifact lane called legacy collect/freeze callback")

    stages = oracle.CandidateStagesV3(
        discover=lambda: candidates,
        normalize=lambda c: c,
        research=lambda c: seeds[c["candidate_id"]],
        eligibility=eligible,
        collect=forbidden,
        freeze=forbidden,
        evidence_research=binding,
        gap_templates=lambda c, coverage: [
            _gap(c["candidate_id"], x) for x in coverage.missing_criterion_ids
        ],
    )
    seen, receipts = [], []
    rubric = yaml.safe_load(open("configs/rubrics/core.yaml").read())

    def evaluation(s, dimension, *, baseline=False):
        identity = {
            k: getattr(s, k)
            for k in [
                "schema_version",
                "run_id",
                "candidate_id",
                "evaluation_round",
                "snapshot_id",
                "evidence_revision",
                "policy_version",
            ]
        }
        return dict(
            **identity,
            dimension=dimension,
            rubric_version=rubric["rubric_version"] if baseline else "synthetic",
            criteria=[
                dict(
                    schema_version=SCHEMA,
                    criterion_id=c.criterion_id,
                    status="observed",
                    rating=4,
                    evidence_ids=[
                        next(
                            e.evidence_id
                            for e in s.evidence.values()
                            if c.criterion_id in e.criterion_ids
                        )
                    ],
                    rationale="Synthetic fixture observation",
                )
                for c in policy.criteria
                if c.dimension == dimension
            ],
            research_gaps=[],
            caveats=[],
        )

    callbacks = {}
    for branch in BRANCH_DIMENSIONS:
        if branch == "technology":

            def technology(s):
                seen.append(("technology", deepcopy(s)))
                e = evaluation(s, "technology", baseline=True)
                llm = FakeLLM(
                    [output_from_evaluation(BaselineEvaluation.model_validate(e))]
                )
                receipt = evaluate_technology(
                    s,
                    rubric=rubric,
                    llm=llm,
                    policy=catalog.model_copy(
                        update={"policy_version": policy.policy_version}
                    ),
                    clock=clock,
                    schema_version=SCHEMA,
                    execution_mode="fixture",
                )
                receipts.append(receipt)
                return receipt.result

            callbacks[branch] = bind_baseline_evaluator_v3(
                branch,
                technology,
                criteria=policy.criteria,
                industry_evidence_dimensions=set(),
            )
        else:

            def synthetic(s, b=branch):
                seen.append((b, deepcopy(s)))
                dims = {d: evaluation(s, d) for d in BRANCH_DIMENSIONS[b]}
                identity = {
                    k: getattr(s, k)
                    for k in [
                        "schema_version",
                        "run_id",
                        "candidate_id",
                        "evaluation_round",
                        "snapshot_id",
                        "evidence_revision",
                        "policy_version",
                    ]
                }
                return EvaluationBranchResult.model_validate(
                    dict(
                        **identity,
                        branch_id=b,
                        status="success",
                        evaluations=dims,
                        errors=[],
                    )
                )

            callbacks[branch] = synthetic
    trace, events = [], []
    options = dict(
        policy=policy,
        catalog=catalog,
        catalog_policy_version=catalog.policy_version,
        run_id="run-synthetic",
        schema_version=SCHEMA,
        support_check=lambda c, e: bool(e),
        applicability_assessments=lambda cid: {},
        applicability_check=lambda *args: False,
        applicability_verifier=None,
        industry_evidence_dimensions=set(),
        clock=clock.now,
        trace_events=trace,
    )
    return dict(
        stages=stages,
        callbacks=callbacks,
        options=options,
        producer=producer,
        backend=backend,
        seeds=seeds,
        seen=seen,
        receipts=receipts,
        events=events,
        trace=trace,
        initial=initial,
    )


def execute(case, engine):
    return engine(case["stages"], case["callbacks"], **case["options"])


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
def test_full_initial_outcome_owned_real_freeze_technology_join(engine, monkeypatch):
    c = setup_case()
    real = snapshot_module.freeze_snapshot
    frozen = []

    def observed(*args, **kwargs):
        result = real(*args, **kwargs)
        frozen.append((deepcopy(args[1]), deepcopy(result)))
        return result

    monkeypatch.setattr(snapshot_module, "freeze_snapshot", observed)
    result = execute(c, engine)
    assert not result.errors
    assert result.candidate_index == 1 and len(result.scores) == 1
    assert len(frozen) == 1 and len(c["seen"]) == 5 and len(c["receipts"]) == 1
    state, snapshot = frozen[0]
    assert state["eligibility_results"]["co-0"]["evidence_revision"] == 1
    assert snapshot.evidence_revision == 2
    assert all(s == snapshot and s is not snapshot for _, s in c["seen"])
    assert state["snapshots"][snapshot.snapshot_id] == snapshot.model_dump(mode="json")
    assert result.research_retry_count == {"co-0": 0}
    owned = result.research_artifacts["co-0"]
    assert owned["state"]["sources"] == state["sources"]
    assert (
        owned["batches"][0]["status"] == "ok" and owned["batches"][0]["initial"] is True
    )
    assert set(owned["batches"][0]["evidence"]) < set(snapshot.evidence)
    assert "ev-co-0" in snapshot.evidence
    for t in c["receipts"][0].trace:
        assert t.snapshot_id == snapshot.snapshot_id
        assert t.chunk_id in snapshot.chunks
        assert t.retrieval_id in snapshot.retrieval_records
        assert snapshot.chunks[t.chunk_id].source_id in snapshot.sources
    assert c["backend"].calls == c["initial"]


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
def test_coverage_generated_gap_runs_same_producer_then_revision_freeze(engine):
    c = setup_case(gap=True)
    batches = []
    original = c["producer"].run

    def record(candidate, gaps, budget):
        batches.append((candidate.candidate_id, deepcopy(gaps), deepcopy(budget)))
        return original(candidate, gaps, budget)

    c["producer"].run = record
    result = execute(c, engine)
    assert not result.errors
    assert len(result.scores) == 1 and len(c["seen"]) == 5
    assert len(batches) == 2 and batches[0][1] == ()
    extra = batches[1][1]
    assert extra and {g.criterion_id for g in extra} == {
        x.criterion_id for x in c["options"]["catalog"].criteria
    } - set(c["initial"])
    assert all(g.status == "open" and g.priority_weight is not None for g in extra)
    assert batches[0][2] == batches[1][2]
    assert result.research_retry_count == {"co-0": 1}
    assert result.coverage_results["co-0"].evidence_revision == 3
    assert result.coverage_results["co-0"].research_ready
    assert result.research_stop_reasons == {"co-0": "ready"}
    owned = result.research_artifacts["co-0"]
    assert [b["initial"] for b in owned["batches"]] == [True, False]
    assert len(owned["state"]["evidence"]) == 24
    snapshot = c["seen"][0][1]
    assert snapshot.evidence_revision == 3
    assert all(s == snapshot for _, s in c["seen"])
    assert c["receipts"][0].trace
    for b in owned["batches"]:
        for eid, item in b["evidence"].items():
            assert eid in snapshot.evidence
            for path in item["provenance"]:
                record = snapshot.retrieval_records[path["retrieval_id"]]
                assert (
                    eid in record.evidence_ids and path["chunk_id"] in record.chunk_ids
                )


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("additional", [False, True])
@pytest.mark.parametrize("failure", ["required", "extractor"])
def test_failed_outcome_retains_original_errors_and_partial_inputs_no_evaluation(
    engine, additional, failure
):
    from tests.fixtures.evidence_research import StubWeb, llm_error

    from skala_rag.agents.evidence_research import WebChannel
    from skala_rag.contracts.error_codes import ErrorCode

    c = setup_case(gap=True)
    original = c["producer"].run
    outcomes = []

    def fail(candidate, gaps, budget):
        if bool(gaps) == additional:
            if failure == "required":
                c["producer"]._web = (
                    WebChannel(
                        "fixture-required",
                        "web",
                        True,
                        StubWeb(
                            {},
                            clock=FakeClock(utc(2026, 9, 1)),
                            error=ErrorCode.TOOL_UNAVAILABLE,
                            name="fixture-required",
                        ),
                    ),
                )
            else:
                c["producer"]._llm = LineLLM(error=llm_error())
        outcome = original(candidate, gaps, budget)
        outcomes.append(deepcopy(outcome))
        return outcome

    c["producer"].run = fail
    result = execute(c, engine)
    failed = outcomes[-1]
    assert failed.status in ("unavailable", "failed") and failed.errors
    assert result.outcomes["co-0"].status == "failed"
    assert result.candidate_index == 1 and c["seen"] == []
    assert not result.scores and not result.decisions
    assert [e.model_dump(mode="json") for e in result.errors] == [
        e.model_dump(mode="json") for e in failed.errors
    ]
    assert result.outcomes["co-0"].failure_ids == [e.error_id for e in failed.errors]
    assert result.research_retry_count == {"co-0": int(additional)}
    owned = result.research_artifacts["co-0"]
    batch = owned["batches"][-1]
    assert batch["admission"] == "rejected_input"
    assert batch["sources"] == {
        k: s.model_dump(mode="json") for k, s in failed.sources.items()
    }
    assert batch["records"] == [r.model_dump(mode="json") for r in failed.records]
    assert batch["chunks"] == {
        k: s.model_dump(mode="json") for k, s in failed.chunks.items()
    }
    assert batch["errors"] == [e.model_dump(mode="json") for e in failed.errors]
    assert not owned["state"]["snapshots"]
    assert owned["state"]["evidence_revisions"]["co-0"] == (2 if additional else 1)
    steps = [t["step"] for t in c["trace"] if t["candidate_id"] == "co-0"]
    assert steps.count("archive") == steps.count("advance") == 1


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("additional", [False, True])
@pytest.mark.parametrize(
    "damage",
    [
        "missing_source",
        "missing_chunk",
        "source_collision",
        "chunk_key",
        "record_index",
        "record_as_of",
        "record_schema",
        "record_collision",
        "evidence_candidate",
        "evidence_schema",
        "industry_not_allowlisted",
        "future_event",
        "future_source",
        "unlinked_evidence",
        "unreferenced_bad_record",
        "call_missing_record",
        "gap_candidate",
        "invalid_status",
        "negative_skipped",
    ],
)
def test_malformed_success_payload_is_rejected_not_promoted(engine, damage, additional):
    c = setup_case(gap=additional)
    original = c["producer"].run

    def corrupt(candidate, gaps, budget):
        out = original(candidate, gaps, budget)
        if bool(gaps) != additional:
            return out
        eid, ev = next(iter(out.evidence.items()))
        rid = ev.provenance[0].retrieval_id
        record_index = next(
            i for i, r in enumerate(out.records) if r.retrieval_id == rid
        )
        record = out.records[record_index]
        source_id = ev.source_id
        if damage == "missing_source":
            out.sources.clear()
        elif damage == "missing_chunk":
            out.chunks.clear()
        elif damage == "source_collision":
            out.sources[source_id] = out.sources[source_id].model_copy(
                update={"title": "Counterfeit core"}
            )
        elif damage == "chunk_key":
            key = next(iter(out.chunks))
            out.chunks[key] = out.chunks[key].model_copy(
                update={"chunk_id": "wrong-map-id"}
            )
        elif damage in ("record_index", "record_as_of"):
            args = deepcopy(record.arguments_without_secrets)
            args["index_version" if damage == "record_index" else "as_of"] = (
                "stale-index" if damage == "record_index" else "2026-08-01"
            )
            out.records[record_index] = record.model_copy(
                update={"arguments_without_secrets": args}
            )
        elif damage == "record_schema":
            out.records[record_index] = record.model_copy(
                update={"schema_version": "stale-schema"}
            )
        elif damage == "record_collision":
            out.records.append(record.model_copy(update={"query": "counterfeit"}))
        elif damage == "unreferenced_bad_record":
            out.records.append(
                record.model_copy(
                    update={
                        "retrieval_id": "unreferenced",
                        "arguments_without_secrets": {"index_version": "stale-index"},
                    }
                )
            )
        elif damage == "evidence_candidate":
            out.evidence[eid] = ev.model_copy(update={"candidate_id": "co-other"})
        elif damage == "evidence_schema":
            out.evidence[eid] = ev.model_copy(update={"schema_version": "stale-schema"})
        elif damage == "industry_not_allowlisted":
            out.evidence[eid] = ev.model_copy(
                update={"scope": "industry", "candidate_id": None}
            )
        elif damage == "future_event":
            out.evidence[eid] = ev.model_copy(update={"event_date": date(2026, 9, 2)})
        elif damage == "future_source":
            out.sources[source_id] = out.sources[source_id].model_copy(
                update={"published_at": date(2026, 9, 2)}
            )
        elif damage == "unlinked_evidence":
            out.records[record_index] = record.model_copy(update={"evidence_ids": []})
        elif damage == "call_missing_record":
            from dataclasses import replace

            out.calls[0] = replace(out.calls[0], retrieval_ids=("absent-record",))
        elif damage == "gap_candidate":
            out.gaps[0] = out.gaps[0].model_copy(update={"candidate_id": "co-other"})
        elif damage == "invalid_status":
            out.status = "invented-success"
        elif damage == "negative_skipped":
            out.skipped = -1
        return out

    c["producer"].run = corrupt
    result = execute(c, engine)
    assert result.outcomes["co-0"].status == "failed"
    assert result.errors and c["seen"] == []
    assert not result.scores and not result.decisions
    owned = result.research_artifacts["co-0"]
    assert owned["state"]["evidence_revisions"]["co-0"] == (2 if additional else 1)
    assert "ev-co-0" in owned["state"]["evidence"]
    assert len(owned["state"]["evidence"]) == (5 if additional else 1)
    assert not owned["state"]["snapshots"]
    assert owned["batches"][-1]["admission"] == "rejected_input"
    assert result.candidate_index == 1


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize(
    "damage",
    [
        "all_errors_missing",
        "one_error_missing",
        "dangling_record_error",
        "error_code_mismatch",
        "error_conflict",
    ],
)
def test_broken_original_error_carriers_fail_closed(engine, damage):
    from tests.fixtures.evidence_research import StubWeb, llm_error

    from skala_rag.agents.evidence_research import WebChannel
    from skala_rag.contracts.error_codes import ErrorCode

    c = setup_case()
    c["producer"]._web = (
        WebChannel(
            "fixture-optional",
            "web",
            False,
            StubWeb(
                {},
                clock=FakeClock(utc(2026, 9, 1)),
                error=ErrorCode.TOOL_UNAVAILABLE,
                name="fixture-optional",
            ),
        ),
    )
    c["producer"]._llm = LineLLM(error=llm_error())
    # Let first RAG extraction succeed, optional tool fail, then next extraction fail.
    llm = LineLLM()
    original_generate = llm.generate
    calls = []

    def generate(**kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise llm_error()
        return original_generate(**kwargs)

    llm.generate = generate
    c["producer"]._llm = llm
    original = c["producer"].run
    supplied = []

    def corrupt(candidate, gaps, budget):
        out = original(candidate, gaps, budget)
        assert len(out.errors) == 2
        if damage == "all_errors_missing":
            out.errors = []
        elif damage == "one_error_missing":
            out.errors = out.errors[1:]
        elif damage == "dangling_record_error":
            i = next(i for i, r in enumerate(out.records) if r.error_id)
            out.records[i] = out.records[i].model_copy(update={"error_id": None})
        elif damage == "error_code_mismatch":
            out.errors[0] = out.errors[0].model_copy(
                update={"error_code": "TOOL_TIMEOUT"}
            )
        else:
            out.errors.append(
                out.errors[0].model_copy(update={"message_redacted": "counterfeit"})
            )
        supplied.append(deepcopy(out))
        return out

    c["producer"].run = corrupt
    result = execute(c, engine)
    assert result.outcomes["co-0"].status == "failed"
    assert c["seen"] == [] and not result.scores
    assert result.errors and result.errors[-1].error_code == "UPSTREAM_INVALID"
    assert not result.research_artifacts["co-0"]["state"]["snapshots"]
    assert (
        result.research_artifacts["co-0"]["batches"][-1]["admission"]
        == "rejected_input"
    )


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("optional_error", [False, True])
def test_optional_empty_or_unavailable_retains_full_history_without_candidate_failure(
    engine, optional_error
):
    from dataclasses import replace

    from tests.fixtures.evidence_research import StubWeb

    from skala_rag.agents.evidence_research import WebChannel
    from skala_rag.contracts.error_codes import ErrorCode

    c = setup_case()
    c["producer"]._web = (
        WebChannel(
            "fixture-optional",
            "web",
            False,
            StubWeb(
                {},
                clock=FakeClock(utc(2026, 9, 1)),
                error=ErrorCode.TOOL_UNAVAILABLE if optional_error else None,
                name="fixture-optional",
            ),
        ),
    )
    old = c["stages"].evidence_research
    c["stages"] = replace(
        c["stages"],
        evidence_research=replace(
            old, budget=old.budget.model_copy(update={"max_calls": 46})
        ),
    )
    result = execute(c, engine)
    assert result.outcomes["co-0"].status == "recommend" and len(result.scores) == 1
    assert result.research_retry_count == {"co-0": 0}
    owned = result.research_artifacts["co-0"]
    batch = owned["batches"][0]
    assert batch["status"] == "ok" and batch["admission"] == "admitted"
    assert len(batch["records"]) == 46
    assert len(owned["state"]["retrieval_history"]) == 47
    optional = [r for r in batch["records"] if r["tool_name"] == "fixture-optional"]
    assert len(optional) == 23
    assert {r["status"] for r in optional} == (
        {"unavailable"} if optional_error else {"empty"}
    )
    assert {e.error_id for e in result.errors} == {
        e["error_id"] for e in batch["errors"]
    }
    assert {r["error_id"] for r in optional if r["error_id"]} == {
        e.error_id for e in result.errors
    }
    assert not (
        set(r["retrieval_id"] for r in optional)
        & set(c["seen"][0][1].retrieval_records)
    )


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
def test_empty_additional_batches_consume_two_attempts_keep_revision_and_frozen_inputs(
    engine,
):
    from skala_rag.prompts.evidence_extraction import ExtractionOutput

    c = setup_case(gap=True)
    original = c["producer"].run
    requested = []

    def empty_extra(candidate, gaps, budget):
        requested.append((bool(gaps), deepcopy(budget)))
        if gaps:
            c["producer"]._llm.generate = lambda **kwargs: ExtractionOutput(claims=[])
        return original(candidate, gaps, budget)

    c["producer"].run = empty_extra
    result = execute(c, engine)
    assert [initial for initial, _ in requested] == [False, True, True]
    assert (
        requested[0][1]
        == requested[1][1]
        == requested[2][1]
        == c["stages"].evidence_research.budget
    )
    assert result.research_retry_count == {"co-0": 2}
    assert result.research_stop_reasons == {"co-0": "exhausted"}
    assert all(g.status == "exhausted" for g in result.research_gaps["co-0"])
    owned = result.research_artifacts["co-0"]
    assert [b["status"] for b in owned["batches"]] == ["ok", "empty", "empty"]
    assert owned["state"]["evidence_revisions"]["co-0"] == 2
    assert owned["state"]["research_retry_count"]["co-0"] == 2
    assert len(owned["state"]["snapshots"]) == 1
    assert all(s.evidence_revision == 2 for _, s in c["seen"])
    assert (
        len(c["backend"].calls) <= 42
    )  # transport budget is shared, never replenished


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
def test_zero_initial_budget_preserves_original_failure_no_backend_or_evaluation(
    engine,
):
    from dataclasses import replace

    c = setup_case()
    binding = c["stages"].evidence_research
    zero = binding.budget.model_copy(update={"max_calls": 0})
    c["stages"] = replace(c["stages"], evidence_research=replace(binding, budget=zero))
    result = execute(c, engine)
    assert result.candidate_index == 1 and result.outcomes["co-0"].status == "failed"
    assert [e.error_code for e in result.errors] == ["BUDGET_EXHAUSTED"]
    assert not c["backend"].calls and not c["seen"]
    assert result.research_artifacts["co-0"]["state"]["evidence_revisions"]["co-0"] == 1
    assert binding.budget.max_calls == 23


def test_outer_intermediate_artifacts_are_json_owned_not_producer_or_typed_seed():
    import json

    from skala_rag.graph.candidate_workflow_v3 import build_candidate_workflow_v3

    c = setup_case(gap=True)
    graph = build_candidate_workflow_v3(
        c["stages"], c["callbacks"], **c["options"]
    ).compile()
    updates = list(graph.stream({}, stream_mode="updates"))
    for update in updates:
        for payload in update.values():
            if "data" in payload:
                encoded = json.dumps(payload["data"], allow_nan=False)
                assert "EvidenceResearch" not in encoded
    assert any("collect" in update for update in updates)
    assert any("additional_research" in update for update in updates)


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
def test_detached_producer_inputs_and_branches_do_not_contaminate_next_candidate(
    engine,
):
    import json

    c = setup_case(count=2)
    original = c["producer"].run
    emitted = []

    def mutate_previous(candidate, gaps, budget):
        if emitted:
            emitted[0].evidence.clear()
            emitted[0].records.clear()
            emitted[0].sources.clear()
        outcome = original(candidate, gaps, budget)
        emitted.append(outcome)
        return outcome

    c["producer"].run = mutate_previous
    original_branch = c["callbacks"]["moat"]

    def mutate_copy(s):
        result = original_branch(s)
        s.evidence.clear()
        s.sources.clear()
        s.chunks.clear()
        return result

    c["callbacks"]["moat"] = mutate_copy
    result = execute(c, engine)
    assert result.candidate_index == 2 and len(result.scores) == 2
    assert result.research_retry_count == {"co-0": 0, "co-1": 0}
    for cid, owned in result.research_artifacts.items():
        assert len(owned["batches"][0]["evidence"]) == 23
        assert len(owned["state"]["snapshots"]) == 1
        assert all(
            e["candidate_id"] == cid for e in owned["state"]["evidence"].values()
        )
        assert all(
            e["candidate_id"] == cid for e in owned["batches"][0]["evidence"].values()
        )
    for seed in c["seeds"].values():
        seed.evidence.clear()
    assert "ev-co-0" in result.research_artifacts["co-0"]["state"]["evidence"]
    json.dumps(result.research_artifacts, allow_nan=False)
    assert len(c["seen"]) == 10


def test_new_lane_oracle_outer_final_payload_and_order_parity(monkeypatch):
    from dataclasses import asdict
    from itertools import count
    from uuid import UUID

    import skala_rag.rag.adapter as adapter
    import skala_rag.tools.runtime as runtime

    a, b = setup_case(gap=True, count=2), setup_case(gap=True, count=2)
    sequence = count(1)
    monkeypatch.setattr(runtime, "uuid4", lambda: UUID(int=next(sequence)))
    monkeypatch.setattr(adapter, "uuid4", lambda: UUID(int=next(sequence)))
    oracle_result = execute(a, oracle.run_candidates_v3)
    sequence = count(1)
    outer_result = execute(b, run_candidate_workflow_v3)
    assert asdict(oracle_result) == asdict(outer_result)
    # Durations are measurements, not parity identities.
    assert [(t["step"], t["candidate_id"]) for t in a["trace"]] == [
        (t["step"], t["candidate_id"]) for t in b["trace"]
    ]


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize(
    "damage",
    [
        "stale_revision",
        "wrong_schema",
        "wrong_run",
        "missing_eligibility_evidence",
        "seed_record_index",
        "unadmitted_unused_source",
    ],
)
def test_company_seed_and_original_eligibility_closure_fail_before_collection(
    engine, damage
):
    from dataclasses import replace

    c = setup_case()
    seed = c["seeds"]["co-0"]
    eligibility = c["stages"].eligibility({"candidate_id": "co-0"}, seed)
    if damage == "stale_revision":
        seed = replace(seed, evidence_revision=0)
    elif damage == "wrong_schema":
        seed = replace(seed, schema_version="stale")
    elif damage == "wrong_run":
        seed = replace(seed, run_id="other-run")
    elif damage == "missing_eligibility_evidence":
        seed = replace(seed, evidence={})
    elif damage == "seed_record_index":
        record = seed.records[0]
        record = dict(
            record, arguments_without_secrets={"index_version": "wrong-index"}
        )
        seed = replace(seed, records=[record])
    else:
        source = next(iter(seed.sources.values()))
        seed = replace(
            seed,
            sources=dict(
                seed.sources,
                forbidden=source.model_copy(update={"source_id": "forbidden"}),
            ),
        )
    c["stages"] = replace(
        c["stages"], research=lambda c: seed, eligibility=lambda *args: eligibility
    )
    result = execute(c, engine)
    assert result.candidate_index == 1 and result.outcomes["co-0"].status == "failed"
    assert result.errors and not result.scores
    assert not c["backend"].calls and not c["seen"]


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
def test_empty_initial_outcome_does_not_raise_revision_or_create_facts(engine):
    c = setup_case()
    c["producer"]._plan = lambda candidate: []
    result = execute(c, engine)
    assert len(result.scores) == 1 and result.research_retry_count == {"co-0": 1}
    owned = result.research_artifacts["co-0"]
    assert [b["status"] for b in owned["batches"]] == ["empty", "ok"]
    assert not owned["batches"][0]["evidence"] and not owned["batches"][0]["calls"]
    assert owned["state"]["eligibility_results"]["co-0"]["evidence_revision"] == 1
    assert owned["state"]["evidence_revisions"]["co-0"] == 2
    assert len(c["backend"].calls) == 23


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
def test_provenance_only_change_admission_advances_revision_once(engine):
    c = setup_case(gap=True)
    original = c["producer"].run

    def replay_initial_criterion(candidate, gaps, budget):
        if gaps:
            # Real research; synthetic backend repeats an existing criterion.
            gaps = [
                gaps[0].model_copy(
                    update={
                        "suggested_queries": [c["initial"][0]],
                        "target_evidence_types": ["claim"],
                    }
                )
            ]
        return original(candidate, gaps, budget)

    c["producer"].run = replay_initial_criterion
    result = execute(c, engine)
    owned = result.research_artifacts["co-0"]
    assert result.research_retry_count == {"co-0": 2}
    assert owned["state"]["evidence_revisions"]["co-0"] == 4
    assert len(owned["state"]["evidence"]) == 5
    repeated = next(
        e for e in owned["state"]["evidence"].values() if len(e["provenance"]) == 3
    )
    assert len({p["retrieval_id"] for p in repeated["provenance"]}) == 3
    assert all(s.evidence_revision == 4 for _, s in c["seen"])


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize(
    "damage",
    [
        "run_input_schema",
        "negative_budget",
        "budget_schema",
        "run_input_live",
        "wrong_index",
        "wrong_run",
        "unmatched_allowlist",
    ],
)
def test_explicit_binding_is_validated_before_any_stage(engine, damage):
    from dataclasses import replace

    c = setup_case()
    binding = c["stages"].evidence_research
    if damage == "run_input_schema":
        binding = replace(
            binding,
            run_input=binding.run_input.model_copy(update={"schema_version": "stale"}),
        )
    elif damage == "negative_budget":
        binding = replace(
            binding, budget=binding.budget.model_copy(update={"max_calls": -1})
        )
    elif damage == "budget_schema":
        binding = replace(
            binding,
            budget=binding.budget.model_copy(update={"schema_version": "stale"}),
        )
    elif damage == "run_input_live":
        binding = replace(
            binding,
            run_input=binding.run_input.model_copy(update={"execution_mode": "live"}),
        )
    elif damage == "wrong_index":
        binding = replace(binding, index_version="wrong-index")
    elif damage == "wrong_run":
        binding = replace(binding, run_id="other-run")
    else:
        binding = replace(binding, allowed_source_ids=frozenset({"other-source"}))
    c["stages"] = replace(c["stages"], evidence_research=binding)
    with pytest.raises(ValueError):
        execute(c, engine)
    assert not c["backend"].calls and not c["seen"]


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
def test_partial_failed_candidate_archives_once_then_next_candidate_can_succeed(engine):
    from tests.fixtures.evidence_research import StubWeb

    from skala_rag.agents.evidence_research import WebChannel
    from skala_rag.contracts.error_codes import ErrorCode

    c = setup_case(count=2)
    original = c["producer"].run
    first_errors = []

    def only_first_fails(candidate, gaps, budget):
        c["producer"]._web = (
            (
                WebChannel(
                    "fixture-required",
                    "web",
                    True,
                    StubWeb(
                        {},
                        clock=FakeClock(utc(2026, 9, 1)),
                        error=ErrorCode.TOOL_UNAVAILABLE,
                        name="fixture-required",
                    ),
                ),
            )
            if candidate.candidate_id == "co-0"
            else ()
        )
        out = original(candidate, gaps, budget)
        if candidate.candidate_id == "co-0":
            first_errors.extend(out.errors)
        return out

    c["producer"].run = only_first_fails
    result = execute(c, engine)
    assert result.candidate_index == 2
    assert result.outcomes["co-0"].status == "failed"
    assert result.outcomes["co-1"].status == "recommend"
    assert set(result.scores) == {"co-1"}
    assert [e.model_dump(mode="json") for e in result.errors] == [
        e.model_dump(mode="json") for e in first_errors
    ]
    assert {s.candidate_id for _, s in c["seen"]} == {"co-1"}
    assert not result.research_artifacts["co-0"]["state"]["snapshots"]
    assert len(result.research_artifacts["co-1"]["state"]["snapshots"]) == 1
    assert result.research_retry_count == {"co-0": 0, "co-1": 0}
    for cid in ("co-0", "co-1"):
        steps = [t["step"] for t in c["trace"] if t["candidate_id"] == cid]
        assert steps.count("archive") == steps.count("advance") == 1


def _generation_case(engine, phase, mutate, *, prepare=None):
    """Exercise the same boundary through seed, collect, or actual generated gaps."""
    from dataclasses import replace

    c = setup_case(gap=phase == "additional")
    if prepare is not None:
        prepare(c)
    supplied = []
    if phase == "seed":
        seed = c["seeds"]["co-0"]
        eligibility = c["stages"].eligibility(
            c["stages"].discover()[0].model_dump(mode="json"), seed
        )
        seed = replace(seed, **mutate(deepcopy(seed)))
        supplied.append(seed)
        c["stages"] = replace(
            c["stages"], research=lambda _: seed, eligibility=lambda *args: eligibility
        )
    else:
        original = c["producer"].run

        def corrupt(candidate, gaps, budget):
            out = original(candidate, gaps, budget)
            if bool(gaps) == (phase == "additional"):
                for name, value in mutate(out).items():
                    setattr(out, name, value)
                supplied.append(deepcopy(out))
            return out

        c["producer"].run = corrupt
    return c, supplied, execute(c, engine)


def _artifact_json(value):
    return deepcopy(
        value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    )


def _assert_generation_refused(c, result, phase):
    assert result.outcomes["co-0"].status == "failed"
    assert result.errors[-1].error_code == "UPSTREAM_INVALID"
    assert c["seen"] == [] and not result.scores and not result.decisions
    assert result.candidate_index == 1
    steps = [t["step"] for t in c["trace"] if t["candidate_id"] == "co-0"]
    assert steps.count("archive") == steps.count("advance") == 1
    if phase == "seed":
        assert not c["backend"].calls
    else:
        owned = result.research_artifacts["co-0"]
        assert owned["batches"][-1]["admission"] == "rejected_input"
        assert owned["state"]["evidence_revisions"]["co-0"] == (
            2 if phase == "additional" else 1
        )
        assert not owned["state"]["snapshots"]
        assert result.research_retry_count == {"co-0": int(phase == "additional")}


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("phase", ["seed", "initial", "additional"])
@pytest.mark.parametrize(
    "damage", ["provenance_schema", "cost_schema", "unused_chunk_schema"]
)
def test_generation_schema_mismatch_is_refused_before_evaluation(engine, phase, damage):
    def mutate(artifacts):
        if damage == "provenance_schema":
            evidence = deepcopy(artifacts.evidence)
            eid = next(iter(evidence))
            item = _artifact_json(evidence[eid])
            item["provenance"][0]["schema_version"] = "stale-schema"
            evidence[eid] = item
            return {"evidence": evidence}
        if damage == "cost_schema":
            records = [_artifact_json(r) for r in artifacts.records]
            records[0]["cost"] = dict(
                schema_version="stale-schema",
                value=0,
                currency="USD",
                unit="USD",
                as_of="2026-09-01",
            )
            return {"records": records}
        chunks = deepcopy(artifacts.chunks)
        source = Source.model_validate(
            _artifact_json(next(iter(artifacts.sources.values()))),
            context={"execution_mode": "fixture"},
        )
        unused = chunk(
            "unused-stale-chunk",
            source,
            "Unused synthetic input",
            candidate_ids=["co-0"],
            corpus=CORPUS,
        ).model_copy(update={"schema_version": "stale-schema"})
        chunks[unused.chunk_id] = unused
        return {"chunks": chunks}

    c, supplied, result = _generation_case(engine, phase, mutate)
    assert len(supplied) == 1
    _assert_generation_refused(c, result, phase)
    if phase != "seed":
        batch = result.research_artifacts["co-0"]["batches"][-1]
        if damage == "provenance_schema":
            assert (
                next(iter(batch["evidence"].values()))["provenance"][0][
                    "schema_version"
                ]
                == "stale-schema"
            )
        elif damage == "cost_schema":
            assert batch["records"][0]["cost"]["schema_version"] == "stale-schema"
        else:
            assert (
                batch["chunks"]["unused-stale-chunk"]["schema_version"]
                == "stale-schema"
            )


def _declare_record_metadata(artifacts, field, value, unused):
    records = [_artifact_json(r) for r in artifacts.records]
    record = deepcopy(records[0])
    if unused:
        record["retrieval_id"] = "unused-generation-record"
        record["source_ids"] = []
        record["chunk_ids"] = []
        record["evidence_ids"] = []
        records.append(record)
    else:
        records[0] = record
    record["arguments_without_secrets"][field] = deepcopy(value)
    return {"records": records}


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("phase", ["seed", "initial", "additional"])
@pytest.mark.parametrize("unused", [False, True])
@pytest.mark.parametrize(
    "declared", ["live", None, False, {}, ["fixture"], "", "fixture "]
)
def test_generation_declared_record_mode_must_match_fixture(
    engine, phase, unused, declared
):
    def mutate(artifacts):
        return _declare_record_metadata(artifacts, "execution_mode", declared, unused)

    c, supplied, result = _generation_case(engine, phase, mutate)
    assert len(supplied) == 1
    _assert_generation_refused(c, result, phase)
    if phase != "seed":
        records = result.research_artifacts["co-0"]["batches"][-1]["records"]
        assert (
            records[-1 if unused else 0]["arguments_without_secrets"]["execution_mode"]
            == declared
        )


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("phase", ["seed", "initial", "additional"])
@pytest.mark.parametrize("unused", [False, True])
@pytest.mark.parametrize("declared", ["foreign-index", None, False, {}, ["index"], ""])
def test_generation_declared_index_identity_must_match_pinned_retriever(
    engine, phase, unused, declared
):
    def mutate(artifacts):
        return _declare_record_metadata(artifacts, "index_identity", declared, unused)

    c, supplied, result = _generation_case(engine, phase, mutate)
    assert len(supplied) == 1
    _assert_generation_refused(c, result, phase)
    if phase != "seed":
        records = result.research_artifacts["co-0"]["batches"][-1]["records"]
        assert (
            records[-1 if unused else 0]["arguments_without_secrets"]["index_identity"]
            == declared
        )


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("phase", ["seed", "initial", "additional"])
@pytest.mark.parametrize("unverifiable", ["opaque_retriever", "foreign_tool"])
def test_generation_declared_but_unverifiable_identity_is_not_admitted(
    engine, phase, unverifiable
):
    from dataclasses import replace

    c = setup_case(gap=phase == "additional")
    retrieve = c["producer"]._retrieve
    identity = retrieve._identity
    if unverifiable == "opaque_retriever":
        # A callable with no inspectable pinned index; no producer/index mutation.
        c["producer"]._retrieve = lambda request: retrieve(request)

    def mutate(artifacts):
        fields = _declare_record_metadata(artifacts, "index_identity", identity, False)
        fields["records"][0]["tool_name"] = (
            "foreign-tool" if unverifiable == "foreign_tool" else retrieve._tool_name
        )
        return fields

    if phase == "seed":
        seed = c["seeds"]["co-0"]
        eligibility = c["stages"].eligibility(
            c["stages"].discover()[0].model_dump(mode="json"), seed
        )
        seed = replace(seed, **mutate(seed))
        c["stages"] = replace(
            c["stages"], research=lambda _: seed, eligibility=lambda *args: eligibility
        )
    else:
        original = c["producer"].run

        def corrupt(candidate, gaps, budget):
            out = original(candidate, gaps, budget)
            if bool(gaps) == (phase == "additional"):
                out.records = mutate(out)["records"]
            # The opaque retriever emits declarations on every record. Refusal can
            # correctly occur in the initial batch before any additional request.
            return out

        c["producer"].run = corrupt
    result = execute(c, engine)
    refused_phase = (
        "initial"
        if phase == "additional" and unverifiable == "opaque_retriever"
        else phase
    )
    _assert_generation_refused(c, result, refused_phase)


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("phase", ["seed", "initial", "additional"])
@pytest.mark.parametrize("metadata", ["declared", "omitted", "opaque_omitted"])
def test_generation_valid_or_absent_optional_metadata_keeps_real_freeze(
    engine, phase, metadata
):
    expected = {}

    def prepare(c):
        retrieve = c["producer"]._retrieve
        expected.update(identity=retrieve._identity, tool=retrieve._tool_name)
        if metadata == "opaque_omitted":

            def without_optional_metadata(request):
                result = retrieve(request)
                for record in result.retrieval_records:
                    record.arguments_without_secrets.pop("execution_mode", None)
                    record.arguments_without_secrets.pop("index_identity", None)
                return result

            c["producer"]._retrieve = without_optional_metadata

    def mutate(artifacts):
        records = [_artifact_json(r) for r in artifacts.records]
        for record in records:
            args = record["arguments_without_secrets"]
            if metadata == "declared":
                record["tool_name"] = expected["tool"]
                args.update(
                    execution_mode="fixture", index_identity=expected["identity"]
                )
            else:
                args.pop("execution_mode", None)
                args.pop("index_identity", None)
            # These JSON keys are vendor data, not Contract DTO generations.
            args["schema_version"] = "opaque-vendor-schema"
            args["opaque"] = {"schema_version": "opaque-nested-schema"}
            record["cost"] = dict(
                schema_version=SCHEMA,
                value=0,
                currency="USD",
                unit="USD",
                as_of="2026-09-01",
            )
        return {"records": records}

    c, supplied, result = _generation_case(engine, phase, mutate, prepare=prepare)
    assert len(supplied) == 1
    assert not result.errors and result.outcomes["co-0"].status == "recommend"
    assert len(c["seen"]) == 5 and len(c["receipts"]) == 1 and len(result.scores) == 1
    owned = result.research_artifacts["co-0"]
    frozen = c["seen"][0][1]
    assert owned["state"]["snapshots"][frozen.snapshot_id] == frozen.model_dump(
        mode="json"
    )
    records = [
        r
        for r in owned["state"]["retrieval_history"]
        if r["arguments_without_secrets"].get("schema_version")
        == "opaque-vendor-schema"
    ]
    assert records
    assert all(r["cost"]["schema_version"] == SCHEMA for r in records)
    assert all(
        r["arguments_without_secrets"]["opaque"]["schema_version"]
        == "opaque-nested-schema"
        for r in records
    )
    if metadata != "declared":
        assert all(
            "execution_mode" not in r["arguments_without_secrets"]
            and "index_identity" not in r["arguments_without_secrets"]
            for r in records
        )
    assert frozen.evidence_revision == (3 if phase == "additional" else 2)
    assert result.research_retry_count == {"co-0": int(phase == "additional")}


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("phase", ["initial", "additional"])
@pytest.mark.parametrize("status", ["ok", "empty"])
@pytest.mark.parametrize(
    "damage",
    [
        "clear_calls",
        "drop_owner",
        "duplicate_owner",
        "terminal_status",
        "no_call_records",
    ],
)
def test_successful_outcome_requires_complete_unique_record_call_ownership(
    engine, phase, status, damage
):
    from dataclasses import replace

    from skala_rag.prompts.evidence_extraction import ExtractionOutput

    def prepare(c):
        if status == "empty":
            original = c["producer"].run

            def zero_claims(candidate, gaps, budget):
                if bool(gaps) == (phase == "additional"):
                    c["producer"]._llm.generate = lambda **kwargs: ExtractionOutput(
                        claims=[]
                    )
                return original(candidate, gaps, budget)

            c["producer"].run = zero_claims

    def mutate(out):
        assert out.status == status and out.calls and out.records
        calls = list(out.calls)
        if damage == "clear_calls":
            calls.clear()
        elif damage == "drop_owner":
            calls.pop(0)
        elif damage == "duplicate_owner":
            calls[0] = replace(
                calls[0],
                retrieval_ids=(*calls[0].retrieval_ids, *calls[0].retrieval_ids),
            )
        elif damage == "terminal_status":
            calls[0] = replace(calls[0], status="empty")
        else:
            calls[0] = replace(calls[0], retrieval_ids=())
        return {"calls": calls}

    c, supplied, result = _generation_case(engine, phase, mutate, prepare=prepare)
    assert len(supplied) == 1
    _assert_generation_refused(c, result, phase)
    batch = result.research_artifacts["co-0"]["batches"][-1]
    assert batch["status"] == status and batch["records"]
    assert bool(batch["evidence"]) == (status == "ok")
    if damage == "clear_calls":
        assert batch["calls"] == []


def _correction_fields(artifacts, *, seed=False, derived=False, damage=None):
    evidence = {k: _artifact_json(v) for k, v in artifacts.evidence.items()}
    records = [_artifact_json(r) for r in artifacts.records]
    old_id = next(iter(evidence))
    old = deepcopy(evidence[old_id])
    if seed:
        # Original Eligibility Evidence remains active and unchanged.
        old_id = "ev-history-211"
        old["evidence_id"] = old_id
        old["claim"] += " synthetic non-Eligibility history"
        evidence[old_id] = old
    corrected = deepcopy(old)
    corrected.update(
        evidence_id="ev-correction-211",
        claim=old["claim"] + " corrected",
        supersedes=old_id,
    )
    evidence[corrected["evidence_id"]] = corrected
    added = [old_id, corrected["evidence_id"]]
    if derived:
        for i in range(2):
            item = deepcopy(old)
            item.update(
                evidence_id=f"ev-derived-211-{i}",
                evidence_kind="derived",
                claim=f"Synthetic derived history {i}",
                derivation="Synthetic fixture dependency",
                supporting_evidence_ids=[old_id if i == 0 else "ev-derived-211-0"],
            )
            evidence[item["evidence_id"]] = item
            added.append(item["evidence_id"])
    refs = {p["retrieval_id"] for p in old["provenance"]}
    for record in records:
        if record["retrieval_id"] in refs:
            record["evidence_ids"] = list(
                dict.fromkeys([*record["evidence_ids"], *added])
            )
    if damage == "future_event":
        evidence[old_id]["event_date"] = "2026-09-02"
    elif damage == "future_value_as_of":
        evidence[old_id].update(
            value=0, unit="USD", currency="USD", value_as_of="2026-09-02"
        )
    elif damage == "inactive_missing_record":
        evidence[old_id]["provenance"][0]["retrieval_id"] = "missing-inactive-record"
    elif damage in ("inactive_unallowed_source", "inactive_future_source"):
        sources = {k: _artifact_json(v) for k, v in artifacts.sources.items()}
        source = deepcopy(next(iter(sources.values())))
        source["source_id"] = "inactive-history-source"
        if damage == "inactive_future_source":
            source["published_at"] = "2026-09-02"
        sources[source["source_id"]] = source
        evidence[old_id]["source_id"] = source["source_id"]
        return {"evidence": evidence, "records": records, "sources": sources}
    elif damage == "inactive_missing_chunk":
        # Seed is manual, so use a rag path to a missing Chunk for this negative.
        evidence[old_id]["provenance"][0].update(
            method="rag", chunk_id="missing-inactive-chunk"
        )
    return {"evidence": evidence, "records": records}


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("phase", ["seed", "initial", "additional"])
@pytest.mark.parametrize("derived", [False, True])
def test_valid_correction_retains_history_but_coverage_freeze_evaluate_only_active(
    engine, phase, derived
):
    c, supplied, result = _generation_case(
        engine,
        phase,
        lambda artifacts: _correction_fields(
            artifacts, seed=phase == "seed", derived=derived
        ),
    )
    assert len(supplied) == 1
    assert not result.errors and result.outcomes["co-0"].status == "recommend"
    assert len(c["seen"]) == 5 and len(result.scores) == 1
    owned = result.research_artifacts["co-0"]
    historical = owned["state"]["evidence"]
    corrected = historical["ev-correction-211"]
    old_id = corrected["supersedes"]
    inactive = {old_id, *(["ev-derived-211-0", "ev-derived-211-1"] if derived else [])}
    assert inactive <= historical.keys()
    frozen = c["seen"][0][1]
    assert set(frozen.evidence) == set(historical) - inactive
    assert historical["ev-correction-211"] == frozen.evidence[
        "ev-correction-211"
    ].model_dump(mode="json")
    assert "ev-co-0" in frozen.evidence
    assert all(s == frozen for _, s in c["seen"])
    assert owned["state"]["snapshots"][frozen.snapshot_id] == frozen.model_dump(
        mode="json"
    )
    coverage_events = [t for t in c["trace"] if t["step"] == "coverage"]
    assert coverage_events
    assert all(not (inactive & set(t["input_ids"])) for t in coverage_events[-1:])
    assert frozen.evidence_revision == (3 if phase == "additional" else 2)
    assert result.research_retry_count == {"co-0": int(phase == "additional")}
    if phase != "seed":
        batch = owned["batches"][-1]
        assert batch["admission"] == "admitted" and inactive <= batch["evidence"].keys()


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("phase", ["seed", "initial", "additional"])
@pytest.mark.parametrize(
    "damage",
    [
        "future_event",
        "future_value_as_of",
        "inactive_missing_record",
        "inactive_missing_chunk",
        "inactive_unallowed_source",
        "inactive_future_source",
    ],
)
def test_inactive_correction_history_still_requires_cutoff_and_provenance(
    engine, phase, damage
):
    c, supplied, result = _generation_case(
        engine,
        phase,
        lambda artifacts: _correction_fields(
            artifacts, seed=phase == "seed", damage=damage
        ),
    )
    assert len(supplied) == 1
    _assert_generation_refused(c, result, phase)


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("empty", [False, True])
def test_optional_multi_attempt_call_keeps_every_original_record_error_and_one_owner(
    engine, empty
):
    from dataclasses import replace

    from tests.fixtures.evidence_research import StubWeb

    from skala_rag.agents.evidence_research import WebChannel, WebResult
    from skala_rag.contracts.error_codes import ErrorCode
    from skala_rag.contracts.retrieval import RetrievalRecord
    from skala_rag.prompts.evidence_extraction import ExtractionOutput

    c = setup_case()
    clock = FakeClock(utc(2026, 9, 1))
    first = StubWeb(
        {}, clock=clock, error=ErrorCode.TOOL_TIMEOUT, name="fixture-first-attempt"
    )
    terminal = StubWeb(
        {},
        clock=clock,
        error=ErrorCode.TOOL_UNAVAILABLE,
        name="fixture-terminal-attempt",
    )
    emitted = []

    def multi_attempt(candidate, query, *, as_of):
        # Synthetic retry history with both original errors supplied, no HTTP or
        # extra controller retry. A call owns multiple records, not one per call.
        errors = (
            *first(candidate, query, as_of=as_of).errors,
            *terminal(candidate, query, as_of=as_of).errors,
        )
        records = tuple(
            RetrievalRecord(
                schema_version=SCHEMA,
                retrieval_id=f"record-{e.error_id}",
                run_id=e.run_id,
                candidate_id=e.candidate_id,
                tool_name="fixture-multi-attempt",
                query=None,
                arguments_without_secrets={},
                started_at=e.timestamp,
                finished_at=e.timestamp,
                status="failed" if e.error_code == "TOOL_TIMEOUT" else "unavailable",
                source_ids=[],
                chunk_ids=[],
                evidence_ids=[],
                error_id=e.error_id,
                cache_hit=False,
            )
            for e in errors
        )
        emitted.extend(errors)
        return WebResult(status="unavailable", records=records, errors=errors)

    c["producer"]._web = (
        WebChannel("fixture-multi-attempt", "web", False, multi_attempt),
    )
    if empty:
        original_run = c["producer"].run

        def empty_initial(candidate, gaps, budget):
            c["producer"]._llm = LineLLM()
            if not gaps:
                c["producer"]._llm.generate = lambda **kwargs: ExtractionOutput(
                    claims=[]
                )
            return original_run(candidate, gaps, budget)

        c["producer"].run = empty_initial
    binding = c["stages"].evidence_research
    c["stages"] = replace(
        c["stages"],
        evidence_research=replace(
            binding, budget=binding.budget.model_copy(update={"max_calls": 46})
        ),
    )
    result = execute(c, engine)
    assert result.outcomes["co-0"].status == "recommend" and len(c["seen"]) == 5
    owned = result.research_artifacts["co-0"]
    originals = {e.error_id: e.model_dump(mode="json") for e in emitted}
    assert {e.error_id: e.model_dump(mode="json") for e in result.errors} == originals
    for batch in owned["batches"]:
        assert batch["admission"] == "admitted"
        ids = [rid for call in batch["calls"] for rid in call["retrieval_ids"]]
        assert len(ids) == len(set(ids)) == len(batch["records"])
        assert set(ids) == {r["retrieval_id"] for r in batch["records"]}
        optional = [
            call for call in batch["calls"] if call["tool"] == "fixture-multi-attempt"
        ]
        assert optional and all(len(call["retrieval_ids"]) == 2 for call in optional)
        assert all(originals[e["error_id"]] == e for e in batch["errors"])
    assert owned["batches"][0]["status"] == ("empty" if empty else "ok")


def _runtime_retry_case(*, additional=False, terminal=False):
    """Only the backend fails; real adapter/runtime/producer keep their contracts."""
    from skala_rag.contracts.error_codes import ErrorCode
    from skala_rag.tools.runtime import TransportFailure

    c = setup_case(gap=additional)
    retrieve = c["producer"]._retrieve
    retrieve._budget = retrieve._budget.model_copy(
        update={"max_calls": 2, "max_retries": 1}
    )
    retrieve._runtime.policy = retrieve._runtime.policy.model_copy(
        update={"retry_delays_seconds": (0,)}
    )
    search = c["backend"].search_once
    run = c["producer"].run
    observations: dict[str, Any] = dict(
        attempts=[], outcomes=[], target=False, failures=0
    )

    def backend(request, **kwargs):
        observations["attempts"].append(request.query)
        if observations["target"] and (terminal or observations["failures"] == 0):
            observations["failures"] += 1
            raise TransportFailure(ErrorCode.TOOL_TIMEOUT)
        return search(request, **kwargs)

    def research(candidate, gaps, budget):
        observations["target"] = bool(gaps) == additional
        out = run(candidate, gaps, budget)
        observations["outcomes"].append(deepcopy(out))
        return out

    c["backend"].search_once = backend
    c["producer"].run = research
    return c, observations


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("additional", [False, True])
@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("omit_metadata", [False, True])
def test_real_runtime_retry_carriers_preserve_terminal_contract(
    engine, additional, terminal, omit_metadata
):
    from pydantic import TypeAdapter

    from skala_rag.agents.evidence_research import ResearchOutcome

    c, observed = _runtime_retry_case(additional=additional, terminal=terminal)
    run = c["producer"].run

    def research(candidate, gaps, budget):
        out = run(candidate, gaps, budget)
        if omit_metadata:
            for record in out.records:
                record.arguments_without_secrets.pop("execution_mode", None)
                record.arguments_without_secrets.pop("index_identity", None)
        return out

    c["producer"].run = research
    result = execute(c, engine)
    out = observed["outcomes"][-1]
    if terminal:
        assert [e.model_dump(mode="json") for e in result.errors] == [
            e.model_dump(mode="json") for e in out.errors
        ]
    else:
        assert result.outcomes["co-0"].status == "recommend"
    owned = result.research_artifacts["co-0"]
    batch = owned["batches"][-1]
    expected = TypeAdapter(ResearchOutcome).dump_python(out, mode="json")
    if omit_metadata:
        for record in expected["records"]:
            record["arguments_without_secrets"].pop("execution_mode", None)
            record["arguments_without_secrets"].pop("index_identity", None)
    assert {k: batch[k] for k in expected} == expected
    historical = [
        r
        for r in out.records
        if r.error_id and r.error_id not in {e.error_id for e in out.errors}
    ]
    assert len(historical) == 1
    originals = c["producer"]._retrieve._runtime.error_history
    assert batch["attempt_errors"] == [
        originals[r.error_id].model_dump(mode="json") for r in historical
    ]
    assert result.research_retry_count == {"co-0": int(additional)}
    if terminal:
        assert result.outcomes["co-0"].status == "failed"
        assert not c["seen"] and not result.scores and not result.decisions
        assert len(out.errors) == 1 and out.errors[0].attempt == 2
        assert [e.model_dump(mode="json") for e in result.errors] == [
            e.model_dump(mode="json") for e in out.errors
        ]
        assert owned["state"]["errors"] == batch["errors"]
        assert batch["admission"] == "rejected_input"
        assert len(out.records) == 2
        assert not owned["state"]["snapshots"]
        assert owned["state"]["evidence_revisions"]["co-0"] == (2 if additional else 1)
    else:
        assert result.outcomes["co-0"].status == "recommend"
        assert len(c["seen"]) == 5 and len(result.scores) == 1
        assert not out.errors and not result.errors and not owned["state"]["errors"]
        assert batch["admission"] == "admitted"
        assert len(observed["attempts"]) == 24
        assert len(out.records) == len(out.calls) + 1
        assert c["seen"][0][1].evidence_revision == (3 if additional else 2)
    assert result.candidate_index == 1
    steps = [t["step"] for t in c["trace"] if t["candidate_id"] == "co-0"]
    assert steps.count("archive") == steps.count("advance") == 1


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("deadline", [False, True])
def test_real_live_runtime_refused_before_any_callback_and_ledger_reservation(
    engine, deadline
):
    from dataclasses import replace
    from datetime import timedelta

    c = setup_case()
    retrieve = c["producer"]._retrieve
    runtime = retrieve._runtime
    runtime.policy = runtime.policy.model_copy(
        update={
            "execution_mode": "live",
            "live_approval_reference": "synthetic-not-an-approval",
            "timing_approval_reference": "synthetic-not-an-approval",
        }
    )
    if deadline:
        retrieve._budget = retrieve._budget.model_copy(
            update={"deadline": runtime.clock.now() + timedelta(hours=1)}
        )
    callbacks = []
    stages = c["stages"]

    def counted(name, original):
        def invoke(*args, **kwargs):
            callbacks.append(name)
            return original(*args, **kwargs)

        return invoke

    c["stages"] = replace(
        stages,
        **{
            name: counted(name, getattr(stages, name))
            for name in ("discover", "normalize", "research", "eligibility")
        },
    )
    c["producer"].run = counted("producer", c["producer"].run)
    c["backend"].search_once = counted("backend", c["backend"].search_once)
    c["callbacks"] = {name: counted(name, fn) for name, fn in c["callbacks"].items()}
    before = runtime.ledger.snapshot()
    with pytest.raises(ValueError, match="runtime"):
        execute(c, engine)
    assert callbacks == [] and not c["seen"] and not c["backend"].calls
    assert runtime.ledger.snapshot() == before
    assert c["producer"]._retrieve is retrieve
    assert c["producer"].execution_mode == "fixture"
    assert stages.evidence_research.run_input.execution_mode == "fixture"
    assert runtime.policy.execution_mode == "live"
    assert runtime.error_history == {} and runtime.readiness_history == {}


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("additional", [False, True])
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize(
    "damage", ["future_source", "source_closure", "source_collision"]
)
def test_rejected_success_keeps_original_errors_diagnostic_not_adopted(
    engine, additional, empty, damage, monkeypatch
):
    from dataclasses import replace

    from tests.fixtures.evidence_research import StubWeb

    from skala_rag.agents.evidence_research import WebChannel
    from skala_rag.contracts.error_codes import ErrorCode
    from skala_rag.prompts.evidence_extraction import ExtractionOutput

    c = setup_case(gap=additional)
    binding = c["stages"].evidence_research
    c["stages"] = replace(
        c["stages"],
        evidence_research=replace(
            binding, budget=binding.budget.model_copy(update={"max_calls": 46})
        ),
    )
    run = c["producer"].run
    supplied = []

    def corrupt(candidate, gaps, budget):
        target = bool(gaps) == additional
        if target:
            c["producer"]._web = (
                WebChannel(
                    "fixture-optional",
                    "web",
                    False,
                    StubWeb(
                        {},
                        clock=c["producer"]._clock,
                        error=ErrorCode.TOOL_UNAVAILABLE,
                        name="fixture-optional",
                    ),
                ),
            )
            if empty:
                c["producer"]._llm.generate = lambda **kwargs: ExtractionOutput(
                    claims=[]
                )
        out = run(candidate, gaps, budget)
        if target:
            assert out.status == ("empty" if empty else "ok") and out.errors
            if damage == "future_source":
                sid = next(iter(out.sources))
                out.sources[sid] = out.sources[sid].model_copy(
                    update={"published_at": date(2026, 9, 2)}
                )
            elif damage == "source_collision":
                sid = next(iter(out.sources))
                out.sources[sid] = out.sources[sid].model_copy(
                    update={"title": "Counterfeit synthetic core"}
                )
            else:
                out.sources.clear()
            supplied.append(deepcopy(out))
        return out

    import skala_rag.graph.candidate_workflow_v3 as outer
    import skala_rag.graph.research_artifacts_v3 as artifacts

    rejected_states = []

    def atomic(owned, *args, **kwargs):
        before = deepcopy(owned["state"])
        try:
            return artifacts.consume_outcome_v3(owned, *args, **kwargs)
        except ValueError:
            assert owned["state"] == before
            rejected_states.append(before)
            raise

    monkeypatch.setattr(oracle, "consume_outcome_v3", atomic)
    monkeypatch.setattr(outer, "consume_outcome_v3", atomic)
    c["producer"].run = corrupt
    retrieve = c["producer"]._retrieve
    result = execute(c, engine)
    assert len(rejected_states) == 1
    assert result.outcomes["co-0"].status == "failed"
    assert [e.error_code for e in result.errors] == ["UPSTREAM_INVALID"]
    assert not c["seen"] and not result.scores and not result.decisions
    owned = result.research_artifacts["co-0"]
    assert owned["state"]["errors"] == []
    assert owned["state"]["evidence_revisions"]["co-0"] == (2 if additional else 1)
    assert not owned["state"]["snapshots"]
    assert owned["batches"][-1]["admission"] == "rejected_input"
    assert owned["batches"][-1]["errors"] == [
        e.model_dump(mode="json") for e in supplied[0].errors
    ]
    assert c["producer"]._retrieve is retrieve


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("additional", [False, True])
@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize(
    "damage",
    [
        "unknown_id",
        "past_history_id",
        "same_batch_unrelated_id",
        "record_id",
        "record_attempt",
        "record_timestamp",
        "call_id",
        "call_owner",
        "call_tool",
        "call_gap",
        "call_reorder",
        "cross_candidate",
        "cross_run",
        "cross_schema",
        "cross_tool",
        "error_attempt",
        "error_timestamp",
        "error_status",
        "retryable",
        "history_deleted",
        "terminal_original_changed",
    ],
)
def test_real_runtime_retry_history_cannot_repair_forged_carriers(
    engine, additional, terminal, damage
):
    from dataclasses import replace
    from datetime import timedelta

    from skala_rag.contracts.error_codes import ErrorCode
    from skala_rag.contracts.retrieval import RetrievalRequest
    from skala_rag.tools.runtime import TransportFailure

    c, observed = _runtime_retry_case(additional=additional, terminal=terminal)
    retrieve = c["producer"]._retrieve
    runtime = retrieve._runtime
    previous_id = None
    request = RetrievalRequest(
        schema_version=SCHEMA,
        query="unrelated synthetic query",
        candidate_id="co-0",
        corpus_version=CORPUS,
        index_version=INDEX,
        as_of=date(2026, 9, 1),
        top_k=5,
        allowed_source_ids=list(c["stages"].evidence_research.allowed_source_ids),
    )
    if damage == "past_history_id":
        c["backend"].failure = TransportFailure(ErrorCode.TOOL_TIMEOUT)
        previous = retrieve(request)
        previous_id = previous.retrieval_records[0].error_id
        c["backend"].failure = None
    run = c["producer"].run

    def corrupt(candidate, gaps, budget):
        out = run(candidate, gaps, budget)
        if bool(gaps) != additional:
            return out
        record = next(r for r in out.records if r.error_id)
        index = out.records.index(record)
        error = runtime.error_history[record.error_id]
        if damage == "unknown_id":
            record.error_id = "runtime-error-invented"
        elif damage == "past_history_id":
            record.error_id = previous_id
        elif damage == "same_batch_unrelated_id":
            # This actual adapter call shares candidate/run/clock/attempt, but is
            # not the call that returned the record in ResearchOutcome.
            c["backend"].failure = TransportFailure(ErrorCode.TOOL_TIMEOUT)
            unrelated = c["producer"]._retrieve(request)
            c["backend"].failure = None
            record.error_id = unrelated.retrieval_records[0].error_id
        elif damage == "record_id":
            old_id = record.retrieval_id
            record.retrieval_id = "runtime-retrieval-forged-1"
            out.calls[0] = replace(
                out.calls[0],
                retrieval_ids=tuple(
                    record.retrieval_id if rid == old_id else rid
                    for rid in out.calls[0].retrieval_ids
                ),
            )
        elif damage == "record_attempt":
            record.arguments_without_secrets["attempt"] = 2
        elif damage == "record_timestamp":
            record.finished_at += timedelta(seconds=1)
        elif damage == "call_id":
            record.arguments_without_secrets["call_id"] = "unrelated-call"
        elif damage == "call_owner":
            out.calls[0] = replace(
                out.calls[0], retrieval_ids=out.calls[0].retrieval_ids[1:]
            )
        elif damage == "call_tool":
            out.calls[0] = replace(out.calls[0], tool="foreign-tool")
            for r in out.records:
                r.arguments_without_secrets["research_tool"] = "foreign-tool"
        elif damage == "call_gap":
            out.calls[0] = replace(out.calls[0], gap_id="unrequested-gap")
        elif damage == "call_reorder":
            out.calls[0] = replace(
                out.calls[0], retrieval_ids=tuple(reversed(out.calls[0].retrieval_ids))
            )
        elif damage == "history_deleted":
            del runtime.error_history[record.error_id]
        elif damage == "terminal_original_changed" and terminal:
            out.errors[0] = out.errors[0].model_copy(
                update={"message_redacted": "forged terminal"}
            )
        else:
            updates = {
                "cross_candidate": {"candidate_id": "co-other"},
                "cross_run": {"run_id": "run-other"},
                "cross_schema": {"schema_version": "schema-other"},
                "cross_tool": {"node": "foreign-tool"},
                "error_attempt": {"attempt": 2},
                "error_timestamp": {
                    "timestamp": error.timestamp + timedelta(seconds=1)
                },
                "error_status": {"error_code": "TOOL_UNAVAILABLE"},
                "retryable": {"retryable": False},
                "terminal_original_changed": {"message_redacted": "forged historical"},
            }
            runtime.error_history[record.error_id] = error.model_copy(
                update=updates[damage]
            )
        out.records[index] = record
        return out

    c["producer"].run = corrupt
    result = execute(c, engine)
    _assert_generation_refused(c, result, "additional" if additional else "initial")
    assert [e.error_code for e in result.errors] == ["UPSTREAM_INVALID"]
    assert result.research_artifacts["co-0"]["state"]["errors"] == []
    assert c["producer"]._retrieve is retrieve
    assert observed["outcomes"][-1].records


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("additional", [False, True])
def test_real_runtime_terminal_error_cannot_be_filled_from_history(engine, additional):
    c, _ = _runtime_retry_case(additional=additional, terminal=True)
    run = c["producer"].run

    def omit(candidate, gaps, budget):
        out = run(candidate, gaps, budget)
        if bool(gaps) == additional:
            out.errors.clear()
        return out

    c["producer"].run = omit
    result = execute(c, engine)
    _assert_generation_refused(c, result, "additional" if additional else "initial")
    assert result.research_artifacts["co-0"]["state"]["errors"] == []


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("additional", [False, True])
def test_real_retry_then_extraction_failure_retains_original_partial_contract(
    engine, additional
):
    from tests.fixtures.evidence_research import llm_error

    c, observed = _runtime_retry_case(additional=additional)
    run = c["producer"].run
    retrieve = c["producer"]._retrieve

    def fail_extract(candidate, gaps, budget):
        if bool(gaps) == additional:
            c["producer"]._llm = LineLLM(error=llm_error())
        return run(candidate, gaps, budget)

    c["producer"].run = fail_extract
    result = execute(c, engine)
    out = observed["outcomes"][-1]
    assert out.status == "failed" and out.calls == [] and len(out.records) == 2
    assert [e.model_dump(mode="json") for e in result.errors] == [
        e.model_dump(mode="json") for e in out.errors
    ]
    assert result.outcomes["co-0"].status == "failed" and not c["seen"]
    batch = result.research_artifacts["co-0"]["batches"][-1]
    assert batch["records"] == [r.model_dump(mode="json") for r in out.records]
    assert batch["attempt_errors"] == [
        retrieve._runtime.error_history[out.records[0].error_id].model_dump(mode="json")
    ]
    assert batch["admission"] == "rejected_input"
    assert c["producer"]._retrieve is retrieve


@pytest.mark.parametrize(
    "engine", [run_candidate_workflow_v3, oracle.run_candidates_v3]
)
@pytest.mark.parametrize("additional", [False, True])
def test_runtime_mode_changed_inside_producer_is_refused_before_mock_backend(
    engine, additional
):
    from datetime import timedelta

    c = setup_case(gap=additional)
    retrieve = c["producer"]._retrieve
    runtime = retrieve._runtime
    retrieve._budget = retrieve._budget.model_copy(
        update={"deadline": runtime.clock.now() + timedelta(hours=1)}
    )
    run = c["producer"].run
    before = []

    def change_mode(candidate, gaps, budget):
        if bool(gaps) == additional:
            before.append((runtime.ledger.snapshot(), len(c["backend"].calls)))
            runtime.policy = runtime.policy.model_copy(
                update={
                    "execution_mode": "live",
                    "live_approval_reference": "synthetic-not-an-approval",
                    "timing_approval_reference": "synthetic-not-an-approval",
                }
            )
        return run(candidate, gaps, budget)

    c["producer"].run = change_mode
    result = execute(c, engine)
    assert result.outcomes["co-0"].status == "failed" and not c["seen"]
    assert len(before) == 1
    assert runtime.ledger.snapshot() == before[0][0]
    assert len(c["backend"].calls) == before[0][1]
    assert c["producer"]._retrieve is retrieve


class _ControlledResearchSearch:
    """In-memory index backend; no embedding model or external request."""

    retry_owner = "runtime"

    def __init__(self, bundle: RetrievalBundle) -> None:
        self.bundle = bundle

    def search_once(
        self, request, *, snapshot, allowed_chunk_ids, timeout_seconds
    ) -> RetrievalBundle:
        return self.bundle.model_copy(deep=True)


@pytest.fixture
def actual_research_case(actual_admission_fixture):
    admission, snapshot, llm, *_ = actual_admission_fixture
    payload = gates_payload()
    payload.update(provider="openai")
    payload["limits"].update(max_calls=8, tool_max_calls={"openai": 4, "retrieve": 4})
    payload["limits"]["max_cost_usd"] = "1.00"
    payload["limits"].update(max_input_tokens=20000, max_output_tokens=8000)
    payload["allowance"].update(input_tokens=8000, output_tokens=2000)
    runtime = runtime_binding(
        run_id=snapshot.run_id, schema_version=snapshot.schema_version, payload=payload
    )
    source = replace(
        admission.source,
        live_gates=runtime.gates,
        live_gate_verifier=lambda _gate, gates: gates == runtime.gates,
    )
    admission = replace(admission, source=source, runtime_binding=runtime)
    llm.runtime = runtime.runtime
    llm.budget, llm.readiness = runtime.budget, runtime.readiness
    llm.allowance_for = lambda _system, _user, _schema: runtime.allowance
    llm.call = runtime.call.model_copy(
        update={"candidate_id": snapshot.candidate_id, "node": "evidence_research"}
    )
    llm.transport._clock = runtime.runtime.clock
    requests = []
    text = "Synthetic Robot supplies warehouse robots."

    def respond(request):
        requests.append(request)
        output = {
            "claims": [
                ClaimDraft(
                    claim=text, excerpt=text, subject="Synthetic Robot"
                ).model_dump(mode="json")
            ]
        }
        return httpx.Response(
            200,
            json=body(
                json.dumps(output), usage={"input_tokens": 10, "output_tokens": 10}
            ),
        )

    llm.transport._http_transport = httpx.MockTransport(respond)
    sid, source = next(iter(snapshot.sources.items()))
    item = Chunk(
        schema_version=snapshot.schema_version,
        chunk_id="synthetic-live-chunk",
        source_id=sid,
        corpus_version=snapshot.corpus_version,
        text=text,
        locator=source.url,
        candidate_ids=[snapshot.candidate_id],
        scope="company",
        language="en",
        embedding_model="synthetic-model",
        embedding_revision="synthetic-1",
    )
    bundle = RetrievalBundle(
        schema_version=snapshot.schema_version, chunks=[item], sources={sid: source}
    )
    index = IndexSnapshot(
        schema_version=snapshot.schema_version,
        corpus_version=snapshot.corpus_version,
        corpus_hash="synthetic-index-hash",
        index_version=snapshot.index_version,
        embedding_model=item.embedding_model,
        embedding_revision=item.embedding_revision,
        search_settings={"synthetic": True},
        bundle=bundle,
    )
    retrieve = IndexedRetriever(
        snapshot=index,
        backend=_ControlledResearchSearch(bundle),
        runtime=runtime.runtime,
        readiness=runtime.readiness,
        budget=runtime.budget,
        allowance=Allowance(
            schema_version=snapshot.schema_version,
            input_tokens=0,
            output_tokens=0,
            max_cost_usd=Decimal(0),
        ),
        run_id=snapshot.run_id,
        schema_version=snapshot.schema_version,
        tool_name="retrieve",
    )
    gap = _gap(snapshot.candidate_id, "technology.integration").model_copy(
        update={"schema_version": snapshot.schema_version}
    )
    producer = EvidenceResearch(
        retrieve=retrieve,
        rag_required=True,
        llm=llm,
        initial_plan=lambda _c: [gap],
        run_id=snapshot.run_id,
        corpus_version=snapshot.corpus_version,
        index_version=snapshot.index_version,
        as_of=snapshot.as_of,
        top_k=1,
        allowed_source_ids=[sid],
        clock=runtime.runtime.clock,
        schema_version=snapshot.schema_version,
        execution_mode="live",
    )
    binding = EvidenceResearchBindingV3(
        research=producer,
        budget=runtime.budget,
        run_input=admission.run_input,
        run_id=snapshot.run_id,
        schema_version=snapshot.schema_version,
        index_version=snapshot.index_version,
        allowed_source_ids=frozenset([sid]),
        industry_evidence_ids=frozenset(),
        actual_admission=admission,
    )
    candidate = Candidate(
        schema_version=snapshot.schema_version,
        candidate_id=snapshot.candidate_id,
        canonical_name="Synthetic Robot",
        aliases=[],
        country="US",
        legal_identifiers={},
        discovery_source_ids=[sid],
    )
    eligibility = EligibilityResult(
        schema_version=snapshot.schema_version,
        eligibility_result_id="synthetic-eligible",
        run_id=snapshot.run_id,
        candidate_id=snapshot.candidate_id,
        evidence_revision=snapshot.evidence_revision,
        policy_version=snapshot.policy_version,
        as_of=snapshot.as_of,
        status="eligible",
        checks={},
        reason_codes=[],
        evidence_ids=snapshot.evidence_ids,
    )
    seed = CompanyResearchArtifactsV3(
        candidate_id=snapshot.candidate_id,
        run_id=snapshot.run_id,
        schema_version=snapshot.schema_version,
        evidence_revision=snapshot.evidence_revision,
        sources=snapshot.sources,
        chunks={},
        evidence=snapshot.evidence,
        records=[
            record.model_copy(
                update={"arguments_without_secrets": {"execution_mode": "live"}}
            )
            for record in snapshot.retrieval_records.values()
        ],
    )
    return binding, candidate, eligibility, seed, requests
