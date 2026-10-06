"""Synthetic profile selection through the public fixture-only controllers."""

from copy import deepcopy
from dataclasses import replace

import pytest
from tests.integration import test_v3_candidates as fixtures

from skala_rag import fixture_runner, run_settings
from skala_rag.contracts import Candidate
from skala_rag.graph import candidate_workflow_v3 as outer


def profile(run_id="run"):
    return run_settings.recommended_profile(
        run_id=run_id,
        selection_source="synthetic-controller-test",
        authority_reference="caller-declaration-not-attestation",
        policy_references=("synthetic-policy-reference",),
        code_version=None,
    )


def distinct_stages(stages, seen, *, duplicate=False):
    def discover():
        seen.append(("discover", None))
        items = stages.discover()
        for item in items:
            cid = item["candidate_id"]
            item["canonical_name"] = f"Synthetic {cid}"
            item["homepage_url"] = f"fixture://{cid}/homepage"
            item["legal_identifiers"] = {"fixture_registry": f"synthetic-{cid}"}
            item["discovery_source_ids"] = [f"synthetic-source-{cid}"]
        if duplicate:
            copied = deepcopy(items[0])
            copied.update(
                candidate_id="duplicate-0",
                canonical_name="Synthetic alias of company-0",
                discovery_source_ids=["synthetic-duplicate-source"],
            )
            items.append(copied)
        return items

    def normalize(items):
        raise AssertionError("profile path must bypass legacy normalize")

    def research(candidate):
        seen.append(("research", candidate["candidate_id"]))
        return stages.research(candidate)

    return replace(stages, discover=discover, normalize=normalize, research=research)


def test_oracle_profile_normalizes_once_and_processes_receipt_order(monkeypatch):
    seen = []
    selections = []
    original = run_settings.normalize_and_select

    def record(items, **kwargs):
        result = original(items, **kwargs)
        selections.append(result.receipt)
        return result

    monkeypatch.setattr(run_settings, "normalize_and_select", record)
    trace = []
    result, calls = fixtures.scenario(
        statuses=("eligible",) * 7,
        ratings=(5,) * 7,
        stages_transform=lambda stages: distinct_stages(stages, seen, duplicate=True),
        run_options={"run_profile": profile(), "trace_events": trace},
    )
    assert len(selections) == 1
    receipt = selections[0]
    assert result.selection_receipt == receipt
    assert receipt.execution_mode == "fixture"
    assert (
        tuple(cid for stage, cid in seen if stage == "research") == receipt.selected_ids
    )
    assert tuple(result.outcomes) == receipt.selected_ids
    assert result.candidate_index == len(result.scores) == 5
    assert len(calls) == 25
    assert {cid for cid, _ in calls} == set(receipt.selected_ids)
    assert not (set(receipt.excluded_ids) | {"duplicate-0"}) & result.outcomes.keys()
    assert receipt.merges[0].merged_candidate_id == "duplicate-0"
    assert sum(e["step"] == "selector" for e in trace) == 1
    assert all(d.label.startswith("RECOMMEND") for d in result.decisions.values())


def test_outer_profile_handoff_keeps_receipt_without_resampling(monkeypatch):
    events = []
    seen = []
    selections = []
    bounds = []
    normalizer = run_settings.normalize_and_select
    derive_bound = outer.candidate_recursion_limit_v3

    def record(items, **kwargs):
        selected = normalizer(items, **kwargs)
        selections.append(selected.receipt)
        return selected

    def bound(count, policy):
        bounds.append(count)
        return derive_bound(count, policy)

    def run(stages, evaluators, **kwargs):
        return outer.run_candidate_workflow_v3(
            stages, evaluators, graph_events=events, **kwargs
        )

    monkeypatch.setattr(run_settings, "normalize_and_select", record)
    monkeypatch.setattr(outer, "candidate_recursion_limit_v3", bound)
    monkeypatch.setattr(fixtures, "run_candidates_v3", run)
    result, calls = fixtures.scenario(
        statuses=("eligible",) * 12,
        ratings=(5,) * 12,
        stages_transform=lambda stages: distinct_stages(stages, seen, duplicate=True),
        run_options={"run_profile": profile()},
    )
    assert len(selections) == 1
    receipt = selections[0]
    assert result.selection_receipt == receipt
    assert bounds == [5]
    assert (
        tuple(cid for stage, cid in seen if stage == "research") == receipt.selected_ids
    )
    assert result.candidate_index == 5
    assert len(calls) == 25
    nodes = [name for ns, updates in events if not ns for name in updates]
    assert nodes.count("discover") == nodes.count("normalize") == 1
    assert nodes.count("selector") == 1
    assert nodes[-1] == "selector"
    for ns, updates in events:
        if not ns:
            for name, update in updates.items():
                if name not in ("discover", "__interrupt__"):
                    assert update["data"]["selection_receipt"] == receipt


