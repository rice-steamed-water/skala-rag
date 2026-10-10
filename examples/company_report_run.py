"""Python-only scenarios; importing this module performs no work.

Supply the operator-owned ActualAdmissionV3 and ActualAuthorityV3 in memory.
Both scenarios select the retained company bound by that admission, never a
fixture company or a guessed identity. The external company_report_qa_authority
module is a prerequisite for the plan's QA invocation, not supplied here.

Read run-result.json even when Python returns normally. When explicit research
changes the corpus, the operator may opt in to
authority.company_report.readmit_after_research(retained, snapshot). A successful
readmission requires fresh authenticated reviews bound to the exact new snapshot
and preserves the same runtime and cumulative budget. Retained source-only
intake is not successful fact supplementation or a report. QA wrappers remain
actual-only; controlled responses are auxiliary proof only.
"""

import json
from pathlib import Path

from skala_rag.company_report import run_company_report
from skala_rag.contracts.company_report import (
    CompanyReportConfigError,
    CompanyReportReceipt,
    EffectiveCompanyReportConfig,
    load_company_report_config,
)
from skala_rag.graph.actual_inputs_v3 import ActualAuthorityV3, canonical, digest
from skala_rag.rag.company_store import CompanyStore
from skala_rag.scoring.approved_consumers import ActualAdmissionV3


def _scenario(
    *,
    config_path: str | Path,
    output_dir: str | Path,
    authority: ActualAuthorityV3,
    actual_admission: ActualAdmissionV3,
    research: bool,
) -> Path:
    """Use configured paths and forward the exact supplied trusted objects."""
    if type(actual_admission) is not ActualAdmissionV3:
        raise CompanyReportConfigError("ACTUAL_ADMISSION_TYPE")
    if type(authority) is not ActualAuthorityV3:
        raise CompanyReportConfigError("ACTUAL_AUTHORITY_TYPE")
    config = load_company_report_config(Path(config_path), research=research)
    store = CompanyStore.from_local(
        config.store_dir,
        model_path=config.model_path,
        receipt_path=config.model_receipt_path,
    )
    before = store.open()
    cid = actual_admission.runtime_binding.call.candidate_id
    if before is None or cid not in before.manifest.companies:
        raise CompanyReportConfigError("RETAINED_ADMITTED_COMPANY_REQUIRED")
    candidate = before.manifest.companies[cid].candidate
    out = run_company_report(
        candidate.canonical_name,
        config_path=Path(config_path),
        output_dir=Path(output_dir),
        research=research,
        homepage_url=candidate.homepage_url,
        legal_identifiers=candidate.legal_identifiers,
        as_of=actual_admission.run_input.as_of,
        actual_admission=actual_admission,
        authority=authority,
    )
    # Observational aliases/snapshots only. Never modify the store or receipts.
    (out / "effective_config.json").write_bytes(
        (out / "effective-config.json").read_bytes()
    )
    after = store.open()
    (out / "corpus_meta.json").write_bytes(
        canonical(
            {
                "execution_scope": actual_admission.execution_scope,
                "actual_proof_established": False,
                "before": before.manifest.model_dump(mode="json"),
                "after": after.manifest.model_dump(mode="json") if after else None,
                "store_dir": str(config.store_dir),
                "review_log": str(out / "reviews.json")
                if (out / "reviews.json").is_file()
                else None,
            }
        )
    )
    return out


def scenario_stored_report(
    *,
    config_path: str | Path,
    output_dir: str | Path,
    authority: ActualAuthorityV3,
    actual_admission: ActualAdmissionV3,
) -> Path:
    """Run the admitted retained company with research explicitly disabled."""
    return _scenario(
        config_path=config_path,
        output_dir=output_dir,
        authority=authority,
        actual_admission=actual_admission,
        research=False,
    )


def scenario_research_target_report(
    *,
    config_path: str | Path,
    output_dir: str | Path,
    authority: ActualAuthorityV3,
    actual_admission: ActualAdmissionV3,
) -> Path:
    """Request bounded target research, preserving any blocked report outcome."""
    return _scenario(
        config_path=config_path,
        output_dir=output_dir,
        authority=authority,
        actual_admission=actual_admission,
        research=True,
    )


