"""Synthetic baseline terminal envelopes; no actual evaluator admission."""

import traceback
from asyncio import CancelledError
from collections.abc import Collection

import pytest
from tests.fixtures.loader import load_common_fixtures

from skala_rag.agents.evaluation_v3_adapter import adapt_baseline_branch_result
from skala_rag.contracts.evaluation import EvaluationResult
from skala_rag.contracts.v3 import EvaluationBranchResult
from skala_rag.scoring.catalog import load_policy


@pytest.fixture
def case():
    policy = load_policy("configs/scoring.draft.json", execution_mode="fixture")
    fixtures = load_common_fixtures(policy)
    snapshot = next(iter(fixtures.snapshots.values()))

    def terminal(branch):
        evaluation = fixtures.evaluations[
            f"{snapshot.candidate_id}:{snapshot.evaluation_round}:{branch}"
        ]
        payload = evaluation.model_dump()
        payload.pop("rubric_version")
        payload.pop("criteria")
        payload.pop("research_gaps")
        payload.pop("caveats")
        return EvaluationResult(
            **payload, status="success", evaluation=evaluation, errors=[]
        )

    return snapshot, policy, terminal


@pytest.mark.parametrize("branch", ["founder", "market", "technology"])
def test_synthetic_success_roundtrip_without_relabelling(case, branch):
    snapshot, policy, terminal = case
    original = terminal(branch)
    with pytest.raises(ValueError):
        EvaluationBranchResult.model_validate(original)
    converted = adapt_baseline_branch_result(
        original,
        branch_id=branch,
        snapshot=snapshot,
        criteria=policy.criteria,
        industry_evidence_dimensions=set(),
    )
    assert converted.policy_version == original.policy_version
    evaluation = converted.evaluations[branch].model_dump()
    for criterion in evaluation["criteria"]:
        for field in (
            "applicability_reason",
            "applicability_rule_id",
            "applicability_evidence_ids",
        ):
            assert criterion.pop(field) is None
    assert evaluation == original.evaluation.model_dump()


def convert(case, result, branch="founder", **options):
    snapshot, policy, _ = case
    return adapt_baseline_branch_result(
        result,
        branch_id=branch,
        snapshot=options.pop("snapshot", snapshot),
        criteria=options.pop("criteria", policy.criteria),
        industry_evidence_dimensions=options.pop("industry_evidence_dimensions", set()),
        **options,
    )


def failure(case, branch="founder"):
    from datetime import UTC, datetime

    from skala_rag.contracts.errors import WorkflowError

    snapshot, _, terminal = case
    raw = terminal(branch).model_dump()
    raw.update(
        status="failure",
        evaluation=None,
        errors=[
            WorkflowError(
                schema_version=snapshot.schema_version,
                error_id="original-synthetic-error",
                run_id=snapshot.run_id,
                candidate_id=snapshot.candidate_id,
                node=branch,
                error_code="TOOL_TIMEOUT",
                message_redacted="Original synthetic timeout",
                retryable=True,
                attempt=3,
                timestamp=datetime(2026, 9, 1, tzinfo=UTC),
            )
        ],
    )
    return EvaluationResult.model_validate(raw)


def test_failure_fields_and_mutable_lists_are_detached(case):
    original = failure(case)
    out = convert(case, original)
    assert out.status == "failure"
    assert out.evaluations is None
    assert [e.model_dump() for e in out.errors] == [
        e.model_dump() for e in original.errors
    ]
    out.errors[0].message_redacted = "Mutation"
    assert original.errors[0].message_redacted == "Original synthetic timeout"


@pytest.mark.parametrize("branch", ["moat", "business_deal", "traction", "unknown"])
def test_unsupported_branch_rejected_even_for_failure(case, branch):
    with pytest.raises(ValueError, match="Invalid baseline"):
        convert(case, failure(case), branch)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "other-schema"),
        ("run_id", "foreign"),
        ("candidate_id", "foreign"),
        ("evaluation_round", 7),
        ("snapshot_id", "foreign"),
        ("evidence_revision", 9),
        ("policy_version", "v3-operational-1.0.0"),
        ("policy_version", "unknown-policy"),
    ],
)
def test_no_generation_stamping_on_success_or_failure(case, field, value):
    for original in (case[2]("founder"), failure(case)):
        raw = original.model_dump()
        raw[field] = value
        if raw["evaluation"] is not None:
            raw["evaluation"][field] = value
        elif field in ("run_id", "candidate_id", "schema_version"):
            raw["errors"][0][field] = value
        result = EvaluationResult.model_validate(raw)
        with pytest.raises(ValueError, match="Invalid baseline"):
            convert(case, result)
        assert getattr(result, field) == value


