"""T25: synthetic provenance and snapshot isolation, no live calls."""

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from skala_rag.contracts.inputs import RunInput
from skala_rag.contracts.state import create_initial_state
from skala_rag.graph.reducers import merge_evidence_with_changes
from skala_rag.graph.snapshot import SnapshotInvalid, freeze_snapshot


@pytest.fixture
def setup():
    payloads = json.loads(
        (Path(__file__).parents[1] / "fixtures/contracts.json").read_text()
    )
    run = RunInput.model_validate(payloads["RunInput"])
    state = create_initial_state(run.model_dump(mode="json"))
    state["sources"] = {"src-synthetic": deepcopy(payloads["Source"])}
    state["chunks"] = {"chunk-synthetic": deepcopy(payloads["Chunk"])}
    state["evidence"] = {"ev-synthetic": deepcopy(payloads["Evidence"])}
    record = deepcopy(payloads["RetrievalRecord"])
    record.update(
        status="ok",
        candidate_id="co-synthetic",
        source_ids=["src-synthetic"],
        chunk_ids=["chunk-synthetic"],
        evidence_ids=["ev-synthetic"],
    )
    state["retrieval_history"] = [record]
    eligibility = deepcopy(payloads["EligibilityResult"])
    eligibility.update(status="eligible", evidence_ids=["ev-synthetic"])
    state["eligibility_results"] = {"co-synthetic": eligibility}
    kwargs = dict(
        run_id="run-synthetic",
        index_version="synthetic-index",
        schema_version="synthetic-1",
        allowed_source_ids={"src-synthetic"},
        industry_evidence_ids=set(),
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    return state, run, kwargs


def freeze(setup):
    state, run, kwargs = setup
    return freeze_snapshot("co-synthetic", state, run, **kwargs)


def add_evidence(state, key, **changes):
    item = deepcopy(state["evidence"]["ev-synthetic"])
    item.update(evidence_id=key, **changes)
    state["evidence"][key] = item
    state["retrieval_history"][0]["evidence_ids"].append(key)
    return item


def test_rounds_and_payload_isolation(setup):
    state, _, _ = setup
    first = freeze(setup)
    original = deepcopy(state["snapshots"])
    state["evidence"]["ev-synthetic"]["limitations"].append("new limitation")
    state["sources"]["src-synthetic"]["bibliographic_metadata"]["pages"].append(3)
    state["chunks"]["chunk-synthetic"]["text"] += " More context"
    state["retrieval_history"][0]["arguments_without_secrets"]["new"] = True
    state["evidence_revisions"]["co-synthetic"] = 1
    second = freeze(setup)
    assert first.evaluation_round == 1 and second.evaluation_round == 2
    assert first.snapshot_id != second.snapshot_id
    assert second.evidence_revision == 1
    assert state["snapshots"][first.snapshot_id] == original[first.snapshot_id]
    assert "new limitation" not in first.evidence["ev-synthetic"].limitations
    second.evidence["ev-synthetic"].limitations.append("return mutation")
    assert (
        "return mutation"
        not in state["snapshots"][second.snapshot_id]["evidence"]["ev-synthetic"][
            "limitations"
        ]
    )


def test_web_rag_merge_keeps_old_snapshot(setup):
    state, _, _ = setup
    state["evidence"]["ev-synthetic"]["provenance"][0].update(
        method="web", chunk_id=None
    )
    first = freeze(setup)
    incoming = deepcopy(state["evidence"]["ev-synthetic"])
    incoming["provenance"][0].update(method="rag", chunk_id="chunk-synthetic")
    merged, changes = merge_evidence_with_changes(
        state["evidence"], {"ev-synthetic": incoming}
    )
    state["evidence"] = merged
    state["evidence_revisions"]["co-synthetic"] = 1
    second = freeze(setup)
    assert changes == {"ev-synthetic"}
    assert len(first.evidence["ev-synthetic"].provenance) == 1
    assert len(second.evidence["ev-synthetic"].provenance) == 2
    assert first.chunks == {}
    assert "chunk-synthetic" in second.chunks


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: s["sources"].clear(),
        lambda s: s["chunks"].clear(),
        lambda s: s["retrieval_history"].clear(),
        lambda s: s["retrieval_history"][0].update(chunk_ids=[]),
        lambda s: s["retrieval_history"][0].update(evidence_ids=[]),
        lambda s: s["retrieval_history"][0].update(source_ids=[]),
        lambda s: s["retrieval_history"][0].update(run_id="other-run"),
        lambda s: s["chunks"]["chunk-synthetic"].update(source_id="other-source"),
        lambda s: s["chunks"]["chunk-synthetic"].update(candidate_ids=["other"]),
        lambda s: s["chunks"]["chunk-synthetic"].update(corpus_version="other"),
        lambda s: s["chunks"]["chunk-synthetic"].update(
            locator="https://wrong.invalid"
        ),
        lambda s: s["evidence"]["ev-synthetic"].update(
            supporting_evidence_ids=["absent"]
        ),
        lambda s: s["evidence"]["ev-synthetic"].update(
            supporting_evidence_ids=["ev-synthetic"]
        ),
        lambda s: s["eligibility_results"]["co-synthetic"].update(
            policy_version="other"
        ),
        lambda s: s["eligibility_results"]["co-synthetic"].update(status="unknown"),
        lambda s: s["evidence"]["ev-synthetic"].update(
            confidence="invalid-secret-value"
        ),
    ],
)
def test_invalid_snapshot_is_atomic_and_redacted(setup, mutation):
    state, _, _ = setup
    mutation(state)
    with pytest.raises(SnapshotInvalid):
        freeze(setup)
    assert state["snapshots"] == {} and state["evaluation_rounds"] == {}
    assert state["errors"][-1]["error_code"] == "SNAPSHOT_INVALID"
    assert state["errors"][-1]["retryable"] is False
    assert "invalid-secret-value" not in state["errors"][-1]["message_redacted"]


