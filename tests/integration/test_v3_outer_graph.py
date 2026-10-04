"""Real outer-node execution with the existing controlled fixture/oracle."""

from dataclasses import replace

import pytest
from tests.integration import test_v3_candidates as fixtures

from skala_rag.graph import candidates_v3 as oracle


def test_real_outer_loop_stream_and_all_candidates(monkeypatch):
    from skala_rag.graph import candidate_workflow_v3 as outer

    receipts = []

    def run(stages, evaluators, **options):
        graph = outer.build_candidate_workflow_v3(
            stages, evaluators, **options
        ).compile()
        # Explicitly bound using the actual discovery population, not a product cap.
        count = len(stages.discover())
        limit = outer.candidate_recursion_limit_v3(count, options["policy"])
        result = None
        for event in graph.stream(
            {}, {"recursion_limit": limit}, stream_mode="updates", subgraphs=True
        ):
            receipts.append(event)
            namespace, updates = event
            if not namespace and "selector" in updates:
                result = updates["selector"]["result"]
        assert result is not None
        return result

    def retry(request):
        return oracle.ResearchResponseV3(
            request.coverage.candidate_id, request.coverage.evidence_revision, ()
        )

    options = dict(
        run_options={"support_check": lambda c, e: False},
        stages_transform=lambda stages: replace(stages, additional_research=retry),
    )
    expected, _ = fixtures.scenario(**options)
    monkeypatch.setattr(fixtures, "run_candidates_v3", run)
    actual, calls = fixtures.scenario(**options)
    assert actual == expected
    assert len(calls) == 10
    nodes = [
        node for namespace, updates in receipts if not namespace for node in updates
    ]
    assert nodes[:3] == ["discover", "normalize", "candidate_iterator"]
    assert nodes.count("coverage") == 6
    assert nodes.count("research_gate") == 6
    assert nodes.count("additional_research") == 4
    assert nodes.count("advance") == 2
    assert nodes.count("selector") == 1
    assert nodes[-1] == "selector"
    assert any("join_v3" in updates for namespace, updates in receipts if namespace)
    assert any(
        "business_deal" in updates for namespace, updates in receipts if namespace
    )
    assert graph_end(outer, options)  # END is an actual compiled topology edge.


def graph_end(outer, options):
    captured = []
    original = fixtures.run_candidates_v3

    def capture(stages, evaluators, **kwargs):
        graph = outer.build_candidate_workflow_v3(
            stages, evaluators, **kwargs
        ).compile()
        captured.extend(graph.get_graph().edges)
        return oracle.run_candidates_v3(stages, evaluators, **kwargs)

    fixtures.run_candidates_v3 = capture
    try:
        fixtures.scenario(**options)
    finally:
        fixtures.run_candidates_v3 = original
    return any(
        edge.source == "selector" and edge.target == "__end__" for edge in captured
    )


class OpaqueResearchContext:
    """Ordinary caller-owned context, deliberately outside MsgPack's DTO support."""

    def __init__(self, candidate_id, invocation):
        self.candidate_id = candidate_id
        self.invocation = invocation
        self.fields = {"callback_field": [candidate_id, invocation]}
        self.callback = lambda: (candidate_id, invocation)


