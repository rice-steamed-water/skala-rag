"""JSON State의 ID 병합. 입력과 기존 snapshot을 수정하지 않는다.

DTO 구조·출처 대응은 수집 경계에서 검증한다. 이 모듈은 ID 충돌과 허용된
병합만 담당하며 정책·Graph wiring·후보별 revision 갱신은 controller 책임이다.
"""

import json
from datetime import datetime
from typing import Any

Payload = dict[str, Any]
PayloadMap = dict[str, Payload]


class MergeConflict(ValueError):
    """같은 ID로 다른 식별 core 또는 결과를 저장하려는 오류."""


def _copy(value: Any) -> Any:
    # NaN·client 객체 등 JSON State 계약을 위반하는 입력도 거절한다.
    return json.loads(json.dumps(value, allow_nan=False))


def merge_result_maps(left: PayloadMap, right: PayloadMap) -> PayloadMap:
    """동일 key·동일 payload는 멱등 삽입, 다른 payload는 오류."""
    result = _copy(left)
    for key, payload in _copy(right).items():
        if key in result and result[key] != payload:
            raise MergeConflict(f"결과 ID 충돌: {key}")
        result[key] = payload
    return result


def _union(left: list, right: list) -> list:
    result = _copy(left)
    for value in right:
        if value not in result:
            result.append(_copy(value))
    return result


def _require_ids(payloads: PayloadMap, id_field: str) -> None:
    for key, payload in payloads.items():
        if payload.get(id_field) != key:
            raise MergeConflict(f"{id_field}와 map key 불일치: {key}")


def merge_sources(left: PayloadMap, right: PayloadMap) -> PayloadMap:
    """Source core가 같을 때 최초 수집 시각을 보존한다."""
    _require_ids(left, "source_id")
    _require_ids(right, "source_id")
    result = _copy(left)
    for key, incoming in _copy(right).items():
        incoming_time = _aware_time(incoming["retrieved_at"])
        if key not in result:
            result[key] = incoming
            continue
        existing = result[key]
        if _without(existing, {"retrieved_at"}) != _without(incoming, {"retrieved_at"}):
            raise MergeConflict(f"Source core 충돌: {key}")
        if incoming_time < _aware_time(existing["retrieved_at"]):
            existing["retrieved_at"] = incoming["retrieved_at"]
    return result


def _aware_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("retrieved_at에는 시간대가 필요합니다")
    return parsed


def _without(payload: Payload, fields: set[str]) -> Payload:
    return {key: value for key, value in payload.items() if key not in fields}


def _merge_provenance(left: list[Payload], right: list[Payload]) -> list[Payload]:
    paths: dict[tuple, Payload] = {}
    for payload in left + right:
        key = (payload["retrieval_id"], payload["method"], payload.get("chunk_id"))
        if key in paths and paths[key] != payload:
            raise MergeConflict("동일 provenance key의 payload 충돌")
        paths[key] = _copy(payload)
    return list(paths.values())


def merge_evidence_with_changes(
    left: PayloadMap,
    right: PayloadMap,
) -> tuple[PayloadMap, set[str]]:
    """병합 결과와 변경된 Evidence ID 집합을 반환한다.

    controller는 변경 ID를 후보/industry 적용 범위에 대응시켜 후보별 revision을
    증가시킨다. reducer 자체는 다른 State 필드나 revision을 수정하지 않는다.
    """
    _require_ids(left, "evidence_id")
    _require_ids(right, "evidence_id")
    result = _copy(left)
    changed: set[str] = set()
    merged_fields = {
        "provenance",
        "excerpt",
        "criterion_ids",
        "conflicts_with",
        "limitations",
        "confidence",
    }
    confidence_order = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
    for key, incoming in _copy(right).items():
        if key not in result:
            incoming["provenance"] = _merge_provenance([], incoming["provenance"])
            result[key] = incoming
            changed.add(key)
            continue
        existing = result[key]
        if _without(existing, merged_fields) != _without(incoming, merged_fields):
            raise MergeConflict(f"Evidence core 충돌: {key}")
        previous = _copy(existing)
        existing["provenance"] = _merge_provenance(
            existing["provenance"], incoming["provenance"]
        )
        for field in ["criterion_ids", "conflicts_with", "limitations"]:
            existing[field] = _union(existing[field], incoming[field])
        existing["confidence"] = min(
            (existing["confidence"], incoming["confidence"]),
            key=confidence_order.__getitem__,
        )
        # 같은 locator의 발췌는 최초값을 유지한다. 대응 검증은 수집 경계 책임이다.
        if existing != previous:
            changed.add(key)
    return result, changed


def merge_evidence(left: PayloadMap, right: PayloadMap) -> PayloadMap:
    """LangGraph reducer용 Evidence map 병합."""
    return merge_evidence_with_changes(left, right)[0]


def merge_errors(left: list[Payload], right: list[Payload]) -> list[Payload]:
    """오류를 error_id로 멱등 병합한다. 오류 내용은 덮어쓰지 않는다."""
    result: PayloadMap = {}
    for payload in left + right:
        identifier = payload["error_id"]
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("error_id는 비어 있지 않은 문자열이어야 합니다")
        result = merge_result_maps(result, {identifier: payload})
    return list(result.values())