def test_superseded_and_transitive_derived_excluded(setup):
    state, _, _ = setup
    add_evidence(
        state,
        "derived",
        evidence_kind="derived",
        supporting_evidence_ids=["ev-synthetic"],
        derivation="Synthetic calculation",
    )
    add_evidence(
        state,
        "derived2",
        evidence_kind="estimated",
        supporting_evidence_ids=["derived"],
        derivation="Synthetic estimate",
    )
    add_evidence(state, "corrected", supersedes="ev-synthetic")
    state["eligibility_results"]["co-synthetic"]["evidence_ids"] = ["corrected"]
    assert freeze(setup).evidence_ids == ["corrected"]


def test_invalidated_eligibility_is_not_rejudged(setup):
    state, _, _ = setup
    add_evidence(state, "corrected", supersedes="ev-synthetic")
    with pytest.raises(SnapshotInvalid, match="Eligibility evidence"):
        freeze(setup)
    assert state["eligibility_results"]["co-synthetic"]["evidence_ids"] == [
        "ev-synthetic"
    ]


def test_conflicts_preserved(setup):
    state, _, _ = setup
    add_evidence(state, "conflict", conflicts_with=["ev-synthetic"])
    state["evidence"]["ev-synthetic"]["conflicts_with"] = ["conflict"]
    snapshot = freeze(setup)
    assert snapshot.evidence["ev-synthetic"].conflicts_with == ["conflict"]
    assert set(snapshot.evidence) == {"conflict", "ev-synthetic"}


@pytest.mark.parametrize("filter_kind", ["source", "event", "value", "allowlist"])
def test_cutoff_or_unapproved_eligibility_fails(setup, filter_kind):
    state, _, kwargs = setup
    if filter_kind == "source":
        state["sources"]["src-synthetic"]["published_at"] = "2026-09-02"
    elif filter_kind == "event":
        state["evidence"]["ev-synthetic"]["event_date"] = "2026-09-02"
    elif filter_kind == "value":
        state["evidence"]["ev-synthetic"].update(
            value=1, unit="million", currency="KRW", value_as_of="2026-09-02"
        )
    else:
        kwargs["allowed_source_ids"] = set()
    with pytest.raises(SnapshotInvalid):
        freeze(setup)


def test_filters_unrelated_and_unconfirmed_industry(setup):
    state, _, kwargs = setup
    add_evidence(state, "other", candidate_id="other-company")
    industry = add_evidence(
        state, "industry", candidate_id=None, scope="industry", provenance=[]
    )
    assert freeze(setup).evidence_ids == ["ev-synthetic"]
    kwargs["industry_evidence_ids"] = {industry["evidence_id"]}
    assert freeze(setup).evidence_ids == ["ev-synthetic", "industry"]


