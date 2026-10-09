"""#213 public offline composite; synthetic sources and explicit mock models.

EvidenceResearch, freeze, Technology/adapter, join, selector and reporting are real.
The remaining evaluation siblings and report model transports are fixture mocks.
"""

import socket
from copy import deepcopy
from dataclasses import replace
from datetime import date
from typing import Literal, assert_never

import pytest
from tests.integration import test_v3_actual_controller as actual_fixture
from tests.integration import test_v3_source_only_controller as source_only_fixture
from tests.integration.test_v3_evidence_snapshot_consumer import setup_case
from tests.unit.test_v3_report_pipeline import Stub

import skala_rag.graph.candidate_workflow_v3 as outer
import skala_rag.graph.snapshot as snapshot_module
from skala_rag.contracts import ValidationErrorDetail, ValidationResult
from skala_rag.contracts.ids import snapshot_id
from skala_rag.reporting.v3_context import build_report_context_from_run_v3
from skala_rag.reporting.v3_pipeline import ReportGeneratorV3, SemanticJudgeV3
from skala_rag.reporting.validator import artifact_hash
from skala_rag.scoring.selector_v3 import select_best_v3

source_only_inputs = source_only_fixture.inputs
actual_admission_fixture = actual_fixture.actual_admission_fixture
research_case = actual_fixture.research_case
controller = actual_fixture.controller


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("offline integration attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


@pytest.mark.parametrize("damage", ["promotion", "decision", "run", "mode"])
def test_actual_scored_context_rejects_changed_original_state(
    controller, damage: Literal["promotion", "decision", "run", "mode"]
):
    # Given: a complete admitted original State run with synthetic evaluations.
    stages, callbacks, options, _, _, frozen = controller
    result = outer.run_candidate_workflow_v3(stages, callbacks, **options)
    artifacts = deepcopy(result.research_artifacts)
    run = stages.evidence_research.run_input
    state = artifacts[frozen.candidate_id]["state"]
    match damage:
        case "promotion":
            state["evaluations_v3"].clear()
        case "decision":
            state["investment_decisions"].clear()
        case "run":
            run = run.model_copy(update={"corpus_version": "foreign"})
        case "mode":
            result = replace(result, execution_mode="fixture")
        case unreachable:
            assert_never(unreachable)
    # When / Then: no context can replace or detach the original scored inputs.
    with pytest.raises(ValueError):
        build_report_context_from_run_v3(
            replace(result, research_artifacts=artifacts),
            run_input=run,
            run_id=result.run_id,
            actual_admission=options["actual_admission"],
        )


def test_actual_unknown_candidate_advances_without_evaluation(controller):
    # Given: discovery survives but eligibility remains unknown.
    stages, callbacks, options, calls, requests, frozen = controller
    eligibility = stages.eligibility({}, None).model_copy(update={"status": "unknown"})
    stages = replace(stages, eligibility=lambda *_args: eligibility)
    # When: drive the public original loop.
    result = outer.run_candidate_workflow_v3(stages, callbacks, **options)
    # Then: archive once, no refill, score, research or evaluator invocation.
    assert result.outcomes[frozen.candidate_id].status == "eligibility_unknown"
    assert result.candidate_index == 1 and not result.scores
    assert not calls and not requests


def test_actual_fixture_locator_rejects_before_research_or_evaluation(controller):
    # Given: CompanyResearch carries a forbidden fixture Source in live mode.
    stages, callbacks, options, calls, requests, frozen = controller
    seed = stages.research({})
    seed = replace(
        seed,
        sources={
            sid: source.model_copy(update={"url": "fixture://forbidden"})
            for sid, source in seed.sources.items()
        },
    )
    stages = replace(stages, research=lambda _candidate: seed)
    # When: the original receipt consumer parses the live seed.
    result = outer.run_candidate_workflow_v3(stages, callbacks, **options)
    # Then: fixture locators never reach retrieval, evaluators or scoring.
    assert not calls and not requests and not result.scores
    assert result.outcomes[frozen.candidate_id].status == "failed"


def test_actual_unreviewed_response_does_not_acquire_semantic_authority(
    controller, actual_admission_fixture
):
    # Given: synthetic callback success does not create a semantic review record.
    _, _, options, _, _, frozen = controller
    admission = options["actual_admission"]
    _, _, _, request, receipt, _ = actual_admission_fixture
    # When: resolve a response absent from the immutable snapshot-bound records.
    resolved = admission.resolve_review(
        frozen,
        admission.registry.rubric("core-0.1.0"),
        request,
        receipt,
        subject="Synthetic Robot",
    )
    # Then: unreviewed remains unknown rather than positive authority.
    assert resolved is None


def composite(case, *, generate, judge, check_pdf=None):
    api = getattr(outer, "run_candidate_report_v3", None)
    assert callable(api), "#213 public original-State report composite missing"
    return api(
        case["stages"],
        case["callbacks"],
        run_input=case["stages"].evidence_research.run_input,
        generate=generate,
        judge=judge,
        check_pdf=check_pdf,
        graph_events=case["events"],
        **case["options"],
    )


def test_public_multi_candidate_original_freeze_to_report(monkeypatch):
    c = setup_case(count=2)
    original_input = c["stages"].evidence_research.run_input.model_dump(mode="json")
    originals, frozen = [], []
    real_outer, real_freeze = (
        outer.run_candidate_workflow_v3,
        snapshot_module.freeze_snapshot,
    )

    def observe_outer(*args, **kwargs):
        result = real_outer(*args, **kwargs)
        originals.append(result)
        return result

    def observe_freeze(*args, **kwargs):
        snapshot = real_freeze(*args, **kwargs)
        frozen.append(deepcopy(snapshot))
        return snapshot

    monkeypatch.setattr(outer, "run_candidate_workflow_v3", observe_outer)
    monkeypatch.setattr(snapshot_module, "freeze_snapshot", observe_freeze)
    generator, judge = Stub(), Stub()
    generate = ReportGeneratorV3(generator)

    def after_all_candidates(context, feedback):
        assert len(originals) == 1 and originals[0].candidate_index == 2
        assert len(c["seen"]) == 10 and len(c["receipts"]) == 2
        assert any(
            not namespace and "selector" in updates
            for namespace, updates in c["events"]
        )
        return generate(context, feedback)

    result, context, report = composite(
        c, generate=after_all_candidates, judge=SemanticJudgeV3(judge)
    )
    assert originals == [result] and originals[0] is result
    assert len(frozen) == 2 and len(result.scores) == 2
    assert result.selection.selected_candidate_id in result.scores
    assert result.status == "ready_for_v3_reporting" and result.reporting_gap
    payload = context.snapshot()
    for snapshot in frozen:
        cid = snapshot.candidate_id
        raw = result.research_artifacts[cid]["state"]["snapshots"][
            result.scores[cid].snapshot_id
        ]
        assert raw == snapshot.model_dump(mode="json") == payload["snapshots"][cid]
    assert len(c["backend"].calls) == 2 * len(c["initial"])
    assert (
        report.status == "completed" and not report.warning and report.validation.valid
    )
    assert report.pdf_validation is None and not report.final_allowed
    assert len(generator.calls) == len(judge.calls) == 1
    assert generator.calls[0][1]["context"] == judge.calls[0][1]["context"] == payload
    assert (
        c["stages"].evidence_research.run_input.model_dump(mode="json")
        == original_input
    )
    result.research_artifacts.clear()
    c["seeds"].clear()
    payload["snapshots"].clear()
    assert len(context.snapshot()["snapshots"]) == 2


@pytest.mark.parametrize("case_kind", ["empty", "low_rating", "mixed"])
def test_original_outcomes_remain_distinct(case_kind):
    c = setup_case(
        count=0 if case_kind == "empty" else 3 if case_kind == "mixed" else 1
    )
    if case_kind == "low_rating":
        # Explicit synthetic rating scenario, not actual scoring authority.
        def low_rating(original):
            def evaluate(snapshot):
                branch = original(snapshot)
                return branch.model_copy(
                    update={
                        "evaluations": {
                            dim: value.model_copy(
                                update={
                                    "criteria": [
                                        criterion.model_copy(update={"rating": 1})
                                        for criterion in value.criteria
                                    ]
                                }
                            )
                            for dim, value in branch.evaluations.items()
                        }
                    }
                )

            return evaluate

        c["callbacks"] = {b: low_rating(fn) for b, fn in c["callbacks"].items()}
    elif case_kind == "mixed":
        stages = c["stages"]

        technology = c["callbacks"]["technology"]

        def failing_technology(snapshot):
            result = technology(snapshot)
            if snapshot.candidate_id == "co-0":
                raise RuntimeError("synthetic post-freeze technical failure")
            return result

        c["callbacks"]["technology"] = failing_technology

        def eligibility(candidate, seed):
            result = stages.eligibility(candidate, seed)
            return (
                result.model_copy(
                    update={"status": "unknown", "reason_codes": ["SYNTHETIC_UNKNOWN"]}
                )
                if candidate["candidate_id"] == "co-1"
                else result
            )

        c["stages"] = replace(stages, eligibility=eligibility)
    generator, judge = Stub(), Stub()
    result, context, report = composite(
        c, generate=ReportGeneratorV3(generator), judge=SemanticJudgeV3(judge)
    )
    assert (
        report.status == "completed" and not report.warning and not report.final_allowed
    )
    assert len(generator.calls) == len(judge.calls) == 1
    payload = context.snapshot()
    if case_kind == "mixed":
        assert {cid: o.status for cid, o in result.outcomes.items()} == {
            "co-0": "failed",
            "co-1": "eligibility_unknown",
            "co-2": "recommend",
        }
        assert result.candidate_index == 3 and result.errors
        assert set(payload["snapshots"]) == set(result.scores) == {"co-2"}
        assert payload["errors"] and len(c["seen"]) == 10
        assert result.research_artifacts["co-0"]["state"]["snapshots"]
        assert "co-0" not in payload["snapshots"]
    else:
        assert result.selection.selected_candidate_id is None
        assert payload["mode"] == "no_recommendation"
        if case_kind == "empty":
            assert result.status == "no_candidates" and result.candidate_index == 0
            assert (
                not payload["scores"]
                and not payload["snapshots"]
                and not c["backend"].calls
            )
        else:
            assert len(payload["scores"]) == len(payload["snapshots"]) == 1
            assert result.outcomes["co-0"].status in ("pass", "watchlist")


@pytest.mark.parametrize(
    "damage",
    [
        "candidate_id",
        "run_id",
        "schema_version",
        "policy_version",
        "corpus_version",
        "as_of",
        "missing",
        "stale",
        "zero_generation",
        "nested_schema",
        "json",
        "json_object",
        "provenance",
        "state_input",
        "state_source",
        "mode",
    ],
)
def test_corrupt_original_handoff_refused_before_report_callbacks(monkeypatch, damage):
    c = setup_case()
    real = outer.run_candidate_workflow_v3
    callbacks = []

    def corrupted(*args, **kwargs):
        result = real(*args, **kwargs)
        state = result.research_artifacts["co-0"]["state"]
        key = result.scores["co-0"].snapshot_id
        raw = state["snapshots"][key]
        if damage in (
            "candidate_id",
            "run_id",
            "schema_version",
            "policy_version",
            "corpus_version",
        ):
            raw[damage] = "wrong-original-identity"
        elif damage == "as_of":
            raw[damage] = "2026-08-31"
        elif damage == "missing":
            state["snapshots"].pop(key)
        elif damage == "stale":
            raw["evaluation_round"] += 1
        elif damage == "zero_generation":
            # Consistent tampered IDs cannot make an impossible freeze round valid.
            identifier = snapshot_id(
                result.run_id,
                "co-0",
                0,
                raw["evidence_revision"],
                result.policy_version,
            )
            raw.update(evaluation_round=0, snapshot_id=identifier)
            state["snapshots"] = {identifier: raw}
            state["evaluation_rounds"]["co-0"] = 0
            result.scores["co-0"] = result.scores["co-0"].model_copy(
                update={"evaluation_round": 0, "snapshot_id": identifier}
            )
        elif damage == "nested_schema":
            next(iter(raw["evidence"].values()))["schema_version"] = "other-schema"
        elif damage == "json":
            raw["untrusted"] = float("nan")
        elif damage == "json_object":
            raw["untrusted"] = object()
        elif damage == "provenance":
            next(iter(raw["retrieval_records"].values()))["evidence_ids"] = []
        elif damage == "state_input":
            state["run_input"]["countries"] = ["other"]
        elif damage == "state_source":
            next(iter(state["sources"].values()))["title"] = "mutated original source"
        else:
            return replace(result, execution_mode="live")
        return result

    def forbidden(*args):
        callbacks.append("report")
        pytest.fail("invalid original snapshot reached a report callback")

    monkeypatch.setattr(outer, "run_candidate_workflow_v3", corrupted)
    with pytest.raises(ValueError):
        composite(c, generate=forbidden, judge=forbidden, check_pdf=forbidden)
    assert callbacks == [] and len(c["seen"]) == 5


def test_explicit_run_and_options_are_detached_before_candidate_callbacks():
    c = setup_case()
    run = c["stages"].evidence_research.run_input
    original_run = run.model_dump(mode="json")
    stages = c["stages"]

    def discover():
        run.countries.append("caller mutation")
        run.as_of = date(2026, 9, 2)
        # Deliberate corruption bypasses the policy's normal frozen setter.
        object.__setattr__(c["options"]["policy"], "execution_mode", "live")
        c["options"]["schema_version"] = "caller mutation"
        return stages.discover()

    c["stages"] = replace(stages, discover=discover)
    generator, judge = Stub(), Stub()
    result, context, report = composite(
        c, generate=ReportGeneratorV3(generator), judge=SemanticJudgeV3(judge)
    )
    assert (
        report.status == "completed" and len(generator.calls) == len(judge.calls) == 1
    )
    assert result.research_artifacts["co-0"]["state"]["run_input"] == original_run
    assert context.snapshot()["as_of"] == original_run["as_of"]
    assert run.as_of == date(2026, 9, 2) and "caller mutation" in run.countries


@pytest.mark.parametrize("damage", ["run", "actual", "actual_binding", "json"])
def test_invalid_explicit_input_refused_before_candidate_or_model_calls(
    damage, monkeypatch
):
    c = setup_case()
    run = c["stages"].evidence_research.run_input.model_copy(deep=True)
    calls = []
    if damage == "run":
        run.corpus_version = "other-corpus"
    elif damage in ("actual", "actual_binding"):
        run.execution_mode = "live"
    else:
        # model_copy is not validation; the composite must revalidate the DTO.
        run = run.model_copy(update={"countries": [object()]})
    stages = c["stages"]
    if damage == "actual_binding":
        stages = replace(
            stages, evidence_research=replace(stages.evidence_research, run_input=run)
        )
    c["stages"] = replace(stages, discover=lambda: calls.append("discover"))

    def forbidden(*args):
        calls.append("report")

    real_outer = outer.run_candidate_workflow_v3

    def observe_outer(*args, **kwargs):
        calls.append("outer")
        return real_outer(*args, **kwargs)

    monkeypatch.setattr(outer, "run_candidate_workflow_v3", observe_outer)
    with pytest.raises(ValueError):
        outer.run_candidate_report_v3(
            c["stages"],
            c["callbacks"],
            run_input=run,
            generate=forbidden,
            judge=forbidden,
            **c["options"],
        )
    assert not calls and not c["backend"].calls and not c["seen"]


@pytest.mark.parametrize("execution_mode", ["live", "fixture"])
def test_source_only_report_composite_refused_before_any_callbacks(
    source_only_inputs, monkeypatch, execution_mode
):
    from skala_rag.source_only_v3 import prepare_source_only_v3

    inputs, configured, requests = source_only_inputs
    boundary = prepare_source_only_v3(**inputs)
    c = setup_case(count=0)
    run = boundary.run_input.model_copy(deep=True)
    run.execution_mode = execution_mode
    options = {
        **c["options"],
        "run_id": boundary.run_id,
        "schema_version": boundary.run_input.schema_version,
    }
    calls, copies, callbacks = [], [], []
    real_outer, real_deepcopy = outer.run_candidate_workflow_v3, outer.deepcopy

    def observe_outer(*args, **kwargs):
        calls.append("outer")
        return real_outer(*args, **kwargs)

    def observe_copy(*args, **kwargs):
        copies.append("deepcopy")
        return real_deepcopy(*args, **kwargs)

    def forbidden(*args, **kwargs):
        callbacks.append("evaluation/report")
        pytest.fail("unsupported report composite reached a callback")

    monkeypatch.setattr(outer, "run_candidate_workflow_v3", observe_outer)
    monkeypatch.setattr(outer, "deepcopy", observe_copy)
    monkeypatch.setattr(outer, "build_evaluation_graph_v3", forbidden)
    with pytest.raises(ValueError):
        outer.run_candidate_report_v3(
            None,
            {},
            run_input=run,
            source_only=boundary,
            run_profile=boundary.run_profile,
            generate=forbidden,
            judge=forbidden,
            check_pdf=forbidden,
            actual_admission=None,
            **options,
        )
    assert calls == configured == requests == callbacks == copies == []
    assert not c["seen"] and not c["backend"].calls and not c["events"]
    assert boundary.run_input.execution_mode == "live"


def test_source_only_real_consumer_cannot_be_promoted_to_report(
    source_only_inputs, monkeypatch
):
    from skala_rag.source_only_v3 import prepare_source_only_v3

    inputs, configured, requests = source_only_inputs
    boundary = prepare_source_only_v3(**inputs)
    c = setup_case(count=0)
    options = {
        **c["options"],
        "run_id": boundary.run_id,
        "schema_version": boundary.run_input.schema_version,
    }
    originals, callbacks = [], []
    real = outer.run_candidate_workflow_v3

    def observe(*args, **kwargs):
        result = real(*args, **kwargs)
        originals.append(result)
        return result

    def forbidden(*args):
        callbacks.append("report")
        pytest.fail("source-only reached scoring/report callback")

    monkeypatch.setattr(outer, "run_candidate_workflow_v3", observe)
    monkeypatch.setattr(outer, "build_evaluation_graph_v3", forbidden)
    # A separate, fresh standalone boundary still supports source-only collection.
    result = outer.run_candidate_workflow_v3(
        None,
        {},
        source_only=boundary,
        run_profile=boundary.run_profile,
        actual_admission=None,
        **options,
    )
    assert len(originals) == 1 and not callbacks
    assert result is originals[0]
    assert result.execution_mode == "live" and result.status == "no_eligible_candidates"
    assert result.candidate_index == 5 and not result.scores and not result.decisions
    assert all(o.status == "eligibility_unknown" for o in result.outcomes.values())
    assert configured == ["official-homepage"] and len(requests) == 5


@pytest.mark.parametrize(
    "behavior",
    ["pdf_pass", "shared_warning", "generate_failure", "stale_judge", "stale_pdf"],
)
def test_existing_report_and_pdf_shared_revision_semantics(behavior):
    c = setup_case()
    generator, judge = (
        Stub(),
        Stub("revise" if behavior == "shared_warning" else "pass"),
    )
    generate = ReportGeneratorV3(generator)
    semantic = SemanticJudgeV3(judge)
    pdf_revisions = []

    def generate_callback(context, feedback):
        if behavior == "generate_failure":
            raise RuntimeError("synthetic model technical failure")
        draft = generate(context, feedback)
        if (
            behavior == "shared_warning"
            and draft.revision == 0
            and len(generator.calls) == 1
        ):
            return draft.model_copy(
                update={
                    "markdown": draft.markdown.replace(
                        "normalized_score", "broken_score"
                    )
                }
            )
        return draft

    def judge_callback(draft, context):
        judged = semantic(draft, context)
        if behavior == "stale_judge":
            return judged.model_copy(update={"judged_artifact_hash": "old-proof"})
        if behavior == "shared_warning" and draft.revision == 2:
            return judged.model_copy(
                update={"verdict": "pass", "revision_instructions": []}
            )
        return judged

    def pdf(draft, context, structural, judged):
        pdf_revisions.append(draft.revision)
        assert structural.valid and judged.verdict == "pass"
        assert (
            structural.artifact_hash
            == judged.judged_artifact_hash
            == artifact_hash(draft)
        )
        # Injected PDF callback proof only; no actual PDF is rendered here.
        return ValidationResult(
            schema_version=draft.schema_version,
            valid=behavior != "shared_warning",
            context_id=context.context_id,
            artifact_hash="old-proof"
            if behavior == "stale_pdf"
            else artifact_hash(draft),
            checks={"action": "revise" if behavior == "shared_warning" else "pass"},
            errors=[
                ValidationErrorDetail(
                    schema_version=draft.schema_version,
                    code="PDF_SUMMARY",
                    location="pdf",
                    message="synthetic layout error",
                )
            ]
            if behavior == "shared_warning"
            else [],
        )

    _, context, report = composite(
        c, generate=generate_callback, judge=judge_callback, check_pdf=pdf
    )
    assert not report.final_allowed and report.context_id == context.context_id
    if behavior == "shared_warning":
        assert report.status == "completed" and report.warning and report.revisions == 2
        assert report.error_code == "BUDGET_EXHAUSTED"
        assert (
            len(generator.calls) == 3 and len(judge.calls) == 2 and pdf_revisions == [2]
        )
        assert not report.pdf_validation.valid
    elif behavior == "pdf_pass":
        assert (
            report.status == "completed"
            and not report.warning
            and report.pdf_validation.valid
        )
        assert len(generator.calls) == len(judge.calls) == 1 and pdf_revisions == [0]
    else:
        assert (
            report.status == "failed" and report.revisions == 0 and not report.warning
        )
        assert report.error_code == (
            "TOOL_FAILED" if behavior == "stale_pdf" else "LLM_OUTPUT_INVALID"
        )
        assert len(generator.calls) == (0 if behavior == "generate_failure" else 1)
        assert len(judge.calls) == (0 if behavior == "generate_failure" else 1)
        assert pdf_revisions == ([0] if behavior == "stale_pdf" else [])


@pytest.mark.parametrize(
    ("damage", "expected_status"),
    [
        ("all", "recommend"),
        ("all", "pass"),
        ("all", "watchlist"),
        ("partial", "recommend"),
        ("orphan", "recommend"),
        ("changed_outcome", "recommend"),
    ],
)
def test_normal_outcome_score_closure_refused_before_report(
    monkeypatch, damage, expected_status
):
    c = setup_case(count=2)
    if expected_status != "recommend":
        # Synthetic ratings still traverse the real original outer/join/decision.
        def rated(original):
            def evaluate(snapshot):
                branch = original(snapshot)
                return branch.model_copy(
                    update={
                        "evaluations": {
                            dim: value.model_copy(
                                update={
                                    "criteria": [
                                        criterion.model_copy(
                                            update={
                                                "rating": 3
                                                if expected_status == "pass"
                                                and dim in ("market", "technology")
                                                else 1
                                            }
                                        )
                                        for criterion in value.criteria
                                    ]
                                }
                            )
                            for dim, value in branch.evaluations.items()
                        }
                    }
                )

            return evaluate

        c["callbacks"] = {b: rated(fn) for b, fn in c["callbacks"].items()}
    real = outer.run_candidate_workflow_v3
    originals, pdf_calls = [], []

    def corrupted(*args, **kwargs):
        result = real(*args, **kwargs)
        originals.append(result)
        assert len(result.scores) == len(result.decisions) == 2
        assert {o.status for o in result.outcomes.values()} == {expected_status}
        assert all(a["state"]["snapshots"] for a in result.research_artifacts.values())
        if damage == "all":
            selection = select_best_v3(
                [],
                c["options"]["policy"],
                run_id=result.run_id,
                schema_version=result.schema_version,
            )
            return replace(result, scores={}, decisions={}, selection=selection)
        if damage == "partial":
            cid = "co-1"
            selection = replace(
                result.selection,
                considered_candidate_ids=(cid,),
                compared_score_summary_ids=(result.scores[cid].score_summary_id,),
                selected_candidate_id=cid,
            )
            return replace(
                result,
                scores={cid: result.scores[cid]},
                decisions={cid: result.decisions[cid]},
                selection=selection,
            )
        if damage == "orphan":
            result.outcomes["orphan"] = result.outcomes["co-0"].model_copy(
                update={"candidate_id": "orphan"}
            )
        else:
            result.outcomes["co-0"] = result.outcomes["co-0"].model_copy(
                update={"status": "failed"}
            )
        return result

    generator, judge = Stub(), Stub()
    monkeypatch.setattr(outer, "run_candidate_workflow_v3", corrupted)
    with pytest.raises(ValueError):
        composite(
            c,
            generate=ReportGeneratorV3(generator),
            judge=SemanticJudgeV3(judge),
            check_pdf=lambda *args: pdf_calls.append("pdf"),
        )
    assert len(originals) == 1 and len(c["seen"]) == 10
    assert generator.calls == judge.calls == pdf_calls == []


@pytest.mark.parametrize("status", ["failed", "ineligible", "eligibility_unknown"])
def test_scoreless_original_outcomes_are_not_promoted_to_scored(status):
    c = setup_case()
    if status == "failed":
        technology = c["callbacks"]["technology"]

        def failed(snapshot):
            technology(snapshot)
            raise RuntimeError("synthetic post-freeze failure without a score")

        c["callbacks"]["technology"] = failed
    else:
        stages = c["stages"]

        def eligibility(candidate, seed):
            return stages.eligibility(candidate, seed).model_copy(
                update={
                    "status": "unknown" if status == "eligibility_unknown" else status,
                    "reason_codes": ["SYNTHETIC_SCORELESS"],
                }
            )

        c["stages"] = replace(stages, eligibility=eligibility)
    generator, judge = Stub(), Stub()
    result, context, report = composite(
        c, generate=ReportGeneratorV3(generator), judge=SemanticJudgeV3(judge)
    )
    assert result.outcomes["co-0"].status == status
    assert not result.scores and not result.decisions
    assert result.selection.selected_candidate_id is None
    payload = context.snapshot()
    assert payload["mode"] == "no_recommendation"
    assert (
        not payload["scores"] and not payload["decisions"] and not payload["snapshots"]
    )
    assert report.status == "completed" and not report.final_allowed
    assert len(generator.calls) == len(judge.calls) == 1
    if status == "failed":
        assert result.research_artifacts["co-0"]["state"]["snapshots"]
        assert result.errors and payload["errors"]
    else:
        assert not c["seen"] and not c["backend"].calls