@pytest.mark.parametrize("target", ["evaluation", "assessment", "error", "gap"])
def test_nested_schema_mismatch_rejected(case, target):
    original = failure(case) if target == "error" else case[2]("founder")
    raw = original.model_dump()
    if target == "error":
        raw["errors"][0]["schema_version"] = "other-schema"
    elif target == "evaluation":
        raw["evaluation"]["schema_version"] = "other-schema"
    elif target == "assessment":
        raw["evaluation"]["criteria"][0]["schema_version"] = "other-schema"
    else:
        raw["evaluation"]["research_gaps"] = [gap(case, schema_version="other-schema")]
    with pytest.raises(ValueError, match="Invalid baseline"):
        convert(case, EvaluationResult.model_validate(raw))


def gap(case, **overrides):
    snapshot, _, terminal = case
    return (
        dict(
            schema_version=snapshot.schema_version,
            gap_id="synthetic-gap",
            candidate_id=snapshot.candidate_id,
            criterion_id=terminal("founder").evaluation.criteria[0].criterion_id,
            eligibility_field=None,
            missing_fields=["founder detail"],
            reason="Unresolved synthetic evidence",
            priority_weight=2,
            suggested_queries=["Synthetic query"],
            attempted_retrieval_ids=list(snapshot.retrieval_records),
            status="exhausted",
        )
        | overrides
    )


def test_missing_note_gap_caveat_and_detachment_are_lossless(case):
    raw = case[2]("founder").model_dump()
    assessment = raw["evaluation"]["criteria"][0]
    assessment.update(
        status="missing",
        rating=None,
        evidence_ids=[],
        missing_reason="custom-original-reason",
        applicability_note="Not an N/A approval",
    )
    raw["evaluation"]["research_gaps"] = [gap(case)]
    original = EvaluationResult.model_validate(raw)
    before = original.model_dump()
    out = convert(case, original)
    c = out.evaluations["founder"].criteria[0]
    assert c.status == "missing" and c.missing_reason == "custom-original-reason"
    assert c.applicability_note == "Not an N/A approval"
    assert c.applicability_rule_id is None
    assert out.evaluations["founder"].research_gaps[0].model_dump() == gap(case)
    out.evaluations["founder"].research_gaps[0].suggested_queries.append("Mutation")
    out.evaluations["founder"].criteria[1].evidence_ids.clear()
    out.evaluations["founder"].caveats.append("Mutation")
    assert original.model_dump() == before


@pytest.mark.parametrize(
    "kind", ["missing", "duplicate", "unknown", "dimension", "forged", "extra"]
)
def test_corrupt_nested_result_is_revalidated(case, kind):
    original = case[2]("founder").model_copy(deep=True)
    criteria = original.evaluation.criteria
    if kind == "missing":
        criteria.pop()
    elif kind == "duplicate":
        criteria.append(criteria[0].model_copy(deep=True))
    elif kind == "unknown":
        criteria[0].criterion_id = "founder.unregistered"
    elif kind == "dimension":
        original.evaluation.dimension = "technology"
    elif kind == "forged":
        criteria[0] = criteria[0].model_copy(update={"rating": 0})
    else:
        criteria[0] = criteria[0].model_copy(
            update={"unrepresentable_field": "must not drop"}
        )
    with pytest.raises(ValueError, match="Invalid baseline"):
        convert(case, original)


@pytest.mark.parametrize(
    "kind", ["outside", "foreign", "attribution", "industry", "forged_snapshot", "gap"]
)
def test_frozen_evidence_and_gap_attribution(case, kind):
    snapshot = case[0].model_copy(deep=True)
    original = case[2]("founder").model_copy(deep=True)
    c = original.evaluation.criteria[0]
    evidence = snapshot.evidence[c.evidence_ids[0]]
    if kind == "outside":
        c.evidence_ids = ["outside"]
    elif kind == "foreign":
        evidence.candidate_id = "another-company"
    elif kind == "attribution":
        evidence.criterion_ids = ["market.size"]
    elif kind == "industry":
        evidence.scope = "industry"
        evidence.candidate_id = None
    elif kind == "forged_snapshot":
        snapshot.evidence_ids.clear()
    else:
        from skala_rag.contracts.coverage import ResearchGap

        original.evaluation.research_gaps = [
            ResearchGap.model_validate(gap(case, candidate_id="foreign"))
        ]
    with pytest.raises(ValueError, match="Invalid baseline"):
        convert(case, original, snapshot=snapshot)