def test_future_correction_does_not_invalidate_past(setup):
    state, _, _ = setup
    add_evidence(state, "future", supersedes="ev-synthetic", event_date="2026-09-02")
    assert freeze(setup).evidence_ids == ["ev-synthetic"]


def test_failure_after_success_keeps_previous_generation(setup):
    state, _, _ = setup
    first = freeze(setup)
    previous = deepcopy(state["snapshots"])
    state["chunks"].clear()
    for _ in range(2):
        with pytest.raises(SnapshotInvalid):
            freeze(setup)
    assert state["snapshots"] == previous
    assert state["evaluation_rounds"] == {"co-synthetic": first.evaluation_round}
    assert [error["attempt"] for error in state["errors"]] == [1, 2]


def test_non_eligibility_future_and_unapproved_evidence_filtered(setup):
    state, _, _ = setup
    add_evidence(state, "future", event_date="2026-09-02")
    excluded = add_evidence(state, "unapproved", source_id="unapproved-source")
    source = deepcopy(state["sources"]["src-synthetic"])
    source["source_id"] = excluded["source_id"]
    state["sources"][source["source_id"]] = source
    assert freeze(setup).evidence_ids == ["ev-synthetic"]


def test_unknown_publication_date_uses_snapshot_date(setup):
    state, _, _ = setup
    state["sources"]["src-synthetic"]["retrieved_at"] = "2026-09-02T00:00:00Z"
    with pytest.raises(SnapshotInvalid):
        freeze(setup)


@pytest.mark.parametrize(
    "field,value",
    [
        ("corpus_version", "different-corpus"),
        ("index_version", "different-index"),
        ("as_of", "2026-09-02"),
    ],
)
def test_retrieval_request_context_mismatch(setup, field, value):
    state, _, _ = setup
    state["retrieval_history"][0]["arguments_without_secrets"][field] = value
    with pytest.raises(SnapshotInvalid, match="request context"):
        freeze(setup)


def test_empty_eligibility_ground_is_rejected(setup):
    state, _, _ = setup
    state["eligibility_results"]["co-synthetic"]["evidence_ids"] = []
    with pytest.raises(SnapshotInvalid):
        freeze(setup)


def test_supersession_cycle_is_rejected(setup):
    state, _, _ = setup
    add_evidence(state, "correction", supersedes="ev-synthetic")
    state["evidence"]["ev-synthetic"]["supersedes"] = "correction"
    with pytest.raises(SnapshotInvalid, match="Cyclic supersession"):
        freeze(setup)


def test_filtered_support_invalidates_derived_chain(setup):
    state, _, _ = setup
    add_evidence(state, "future-input", event_date="2026-09-02")
    add_evidence(
        state,
        "derived",
        evidence_kind="derived",
        supporting_evidence_ids=["future-input"],
    )
    add_evidence(
        state,
        "derived2",
        evidence_kind="estimated",
        supporting_evidence_ids=["derived"],
    )
    assert freeze(setup).evidence_ids == ["ev-synthetic"]


def test_successful_support_closure_and_other_candidate_round(setup):
    state, _, _ = setup
    add_evidence(
        state,
        "derived",
        evidence_kind="derived",
        supporting_evidence_ids=["ev-synthetic"],
    )
    state["evaluation_rounds"]["other-candidate"] = 7
    snapshot = freeze(setup)
    assert set(snapshot.evidence) == {"ev-synthetic", "derived"}
    assert state["evaluation_rounds"]["other-candidate"] == 7
    assert snapshot.evidence["derived"].supporting_evidence_ids == ["ev-synthetic"]


def test_fixture_locator_context_is_propagated(setup):
    state, _, _ = setup
    state["sources"]["src-synthetic"]["url"] = "fixture://source"
    state["chunks"]["chunk-synthetic"]["locator"] = "fixture://source#p1"
    state["evidence"]["ev-synthetic"]["locator"] = "fixture://source#p1"
    assert freeze(setup).chunks["chunk-synthetic"].locator == "fixture://source#p1"