@pytest.fixture(params=["oracle", "outer", "builder"])
def controller(request, monkeypatch):
    if request.param == "outer":
        monkeypatch.setattr(
            fixtures, "run_candidates_v3", outer.run_candidate_workflow_v3
        )
    elif request.param == "builder":

        def run(stages, evaluators, **options):
            graph = outer.build_candidate_workflow_v3(
                stages, evaluators, **options
            ).compile()
            limit = outer.candidate_recursion_limit_v3(5, options["policy"])
            return graph.invoke({}, {"recursion_limit": limit})["result"]

        monkeypatch.setattr(fixtures, "run_candidates_v3", run)
    return request.param


@pytest.mark.parametrize(
    "count,duplicate,rng_count",
    [
        (0, False, 0),
        (1, False, 0),
        (5, False, 0),
        (5, True, 0),
        (6, False, 1),
        (12, True, 1),
    ],
)
def test_public_profile_dedup_sample_boundaries(
    controller, monkeypatch, count, duplicate, rng_count
):
    draws = []
    normalizations = []
    random_class = run_settings.random.Random
    normalize = run_settings.discovery.normalize_candidates

    class RecordingRandom(random_class):
        def __init__(self, seed):
            draws.append(("rng", seed))
            super().__init__(seed)

        def sample(self, population, k, *, counts=None):
            draws.append(("sample", tuple(population), k))
            return super().sample(population, k, counts=counts)

    def record(items, **kwargs):
        normalizations.append(tuple(c.candidate_id for c in items))
        return normalize(items, **kwargs)

    monkeypatch.setattr(run_settings.random, "Random", RecordingRandom)
    monkeypatch.setattr(run_settings.discovery, "normalize_candidates", record)
    seen = []
    trace = []
    result, calls = fixtures.scenario(
        statuses=("eligible",) * count,
        ratings=(5,) * count,
        stages_transform=lambda stages: distinct_stages(
            stages, seen, duplicate=duplicate
        ),
        run_options={"run_profile": profile(), "trace_events": trace},
    )
    receipt = result.selection_receipt
    assert receipt is not None
    assert len(normalizations) == 1
    assert len([d for d in draws if d[0] == "rng"]) == rng_count
    assert len([d for d in draws if d[0] == "sample"]) == rng_count
    if rng_count:
        assert draws == [("rng", 42), ("sample", receipt.population_ids_ascii, 5)]
    else:
        assert receipt.selected_ids == tuple(f"company-{i}" for i in range(count))
    assert (
        tuple(cid for stage, cid in seen if stage == "research") == receipt.selected_ids
    )
    assert tuple(result.outcomes) == receipt.selected_ids
    assert len(calls) == 5 * min(count, 5)
    assert result.candidate_index == min(count, 5)
    assert sum(e["step"] == "selector" for e in trace) == 1


@pytest.mark.parametrize(
    "poison", ["run_id", "seed", "bool", "references", "not-profile"]
)
def test_invalid_profile_binding_fails_before_callbacks(controller, poison):
    chosen = profile("different-run") if poison == "run_id" else profile()
    if poison == "seed":
        object.__setattr__(chosen, "seed", 43)
    elif poison == "bool":
        object.__setattr__(chosen, "initial_company_research", True)
    elif poison == "references":
        object.__setattr__(chosen, "policy_references", ["not-immutable"])
    elif poison == "not-profile":
        chosen = {"run_id": "run"}
    seen = []
    with pytest.raises(ValueError):
        fixtures.scenario(
            stages_transform=lambda stages: distinct_stages(stages, seen),
            run_options={"run_profile": chosen},
        )
    assert seen == []


@pytest.mark.parametrize(
    "poison", ["dto", "id-reuse", "source-ids", "legal", "profile-id"]
)
def test_poisoned_discovery_rejects_before_research(controller, poison):
    seen = []

    def transform(stages):
        changed = distinct_stages(stages, seen)

        def discover():
            items = changed.discover()
            if poison == "dto":
                dto = Candidate.model_validate(
                    items[0], context={"execution_mode": "fixture"}
                )
                items[0] = dto.model_copy(update={"aliases": [None]})
            elif poison == "id-reuse":
                items[1]["candidate_id"] = items[0]["candidate_id"]
            elif poison == "source-ids":
                items[1]["discovery_source_ids"] = [False]
            elif poison == "legal":
                items[1]["legal_identifiers"] = {"fixture_registry": []}
            else:
                items[1]["candidate_id"] = "company Unicode 한글"
            return items

        return replace(changed, discover=discover)

    result, calls = fixtures.scenario(
        stages_transform=transform,
        run_options={"run_profile": profile()},
    )
    assert result.status == "discovery_failed"
    assert result.selection_receipt is None
    assert seen == [("discover", None)]
    assert not calls and not result.outcomes and not result.scores


