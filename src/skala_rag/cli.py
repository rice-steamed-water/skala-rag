"""Offline v3 runner and artifact persistence; live execution is gated out."""

import argparse
import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from skala_rag.contracts.inputs import RunInput
from skala_rag.contracts.manifest import ArtifactMetadata, RunManifest
from skala_rag.fixture_runner import run_fixture
from skala_rag.scoring.v3_policy import load_v3_policy


def encode(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError("unsupported artifact value")


def digest(content):
    return hashlib.sha256(content).hexdigest()


def run(theme, *, output_dir, policy_path, catalog_path, config_path):
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
    result, _ = run_fixture(
        run_id=run_id, trace=trace, policy_path=policy_path, catalog_path=catalog_path
    )
    destination = Path(output_dir) / run_id
    destination.mkdir(parents=True, exist_ok=False)
    artifacts = {}

    def save(name, content):
        data = content.encode("utf-8")
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
    # Reporting compatibility is explicitly unresolved upstream (#94). Do not
    # manufacture a ReportContext or mark a diagnostic as a validated report.
    save(
        "draft.md",
        "# Fixture diagnostic draft\n\n"
        "가상 데이터이며 실제 기업 조사·실측 결과가 아닙니다.\n\n"
        "보고서 context/구조/의미/PDF 검증은 미실행(#94/#95).\n\n"
        + "\n".join(
            f"- {cid}: {decision.label}; "
            f"exact score={result.scores[cid].normalized_score}"
            for cid, decision in result.decisions.items()
        ),
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
        prompt_versions={"evaluation": "synthetic"},
        model_versions={"evaluation": "synthetic"},
        tool_status={
            "execution_mode": "fixture",
            "external_calls": "disabled",
            "reporting": "blocked: v3 context adapter #94",
            "pdf": "not_run",
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
            "report_revisions": 0,
        },
        artifacts=artifacts,
        validation_results={},
        workflow_status="running",
        run_outcome=None,
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
        )
    except (ValueError, OSError) as exc:
        parser.exit(1, f"fixture execution refused ({type(exc).__name__})\n")
    print(
        f"Fixture artifacts: {destination}; reporting blocked (#94), no validated final"
    )
    # Exit 2 is reserved for report revision exhaustion Warning.
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