@pytest.mark.parametrize("representation", ["opaque", "nested", "dto"])
def test_public_callable_preserves_opaque_research_contract(
    monkeypatch, representation
):
    from pydantic import BaseModel, ConfigDict

    from skala_rag.graph.candidate_workflow_v3 import run_candidate_workflow_v3

    class ResearchDTO(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)
        payload: OpaqueResearchContext

    seen = []
    originals = []

    def transform(stages):
        def research(candidate):
            context = OpaqueResearchContext(candidate["candidate_id"], "fixture")
            originals.append(context)
            if representation == "nested":
                return {"nested": [context], "extra_field": {"preserved": True}}
            if representation == "dto":
                return ResearchDTO(payload=context)
            return context

        def inspect_context(stage, candidate, value):
            if representation == "nested":
                assert type(value) is dict
                assert value["extra_field"] == {"preserved": True}
                context = value["nested"][0]
                value["extra_field"]["preserved"] = False
            elif representation == "dto":
                assert isinstance(value, ResearchDTO)
                context = value.payload
            else:
                context = value
            assert isinstance(context, OpaqueResearchContext)
            cid = candidate["candidate_id"]
            assert context.candidate_id == cid
            assert context.invocation == "fixture"
            assert context.fields == {"callback_field": [cid, "fixture"]}
            assert context.callback() == (cid, "fixture")
            assert all(context is not original for original in originals)
            seen.append((stage, cid))
            context.fields["callback_field"].append("mutated-detached-copy")
            context.candidate_id = "mutated-detached-copy"

        def eligibility(candidate, context):
            inspect_context("eligibility", candidate, context)
            result = stages.eligibility(candidate, context)
            # Freeze receives eligibility, not research: preserve this exact
            # contract while verifying callback-derived fields reach freeze.
            result["checks"] = {"opaque_callback_field": candidate["candidate_id"]}
            return result

        def collect(candidate, context):
            inspect_context("collect", candidate, context)
            return stages.collect(candidate, context)

        def additional(request):
            inspect_context("additional_research", request.candidate, request.research)
            assert (request.attempt, request.remaining_requests) in ((1, 1), (2, 0))
            return oracle.ResearchResponseV3(
                request.candidate["candidate_id"],
                request.coverage.evidence_revision,
                (),
            )

        def freeze(candidate, eligibility, coverage):
            assert eligibility.checks == {
                "opaque_callback_field": candidate["candidate_id"]
            }
            seen.append(("freeze", candidate["candidate_id"]))
            return stages.freeze(candidate, eligibility, coverage)

        return replace(
            stages,
            research=research,
            eligibility=eligibility,
            collect=collect,
            additional_research=additional,
            freeze=freeze,
        )

    options = dict(
        stages_transform=transform,
        run_options={"support_check": lambda c, e: False},
    )
    expected, expected_calls = fixtures.scenario(**options)
    expected_seen = list(seen)
    assert expected.candidate_index == 2 and len(expected_calls) == 10
    assert not expected.errors
    seen.clear()
    originals.clear()
    monkeypatch.setattr(fixtures, "run_candidates_v3", run_candidate_workflow_v3)
    actual, calls = fixtures.scenario(**options)
    assert actual == expected
    assert sorted(calls) == sorted(expected_calls)
    assert (
        seen
        == expected_seen
        == [
            (stage, cid)
            for cid in ("company-0", "company-1")
            for stage in (
                "eligibility",
                "collect",
                "additional_research",
                "additional_research",
                "freeze",
            )
        ]
    )
    assert actual.research_retry_count == {"company-0": 2, "company-1": 2}
    for original in originals:
        assert original.fields == {"callback_field": [original.candidate_id, "fixture"]}


def test_opaque_public_calls_repeat_and_overlap_without_cross_run_aliases(monkeypatch):
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor
    from itertools import count
    from threading import Barrier, Lock

    from skala_rag.graph.candidate_workflow_v3 import run_candidate_workflow_v3

    expected, _ = fixtures.scenario()
    serial = count()
    lock = Lock()
    originals = {}
    observations = []

    def transform(stages):
        def research(candidate):
            with lock:
                token = next(serial)
                context = OpaqueResearchContext(candidate["candidate_id"], token)
                originals[token] = context
            return context

        def inspect_context(stage, candidate, context):
            assert isinstance(context, OpaqueResearchContext)
            cid, token = candidate["candidate_id"], context.invocation
            assert context.candidate_id == cid
            assert context.fields == {"callback_field": [cid, token]}
            assert context.callback() == (cid, token)
            with lock:
                assert context is not originals[token]
                observations.append((token, stage, cid))
            context.fields["callback_field"].append("mutated-copy")
            return token

        def eligibility(candidate, context):
            token = inspect_context("eligibility", candidate, context)
            result = stages.eligibility(candidate, context)
            result["checks"] = {"context_token": token}
            return result

        def collect(candidate, context):
            inspect_context("collect", candidate, context)
            return stages.collect(candidate, context)

        def freeze(candidate, eligibility, coverage):
            token = eligibility.checks["context_token"]
            with lock:
                assert originals[token].candidate_id == candidate["candidate_id"]
                observations.append((token, "freeze", candidate["candidate_id"]))
            return stages.freeze(candidate, eligibility, coverage)

        return replace(
            stages,
            research=research,
            eligibility=eligibility,
            collect=collect,
            freeze=freeze,
        )

    def run(stages, evaluators, **options):
        def execute(_):
            # Separate real five-way barriers for every invocation/candidate,
            # including simultaneous invocations with the same run_id.
            barriers = {cid: Barrier(5, timeout=5) for cid in expected.outcomes}

            def branch(name, snapshot):
                barriers[snapshot.candidate_id].wait()
                return evaluators[name](snapshot)

            callbacks = {
                name: lambda snapshot, name=name: branch(name, snapshot)
                for name in evaluators
            }
            return run_candidate_workflow_v3(stages, callbacks, **options)

        first, second = execute(None), execute(None)
        with ThreadPoolExecutor(max_workers=2) as executor:
            overlapping = list(executor.map(execute, range(2)))
        assert first == second == expected
        assert overlapping == [expected, expected]
        return first

    monkeypatch.setattr(fixtures, "run_candidates_v3", run)
    actual, calls = fixtures.scenario(stages_transform=transform)
    assert actual == expected
    assert len(calls) == 40
    assert len(originals) == 8
    assert Counter(stage for _, stage, _ in observations) == {
        "eligibility": 8,
        "collect": 8,
        "freeze": 8,
    }
    for token, original in originals.items():
        assert original.fields == {"callback_field": [original.candidate_id, token]}
        assert [(stage, cid) for t, stage, cid in observations if t == token] == [
            (stage, original.candidate_id)
            for stage in ("eligibility", "collect", "freeze")
        ]


