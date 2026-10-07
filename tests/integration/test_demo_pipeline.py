"""Mocked model responses + actual Chromium PDF; not live API evidence."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from tests.unit.test_demo_budget import approve
from tests.unit.test_demo_scoring import missing_review, observe
from tests.unit.test_local_demo import retrieval_fixture

from skala_rag.contracts import ReportJudgement
from skala_rag.demo_context import SCHEMA, WARNING, research_material
from skala_rag.demo_scoring import load_demo_rubric
from skala_rag.reporting.v3_pipeline import ReportContentV3


@pytest.mark.browser
def test_synthetic_pipeline_generates_new_verified_artifacts(tmp_path, monkeypatch):
    import skala_rag.local_demo as demo

    approve(tmp_path)
    project = Path(__file__).resolve().parents[2]
    shutil.copytree(project / "configs", tmp_path / "configs")
    rubric = load_demo_rubric(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-secret")
    stages = []

    def retrieve(**kwargs):
        bundle, records = retrieval_fixture(tmp_path)
        records[0].run_id = kwargs["run_id"]
        return research_material(
            root=tmp_path, bundle=bundle, records=records, run_id=kwargs["run_id"]
        )

    def generate(self, *, system, user, output_schema):
        request = json.loads(user)
        self.progress(self.node)
        if output_schema is demo.Review:
            eid = next(iter(request["evidence"]))
            assert request["rubric"]["criteria"]
            assert "No ratings" not in system
            review = missing_review(self.node, rubric)
            if self.node == "technology":
                observe(review, "technology.maturity", rubric, evidence_id=eid)
                review["criteria"][0]["supports"][0]["quote"] = request["evidence"][
                    eid
                ]["excerpt"]
            return demo.Review(
                criteria=review["criteria"],
                schema_version=SCHEMA,
                observations=[demo.Claim(text="합성 테스트 관측", evidence_ids=[eid])],
                interpretations=[],
                missing=["합성 테스트이며 실제 기업 정보 아님"],
            )
        if output_schema is ReportContentV3:
            eid = next(iter(request["context"]["evidence"]))
            sentence = (
                f"합성 테스트 자료는 로봇 조작 학습을 기술합니다. [@evidence:{eid}]"
            )
            return ReportContentV3(
                schema_version=SCHEMA,
                summary=WARNING + sentence,
                company_team=sentence,
                technology=sentence,
                market="시장 규모·수요는 이 합성 자료에서 확인하지 않았다.",
                assessment_risks=sentence,
                limitations=["합성 테스트 자료"],
            )
        return ReportJudgement(
            schema_version=SCHEMA,
            verdict="pass",
            context_id=request["context_id"],
            findings=[],
            revision_instructions=[],
            judged_artifact_hash=request["artifact_hash"],
        )

    monkeypatch.setattr(demo, "_retrieve", retrieve)
    monkeypatch.setattr(demo.DemoLLM, "generate", generate)
    first = demo.run_demo(
        root=tmp_path, company="Physical Intelligence", progress=stages.append
    )
    receipt = json.loads((first / "run-result.json").read_text())
    assert receipt["status"] == "completed", receipt
    assert receipt["publication_allowed"] is False
    assert receipt["eligibility_checked"] is False
    context = json.loads((first / "report-context.json").read_text())
    scores = json.loads((first / "role-scores.json").read_text())
    assert scores == context["role_scores"]
    assert scores["technology"]["score"] == "24"
    assert scores["founder"]["score"] is None
    assert context["scoring"]["rubric"]["artifacts"] == rubric["artifacts"]
    assert (first / "reviews.json").is_file()
    assert (first / "report.pdf").read_bytes().startswith(b"%PDF")
    assert (
        receipt["artifact_hashes"]["report.pdf"]
        == hashlib.sha256((first / "report.pdf").read_bytes()).hexdigest()
    )
    assert stages == ["local_rag", *demo.ROLES, "generator", "judge", "pdf"]
    second = demo.run_demo(
        root=tmp_path, company="Physical Intelligence", progress=lambda _: None
    )
    assert second != first
    assert json.loads((second / "run-result.json").read_text())["status"] == "completed"
