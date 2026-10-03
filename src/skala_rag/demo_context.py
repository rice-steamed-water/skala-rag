"""Validated research/role-score adapter, distinct from the investment selector.

Only locally hash-verified sources and chunks returned in this run are admitted.
Optional rubric scores do not manufacture investment score/outcome DTOs.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from skala_rag.contracts import Evidence, RetrievalBundle, RetrievalRecord
from skala_rag.demo_scoring import score_reviews
from skala_rag.reporting.v3_context import ReportContextV3, canonical

SCHEMA = "local-demo-v1"
COMPANY = "Physical Intelligence"
CANDIDATE_ID = "co-physical-intelligence"
WARNING = (
    "자료 기반 검토 초안 — 역할 점수는 rubric과 항목별 근거로 산정하며, "
    "근거 없는 역할은 공란입니다. 투자 적격성·추천은 판정하지 않았습니다. "
    "논문 저자의 보고와 분석자의 해석을 구분하며, 전체 M3 검증 완료가 아닙니다."
)


def _unique_by_id(items, field: str, error: str):
    """Deduplicate identical DTOs; never silently overwrite conflicting traces."""
    unique = {}
    for item in items:
        key = getattr(item, field)
        if key in unique and unique[key] != item:
            raise ValueError(error)
        unique[key] = item
    return list(unique.values())


def research_material(*, root: Path, bundle, records, run_id: str) -> dict:
    bundle = RetrievalBundle.model_validate(bundle, context={"execution_mode": "live"})
    records = [RetrievalRecord.model_validate(r) for r in records]
    records = _unique_by_id(records, "retrieval_id", "RETRIEVAL_ID_CONFLICT")
    bundle.chunks = _unique_by_id(bundle.chunks, "chunk_id", "CHUNK_ID_CONFLICT")
    if not bundle.chunks or not records:
        raise ValueError("EMPTY_RETRIEVAL")
    if any(r.run_id != run_id or r.candidate_id != CANDIDATE_ID for r in records):
        raise ValueError("RETRIEVAL_IDENTITY_MISMATCH")
    evidence, sources, chunks = {}, {}, {}
    for chunk in bundle.chunks:
        if chunk.candidate_ids != [CANDIDATE_ID] or chunk.scope != "company":
            raise ValueError("RETRIEVAL_COMPANY_MISMATCH")
        source = bundle.sources[chunk.source_id]
        path = (root / (source.local_path or "")).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError("SOURCE_PATH_INVALID")
        if (
            "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            != source.content_hash
        ):
            raise ValueError("SOURCE_HASH_MISMATCH")
        matching = [
            r
            for r in records
            if r.status == "ok"
            and chunk.chunk_id in r.chunk_ids
            and source.source_id in r.source_ids
        ]
        if not matching:
            raise ValueError("RETRIEVAL_TRACE_MISSING")
        digest = hashlib.sha256(
            canonical(
                [source.content_hash, chunk.chunk_id, chunk.locator, chunk.text]
            ).encode()
        ).hexdigest()[:16]
        item = Evidence(
            schema_version=SCHEMA,
            evidence_id=f"ev-demo-{digest}",
            candidate_id=CANDIDATE_ID,
            scope="company",
            criterion_ids=[],
            claim="원문 페이지 발췌: 저자가 보고한 내용이며 독립 검증된 사실이 아님",
            source_id=source.source_id,
            locator=chunk.locator,
            excerpt=chunk.text,
            provenance=[
                {
                    "schema_version": SCHEMA,
                    "retrieval_id": r.retrieval_id,
                    "method": "rag",
                    "chunk_id": chunk.chunk_id,
                }
                for r in matching
            ],
            evidence_kind="reported",
            confidence="medium",
            limitations=[
                "텍스트 전용. 그림·표 수치 및 기업 재무·투자조건은 별도 확인 필요."
            ],
            supporting_evidence_ids=[],
            conflicts_with=[],
        )
        evidence[item.evidence_id] = item.model_dump(mode="json")
        sources[source.source_id] = source.model_dump(mode="json")
        chunks[chunk.chunk_id] = chunk.model_dump(mode="json")
    versions = {c.corpus_version for c in bundle.chunks}
    if len(versions) != 1:
        raise ValueError("CORPUS_VERSION_MISMATCH")
    material = {
        "run_id": run_id,
        "corpus_version": versions.pop(),
        "evidence": evidence,
        "sources": sources,
        "chunks": chunks,
        "retrieval_records": {
            r.retrieval_id: r.model_dump(mode="json") for r in records
        },
    }
    _validate_provenance(material)
    return material


def _validate_provenance(material: dict) -> None:
    """Check the actual exported maps, not pre-serialization list entries."""
    error = "RESEARCH_PROVENANCE_INVALID"
    try:
        for name, field in (
            ("sources", "source_id"),
            ("chunks", "chunk_id"),
            ("retrieval_records", "retrieval_id"),
            ("evidence", "evidence_id"),
        ):
            if any(key != item[field] for key, item in material[name].items()):
                raise ValueError(error)
        for item in material["evidence"].values():
            source = material["sources"][item["source_id"]]
            if not item["provenance"] or item["candidate_id"] != CANDIDATE_ID:
                raise ValueError(error)
            for path in item["provenance"]:
                record = material["retrieval_records"][path["retrieval_id"]]
                chunk = material["chunks"][path["chunk_id"]]
                if (
                    path["method"] != "rag"
                    or record["status"] != "ok"
                    or record["run_id"] != material["run_id"]
                    or record["candidate_id"] != CANDIDATE_ID
                    or chunk["candidate_ids"] != [CANDIDATE_ID]
                    or chunk["scope"] != item["scope"]
                    or item["scope"] != "company"
                    or chunk["corpus_version"] != material["corpus_version"]
                    or chunk["chunk_id"] not in record["chunk_ids"]
                    or source["source_id"] not in record["source_ids"]
                    or chunk["source_id"] != source["source_id"]
                    or chunk["text"] != item["excerpt"]
                    or chunk["locator"] != item["locator"]
                ):
                    raise ValueError(error)
    except (KeyError, TypeError) as exc:
        raise ValueError(error) from exc


def build_research_context(
    *, run_id: str, material: dict, reviews: dict, rubric: dict | None = None
) -> ReportContextV3:
    if material["run_id"] != run_id or not material["evidence"]:
        raise ValueError("RESEARCH_CONTEXT_INVALID")
    _validate_provenance(material)
    for review in reviews.values():
        for claim in review["observations"] + review["interpretations"]:
            if not claim["evidence_ids"] or not set(claim["evidence_ids"]) <= set(
                material["evidence"]
            ):
                raise ValueError("REVIEW_EVIDENCE_INVALID")
    scoring_payload = {}
    if rubric is not None:
        scoring_payload = {
            "role_scores": score_reviews(
                reviews, evidence=material["evidence"], rubric=rubric
            ),
            "scoring": {"method": rubric["method"], "rubric": rubric},
            "eligibility_checked": False,
            "recommendation_performed": False,
            "publication_allowed": False,
        }
    payload = canonical(
        {
            **scoring_payload,
            "schema_version": SCHEMA,
            "run_id": run_id,
            "execution_mode": "live",
            "as_of": datetime.now(UTC).date().isoformat(),
            "corpus_version": material["corpus_version"],
            "policy_version": "unscored-research-only-1",
            "mode": "no_recommendation",
            "research_subject": COMPANY,
            "selection": {
                "selected_candidate_id": None,
                "reason": "사용자 지정 조사 대상이며 투자 추천 후보 선정은 미실시",
            },
            "outcomes": {},
            "scores": {},
            "decisions": {},
            "snapshots": {},
            "sources": material["sources"],
            "evidence": material["evidence"],
            "research_trace": {
                "chunks": material["chunks"],
                "retrieval_records": material["retrieval_records"],
            },
            "live_reviews": reviews,
            "required_warning": WARNING,
            "scope": "로컬 논문 텍스트 기반 투자 검토; 시장/재무/투자조건 실사 미완료",
        }
    )
    return ReportContextV3(
        "sha256:" + hashlib.sha256(payload.encode()).hexdigest(), payload
    )