@pytest.mark.parametrize(
    "kind", ["short", "duplicate", "wrong_dimension", "forged_weight"]
)
def test_complete_supplied_catalog_revalidated(case, kind):
    criteria = list(case[1].criteria)
    if kind == "short":
        criteria.pop()
    elif kind == "duplicate":
        criteria[-1] = criteria[0]
    elif kind == "wrong_dimension":
        criteria = [c.model_copy(update={"dimension": "founder"}) for c in criteria]
    else:
        criteria[-1] = criteria[-1].model_copy(update={"weight": 0})
    with pytest.raises(ValueError, match="Invalid baseline"):
        convert(case, failure(case), criteria=criteria)


@pytest.mark.parametrize(
    "outcome",
    ["success", "failure", "exception", "mutated_generation", "mutated_evidence"],
)
def test_binder_invokes_once_with_detached_original_generation(case, outcome):
    from skala_rag.agents.evaluation_v3_adapter import bind_baseline_evaluator_v3

    snapshot, policy, terminal = case
    before = snapshot.model_dump()
    calls = []
    result = failure(case) if outcome == "failure" else terminal("founder")
    result_before = result.model_dump()

    def upstream(received):
        calls.append(received)
        assert received is not snapshot
        assert received.evidence is not snapshot.evidence
        if outcome == "exception":
            raise RuntimeError("synthetic upstream failure")
        if outcome == "mutated_generation":
            received.policy_version = "forged-policy"
            return result.model_copy(update={"policy_version": "forged-policy"})
        if outcome == "mutated_evidence":
            received.evidence["forged-evidence"] = next(
                iter(received.evidence.values())
            ).model_copy(update={"evidence_id": "forged-evidence"}, deep=True)
            received.evidence_ids.append("forged-evidence")
            forged = result.model_copy(deep=True)
            forged.evaluation.criteria[0].evidence_ids = ["forged-evidence"]
            return forged
        received.evidence.clear()
        received.evidence_ids.clear()
        return result

    bound = bind_baseline_evaluator_v3(
        "founder",
        upstream,
        criteria=policy.criteria,
        industry_evidence_dimensions=set(),
    )
    if outcome == "exception":
        with pytest.raises(RuntimeError, match="synthetic upstream failure"):
            bound(snapshot)
    elif outcome.startswith("mutated_"):
        with pytest.raises(ValueError, match="Invalid baseline"):
            bound(snapshot)
    else:
        out = bound(snapshot)
        assert out.status == outcome
    assert len(calls) == 1
    assert snapshot.model_dump() == before
    assert result.model_dump() == result_before


def test_binder_revalidates_snapshot_before_upstream(case):
    from skala_rag.agents.evaluation_v3_adapter import bind_baseline_evaluator_v3

    snapshot, policy, terminal = case
    calls = []
    bound = bind_baseline_evaluator_v3(
        "founder",
        lambda s: calls.append(s) or terminal("founder"),
        criteria=policy.criteria,
        industry_evidence_dimensions=set(),
    )
    forged = snapshot.model_copy(update={"evidence_ids": []}, deep=True)
    with pytest.raises(ValueError, match="Invalid baseline"):
        bound(forged)
    assert calls == []


def test_no_network_or_provider_in_converter(case, monkeypatch):
    import socket

    import httpx

    from skala_rag.fakes import FakeLLM

    def prohibited(*args, **kwargs):
        pytest.fail("Converter must not request network/provider work")

    monkeypatch.setattr(socket.socket, "connect", prohibited)
    monkeypatch.setattr(httpx.Client, "send", prohibited)
    monkeypatch.setattr(FakeLLM, "generate", prohibited)
    assert convert(case, case[2]("founder")).status == "success"
    assert convert(case, failure(case)).status == "failure"


@pytest.mark.parametrize("branch", ["founder", "market", "technology"])
@pytest.mark.parametrize("status", ["success", "failure"])
def test_wrong_supported_dimension_rejected(case, branch, status):
    result = case[2](branch) if status == "success" else failure(case, branch)
    other = "market" if branch == "founder" else "founder"
    with pytest.raises(ValueError, match="Invalid baseline"):
        convert(case, result, other)


