"""T04 적격성 가상 fixture. 실제 기업·단계 정규화 결과가 아니다.

``research_bundle``은 조건별 근거를 가진 ``CompanyResearchBundle``을 만든다.
기본값은 모든 조건이 근거로 확인된 비상장 explicit Seed다. 케이스는 인자로
바꾼다. 모든 locator는 fixture://이며 검증 context가 필요하다.
"""

from skala_rag.agents.eligibility import (
    FIELD_BUSINESS,
    FIELD_DOMAIN,
    FIELD_EXIT,
    FIELD_IDENTITY,
    FIELD_LISTING,
    FIELD_STAGE,
)
from skala_rag.contracts.candidates import Candidate
from skala_rag.contracts.tools import CompanyResearchBundle

SCHEMA = "synthetic-eligibility-1"
AS_OF = "2026-09-30"
POLICY = {"policy_version": "main-draft-0.1.0"}
CONTEXT = {"execution_mode": "fixture"}
ALL_FIELDS = (
    FIELD_DOMAIN,
    FIELD_LISTING,
    FIELD_STAGE,
    FIELD_EXIT,
    FIELD_IDENTITY,
    FIELD_BUSINESS,
)


def evidence_payload(candidate_id: str, field: str, **overrides) -> dict:
    return {
        "schema_version": SCHEMA,
        "evidence_id": f"ev-{candidate_id}-{field}",
        "candidate_id": candidate_id,
        "scope": "company",
        "criterion_ids": [],
        "claim": f"가상 {field} 적격성 근거. 실측 아님.",
        "source_id": f"src-{candidate_id}",
        "locator": f"fixture://eligibility/{candidate_id}#{field}",
        "excerpt": f"가상 {field} 예시",
        "provenance": [
            {
                "schema_version": SCHEMA,
                "retrieval_id": f"retrieval-{candidate_id}",
                "method": "manual",
            }
        ],
        "evidence_kind": "reported",
        "confidence": "unknown",
        "limitations": ["가상 fixture"],
        "supporting_evidence_ids": [],
        "conflicts_with": [],
        **overrides,
    }


def research_bundle(
    candidate_id: str,
    *,
    domain_match: bool | None = True,
    is_listed: bool | None = False,
    exit_completed: bool | None = False,
    raw_label: str | None = "Seed",
    normalized_round: str = "seed",
    method: str = "explicit",
    fields: tuple[str, ...] = ALL_FIELDS,
    evidence_overrides: dict[str, dict] | None = None,
    extra_evidence: list[dict] | None = None,
) -> CompanyResearchBundle:
    """``fields``에 든 조건만 근거를 가진다. 나머지는 근거 없음."""
    overrides = evidence_overrides or {}
    evidence = [
        evidence_payload(candidate_id, f, **overrides.get(f, {})) for f in fields
    ]
    evidence += extra_evidence or []
    source_id = f"src-{candidate_id}"
    return CompanyResearchBundle.model_validate(
        {
            "schema_version": SCHEMA,
            "sources": {
                source_id: {
                    "schema_version": SCHEMA,
                    "source_id": source_id,
                    "title": f"가상 적격성 자료 {candidate_id}",
                    "publisher": "가상 fixture 제작자",
                    "source_kind": "report",
                    "url": f"fixture://eligibility/{candidate_id}",
                    "retrieved_at": "2026-09-30T00:00:00Z",
                    "content_hash": "0" * 64,
                    "language": "ko",
                    "access_notes": "가상 데이터",
                    "bibliographic_metadata": {"synthetic": True},
                }
            },
            "evidence": {e["evidence_id"]: e for e in evidence},
            "profile": {
                "schema_version": SCHEMA,
                "candidate_id": candidate_id,
                "domain_match": domain_match,
                "is_listed": is_listed,
                "exit_completed": exit_completed,
                "stage": {
                    "schema_version": SCHEMA,
                    "raw_label": raw_label,
                    "normalized_round": normalized_round,
                    "bucket": "unknown",
                    "method": method,
                    "source_ids": [source_id],
                    "confidence": "unknown",
                    "rationale": "가상 단계 예시. 실제 정규화 결과 아님.",
                },
                "as_of": AS_OF,
                "field_evidence_ids": {
                    f: [f"ev-{candidate_id}-{f}"] for f in fields if f in ALL_FIELDS
                },
            },
        },
        context=CONTEXT,
    )


def candidate(
    candidate_id: str, name: str = "가상 로봇 알파", country: str = "KR"
) -> Candidate:
    return Candidate.model_validate(
        {
            "schema_version": SCHEMA,
            "candidate_id": candidate_id,
            "canonical_name": name,
            "aliases": [],
            "country": country,
            "homepage_url": f"fixture://eligibility/{candidate_id}/home",
            "legal_identifiers": {"fixture_registry": candidate_id},
            "discovery_source_ids": [f"src-{candidate_id}"],
        },
        context=CONTEXT,
    )
