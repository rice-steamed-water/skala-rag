"""Versioned deterministic IDs; JSON framing avoids delimiter collisions."""

import hashlib
import json
import unicodedata
from collections.abc import Sequence
from datetime import date


def _text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("ID components must be nonblank strings")
    return value


def _counter(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("ID counters must be nonnegative integers")
    return value


def _id(kind: str, parts: list) -> str:
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


def normalize_claim(claim: str) -> str:
    """NFC와 공백 축약만 한다. 의미를 바꾸는 요약·번역은 하지 않는다."""
    return " ".join(unicodedata.normalize("NFC", _text(claim)).split())


def _number(value: int | float | None) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Evidence value must be a number")
    # 1과 1.0은 같은 관측이다.
    return int(value) if float(value).is_integer() else float(value)


def _day(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def evidence_id(
    *,
    source_id: str,
    locator: str,
    claim: str,
    candidate_id: str | None,
    scope: str,
    value: int | float | None,
    unit: str | None,
    currency: str | None,
    value_as_of: date | None,
    period: str | None,
    geography: str | None,
    event_date: date | None,
    evidence_kind: str,
    supporting_evidence_ids: Sequence[str],
    derivation: str | None,
    supersedes: str | None,
) -> str:
    """contracts §3 식별 core로만 만든다. provenance·excerpt·해석 필드는 제외한다.

    같은 Source snapshot·locator의 같은 주장은 Web/RAG 어느 경로로 얻어도 같은
    ID가 된다. claim은 ``normalize_claim`` 결과로 비교하므로 저장값도 정규화한다.
    """
    optional = [unit, currency, period, geography, derivation, supersedes, candidate_id]
    if any(v is not None and not (isinstance(v, str) and v.strip()) for v in optional):
        raise ValueError("optional Evidence core text must be null or nonblank")
    return _id(
        "evidence",
        [
            _text(source_id),
            _text(locator),
            normalize_claim(claim),
            candidate_id,
            _text(scope),
            _number(value),
            unit,
            currency,
            _day(value_as_of),
            period,
            geography,
            _day(event_date),
            _text(evidence_kind),
            sorted(_text(item) for item in supporting_evidence_ids),
            derivation,
            supersedes,
        ],
    )