def test_explicit_industry_scope_is_required_and_preserved(case):
    snapshot = case[0].model_copy(deep=True)
    result = case[2]("market")
    cited = result.evaluation.criteria[0].evidence_ids[0]
    snapshot.evidence[cited].scope = "industry"
    snapshot.evidence[cited].candidate_id = None
    with pytest.raises(ValueError, match="Invalid baseline"):
        convert(case, result, "market", snapshot=snapshot)
    out = convert(
        case,
        result,
        "market",
        snapshot=snapshot,
        industry_evidence_dimensions={"market"},
    )
    assert out.evaluations["market"].criteria[0].evidence_ids == [cited]


@pytest.mark.parametrize("policy_version", ["main-draft-0.1.0", "v3-operational-1.0.0"])
def test_synthetic_matching_generation_chosen_before_terminal_creation(
    case, policy_version
):
    from skala_rag.contracts.evaluation import EvaluationSnapshot

    # Synthetic DTO construction, not migrating output from an actual evaluator.
    # Select the generation before creating the snapshot and terminal envelope.
    snapshot_payload = case[0].model_dump()
    snapshot_payload["policy_version"] = policy_version
    snapshot = EvaluationSnapshot.model_validate(
        snapshot_payload, context={"execution_mode": "fixture"}
    )
    raw = case[2]("founder").model_dump()
    raw["policy_version"] = raw["evaluation"]["policy_version"] = policy_version
    synthetic = EvaluationResult.model_validate(raw)
    out = convert(case, synthetic, snapshot=snapshot)
    assert (
        out.policy_version
        == out.evaluations["founder"].policy_version
        == policy_version
    )
    assert synthetic.policy_version == policy_version
    # The original draft fixture is not repaired to fit a different snapshot.
    if policy_version != case[0].policy_version:
        with pytest.raises(ValueError, match="Invalid baseline"):
            convert(case, case[2]("founder"), snapshot=snapshot)


@pytest.mark.parametrize("kind", ["branch", "catalog", "industry", "callable"])
def test_binder_configuration_rejected_without_upstream_work(case, kind):
    from skala_rag.agents.evaluation_v3_adapter import bind_baseline_evaluator_v3

    calls = []
    branch = "moat" if kind == "branch" else "founder"
    criteria = case[1].criteria[:-1] if kind == "catalog" else case[1].criteria
    industry = {"unapproved-dimension"} if kind == "industry" else set()
    upstream = None if kind == "callable" else lambda s: calls.append(s)
    with pytest.raises(ValueError, match="Invalid baseline"):
        bind_baseline_evaluator_v3(
            branch, upstream, criteria=criteria, industry_evidence_dimensions=industry
        )
    assert calls == []


def test_unrepresentable_subclass_fields_are_rejected_not_serialized_away(case):
    from skala_rag.contracts.assessment import CriterionAssessment

    class ExtendedAssessment(CriterionAssessment):
        source_receipt: str

    original = case[2]("founder").model_copy(deep=True)
    assessment = original.evaluation.criteria[0]
    extended = ExtendedAssessment(
        **assessment.model_dump(), source_receipt="unrepresentable-original-receipt"
    )
    original.evaluation.criteria[0] = extended
    with pytest.raises(ValueError, match="Invalid baseline") as rejected:
        convert(case, original)
    assert "unrepresentable-original-receipt" not in str(rejected.value)
    assert original.evaluation.criteria[0].source_receipt == extended.source_receipt


def test_binding_captures_detached_mutable_catalog_and_scope(case):
    from skala_rag.agents.evaluation_v3_adapter import bind_baseline_evaluator_v3

    snapshot, policy, terminal = case
    criteria = list(policy.criteria)
    industry = set()
    bound = bind_baseline_evaluator_v3(
        "founder",
        lambda s: terminal("founder"),
        criteria=criteria,
        industry_evidence_dimensions=industry,
    )
    criteria.clear()
    industry.add("unknown-dimension")
    assert bound(snapshot).status == "success"


