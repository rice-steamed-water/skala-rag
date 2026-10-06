"""Offline v3 synthetic demo; source checkout + uv dev environment only.

Existing test helpers supply synthetic companies, retrieval and evaluators.
The public graph, approval arithmetic and ReportLab PDF really execute; semantic
Judge pass is a stub, never actual company evaluation or publication approval.
"""

import hashlib
import json
import sys
from contextlib import chdir
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # Explicit existing tests fixture helper reuse.

from tests.integration.test_v3_evidence_snapshot_consumer import (  # noqa: E402
    setup_case,
)

from skala_rag.fixture_reporting import FixtureReportLLM  # noqa: E402
from skala_rag.graph.candidate_workflow_v3 import run_candidate_report_v3  # noqa: E402
from skala_rag.reporting.pdf import (  # noqa: E402
    PDFLayoutValidator,
    PDFRenderer,
    load_pdf_profile,
)
from skala_rag.reporting.v3_pipeline import (  # noqa: E402
    ReportGeneratorV3,
    SemanticJudgeV3,
)
from skala_rag.scoring.approval_registry import pinned_approval_registry  # noqa: E402
from skala_rag.scoring.approved_consumers import ApprovedPolicySource  # noqa: E402


def run_offline_demo(output_dir: Path) -> Path:
    """Run once into a new directory; no overwrites or actual product/model calls.

    This single-process example briefly changes cwd for the existing fixture's
    checkout-relative settings and restores it on both success and failure.
    """
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=False)
    with chdir(ROOT):
        case = setup_case(count=2)
        registry = pinned_approval_registry(ROOT)
        approved = ApprovedPolicySource(
            path=ROOT / "configs/scoring.v3.json",
            approvals=registry.policy_approvals(),
            approval_verifier=registry.verify_policy,
            execution_mode="fixture",
        )
        llm = FixtureReportLLM()
        generator, judge = ReportGeneratorV3(llm), SemanticJudgeV3(llm)
        calls = {"fixture_generator": 0, "fixture_judge": 0}
        renders = []
        profile = load_pdf_profile(ROOT / "configs/pdf.layout.v1.json")

        def generate(context, feedback):
            calls["fixture_generator"] += 1
            return generator(context, feedback)

        def judged(draft, context):
            calls["fixture_judge"] += 1
            return judge(draft, context)

        def check_pdf(draft, context, structural, judgement):
            render = PDFRenderer(
                profile=profile,
                output_dir=out,
                proof=lambda _: (structural, judgement),
                execution_mode="fixture",
            )(draft, profile.version)
            renders.append(render)
            return PDFLayoutValidator()(draft, context, render)

        result, context, report = run_candidate_report_v3(
            case["stages"],
            case["callbacks"],
            run_input=case["stages"].evidence_research.run_input,
            generate=generate,
            judge=judged,
            check_pdf=check_pdf,
            approved_policy_source=approved,
            graph_events=case["events"],
            **case["options"],
        )

    # Persist original objects directly, including errors and generation identities.
    adapter = TypeAdapter(Any)
    values = {
        "candidates.json": result,
        "context.json": context.snapshot(),
        "context-dto.json": context,
        "report.json": report,
        "graph-events.json": case["events"],
        "trace.json": case["trace"],
        "renders.json": renders,
    }
    for name, value in values.items():
        (out / name).write_bytes(adapter.dump_json(value, indent=2))
    if report.draft is not None:
        (out / "report-demo.md").write_text(report.draft.markdown, encoding="utf-8")
    render = renders[-1] if renders else None
    measurements = render.layout_measurements if render else {}
    artifacts = [
        {
            "path": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(out.iterdir())
        if path.is_file()
    ]
    manifest = {
        "demo_version": "offline-v3-demo-1",
        "execution_mode": "fixture",
        "synthetic": True,
        "limits": [
            "Synthetic company, retrieval, evaluation and Generator/Judge data",
            "Real public graph/approved arithmetic/ReportLab, not semantic authority",
            "Source checkout + uv dev environment; not an installed-wheel promise",
            "No actual BGE/index/source-proof inputs or actual-runtime receipt",
        ],
        "candidate_status": result.status,
        "report_status": report.status,
        "warning": report.warning,
        "final_allowed": report.final_allowed,
        "publication_allowed": False,
        "usage": None,
        "observed_calls": {
            "fixture_backend": len(case["backend"].calls),
            "fixture_evaluator": len(case["seen"]),
            **calls,
            "product": 0,  # All supplied model/backend callbacks are fixture-only.
        },
        "versions": {
            "schema": result.schema_version,
            "policy": result.policy_version,
            "corpus": context.snapshot()["corpus_version"],
            "report_prompt": report.prompt_version,
            "pdf_profile": profile.version,
            "renderer": profile.renderer_version,
        },
        "pdf": {
            "path": Path(render.artifact_path).name
            if render and render.artifact_path
            else None,
            "page_count": render.page_count if render else None,
            "summary_fraction": measurements.get("summary_fraction"),
            "verified": measurements.get("pdf_verified", False),
        },
        "artifacts": artifacts,
    }
    # No self-hash cycle: manifest hashes only the already persisted artifacts.
    (out / "demo-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if (
        report.status != "completed"
        or report.warning
        or report.pdf_validation is None
        or not report.pdf_validation.valid
    ):
        raise RuntimeError(f"offline demo blocked; original diagnostics saved in {out}")
    return out


if __name__ == "__main__":
    print(run_offline_demo(ROOT / "outputs" / f"v3-offline-demo-{uuid4().hex}"))
