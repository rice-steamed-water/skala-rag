"""Controlled wires exercise retained examples, never actual model QA."""

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

from skala_rag.contracts.company_report import CompanyReportConfigError, ResearchLimits


def test_stored_scenario_reads_real_artifacts_and_reopens_lineage(
    tmp_path, monkeypatch
):
    # Given a real SQLite store and explicit synthetic model wires.
    case = Harness(tmp_path / "stored", monkeypatch)
    before = case.store.open()
    assert before is not None
    # When the retained Python example invokes the production public API.
    out = scenario_stored_report(
        "Synthetic a Robotics",
        config_path=case.config,
        output_dir=case.root / "scenario",
    )
    # Then the saved partial PDF is validated and originals are unchanged.
    result = _check_report(out, research=False)
    assert result.outcome == "partial" and result.completeness == "partial"
    after = case.store.open()
    assert after is not None and after.payload == before.payload
    assert result.subject_id == "co-a"
    assert case.roles == ["AnalysisReply", "CompositionReply", "SemanticJudgeReply"]


def test_research_scenario_preserves_changed_corpus_block(tmp_path, monkeypatch):
    # Given no collection authorization and a valid original store.
    case = Harness(tmp_path / "research", monkeypatch, needs=True)
    before = case.store.open()
    assert before is not None
    # When explicit finite research is requested through the supported example.
    out = scenario_research_target_report(
        "Synthetic a Robotics",
        config_path=case.config,
        output_dir=case.root / "scenario",
        research_limits=ResearchLimits(
            max_calls=1, deadline_seconds=1.0, max_cost_usd="0.01"
        ),
    )
    # Then unavailable research is not relabeled as new evidence or full output.
    result = receipt(out)
    assert result.research_requested and result.outcome == "partial"
    assert result.validation == "passed" and result.pdf_path is not None
    assert result.collection_records == []
    after = case.store.open()
    assert after is not None and after.payload == before.payload
    _check_report(out, research=True)


@pytest.mark.parametrize("qa", [qa_stored, qa_research])
def test_actual_qa_rejects_controlled_scope_before_dispatch(tmp_path, monkeypatch, qa):
    # Given explicitly synthetic wires with no genuine model assets or receipt.
    case = Harness(tmp_path / "scope", monkeypatch)
    out = case.root / "forbidden"
    arguments = dict(config_path=case.config, output_dir=out)
    if qa is qa_research:
        arguments["research_limits"] = ResearchLimits(
            max_calls=1, deadline_seconds=1.0, max_cost_usd="0.01"
        )
    # When prerequisites for the actual-only example are absent.
    with pytest.raises(CompanyReportConfigError) as error:
        qa("Synthetic a Robotics", **arguments)
    # Then no model request, output or synthetic actual-success claim is emitted.
    assert error.value.code == "ACTUAL_QA_SCOPE_REQUIRED"
    assert case.requests == []
    assert not out.exists()
