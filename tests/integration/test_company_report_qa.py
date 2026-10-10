"""Auxiliary controlled wires, synthetic embeddings; never actual QA evidence."""

import json

import pytest
from examples.company_report_run import (
    _check_report,
    qa_research,
    qa_stored,
    scenario_research_target_report,
    scenario_stored_report,
)
from tests.integration.test_company_report import Harness, receipt
from tests.integration.test_company_report import offline as offline

from skala_rag.contracts.company_report import CompanyReportConfigError


def test_stored_scenario_reads_real_artifacts_and_reopens_lineage(
    tmp_path, monkeypatch
):
    # Given: existing integration wires, real SQLite/PDF and synthetic vectors.
    case = Harness(tmp_path / "stored", monkeypatch)
    case.config_data["research_enabled"] = True
    case.config.write_text(json.dumps(case.config_data), encoding="utf-8")
    # When: the exported Python scenario explicitly disables research.
    out = scenario_stored_report(
        config_path=case.config,
        output_dir=case.root / "scenario",
        authority=case.authority,
        actual_admission=case.actual,
    )
    # Then: read-only artifact observations match public receipts and the store.
    _check_report(out, research=False)
    meta = json.loads((out / "corpus_meta.json").read_bytes())
    assert meta["execution_scope"] == "controlled_response"
    assert meta["actual_proof_established"] is False
    assert meta["before"] == case.before.manifest.model_dump(mode="json")
    reopened = case.store.open()
    assert reopened is not None
    assert meta["after"] == reopened.manifest.model_dump(mode="json")
    assert (out / "effective_config.json").read_bytes() == (
        out / "effective-config.json"
    ).read_bytes()
    assert receipt(out).candidate_id == case.actual.runtime_binding.call.candidate_id
    assert all(event["kind"] == "analysis" for event in case.events)


def test_research_scenario_preserves_changed_corpus_block(tmp_path, monkeypatch):
    # Given: starting admission, with no future-corpus preview or replacement.
    case = Harness(tmp_path / "research", monkeypatch)
    case.stale = True
    # When: the exported scenario requests target-only research.
    out = scenario_research_target_report(
        config_path=str(case.config),
        output_dir=str(case.root / "scenario"),
        authority=case.authority,
        actual_admission=case.actual,
    )
    # Then: source-only intake cannot be relabeled as report supplementation.
    result = receipt(out)
    assert result.research_requested
    assert result.outcome == "research_blocked"
    assert result.reason_codes == ("CORPUS_ADMISSION_MISMATCH",)
    assert result.report_validation == "not_run"
    assert result.report_path is None
    meta = json.loads((out / "corpus_meta.json").read_bytes())
    assert meta["before"]["version"] != meta["after"]["version"]
    assert meta["before"]["evidence"] == meta["after"]["evidence"]
    assert meta["review_log"] is None
    reopened = case.store.open()
    assert reopened is not None
    assert not reopened.manifest.reports


@pytest.mark.parametrize("qa", [qa_stored, qa_research])
def test_actual_qa_rejects_controlled_scope_before_dispatch(tmp_path, monkeypatch, qa):
    # Given: explicitly synthetic authority from the existing integration helper.
    case = Harness(tmp_path / "scope", monkeypatch)
    out = case.root / "forbidden"
    # When: an actual-only QA wrapper is called with controlled responses.
    with pytest.raises(CompanyReportConfigError) as error:
        qa(
            config_path=case.config,
            output_dir=out,
            authority=case.authority,
            actual_admission=case.actual,
        )
    # Then: there are no requests, artifacts, or synthetic actual success.
    assert error.value.code == "ACTUAL_QA_SCOPE_REQUIRED"
    assert case.events == []
    assert not out.exists()
