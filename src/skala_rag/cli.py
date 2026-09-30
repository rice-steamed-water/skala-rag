"""Offline v3 runner and artifact persistence; live execution is gated out."""

import argparse
import hashlib
import json
import subprocess
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from skala_rag.contracts.inputs import RunInput
from skala_rag.contracts.manifest import ArtifactMetadata, RunManifest
from skala_rag.fixture_reporting import FixtureReportAdapter
from skala_rag.fixture_runner import run_fixture
from skala_rag.reporting.v3_pipeline import PROMPT_VERSION
from skala_rag.run_finalization import finalize_fixture
from skala_rag.scoring.v3_policy import load_v3_policy


def encode(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError("unsupported artifact value")


def digest(content):
    return hashlib.sha256(content).hexdigest()


def run(
    theme,
    *,
    output_dir,
    policy_path,
    catalog_path,
    config_path,
    report_adapter=None,
    pdf_profile="configs/pdf.layout.v1.json",
):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    config["investment_theme"] = theme
    run_input = RunInput.model_validate(config)
    if run_input.execution_mode != "fixture":
        raise ValueError(
            "live execution requires approved policy, budgets and readiness; #96"
        )
    policy = load_v3_policy(policy_path, execution_mode="fixture")
    if run_input.policy_version != policy.policy_version:
        raise ValueError("run input policy version differs from loaded policy")
    fixture = Path(__file__).parent / "fixtures/cli.json"
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    snapshot = next(iter(raw["snapshots"].values()))
    if (
        run_input.corpus_version != snapshot["corpus_version"]
        or run_input.as_of.isoformat() != snapshot["as_of"]
    ):
        raise ValueError("fixture corpus/as_of mismatch")
    run_id = str(uuid4())
    trace = []
    snapshots = {}
    result, _ = run_fixture(
        run_id=run_id,
        trace=trace,
        snapshots=snapshots,
        policy_path=policy_path,
        catalog_path=catalog_path,
    )
    destination = Path(output_dir) / run_id
    destination.mkdir(parents=True, exist_ok=False)
    default_adapter = None
    if report_adapter is None:
        default_adapter = FixtureReportAdapter(
            snapshots=snapshots,
            run_input=run_input,
            destination=destination,
            trace=trace,
            pdf_profile=pdf_profile,
        )
        report_adapter = default_adapter
    report = None
    adapter_failed = False
    if report_adapter is not None:
        try:
            report = report_adapter(deepcopy(result))
        except Exception:
            # Never persist arbitrary provider errors, prompts or credentials.
            adapter_failed = True
    terminal = finalize_fixture(report)
    artifacts = {}

    def save(name, content):
        data = content if isinstance(content, bytes) else content.encode("utf-8")
        path = destination / name
        path.write_bytes(data)
        artifacts[name] = ArtifactMetadata(
            schema_version=result.schema_version,
            artifact_path=str(path),
            artifact_hash=digest(data),
        )

    save(
        "candidate-result.json",
        json.dumps(asdict(result), default=encode, ensure_ascii=False, indent=2),
    )
    save("trace.json", json.dumps(trace, ensure_ascii=False, indent=2))
    # Preserve a redacted diagnostic when the report adapter cannot produce a draft.
    save(
        "draft.md",
        "# Fixture diagnostic draft\n\n"
        "가상 데이터이며 실제 기업 조사·실측 결과가 아닙니다.\n\n"
        "보고서 경로 실패로 검증된 final을 발행하지 않습니다.\n\n"
        + "\n".join(
            f"- {cid}: {decision.label}; "
            f"exact score={result.scores[cid].normalized_score}"
            for cid, decision in result.decisions.items()
        ),
    )
    if report is not None and terminal.reason not in (
        "INVALID_REPORT_COMPLETION",
        "INVALID_REPORT_REVISION_COUNT",
        "REPORT_REVISION_MISMATCH",
        "STALE_REPORT_VALIDATION",
        "INVALID_REPORT_STAGE_ORDER",
    ):
        save("draft.md", report.draft.markdown)
        save("report-draft.json", report.draft.model_dump_json(indent=2))
    if default_adapter is not None:
        if default_adapter.context:
            save("report-context.json", default_adapter.context.payload)
        if default_adapter.pipeline:
            save(
                "report-pipeline.json",
                json.dumps(
                    asdict(default_adapter.pipeline),
                    default=encode,
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        if default_adapter.rendered and default_adapter.rendered.artifact_path:
            pdf_path = Path(default_adapter.rendered.artifact_path)
            save(pdf_path.name, pdf_path.read_bytes())
    save(
        "run-result.json",
        json.dumps(
            {
                "execution_mode": "fixture",
                "workflow_status": terminal.workflow_status,
                "run_outcome": terminal.run_outcome,
                "exit_code": terminal.exit_code,
                "acceptance": terminal.acceptance,
                "publication_allowed": terminal.publication_allowed,
                "warnings": terminal.warnings,
                "reason": "REPORT_ADAPTER_FAILED"
                if adapter_failed
                else terminal.reason,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    if terminal.validations:
        save(
            "validation-results.json",
            json.dumps(terminal.validations, default=encode, indent=2),
        )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, check=False
    )
    research_budget = policy.research.additional_requests_per_candidate
    manifest = RunManifest(
        schema_version=result.schema_version,
        run_id=run_id,
        run_input=run_input,
        code_revision=revision.stdout.strip() if revision.returncode == 0 else None,
        uncommitted=bool(dirty.stdout)
        or dirty.returncode != 0
        or revision.returncode != 0,
        policy_version=policy.policy_version,
        corpus_version=run_input.corpus_version,
        corpus_hash=digest(fixture.read_bytes()),
        prompt_versions={
            "evaluation": "synthetic",
            "report_generator": PROMPT_VERSION
            if default_adapter
            else "unknown-injected",
            "report_judge": PROMPT_VERSION if default_adapter else "unknown-injected",
        },
        model_versions={
            "evaluation": "synthetic",
            "report_generator": "fixture-stub"
            if default_adapter
            else "unknown-injected",
            "report_judge": "fixture-stub" if default_adapter else "unknown-injected",
        },
        tool_status={
            "execution_mode": "fixture",
            "external_calls": "disabled",
            "reporting": terminal.reason or terminal.acceptance,
            "pdf": "fixture_verified"
            if "pdf" in terminal.validations
            and terminal.validations["pdf"].valid
            and terminal.validations["pdf"].checks.get("pdf_verified") is True
            else "not_verified",
            "report_context_id": report.draft.context_id
            if terminal.validations
            else None,
            "index_version": snapshot["index_version"],
            "fixture_hash": digest(fixture.read_bytes()),
            "policy_hash": digest(Path(policy_path).read_bytes()),
            "catalog_hash": digest(Path(catalog_path).read_bytes()),
            "config_file_hash": digest(Path(config_path).read_bytes()),
            "effective_input_hash": digest(
                json.dumps(run_input.model_dump(mode="json"), sort_keys=True).encode()
            ),
        },
        budgets={
            "research_additional_requests": research_budget,
            "report_shared_revisions": policy.report.shared_revisions,
        },
        usage={
            "external_requests": 0,
            "llm_tokens": 0,
            "cost_usd": "0",
            "fixture_evaluation_branches": sum(
                t["step"]
                in ("founder", "market", "technology", "moat", "business_deal")
                for t in trace
            ),
            "report_revisions": (report.revisions if terminal.validations else 0),
            "fixture_report_generations": sum(
                t["step"] == "report_generate" for t in trace
            ),
            "fixture_judge_calls": sum(t["step"] == "report_judge" for t in trace),
        },
        artifacts=artifacts,
        validation_results=terminal.validations,
        workflow_status=terminal.workflow_status,
        run_outcome=terminal.run_outcome,
    )
    (destination / "manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--theme", required=True)
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--config", required=True)
    parser.add_argument("--policy", default="configs/scoring.v3.json")
    parser.add_argument("--catalog", default="configs/scoring.draft.json")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--pdf-profile", default="configs/pdf.layout.v1.json")
    args = parser.parse_args(argv)
    if args.mode == "live":
        parser.exit(
            1,
            "live refused: missing policy/budget/readiness integration (#96)\n",
        )
    try:
        destination = run(
            args.theme,
            output_dir=args.output_dir,
            policy_path=args.policy,
            catalog_path=args.catalog,
            config_path=args.config,
            pdf_profile=args.pdf_profile,
        )
    except (ValueError, OSError) as exc:
        parser.exit(1, f"fixture execution refused ({type(exc).__name__})\n")
    receipt = json.loads((destination / "run-result.json").read_text())
    print(
        f"Fixture artifacts: {destination}; "
        f"status={receipt['workflow_status']}; "
        f"acceptance={receipt['acceptance']}; no validated final"
    )
    return receipt["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
