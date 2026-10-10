"""Original-only company context; retained reports are retrieval, not authority."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from skala_rag.agents.eligibility import check_eligibility
from skala_rag.contracts import CompanyProfile, EligibilityResult, Evidence
from skala_rag.contracts.common import JSONMap
from skala_rag.contracts.evaluation import EvaluationSnapshot
from skala_rag.graph.candidates_v3 import CandidateRunV3
from skala_rag.rag.company_store import (
    CompanyStoreError,
    StoreSnapshot,
    _evidence_closure,
    _has_ancestor,
    evidence_fingerprint,
)
from skala_rag.rag.retrieval import source_date
from skala_rag.reporting.v3_context import (
    ReportContextV3,
    build_report_context_v3,
    canonical,
)
from skala_rag.reporting.validator import TOKEN
from skala_rag.scoring.approved_consumers import ActualAdmissionV3


class CompanyContextError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CompanyEvidence:
    evidence: tuple[Evidence, ...]
    prior_interpretations: tuple[JSONMap, ...]


def validate_company_snapshot(
    snapshot: EvaluationSnapshot, retained: StoreSnapshot
) -> None:
    """Accept unchanged originals or a current local reread, never rewritten facts.

    A current reread replaces only provenance and commits the retained original
    Evidence fingerprints. Historic receipts remain in the immutable store.
    Exact snapshot-bound operator reviews still apply to the new reread.
    """
    manifest = retained.manifest
    originals = _evidence_closure(
        set(snapshot.evidence), manifest.evidence, manifest.sources
    )
    if set(originals) != set(snapshot.evidence) or any(
        item.model_copy(update={"provenance": manifest.evidence[eid].provenance})
        != manifest.evidence[eid]
        or snapshot.sources.get(item.source_id) != manifest.sources[item.source_id]
        for eid, item in snapshot.evidence.items()
    ):
        raise CompanyContextError("EVALUATED_ORIGINALS_MISMATCH")
    source_ids = {e.source_id for e in snapshot.evidence.values()}
    if set(snapshot.sources) != source_ids or any(
        c.source_id not in source_ids
        or c.chunk_id not in manifest.chunks
        or c.model_copy(update={"corpus_version": manifest.version})
        != manifest.chunks[c.chunk_id]
        for c in snapshot.chunks.values()
    ):
        raise CompanyContextError("EVALUATED_PROVENANCE_MISMATCH")
    fingerprints = {
        eid: evidence_fingerprint(e, manifest.sources[e.source_id])
        for eid, e in manifest.evidence.items()
        if eid in snapshot.evidence
    }
    for record in snapshot.retrieval_records.values():
        if (
            not set(record.source_ids) <= source_ids
            or not set(record.evidence_ids) <= set(snapshot.evidence)
            or not set(record.chunk_ids) <= set(snapshot.chunks)
        ):
            raise CompanyContextError("EVALUATED_PROVENANCE_MISMATCH")
        if record == manifest.retrieval_records.get(record.retrieval_id):
            continue
        if (
            record.tool_name != "company-store-retrieval"
            or record.run_id != snapshot.run_id
            or record.candidate_id != snapshot.candidate_id
            or record.status != "ok"
            or not record.cache_hit
            or snapshot.corpus_version != manifest.version
            or snapshot.index_version != manifest.index_metadata.index_version
            or record.arguments_without_secrets
            != {
                "corpus_version": manifest.version,
                "index_version": manifest.index_metadata.index_version,
                "as_of": snapshot.as_of.isoformat(),
                "original_evidence_hashes": fingerprints,
            }
            or set(record.evidence_ids) != set(snapshot.evidence)
            or set(record.source_ids) != source_ids
            or set(record.chunk_ids) != set(snapshot.chunks)
        ):
            raise CompanyContextError("EVALUATED_PROVENANCE_MISMATCH")
    for eid, item in snapshot.evidence.items():
        if item.provenance == manifest.evidence[eid].provenance:
            continue
        if not item.provenance or any(
            p.retrieval_id not in snapshot.retrieval_records
            or snapshot.retrieval_records[p.retrieval_id].tool_name
            != "company-store-retrieval"
            or p.method != "rag"
            or p.chunk_id not in snapshot.chunks
            or snapshot.chunks[p.chunk_id].source_id != item.source_id
            or item.excerpt not in snapshot.chunks[p.chunk_id].text
            for p in item.provenance
        ):
            raise CompanyContextError("EVALUATED_PROVENANCE_MISMATCH")


def resolve_company_evidence(
    stored: StoreSnapshot,
    chunk_ids: Sequence[str],
    *,
    candidate_id: str,
    as_of: date,
    current_report_id: str,
) -> CompanyEvidence:
    """Resolve hit IDs against the frozen store before evaluating originals.

    The store owns payloads; caller-supplied hit text cannot introduce facts.
    Missing/circular lineage excludes the affected hit, not unrelated originals.
    Independent keyword inputs bind subject, cutoff and forbidden self-ancestry.
    """
    manifest = stored.manifest
    evidence: dict[str, Evidence] = {}
    interpretations: list[JSONMap] = []
    for chunk_id in sorted(set(chunk_ids)):
        chunk = manifest.chunks.get(chunk_id)
        if chunk is None or (
            chunk.scope == "company" and candidate_id not in chunk.candidate_ids
        ):
            continue
        source = manifest.sources.get(chunk.source_id)
        if source is None:
            continue
        report = next(
            (r for r in manifest.reports.values() if source.source_id in r.source_ids),
            None,
        )
        try:
            if report is not None:
                if report.as_of > as_of or _has_ancestor(
                    report.draft.report_id, current_report_id, manifest.reports
                ):
                    continue
                ids = set(TOKEN.findall(chunk.text))
            else:
                ids = {
                    e.evidence_id
                    for e in manifest.evidence.values()
                    if e.source_id == source.source_id and e.excerpt in chunk.text
                }
            originals = _evidence_closure(ids, manifest.evidence, manifest.sources)
        except CompanyStoreError:
            continue
        items = [manifest.evidence[eid] for eid in originals]
        if not items or any(
            (e.scope == "company" and e.candidate_id != candidate_id)
            or (e.scope == "industry" and e.candidate_id is not None)
            or source_date(manifest.sources[e.source_id]) > as_of
            or any(d is not None and d > as_of for d in (e.event_date, e.value_as_of))
            for e in items
        ):
            continue
        if report is not None:
            if any(
                report.original_evidence_hashes.get(e.evidence_id)
                != evidence_fingerprint(e, manifest.sources[e.source_id])
                for e in items
            ):
                continue
            interpretations.append(
                {
                    "kind": "prior_report_interpretation",
                    "report_id": report.draft.report_id,
                    "run_id": report.run_id,
                    "as_of": report.as_of.isoformat(),
                    "generation_model": report.generation_model,
                    "generation_revision": report.generation_revision,
                    "validated_artifact_hash": report.validated_artifact_hash,
                    "validation_receipt_hashes": list(report.validation_receipt_hashes),
                    "chunk_id": chunk_id,
                    "text": chunk.text,
                    "original_evidence_ids": list(originals),
                    "original_source_ids": sorted({e.source_id for e in items}),
                    "independent_support": False,
                }
            )
        evidence.update((e.evidence_id, e) for e in items)
    return CompanyEvidence(
        tuple(evidence[eid] for eid in sorted(evidence)), tuple(interpretations)
    )


def build_company_report_context(
    result: CandidateRunV3,
    snapshot: EvaluationSnapshot,
    *,
    profile: CompanyProfile,
    eligibility: EligibilityResult,
    retained: StoreSnapshot,
    pre_research: StoreSnapshot | None,
    current_report_id: str,
    target_chunk_ids: Sequence[str] = (),
    competitor_chunk_ids: Sequence[str] = (),
    actual_admission: ActualAdmissionV3 | None = None,
) -> ReportContextV3:
    """Check supplied eligibility and preserve the exact evaluated generation.

    Separate retained/pre-research inputs are intentional: target research may
    advance storage, but never the competitor corpus. No collection occurs here.
    Trusted profile/admission construction remains the caller's responsibility.
    """
    cid = snapshot.candidate_id
    validate_company_snapshot(snapshot, retained)
    checked = check_eligibility(
        profile,
        snapshot.evidence,
        {"policy_version": snapshot.policy_version},
        run_id=snapshot.run_id,
        evidence_revision=snapshot.evidence_revision,
    )
    if checked.status != "eligible" or eligibility.status != "eligible":
        raise CompanyContextError("ELIGIBILITY_NOT_CONFIRMED")
    if (
        checked != eligibility
        or profile.candidate_id != cid
        or profile.as_of != snapshot.as_of
        or profile.schema_version != snapshot.schema_version
        or set(result.outcomes) != {cid}
        or result.outcomes[cid].eligibility_result_id
        != eligibility.eligibility_result_id
    ):
        raise CompanyContextError("ELIGIBILITY_PROOF_MISMATCH")
    context = build_report_context_v3(
        result,
        {cid: snapshot},
        as_of=snapshot.as_of,
        corpus_version=snapshot.corpus_version,
        execution_mode=result.execution_mode,
        actual_admission=actual_admission,
    )
    data = context.snapshot()
    target = resolve_company_evidence(
        retained,
        target_chunk_ids,
        candidate_id=cid,
        as_of=snapshot.as_of,
        current_report_id=current_report_id,
    )
    interpretations = [
        item
        for item in target.prior_interpretations
        if set(item["original_evidence_ids"]) <= set(snapshot.evidence)
    ]
    frozen = pre_research.manifest if pre_research is not None else None
    competitors = []
    for competitor_id, identity in sorted(
        frozen.companies.items() if frozen is not None else ()
    ):
        assert frozen is not None and pre_research is not None
        if competitor_id == cid:
            continue
        resolved = resolve_company_evidence(
            pre_research,
            competitor_chunk_ids,
            candidate_id=competitor_id,
            as_of=snapshot.as_of,
            current_report_id=current_report_id,
        )
        items = [e for e in resolved.evidence if e.candidate_id == competitor_id]
        if not items:
            continue
        # Never add unevaluated industry facts through competitor retrieval.
        ids = {e.evidence_id for e in items}
        if not set(identity.field_evidence_ids["canonical_name"]) <= ids or any(
            not set(e.supporting_evidence_ids) <= ids for e in items
        ):
            continue
        for item in items:
            raw = item.model_dump(mode="json")
            source = frozen.sources[item.source_id].model_dump(mode="json")
            if (
                item.evidence_id in data["evidence"]
                and data["evidence"][item.evidence_id] != raw
            ) or (
                item.source_id in data["sources"]
                and data["sources"][item.source_id] != source
            ):
                raise CompanyContextError("FROZEN_EVIDENCE_CONFLICT")
            data["evidence"][item.evidence_id] = raw
            data["sources"][item.source_id] = source
        competitors.append(
            {
                "candidate_id": competitor_id,
                "canonical_name": identity.candidate.canonical_name,
                "evidence_ids": sorted(ids),
            }
        )
        interpretations.extend(
            item
            for item in resolved.prior_interpretations
            if set(item["original_evidence_ids"]) <= ids
        )
    data["company_context"] = {
        "eligibility": eligibility.model_dump(mode="json"),
        "company_evidence_ids": sorted(
            e.evidence_id for e in snapshot.evidence.values() if e.scope == "company"
        ),
        "industry_evidence_ids": sorted(
            e.evidence_id for e in snapshot.evidence.values() if e.scope == "industry"
        ),
        "competitor_index_version": frozen.version if frozen is not None else None,
        "competitors": competitors,
        "comparison_section": "MARKET",
        "comparison_gap": None if competitors else "NO_STORED_COMPETITOR_EVIDENCE",
        "prior_interpretations": interpretations,
        "usage_policy": {
            "industry_is_company_performance": False,
            "prior_interpretation_is_fact_or_score": False,
            "missing_data": "Preserve evaluated missing values; never infer ratings.",
            "comparison": "Stored facts only in MARKET/Moat narrative; no new section.",
        },
    }
    payload = canonical(data)
    return ReportContextV3(
        "sha256:" + hashlib.sha256(payload.encode()).hexdigest(), payload
    )