@pytest.mark.parametrize("field", ["evidence_id", "excerpt", "schema_version"])
def test_forged_nested_snapshot_evidence_is_revalidated_without_mutation(case, field):
    snapshot = case[0].model_copy(deep=True)
    eid = next(iter(snapshot.evidence))
    value = "wrong-map-key" if field == "evidence_id" else ""
    snapshot.evidence[eid] = snapshot.evidence[eid].model_copy(update={field: value})
    before = snapshot.model_dump()
    with pytest.raises(ValueError, match="Invalid baseline"):
        convert(case, case[2]("founder"), snapshot=snapshot)
    assert snapshot.model_dump() == before


class DetachmentBomb:
    def __init__(self, error_type=RuntimeError):
        self.error_type = error_type

    def __deepcopy__(self, memo):
        raise self.error_type("synthetic detachment diagnostic")

    def __repr__(self):
        raise AssertionError("Malformed payload must not be represented")


class BrokenIndustryCollection(Collection[str]):
    def __len__(self):
        return 0

    def __contains__(self, item):
        return False

    def __iter__(self):
        raise RuntimeError("synthetic collection diagnostic")


def malformed_value(kind):
    if kind == "cycle":
        value = []
        value.append(value)
        return value
    return DetachmentBomb(RecursionError if kind == "recursion" else RuntimeError)


def assert_redacted(rejected):
    assert type(rejected.value) is ValueError
    assert str(rejected.value) == "Invalid baseline-to-v3 adapter input"
    assert rejected.value.__cause__ is None
    assert rejected.value.__suppress_context__ is True

    rendered = "".join(traceback.format_exception(rejected.value))
    assert "synthetic detachment diagnostic" not in rendered
    assert "synthetic collection diagnostic" not in rendered


@pytest.mark.parametrize("kind", ["runtime", "recursion", "cycle"])
@pytest.mark.parametrize("target", ["result", "snapshot", "catalog"])
def test_adapter_detachment_failures_are_redacted(case, kind, target):
    result = case[2]("founder").model_copy(deep=True)
    options = {}
    value = malformed_value(kind)
    if target == "result":
        result.evaluation.criteria[0] = result.evaluation.criteria[0].model_copy(
            update={"rationale": value}
        )
    elif target == "snapshot":
        snapshot = case[0].model_copy(deep=True)
        eid = next(iter(snapshot.evidence))
        snapshot.evidence[eid] = snapshot.evidence[eid].model_copy(
            update={"excerpt": value}
        )
        options["snapshot"] = snapshot
    else:
        criteria = list(case[1].criteria)
        criteria[0] = criteria[0].model_copy(update={"display_name": value})
        options["criteria"] = criteria
    with pytest.raises(ValueError) as rejected:
        convert(case, result, **options)
    assert_redacted(rejected)


def test_adapter_collection_failures_are_redacted(case):
    with pytest.raises(ValueError) as rejected:
        convert(
            case,
            case[2]("founder"),
            industry_evidence_dimensions=BrokenIndustryCollection(),
        )
    assert_redacted(rejected)


@pytest.mark.parametrize("kind", ["runtime", "recursion", "cycle"])
@pytest.mark.parametrize("target", ["catalog", "snapshot", "returned_result"])
def test_binder_detachment_failure_boundaries(case, kind, target):
    from skala_rag.agents.evaluation_v3_adapter import bind_baseline_evaluator_v3

    snapshot, policy, terminal = case
    result = terminal("founder").model_copy(deep=True)
    criteria = list(policy.criteria)
    calls = []
    value = malformed_value(kind)
    if target == "catalog":
        criteria[0] = criteria[0].model_copy(update={"display_name": value})
    elif target == "snapshot":
        snapshot = snapshot.model_copy(deep=True)
        eid = next(iter(snapshot.evidence))
        snapshot.evidence[eid] = snapshot.evidence[eid].model_copy(
            update={"excerpt": value}
        )
    else:
        result.evaluation.criteria[0] = result.evaluation.criteria[0].model_copy(
            update={"rationale": value}
        )
    with pytest.raises(ValueError) as rejected:
        bound = bind_baseline_evaluator_v3(
            "founder",
            lambda received: calls.append(received) or result,
            criteria=criteria,
            industry_evidence_dimensions=set(),
        )
        bound(snapshot)
    assert_redacted(rejected)
    assert len(calls) == (1 if target == "returned_result" else 0)


def test_binder_collection_failure_before_upstream(case):
    from skala_rag.agents.evaluation_v3_adapter import bind_baseline_evaluator_v3

    calls = []
    with pytest.raises(ValueError) as rejected:
        bind_baseline_evaluator_v3(
            "founder",
            lambda received: calls.append(received) or case[2]("founder"),
            criteria=case[1].criteria,
            industry_evidence_dimensions=BrokenIndustryCollection(),
        )
    assert_redacted(rejected)
    assert calls == []