def test_selected_unknown_skips_all_downstream_callbacks_without_refill(controller):
    seen = []
    callbacks = []
    trace = []

    def transform(stages):
        changed = distinct_stages(stages, seen, duplicate=True)

        def eligibility(candidate, research):
            result = stages.eligibility(candidate, research)
            cid = candidate["candidate_id"]
            callbacks.append(("eligibility", cid))
            if cid == next(cid for stage, cid in seen if stage == "research"):
                result["status"] = "unknown"
            return result

        def collect(candidate, research):
            callbacks.append(("collect", candidate["candidate_id"]))
            return stages.collect(candidate, research)

        def freeze(candidate, eligible, coverage):
            callbacks.append(("freeze", candidate["candidate_id"]))
            return stages.freeze(candidate, eligible, coverage)

        def retry(request):
            callbacks.append(("retry", request.candidate["candidate_id"]))
            raise AssertionError("no retry on sufficient coverage or unknown")

        return replace(
            changed,
            eligibility=eligibility,
            collect=collect,
            freeze=freeze,
            additional_research=retry,
        )

    result, calls = fixtures.scenario(
        statuses=("eligible",) * 9,
        ratings=(5,) * 9,
        stages_transform=transform,
        run_options={"run_profile": profile(), "trace_events": trace},
    )
    receipt = result.selection_receipt
    assert receipt is not None
    unknown = receipt.selected_ids[0]
    assert result.outcomes[unknown].status == "eligibility_unknown"
    assert callbacks.count(("eligibility", unknown)) == 1
    assert ("collect", unknown) not in callbacks and (
        "freeze",
        unknown,
    ) not in callbacks
    assert not any(step == "retry" for step, _ in callbacks)
    assert not any(cid == unknown for cid, _ in calls)
    assert result.research_retry_count[unknown] == 0
    assert tuple(result.outcomes) == receipt.selected_ids
    assert len(result.scores) == 4 and len(calls) == 20
    assert not (set(receipt.excluded_ids) | {"duplicate-0"}) & {
        cid for _, cid in callbacks
    }
    for cid in receipt.selected_ids:
        steps = [e["step"] for e in trace if e["candidate_id"] == cid]
        assert steps.count("archive") == steps.count("advance") == 1


def test_fixture_profile_opt_in_runs_actual_outer_source_consumer(monkeypatch):
    def forbidden_oracle(*args, **kwargs):
        raise AssertionError("profile source runner must use the actual outer callable")

    monkeypatch.setattr(fixture_runner, "run_candidates_v3", forbidden_oracle)
    trace = []
    result, calls = fixture_runner.run_fixture(
        statuses=("eligible",) * 7,
        ratings=(5,) * 7,
        run_profile=profile(),
        trace=trace,
    )
    assert result.selection_receipt is not None
    assert len(result.selection_receipt.normalized_ids) == 7
    assert tuple(result.outcomes) == result.selection_receipt.selected_ids
    assert result.candidate_index == len(result.scores) == 5
    assert len(calls) == 25
    assert any(e.get("controller") == "langgraph" for e in trace)
    assert all(e["execution_mode"] == "fixture" for e in trace)


def test_fixture_mismatched_profile_fails_before_custom_callback():
    seen = []

    def catalog_callback(catalog):
        seen.append("catalog")
        return catalog

    with pytest.raises(ValueError, match="run_id mismatch"):
        fixture_runner.run_fixture(
            run_profile=profile("other-run"),
            catalog_mutation=catalog_callback,
        )
    assert seen == []


def test_profile_kept_order_and_merged_metadata_are_detached(controller):
    seen = []
    received = []

    def transform(stages):
        changed = distinct_stages(stages, seen, duplicate=True)

        def discover():
            items = changed.discover()
            return [*reversed(items[:-1]), items[-1]]

        def research(candidate):
            received.append(deepcopy(candidate))
            value = changed.research(candidate)
            candidate["aliases"].append("callback mutation")
            candidate["legal_identifiers"].clear()
            candidate["discovery_source_ids"].clear()
            return value

        def eligibility(candidate, context):
            assert candidate == received[-1]
            return stages.eligibility(candidate, context)

        return replace(
            changed, discover=discover, research=research, eligibility=eligibility
        )

    result, _ = fixtures.scenario(
        statuses=("eligible",) * 5,
        ratings=(5,) * 5,
        stages_transform=transform,
        run_options={"run_profile": profile()},
    )
    receipt = result.selection_receipt
    assert receipt is not None
    assert receipt.selected_ids == tuple(f"company-{i}" for i in reversed(range(5)))
    assert tuple(result.outcomes) == receipt.selected_ids
    assert received[-1]["discovery_source_ids"] == [
        "synthetic-source-company-0",
        "synthetic-duplicate-source",
    ]
    assert "Synthetic alias of company-0" in received[-1]["aliases"]
    assert "callback mutation" not in receipt.to_json()
    assert len(result.scores) == 5


