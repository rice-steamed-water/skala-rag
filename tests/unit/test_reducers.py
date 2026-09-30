"""T05: 가상 근거 병합과 snapshot 보존. 실제 기업 자료를 사용하지 않는다."""

import copy
import json
from pathlib import Path

import pytest

from skala_rag.contracts import Evidence, Source
from skala_rag.graph.reducers import (
    MergeConflict,
    merge_errors,
    merge_evidence,
    merge_evidence_with_changes,
    merge_result_maps,
    merge_sources,
)


@pytest.fixture
def payloads():
    return json.loads(
        (Path(__file__).parents[1] / "fixtures/contracts.json").read_text()
    )


@pytest.fixture
def evidence(payloads):
    evidence = Evidence.model_validate(
        payloads["Evidence"], context={"execution_mode": "fixture"}
    )
    return evidence.model_dump(mode="json")


@pytest.fixture
def source(payloads):
    source = Source.model_validate(
        payloads["Source"], context={"execution_mode": "fixture"}
    )
    return source.model_dump(mode="json")


def test_web_then_rag_keeps_one_evidence_two_paths_and_snapshot(evidence):
    web = copy.deepcopy(evidence)
    web["provenance"] = [
        dict(
            schema_version=web["schema_version"],
            retrieval_id="web-fixture",
            method="web",
            chunk_id=None,
        )
    ]
    key = web["evidence_id"]
    before = {key: web}
    snapshot = copy.deepcopy(before)
    rag = copy.deepcopy(evidence)
    rag["excerpt"] = "같은 locator의 가상 발췌 변형"
    result, changed = merge_evidence_with_changes(before, {key: rag})
    assert set(result) == {key}
    assert len(result[key]["provenance"]) == 2
    assert result[key]["excerpt"] == web["excerpt"]
    assert changed == {key}
    assert before == snapshot
    again, changed_again = merge_evidence_with_changes(result, {key: rag})
    assert again == result
    assert changed_again == set()
    result[key]["limitations"].append("결과 객체 변경")
    assert before == snapshot


@pytest.mark.parametrize(
    "field,value",
    [
        ("claim", "다른 주장"),
        ("candidate_id", "다른기업"),
        ("value", 42),
        ("source_id", "다른출처"),
        ("value_as_of", "2026-09-29"),
        ("locator", "fixture://different"),
        ("supporting_evidence_ids", ["different"]),
        ("schema_version", "different"),
    ],
)
def test_evidence_core_collision(evidence, field, value):
    incoming = copy.deepcopy(evidence)
    incoming[field] = value
    with pytest.raises(MergeConflict):
        merge_evidence(
            {evidence["evidence_id"]: evidence}, {evidence["evidence_id"]: incoming}
        )


def test_interpretation_union_and_lower_confidence(evidence):
    incoming = copy.deepcopy(evidence)
    incoming["criterion_ids"].append("technology.reliability")
    incoming["conflicts_with"].append("ev-conflict")
    incoming["limitations"].append("추가 한계")
    incoming["confidence"] = "unknown"
    key = evidence["evidence_id"]
    result, changed = merge_evidence_with_changes({key: evidence}, {key: incoming})
    assert result[key]["confidence"] == "unknown"
    for field in ["criterion_ids", "conflicts_with", "limitations"]:
        assert set(result[key][field]) == set(evidence[field]) | set(incoming[field])
    assert changed == {key}
    assert merge_evidence(result, {key: incoming}) == result


def test_provenance_payload_conflict(evidence):
    incoming = copy.deepcopy(evidence)
    incoming["provenance"][0]["schema_version"] = "different"
    key = evidence["evidence_id"]
    with pytest.raises(MergeConflict):
        merge_evidence({key: evidence}, {key: incoming})


def test_new_evidence_signals_change(evidence):
    key = evidence["evidence_id"]
    result, changed = merge_evidence_with_changes({}, {key: evidence})
    assert result == {key: evidence}
    assert changed == {key}


def test_source_earliest_timestamp_and_timezone(source):
    source["retrieved_at"] = "2026-09-30T09:00:00+09:00"
    incoming = copy.deepcopy(source)
    incoming["retrieved_at"] = "2026-09-29T23:00:00+00:00"
    key = source["source_id"]
    result = merge_sources({key: source}, {key: incoming})
    assert result[key]["retrieved_at"] == incoming["retrieved_at"]
    assert source["retrieved_at"] == "2026-09-30T09:00:00+09:00"
    assert merge_sources(result, {key: source}) == result


@pytest.mark.parametrize("field", ["content_hash", "title", "url", "access_notes"])
def test_source_core_collision(source, field):
    incoming = copy.deepcopy(source)
    incoming[field] = "different"
    key = source["source_id"]
    with pytest.raises(MergeConflict):
        merge_sources({key: source}, {key: incoming})


def test_naive_source_time_rejected(source):
    source["retrieved_at"] = "2026-09-30T09:00:00"
    with pytest.raises(ValueError, match="시간대"):
        merge_sources({}, {source["source_id"]: source})


@pytest.mark.parametrize(
    "reducer,id_field",
    [
        (merge_sources, "source_id"),
        (merge_evidence, "evidence_id"),
    ],
)
def test_map_key_must_match_payload(reducer, id_field):
    with pytest.raises(MergeConflict):
        reducer({}, {"wrong": {id_field: "actual"}})


def test_result_maps_idempotent_conflict_and_copy():
    payload = {"result": {"items": [1]}}
    result = merge_result_maps(payload, payload)
    assert result == payload
    result["result"]["items"].append(2)
    assert payload["result"]["items"] == [1]
    with pytest.raises(MergeConflict):
        merge_result_maps(payload, {"result": {"items": [2]}})


def test_errors_merge_by_id_and_copy():
    error = {"error_id": "err-fixture", "message_redacted": "가상 오류"}
    result = merge_errors([error], [error])
    assert result == [error]
    result[0]["message_redacted"] = "변경"
    assert error["message_redacted"] == "가상 오류"
    with pytest.raises(MergeConflict):
        merge_errors([error], [dict(error, message_redacted="다른 오류")])


def test_json_state_contract_rejects_client_and_nan():
    with pytest.raises(TypeError):
        merge_result_maps({}, {"id": {"client": object()}})
    with pytest.raises(ValueError):
        merge_result_maps({}, {"id": {"value": float("nan")}})