def test_binder_final_snapshot_copy_failure_before_upstream(case, monkeypatch):
    from skala_rag.agents.evaluation_v3_adapter import bind_baseline_evaluator_v3
    from skala_rag.contracts.evaluation import EvaluationSnapshot

    calls = []
    bound = bind_baseline_evaluator_v3(
        "founder",
        lambda received: calls.append(received) or case[2]("founder"),
        criteria=case[1].criteria,
        industry_evidence_dimensions=set(),
    )

    def broken_copy(self, *, update=None, deep=False):
        assert deep is True
        raise RuntimeError("synthetic detachment diagnostic")

    monkeypatch.setattr(EvaluationSnapshot, "model_copy", broken_copy)
    with pytest.raises(ValueError) as rejected:
        bound(case[0])
    assert_redacted(rejected)
    assert calls == []


@pytest.mark.parametrize("error_type", [RuntimeError, RecursionError])
def test_binder_preserves_upstream_exception_identity(case, error_type):
    from skala_rag.agents.evaluation_v3_adapter import bind_baseline_evaluator_v3

    original = error_type("synthetic upstream diagnostic")
    cause = ValueError("synthetic upstream cause")
    calls = []

    def upstream(received):
        calls.append(received)
        raise original from cause

    bound = bind_baseline_evaluator_v3(
        "founder",
        upstream,
        criteria=case[1].criteria,
        industry_evidence_dimensions=set(),
    )
    with pytest.raises(error_type) as raised:
        bound(case[0])
    assert raised.value is original
    assert raised.value.__cause__ is cause
    assert str(raised.value) == "synthetic upstream diagnostic"
    assert len(calls) == 1


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, CancelledError])
@pytest.mark.parametrize("boundary", ["converter", "binder_config", "binder_snapshot"])
def test_adapter_does_not_swallow_process_control(case, error_type, boundary):
    from skala_rag.agents.evaluation_v3_adapter import bind_baseline_evaluator_v3

    value = DetachmentBomb(error_type)
    calls = []
    if boundary == "converter":
        result = case[2]("founder").model_copy(deep=True)
        result.evaluation.criteria[0] = result.evaluation.criteria[0].model_copy(
            update={"rationale": value}
        )
        with pytest.raises(error_type):
            convert(case, result)
    elif boundary == "binder_config":
        criteria = list(case[1].criteria)
        criteria[0] = criteria[0].model_copy(update={"display_name": value})
        with pytest.raises(error_type):
            bind_baseline_evaluator_v3(
                "founder",
                lambda received: calls.append(received) or case[2]("founder"),
                criteria=criteria,
                industry_evidence_dimensions=set(),
            )
    else:
        snapshot = case[0].model_copy(deep=True)
        eid = next(iter(snapshot.evidence))
        snapshot.evidence[eid] = snapshot.evidence[eid].model_copy(
            update={"excerpt": value}
        )
        bound = bind_baseline_evaluator_v3(
            "founder",
            lambda received: calls.append(received) or case[2]("founder"),
            criteria=case[1].criteria,
            industry_evidence_dimensions=set(),
        )
        with pytest.raises(error_type):
            bound(snapshot)
    assert calls == []


def test_documented_offline_python_example_executes_without_network(
    monkeypatch, capsys
):
    import re
    import socket
    from pathlib import Path

    import httpx

    def prohibited(*args, **kwargs):
        pytest.fail("Offline documentation example must not request network work")

    monkeypatch.setattr(socket.socket, "connect", prohibited)
    monkeypatch.setattr(httpx.Client, "send", prohibited)
    root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(root)
    doc = (root / "docs/implementation/evaluation-v3-adapter.md").read_text()
    blocks = re.findall(r"```python\n(.*?)```", doc, re.S)
    assert len(blocks) == 1
    namespace = {"__name__": "offline_doc_example"}
    exec(compile(blocks[0], "evaluation-v3-adapter.md", "exec"), namespace)
    assert namespace["market"].status == namespace["technology"].status == "success"
    assert namespace["technology_receipts"][0].trace
    assert len(namespace["llm"].calls) == 1
    assert "'fake_calls': 1" in capsys.readouterr().out