@pytest.mark.parametrize("failure", ["atomic-branch", "generation", "zero-denominator"])
def test_profile_preserves_failure_guards_and_advances(controller, failure):
    seen = []
    options = {}
    if failure == "atomic-branch":
        options["broken"] = {"company-0"}
    elif failure == "generation":
        options["stale"] = {"company-0"}
    else:
        options["na"] = {"market.size", "market.growth", "market.demand"}
    result, calls = fixtures.scenario(
        statuses=("eligible",) * 5,
        ratings=(5,) * 5,
        stages_transform=lambda stages: distinct_stages(stages, seen),
        run_options={"run_profile": profile()},
        **options,
    )
    receipt = result.selection_receipt
    assert receipt is not None and tuple(result.outcomes) == receipt.selected_ids
    assert result.candidate_index == 5
    assert result.outcomes["company-0"].status == "failed"
    assert "company-0" not in result.scores and "company-0" not in result.decisions
    if failure == "zero-denominator":
        assert not result.scores and not result.decisions
        assert result.selection.selected_candidate_id is None
        assert len(calls) == 25
    else:
        assert len(result.scores) == 4
        assert result.selection.selected_candidate_id == "company-1"
        assert len(calls) == (20 if failure == "generation" else 25)


def test_profile_declarations_do_not_open_live_policy(controller, monkeypatch):
    run = fixtures.run_candidates_v3
    seen = []

    def deny(stages, evaluators, **options):
        options["policy"] = options["policy"].model_copy(
            update={"execution_mode": "live"}
        )
        return run(stages, evaluators, **options)

    monkeypatch.setattr(fixtures, "run_candidates_v3", deny)
    with pytest.raises(ValueError, match="fixture V3Policy required"):
        fixtures.scenario(
            stages_transform=lambda stages: distinct_stages(stages, seen),
            run_options={"run_profile": profile()},
        )
    assert seen == []


def test_profile_binding_is_pinned_before_discovery(controller):
    chosen = profile()
    seen = []

    def transform(stages):
        changed = distinct_stages(stages, seen)

        def discover():
            object.__setattr__(chosen, "seed", 43)
            return changed.discover()

        return replace(changed, discover=discover)

    result, _ = fixtures.scenario(
        statuses=("eligible",) * 6,
        ratings=(5,) * 6,
        stages_transform=transform,
        run_options={"run_profile": chosen},
    )
    assert chosen.seed == 43
    assert result.selection_receipt is not None
    assert result.selection_receipt.profile.seed == 42
    assert len(result.scores) == 5


def test_source_runner_none_keeps_legacy_oracle_and_uncapped_identity(monkeypatch):
    def forbidden_outer(*args, **kwargs):
        raise AssertionError("None must keep the legacy oracle path")

    monkeypatch.setattr(fixture_runner, "run_candidate_workflow_v3", forbidden_outer)
    trace = []
    result, calls = fixture_runner.run_fixture(
        statuses=("eligible",) * 6,
        ratings=(5,) * 6,
        trace=trace,
    )
    assert result.selection_receipt is None
    assert result.candidate_index == len(result.scores) == 6
    assert len(calls) == 30
    assert any(e.get("controller") == "python" for e in trace)
    assert not any(e.get("controller") == "langgraph" for e in trace)


@pytest.mark.parametrize("statuses", [(), ("unknown", "ineligible"), ("eligible",) * 7])
def test_source_outer_profile_result_equals_real_public_oracle(monkeypatch, statuses):
    from skala_rag.graph.candidates_v3 import run_candidates_v3

    expected = []
    actual_outer = fixture_runner.run_candidate_workflow_v3

    def callthrough(stages, evaluators, **options):
        # This diagnostic wrapper still executes the real production outer flow.
        expected.append(run_candidates_v3(stages, evaluators, **options))
        return actual_outer(stages, evaluators, **options)

    monkeypatch.setattr(fixture_runner, "run_candidate_workflow_v3", callthrough)
    result, _ = fixture_runner.run_fixture(
        statuses=statuses,
        ratings=(5,) * len(statuses),
        run_profile=profile(),
    )
    assert expected == [result]
