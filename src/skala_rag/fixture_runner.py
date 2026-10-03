"""Offline end-to-end synthetic all-candidate v3 controller through #20/#24."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from skala_rag.contracts.v3 import (
    BRANCH_DIMENSIONS,
    ApplicabilityAssessment,
    EvaluationBranchResult,
)
from skala_rag.graph.candidates_v3 import CandidateStagesV3, run_candidates_v3
from skala_rag.scoring.catalog import load_policy
from skala_rag.scoring.v3_policy import load_v3_policy


def run_fixture(
    statuses=("eligible", "eligible"),
    ratings=(5, 4),
    broken=(),
    stale=(),
    na=(),
    discovery_failure=False,
    freeze_mutation=None,
    eligibility_schema=None,
    catalog_mutation=None,
    schema_version=None,
    run_id="run",
    fixture_path=None,
    trace=None,
    snapshots=None,
    policy_path="configs/scoring.v3.json",
    catalog_path="configs/scoring.draft.json",
):
    policy = load_v3_policy(policy_path, execution_mode="fixture")
    catalog = load_policy(catalog_path, execution_mode="fixture")
    if catalog_mutation:
        catalog = catalog_mutation(catalog)
    raw = json.loads(
        (
            Path(fixture_path)
            if fixture_path
            else Path(__file__).parent / "fixtures/cli.json"
        ).read_text()
    )
    template = deepcopy(next(iter(raw["snapshots"].values())))
    candidate_template = next(iter(raw["candidates"].values()))
    ids = [f"company-{i}" for i in range(len(statuses))]
    candidates = [
        {**candidate_template, "candidate_id": cid, "discovery_source_ids": []}
        for cid in ids
    ]
    calls = []
    from threading import Lock
    from time import perf_counter

    lock = Lock()

    def traced(step, fn):
        def call(*args):
            start = perf_counter()
            now = datetime.now(timezone.utc).isoformat()
            cid = None
            if args:
                cid = (
                    args[0].get("candidate_id")
                    if isinstance(args[0], dict)
                    else getattr(args[0], "candidate_id", None)
                )
            status = "failed"
            output_ids = []
            input_ids = [cid] if cid else []
            if args and hasattr(args[0], "snapshot_id"):
                input_ids.append(args[0].snapshot_id)
            try:
                value = fn(*args)
                status = "ok"
                if isinstance(value, dict):
                    output_ids = [
                        value[k]
                        for k in (
                            "candidate_id",
                            "snapshot_id",
                            "eligibility_result_id",
                        )
                        if k in value
                    ]
                elif isinstance(value, (list, tuple)):
                    output_ids = [
                        item.get("evidence_id", item.get("candidate_id"))
                        for item in value
                        if isinstance(item, dict)
                    ]
                    output_ids = [item for item in output_ids if item]
                elif hasattr(value, "snapshot_id"):
                    output_ids = [value.snapshot_id]
                return value
            finally:
                if trace is not None:
                    with lock:
                        trace.append(
                            dict(
                                run_id=run_id,
                                candidate_id=cid,
                                step=step,
                                status=status,
                                started_at=now,
                                duration_seconds=perf_counter() - start,
                                input_ids=input_ids,
                                output_ids=output_ids,
                                execution_mode="fixture",
                            )
                        )

        return call

    def freeze(candidate, eligibility, coverage, *, tamper=True):
        snap = deepcopy(template)
        snap.update(
            schema_version=schema_version or template["schema_version"],
            run_id=run_id,
            candidate_id=candidate["candidate_id"],
            policy_version=policy.policy_version,
            evaluation_round=1,
            evidence_revision=coverage.evidence_revision
            + (candidate["candidate_id"] in stale),
            snapshot_id=f"snapshot-{candidate['candidate_id']}",
        )
        cid = candidate["candidate_id"]
        evidence_ids = {eid: f"{eid}-{cid}" for eid in snap["evidence"]}
        snap["evidence"] = {
            evidence_ids[eid]: {
                **item,
                "evidence_id": evidence_ids[eid],
                "supporting_evidence_ids": [
                    evidence_ids[x] for x in item.get("supporting_evidence_ids", [])
                ],
                "conflicts_with": [
                    evidence_ids.get(x, x) for x in item.get("conflicts_with", [])
                ],
                "supersedes": evidence_ids.get(
                    item.get("supersedes"), item.get("supersedes")
                ),
            }
            for eid, item in snap["evidence"].items()
        }
        snap["evidence_ids"] = list(snap["evidence"])
        for record in snap["retrieval_records"].values():
            record["evidence_ids"] = [
                evidence_ids.get(x, x) for x in record["evidence_ids"]
            ]
        for evidence in snap["evidence"].values():
            evidence["candidate_id"] = candidate["candidate_id"]
            evidence["criterion_ids"] = [c.criterion_id for c in policy.criteria]
            evidence["locator"] = snap["chunks"]["chunk-fixture-eligible"]["locator"]
        for record in snap["retrieval_records"].values():
            record["run_id"] = run_id
            record["candidate_id"] = candidate["candidate_id"]
        for chunk in snap["chunks"].values():
            chunk["candidate_ids"] = [candidate["candidate_id"]]
            chunk["text"] = "\n".join(e["excerpt"] for e in snap["evidence"].values())
        if tamper and freeze_mutation and candidate["candidate_id"] == "company-0":
            freeze_mutation(snap)
        if tamper and snapshots is not None:
            snapshots[cid] = deepcopy(snap)
        return snap

    def evaluate(branch, snap):
        cid = snap.candidate_id
        calls.append((cid, branch))
        if cid in broken and branch == "business_deal":
            raise RuntimeError("secret synthetic provider error")
        index = ids.index(cid)
        identity = {
            k: getattr(snap, k)
            for k in (
                "schema_version",
                "run_id",
                "candidate_id",
                "evaluation_round",
                "snapshot_id",
                "evidence_revision",
                "policy_version",
            )
        }
        evaluations = {}
        for dimension in BRANCH_DIMENSIONS[branch]:
            evaluations[dimension] = dict(
                **identity,
                dimension=dimension,
                rubric_version="synthetic",
                criteria=[
                    dict(
                        schema_version=snap.schema_version,
                        criterion_id=c.criterion_id,
                        status="not_applicable" if c.criterion_id in na else "observed",
                        rating=None if c.criterion_id in na else ratings[index],
                        evidence_ids=[]
                        if c.criterion_id in na
                        else [next(iter(snap.evidence))],
                        rationale="synthetic",
                        applicability_reason="synthetic applicability"
                        if c.criterion_id in na
                        else None,
                        applicability_rule_id="approved-external-rule"
                        if c.criterion_id in na
                        else None,
                        applicability_evidence_ids=[next(iter(snap.evidence))]
                        if c.criterion_id in na
                        else None,
                    )
                    for c in policy.criteria
                    if c.dimension == dimension
                ],
                research_gaps=[],
                caveats=[],
            )
        return EvaluationBranchResult.model_validate(
            dict(
                **identity,
                branch_id=branch,
                status="success",
                evaluations=evaluations,
                errors=[],
            )
        )

    stages = CandidateStagesV3(
        discover=lambda: (
            (_ for _ in ()).throw(RuntimeError("source failed"))
            if discovery_failure
            else deepcopy(candidates)
        ),
        normalize=lambda items: items,
        research=lambda c: c["candidate_id"],
        eligibility=lambda c, research: dict(
            schema_version=eligibility_schema
            or schema_version
            or template["schema_version"],
            eligibility_result_id=f"elig-{c['candidate_id']}",
            run_id=run_id,
            candidate_id=c["candidate_id"],
            evidence_revision=1,
            policy_version=policy.policy_version,
            as_of=template["as_of"],
            status=statuses[ids.index(c["candidate_id"])],
            checks={},
            reason_codes=["synthetic"],
            evidence_ids=[],
        ),
        collect=lambda c, research: list(
            freeze(c, None, type("C", (), {"evidence_revision": 1})(), tamper=False)[
                "evidence"
            ].values()
        ),
        freeze=freeze,
    )
    stages = replace(
        stages,
        **{
            name: traced(name, getattr(stages, name))
            for name in stages.__dataclass_fields__
            if getattr(stages, name) is not None
        },
    )
    result = run_candidates_v3(
        stages,
        {b: traced(b, lambda snap, b=b: evaluate(b, snap)) for b in BRANCH_DIMENSIONS},
        policy=policy,
        catalog=catalog,
        catalog_policy_version="main-draft-0.1.0",
        run_id=run_id,
        schema_version=schema_version or template["schema_version"],
        support_check=lambda criterion, evidence: bool(evidence),
        applicability_assessments=lambda cid: {
            criterion_id: ApplicabilityAssessment(
                schema_version=schema_version or template["schema_version"],
                applicability_reason="synthetic applicability",
                applicability_rule_id="approved-external-rule",
                evidence_ids=[f"{next(iter(template['evidence']))}-{cid}"],
            )
            for criterion_id in na
        },
        applicability_check=lambda cid, criterion, assessment, evidence: (
            assessment.applicability_rule_id == "approved-external-rule"
        ),
        applicability_verifier=lambda assessment, snap: (
            assessment.applicability_rule_id == "approved-external-rule"
        ),
        industry_evidence_dimensions=set(),
        clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
        trace_events=trace,
    )
    return result, calls