# Re-execute the unchanged controller acceptance cases at the new graph surface.
# Each callback is called only by this surface, so request/clock/error assertions
# remain the originals rather than a second, weaker set of expectations.
def _acceptance_cases():
    from itertools import product

    from tests.integration import test_v3_research_loop as loop_cases

    cases = []
    for module in (fixtures, loop_cases):
        for name, function in vars(module).items():
            if not name.startswith("test_") or not callable(function):
                continue
            variants = [{}]
            for mark in getattr(function, "pytestmark", []):
                if mark.name != "parametrize":
                    continue
                names, values = mark.args[:2]
                names = (
                    [n.strip() for n in names.split(",")]
                    if isinstance(names, str)
                    else names
                )
                rows = [
                    dict(zip(names, (value,) if len(names) == 1 else value))
                    for value in values
                ]
                variants = [
                    {**left, **right} for left, right in product(variants, rows)
                ]
            for index, arguments in enumerate(variants):
                cases.append(
                    (
                        f"{module.__name__.split('.')[-1]}::{name}[{index}]",
                        function,
                        arguments,
                    )
                )
    return cases


@pytest.mark.parametrize(
    "name,case,arguments",
    _acceptance_cases(),
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_existing_acceptance_at_outer_surface(monkeypatch, name, case, arguments):
    from skala_rag.graph.candidate_workflow_v3 import run_candidate_workflow_v3

    # Exercise the public normalized-population handoff as well as the real nodes;
    # every original acceptance assertion remains unchanged.
    monkeypatch.setattr(fixtures, "run_candidates_v3", run_candidate_workflow_v3)
    case(**arguments)


def test_callable_derives_limit_after_normalize_and_streams_real_end(monkeypatch):
    from skala_rag.graph import candidate_workflow_v3 as outer

    receipts = []
    callbacks = []
    bound_counts = []
    derive_limit = outer.candidate_recursion_limit_v3

    def record_limit(count, policy):
        bound_counts.append(count)
        return derive_limit(count, policy)

    monkeypatch.setattr(outer, "candidate_recursion_limit_v3", record_limit)

    def transform(stages):
        def discover():
            callbacks.append("discover")
            return stages.discover()

        def normalize(items):
            callbacks.append("normalize")
            return items[:10]

        return replace(stages, discover=discover, normalize=normalize)

    options = dict(
        statuses=("eligible",) * 12, ratings=(5,) * 12, stages_transform=transform
    )
    expected, _ = fixtures.scenario(**options)
    callbacks.clear()

    def run(stages, evaluators, **kwargs):
        return outer.run_candidate_workflow_v3(
            stages, evaluators, graph_events=receipts, **kwargs
        )

    monkeypatch.setattr(fixtures, "run_candidates_v3", run)
    actual, calls = fixtures.scenario(**options)
    assert actual == expected
    assert actual.candidate_index == 10
    assert len(calls) == 50
    assert callbacks == ["discover", "normalize"]
    assert bound_counts == [10]  # Not the discovery population of twelve.
    assert sum(not ns and "__interrupt__" in updates for ns, updates in receipts) == 1
    nodes = [
        node
        for namespace, updates in receipts
        if not namespace
        for node in updates
        if node != "__interrupt__"
    ]
    assert nodes.count("discover") == nodes.count("normalize") == 1
    assert nodes.count("advance") == 10
    assert nodes[-1] == "selector"


def test_research_context_type_and_detached_callbacks_preserved(monkeypatch):
    from skala_rag.contracts.candidates import Candidate
    from skala_rag.graph import candidate_workflow_v3 as outer

    seen = []

    def transform(stages):
        def research(candidate):
            return Candidate.model_validate(
                candidate, context={"execution_mode": "fixture"}
            )

        def eligibility(candidate, context):
            assert isinstance(context, Candidate)
            seen.append(("eligibility", context.candidate_id))
            result = stages.eligibility(candidate, context)
            context.candidate_id = "mutated-copy"
            candidate["candidate_id"] = "mutated-candidate-copy"
            return result

        def collect(candidate, context):
            assert isinstance(context, Candidate)
            assert context.candidate_id == candidate["candidate_id"]
            seen.append(("collect", context.candidate_id))
            return stages.collect(candidate, context)

        return replace(
            stages, research=research, eligibility=eligibility, collect=collect
        )

    expected, _ = fixtures.scenario(stages_transform=transform)
    seen.clear()

    def run(stages, evaluators, **kwargs):
        return outer.run_candidate_workflow_v3(stages, evaluators, **kwargs)

    monkeypatch.setattr(fixtures, "run_candidates_v3", run)
    actual, calls = fixtures.scenario(stages_transform=transform)
    assert actual == expected
    assert len(calls) == 10
    assert seen == [
        (stage, cid)
        for cid in ("company-0", "company-1")
        for stage in ("eligibility", "collect")
    ]


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"statuses": ("unknown", "ineligible")},
        {"statuses": ()},
        {"ratings": (3, 3)},
        {"ratings": (1, 1)},
        {"broken": {"company-0"}},
        {"stale": {"company-0"}},
        {"discovery_failure": True},
        {"run_options": {"support_check": lambda c, e: False}},
    ],
)
def test_callable_full_semantic_equivalence(monkeypatch, options):
    from skala_rag.graph.candidate_workflow_v3 import run_candidate_workflow_v3

    expected, expected_calls = fixtures.scenario(**options)
    monkeypatch.setattr(fixtures, "run_candidates_v3", run_candidate_workflow_v3)
    actual, calls = fixtures.scenario(**options)
    assert actual == expected
    assert sorted(calls) == sorted(expected_calls)