def _check_report(out: Path, *, research: bool) -> None:
    """Check persisted proof and fresh local retrieval, never process exit alone."""
    receipt = CompanyReportReceipt.model_validate_json(
        (out / "run-result.json").read_bytes()
    )
    assert receipt.outcome == "completed", receipt.model_dump(mode="json")
    assert receipt.eligibility_status == "eligible"
    assert receipt.report_validation == "passed"
    assert receipt.ingestion_status == "succeeded"
    assert receipt.research_requested is research
    assert receipt.publication_allowed is False
    effective = (out / "effective-config.json").read_bytes()
    assert digest(effective) == receipt.effective_config_hash
    config = EffectiveCompanyReportConfig.model_validate_json(effective)
    reopened_store = CompanyStore.from_local(
        config.store_dir,
        model_path=config.model_path,
        receipt_path=config.model_receipt_path,
    )
    reopened = reopened_store.open()
    assert reopened is not None
    report = reopened.manifest.reports[f"company-report-{receipt.run_id}"]
    assert report.validation.valid and report.pdf_validation.valid
    assert report.judgement.verdict == "pass"
    assert report.validation_receipt_hashes == tuple(
        f"sha256:{value}" for value in receipt.validation_receipt_hashes
    )
    assert receipt.report_path is not None
    assert (
        f"sha256:{digest(Path(receipt.report_path).read_bytes())}"
        == report.content_hash
    )
    rendered = json.loads((out / "render.json").read_bytes())
    pdf = Path(rendered["artifact_path"]).read_bytes()
    assert pdf.startswith(b"%PDF")
    assert digest(pdf) == rendered["layout_measurements"]["artifact_hash"]
    assert json.loads((out / "reviews.json").read_bytes())
    assert json.loads((out / "analysis-records.json").read_bytes())
    hits = reopened_store.search_local(
        receipt.company_name,
        top_k=len(reopened.manifest.chunks),
        timeout_seconds=config.runtime_document.profiles.actual_v3.timeout_seconds,
        snapshot=reopened,
    )
    report_hits = [h for h in hits if h.prior_report == report]
    assert report_hits
    originals = {e.evidence_id for h in report_hits for e in h.original_evidence}
    assert originals == set(report.original_evidence_ids)
    assert all(
        not reopened.manifest.sources[
            reopened.manifest.evidence[eid].source_id
        ].bibliographic_metadata.get("generated_report", False)
        for eid in originals
    )
    if research:
        before = json.loads((out / "corpus_meta.json").read_bytes())["before"]
        new_ids = set(reopened.manifest.evidence) - set(before["evidence"])
        assert new_ids, "Source-only or pre-pinned intake is not new facts."
        assert receipt.collection_records
        assert all(
            r.candidate_id == receipt.candidate_id for r in receipt.collection_records
        )
    else:
        assert not receipt.collection_records


def qa_stored(
    *,
    config_path: str | Path,
    output_dir: str | Path,
    authority: ActualAuthorityV3,
    actual_admission: ActualAdmissionV3,
) -> Path:
    """Actual-only stored QA; supplied authority must already admit this run."""
    if actual_admission.execution_scope != "actual":
        raise CompanyReportConfigError("ACTUAL_QA_SCOPE_REQUIRED")
    out = scenario_stored_report(
        config_path=config_path,
        output_dir=output_dir,
        authority=authority,
        actual_admission=actual_admission,
    )
    _check_report(out, research=False)
    return out


def qa_research(
    *,
    config_path: str | Path,
    output_dir: str | Path,
    authority: ActualAuthorityV3,
    actual_admission: ActualAdmissionV3,
) -> Path:
    """Actual-only research QA; changed corpora require operator readmission."""
    if actual_admission.execution_scope != "actual":
        raise CompanyReportConfigError("ACTUAL_QA_SCOPE_REQUIRED")
    out = scenario_research_target_report(
        config_path=config_path,
        output_dir=output_dir,
        authority=authority,
        actual_admission=actual_admission,
    )
    _check_report(out, research=True)
    return out
