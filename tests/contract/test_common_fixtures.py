"""T01: 공통 가상 fixture schema·참조·catalog 검증."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.fixtures.loader import load_common_fixtures

from skala_rag.contracts.ids import eligibility_result_id, snapshot_id
from skala_rag.scoring.catalog import load_policy

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/common.json"


@pytest.fixture
def catalog():
    return load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")


def test_four_cases_and_same_name_are_distinct(catalog):
    data = load_common_fixtures(catalog)
    assert set(data.cases) == {"eligible", "ineligible", "unknown", "same_name"}
    assert len(data.candidates) == 4
    status = {e.candidate_id: e.status for e in data.eligibility_results.values()}
    assert status[data.cases["eligible"]] == "eligible"
    assert status[data.cases["ineligible"]] == "ineligible"
    assert status[data.cases["unknown"]] == "unknown"
    first = data.candidates[data.cases["eligible"]]
    second = data.candidates[data.cases["same_name"]]
    assert first.canonical_name == second.canonical_name
    assert first.candidate_id != second.candidate_id
    assert first.country != second.country
    assert first.homepage_url != second.homepage_url
    assert first.legal_identifiers != second.legal_identifiers
    assert data.profiles[data.cases["unknown"]].is_listed is None


def test_six_evaluations_cover_catalog_and_include_missing(catalog):
    data = load_common_fixtures(catalog)
    assert {e.dimension for e in data.evaluations.values()} == set(
        catalog.dimension_weights
    )
    assert len(data.evaluations) == 6
    assessments = [c for e in data.evaluations.values() for c in e.criteria]
    assert len(assessments) == 23
    assert {a.criterion_id for a in assessments} == {
        c.criterion_id for c in catalog.criteria
    }
    missing = [a for a in assessments if a.status == "missing"]
    assert len(missing) == 1
    assert missing[0].rating is None and missing[0].missing_reason


def test_trace_ids_and_fixture_hashes_are_reproducible(catalog):
    data = load_common_fixtures(catalog)
    for snapshot in data.snapshots.values():
        assert snapshot.snapshot_id == snapshot_id(
            snapshot.run_id,
            snapshot.candidate_id,
            snapshot.evaluation_round,
            snapshot.evidence_revision,
            snapshot.policy_version,
        )
    for result in data.eligibility_results.values():
        assert result.eligibility_result_id == eligibility_result_id(
            result.run_id,
            result.candidate_id,
            result.evidence_revision,
            result.policy_version,
        )
    for chunk in data.chunks.values():
        assert (
            data.sources[chunk.source_id].content_hash
            == hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
        )
    for evaluation in data.evaluations.values():
        snapshot = data.snapshots[evaluation.snapshot_id]
        for assessment in evaluation.criteria:
            for eid in assessment.evidence_ids:
                evidence = snapshot.evidence[eid]
                for provenance in evidence.provenance:
                    record = snapshot.retrieval_records[provenance.retrieval_id]
                    assert provenance.chunk_id in record.chunk_ids
                    assert (
                        snapshot.chunks[provenance.chunk_id].source_id
                        == evidence.source_id
                    )


def test_each_load_has_independent_objects(catalog):
    first = load_common_fixtures(catalog)
    second = load_common_fixtures(catalog)
    key = first.cases["eligible"]
    first.candidates[key].aliases.append("변경")
    next(iter(first.snapshots.values())).evidence.clear()
    assert second.candidates[key].aliases == []
    assert next(iter(second.snapshots.values())).evidence
    json.dumps(second.model_dump(mode="json"), allow_nan=False)


@pytest.mark.parametrize(
    "problem",
    [
        "source",
        "candidate",
        "profile_evidence",
        "stage_source",
        "chunk_source",
        "provenance_record",
        "provenance_chunk",
        "retrieval_evidence",
        "supporting",
        "assessment_evidence",
        "snapshot_source",
        "snapshot_payload",
        "evaluation_snapshot",
        "evaluation_round",
        "criterion_duplicate",
        "criterion_missing",
        "policy",
        "id_key",
        "locator",
        "schema",
    ],
)
def test_invalid_fixture_rejected(catalog, tmp_path, problem):
    payload = json.loads(FIXTURE.read_text())
    candidate = payload["cases"]["eligible"]
    evidence = next(iter(payload["evidence"].values()))
    chunk = next(iter(payload["chunks"].values()))
    record = next(iter(payload["retrieval_records"].values()))
    snapshot = next(iter(payload["snapshots"].values()))
    evaluation = next(iter(payload["evaluations"].values()))
    if problem == "source":
        evidence["source_id"] = "absent"
    elif problem == "candidate":
        payload["cases"]["eligible"] = "absent"
    elif problem == "profile_evidence":
        payload["profiles"][candidate]["field_evidence_ids"]["fixture_profile"] = [
            "absent"
        ]
    elif problem == "stage_source":
        payload["profiles"][candidate]["stage"]["source_ids"] = ["absent"]
    elif problem == "chunk_source":
        chunk["source_id"] = "absent"
    elif problem == "provenance_record":
        evidence["provenance"][0]["retrieval_id"] = "absent"
    elif problem == "provenance_chunk":
        evidence["provenance"][0]["chunk_id"] = "absent"
    elif problem == "retrieval_evidence":
        record["evidence_ids"].append("absent")
    elif problem == "supporting":
        evidence["supporting_evidence_ids"] = ["absent"]
    elif problem == "assessment_evidence":
        evaluation["criteria"][0]["evidence_ids"] = ["absent"]
    elif problem == "snapshot_source":
        snapshot["sources"].clear()
    elif problem == "snapshot_payload":
        next(iter(snapshot["evidence"].values()))["claim"] = "변경"
    elif problem == "evaluation_snapshot":
        evaluation["snapshot_id"] = "absent"
    elif problem == "evaluation_round":
        evaluation["evaluation_round"] += 1
    elif problem == "criterion_duplicate":
        evaluation["criteria"].append(evaluation["criteria"][0])
    elif problem == "criterion_missing":
        evaluation["criteria"].pop()
    elif problem == "policy":
        payload["policy_version"] = "different"
    elif problem == "id_key":
        evidence["evidence_id"] = "different"
    elif problem == "locator":
        evidence["locator"] = "https://example.com/real"
    else:
        del evaluation["criteria"][0]["schema_version"]
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload, ensure_ascii=False))
    with pytest.raises((ValueError, ValidationError)):
        load_common_fixtures(catalog, path)