def test_outer_reuses_real_five_way_barrier_and_has_no_shared_run_state(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from skala_rag.graph.candidate_workflow_v3 import (
        build_candidate_workflow_v3,
        candidate_recursion_limit_v3,
    )

    expected, _ = fixtures.scenario()

    def run(stages, evaluators, **options):
        # A barrier is created per candidate; a sequential five-branch execution
        # cannot get past it, and becomes a failure instead of a synthetic pass.
        barriers = {cid: Barrier(5, timeout=5) for cid in expected.outcomes}

        def branch(name, snapshot):
            barriers[snapshot.candidate_id].wait()
            return evaluators[name](snapshot)

        callbacks = {
            name: lambda snapshot, name=name: branch(name, snapshot)
            for name in evaluators
        }
        graph = build_candidate_workflow_v3(stages, callbacks, **options).compile()
        config = {"recursion_limit": candidate_recursion_limit_v3(2, options["policy"])}
        first = graph.invoke({}, config)["result"]
        # Reusing the compiled graph starts from a fresh node-local container.
        second = graph.invoke({}, config)["result"]
        assert first == second == expected
        with ThreadPoolExecutor(max_workers=2) as executor:
            concurrent = list(
                executor.map(lambda _: graph.invoke({}, config)["result"], range(2))
            )
        assert concurrent == [expected, expected]
        return first

    monkeypatch.setattr(fixtures, "run_candidates_v3", run)
    actual, calls = fixtures.scenario()
    assert actual == expected
    assert len(calls) == 40  # Four genuine invocations, ten branch calls each.
