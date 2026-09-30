"""Versioned deterministic IDs; JSON framing avoids delimiter collisions."""

import hashlib
import json


def _text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("ID components must be nonblank strings")
    return value


def _counter(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("ID counters must be nonnegative integers")
    return value


def _id(kind: str, parts: list[str | int]) -> str:
    payload = json.dumps(
        ["skala-rag-id-v1", kind, *parts], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return f"{kind}-v1-{hashlib.sha256(payload).hexdigest()}"


def snapshot_id(
    run_id: str,
    candidate_id: str,
    evaluation_round: int,
    evidence_revision: int,
    policy_version: str,
) -> str:
    return _id(
        "snapshot",
        [
            _text(run_id),
            _text(candidate_id),
            _counter(evaluation_round),
            _counter(evidence_revision),
            _text(policy_version),
        ],
    )


def eligibility_result_id(
    run_id: str,
    candidate_id: str,
    evidence_revision: int,
    policy_version: str,
) -> str:
    return _id(
        "eligibility",
        [
            _text(run_id),
            _text(candidate_id),
            _counter(evidence_revision),
            _text(policy_version),
            "eligibility",
        ],
    )


def score_summary_id(
    run_id: str,
    candidate_id: str,
    evaluation_round: int,
    policy_version: str,
) -> str:
    return _id(
        "score",
        [
            _text(run_id),
            _text(candidate_id),
            _counter(evaluation_round),
            _text(policy_version),
            "score",
        ],
    )


def decision_id(score_summary_id: str) -> str:
    return _id("decision", [_text(score_summary_id), "decision"])


def evaluation_key(candidate_id: str, evaluation_round: int, dimension: str) -> str:
    candidate_id, dimension = _text(candidate_id), _text(dimension)
    if ":" in candidate_id or ":" in dimension:
        raise ValueError("evaluation key components must not contain ':'")
    return f"{candidate_id}:{_counter(evaluation_round)}:{dimension}"
