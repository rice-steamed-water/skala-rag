"""#22: LLM 평가 출력 검증·조립 (T01·T22 평가 부분). 가상 데이터만 사용."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from skala_rag.agents.evaluation import (
    DimensionAssessmentOutput,
    EvaluationValidationError,
    assemble_evaluation,
    validate_output,
)
from skala_rag.contracts import EvaluationResult, EvaluationSnapshot, Evidence
from skala_rag.scoring import build_score_summary
from skala_rag.scoring.catalog import load_policy

ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
CORE = yaml.safe_load((ROOT / "configs/rubrics/core.yaml").read_text())
FINANCE = yaml.safe_load((ROOT / "configs/rubrics/finance.yaml").read_text())
BASE = json.loads((ROOT / "tests/fixtures/contracts.json").read_text())["Evidence"]
SV = "synthetic-1"
CAND = "cand-1"


def _ev(eid, *, candidate=CAND, scope="company"):
    return Evidence.model_validate(
        {**BASE, "evidence_id": eid, "candidate_id": candidate, "scope": scope}
    )


EVIDENCE = [
    _ev("ev-a"),
    _ev("ev-b"),
    _ev("ev-other", candidate="cand-2"),
    _ev("ev-ind", candidate=None, scope="industry"),
]
SNAPSHOT = EvaluationSnapshot(
    schema_version=SV,
    snapshot_id="snap-1",
    run_id="run-1",
    candidate_id=CAND,
    evaluation_round=1,
    evidence_revision=3,
    policy_version=POLICY.policy_version,
    corpus_version="corpus-fixture",
    index_version="index-fixture",
    as_of="2026-09-01",
    evidence_ids=[e.evidence_id for e in EVIDENCE],
    evidence={e.evidence_id: e for e in EVIDENCE},
    sources={},
    chunks={},
    retrieval_records={},
)


def _ids(dim):
    return [c.criterion_id for c in POLICY.criteria if c.dimension == dim]


def _output(dim, rating=4, evidence=("ev-a",), **overrides):
    criteria = [
        {
            "criterion_id": cid,
            "status": "observed",
            "rating": rating,
            "evidence_ids": list(evidence),
            "rationale": "가상 근거에 따른 판단",
        }
        for cid in _ids(dim)
    ]
    payload = {"criteria": criteria, **overrides}
    return DimensionAssessmentOutput.model_validate(payload)


def _assemble(dim, output, rubric=None):
    return assemble_evaluation(
        output,
        dimension=dim,
        snapshot=SNAPSHOT,
        policy=POLICY,
        rubric=rubric or (FINANCE if dim in ("traction", "deal_terms") else CORE),
        schema_version=SV,
    )


def test_envelope_comes_from_snapshot_not_model():
    ev = _assemble("technology", _output("technology"))
    assert (ev.run_id, ev.candidate_id, ev.snapshot_id) == ("run-1", CAND, "snap-1")
    assert ev.evidence_revision == 3 and ev.evaluation_round == 1
    assert ev.rubric_version == CORE["rubric_version"]


def test_model_cannot_send_scores_or_ids():
    payload = _output("moat").model_dump()
    payload["criteria"][0]["points"] = 5
    with pytest.raises(ValidationError):
        DimensionAssessmentOutput.model_validate(payload)
    with pytest.raises(ValidationError):
        DimensionAssessmentOutput.model_validate({**payload, "snapshot_id": "forged"})


def test_rating_without_evidence_rejected():
    with pytest.raises(EvaluationValidationError) as err:
        _assemble("moat", _output("moat", evidence=()))
    assert any(v.startswith("EVIDENCE_REQUIRED") for v in err.value.violations)


def test_other_company_evidence_rejected():
    with pytest.raises(EvaluationValidationError) as err:
        _assemble("founder", _output("founder", evidence=("ev-other",)))
    assert any(v.startswith("OTHER_COMPANY_EVIDENCE") for v in err.value.violations)


def test_evidence_outside_snapshot_rejected():
    with pytest.raises(EvaluationValidationError) as err:
        _assemble("founder", _output("founder", evidence=("ev-unknown",)))
    assert any(v.startswith("EVIDENCE_NOT_IN_SNAPSHOT") for v in err.value.violations)


def test_industry_evidence_follows_rubric():
    # core rubric: 산업 근거는 market만 허용
    _assemble("market", _output("market", evidence=("ev-ind",)))
    with pytest.raises(EvaluationValidationError) as err:
        _assemble("technology", _output("technology", evidence=("ev-ind",)))
    assert any(v.startswith("INDUSTRY_EVIDENCE") for v in err.value.violations)


def test_each_criterion_exactly_once():
    out = _output("technology")
    dup = DimensionAssessmentOutput(
        criteria=[*out.criteria, out.criteria[0]], research_gaps=[], caveats=[]
    )
    short = DimensionAssessmentOutput(criteria=out.criteria[1:])
    for bad in (dup, short):
        with pytest.raises(EvaluationValidationError) as err:
            _assemble("technology", bad)
        assert any(v.startswith("CRITERIA_SET") for v in err.value.violations)


def test_missing_is_valid_but_needs_reason():
    out = _output("market").model_dump()
    out["criteria"][0].update(
        status="missing",
        rating=None,
        evidence_ids=[],
        missing_reason="segment_mismatch",
    )
    ev = _assemble("market", DimensionAssessmentOutput.model_validate(out))
    assert ev.criteria[0].status == "missing"
    out["criteria"][0]["missing_reason"] = None
    violations = validate_output(
        DimensionAssessmentOutput.model_validate(out),
        dimension="market",
        snapshot=SNAPSHOT,
        policy=POLICY,
        rubric=CORE,
    )
    assert any(v.startswith("MISSING_REASON_REQUIRED") for v in violations)


def test_gaps_get_wrapper_ids_and_catalog_priority():
    gap = {
        "criterion_id": "market.size",
        "missing_fields": ["SAM"],
        "reason": "세부 시장 수치 없음",
        "suggested_queries": ["물류 AMR 시장 규모"],
    }
    ev = _assemble("market", _output("market", research_gaps=[gap]))
    assert ev.research_gaps[0].gap_id == f"gap:{CAND}:1:market:market.size"
    assert ev.research_gaps[0].priority_weight == 10
    bad = {**gap, "criterion_id": "moat.ip"}
    with pytest.raises(EvaluationValidationError):
        _assemble("market", _output("market", research_gaps=[bad]))


def test_rubric_version_required():
    with pytest.raises(EvaluationValidationError):
        _assemble("moat", _output("moat"), rubric={"common_rules": {}})


def test_six_assembled_evaluations_feed_score_summary():
    results = []
    for dim in ("founder", "market", "technology", "moat", "traction", "deal_terms"):
        ev = _assemble(dim, _output(dim, rating=4))
        results.append(
            EvaluationResult(
                schema_version=SV,
                run_id=ev.run_id,
                candidate_id=ev.candidate_id,
                dimension=dim,
                evaluation_round=ev.evaluation_round,
                snapshot_id=ev.snapshot_id,
                evidence_revision=ev.evidence_revision,
                policy_version=ev.policy_version,
                status="success",
                evaluation=ev,
                errors=[],
            )
        )
    summary = build_score_summary(results, POLICY, schema_version=SV)
    assert summary.observed_score == Decimal(80)
