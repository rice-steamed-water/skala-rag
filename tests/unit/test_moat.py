"""Offline Moat fixtures; not investment or live evidence."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from tests.fixtures.loader import load_common_fixtures

from skala_rag.agents.evaluation import output_from_evaluation
from skala_rag.fakes import FakeClock, FakeLLM
from skala_rag.scoring.catalog import load_policy

ROOT = Path(__file__).resolve().parents[2]
POLICY = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
RUBRIC = yaml.safe_load((ROOT / "configs/rubrics/core.yaml").read_text())


def _closed_snapshot(snapshot):
    # Common legacy fixture chunk lacks excerpts; enrich only this synthetic test.
    chunks = {
        cid: chunk.model_copy(
            update={
                "text": "\n".join(e.excerpt for e in snapshot.evidence.values()),
                "candidate_ids": [snapshot.candidate_id],
                "corpus_version": snapshot.corpus_version,
            }
        )
        for cid, chunk in snapshot.chunks.items()
    }
    return snapshot.model_copy(update={"chunks": chunks}, deep=True)


@pytest.mark.parametrize(
    "patent_state",
    ["absent", "other_owner", "unknown_status", "valid", "no_comparison"],
)
def test_snapshot_only_moat_bridge(patent_state):
    from skala_rag.agents.moat import evaluate_moat

    fixtures = load_common_fixtures(POLICY)
    snapshot = _closed_snapshot(next(iter(fixtures.snapshots.values())))
    output = output_from_evaluation(
        fixtures.evaluations[
            f"{snapshot.candidate_id}:{snapshot.evaluation_round}:moat"
        ]
    )
    from skala_rag.agents.moat import IndependentComparison, VerifiedPatent

    cited = {c.criterion_id: tuple(c.evidence_ids) for c in output.criteria}
    patents = (
        {}
        if patent_state == "absent"
        else {
            "moat.ip": VerifiedPatent(
                "other" if patent_state == "other_owner" else snapshot.candidate_id,
                "unknown" if patent_state == "unknown_status" else "active",
                "verified fixture claim scope",
                cited["moat.ip"],
            )
        }
    )
    comparisons = (
        {}
        if patent_state == "no_comparison"
        else {
            "moat.differentiation": IndependentComparison(
                "competitor", cited["moat.differentiation"]
            )
        }
    )
    result = evaluate_moat(
        snapshot,
        rubric=RUBRIC,
        policy=POLICY,
        llm=FakeLLM([output]),
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=UTC)),
        schema_version="fixture-1",
        verify_observation=lambda criterion, evidence: True,
        verified_patents=patents,
        independent_comparisons=comparisons,
    )
    assert result.branch_id == "moat"
    assert result.status == ("success" if patent_state == "valid" else "failure")
    assert result.snapshot_id == snapshot.snapshot_id


@pytest.mark.parametrize("kind", ["missing", "unknown", "foreign", "timeout"])
def test_offline_boundaries(kind):
    from skala_rag.agents.moat import evaluate_moat
    from skala_rag.contracts.error_codes import ErrorCode
    from skala_rag.contracts.interfaces import LLMError

    fixtures = load_common_fixtures(POLICY)
    snapshot = _closed_snapshot(next(iter(fixtures.snapshots.values())))
    output = output_from_evaluation(
        fixtures.evaluations[
            f"{snapshot.candidate_id}:{snapshot.evaluation_round}:moat"
        ]
    ).model_dump()
    for criterion in output["criteria"]:
        criterion.update(
            status="missing",
            rating=None,
            evidence_ids=[],
            missing_reason="not_disclosed",
        )
    if kind in {"unknown", "foreign"}:
        criterion = output["criteria"][0]
        eid = "unknown"
        if kind == "foreign":
            base = next(iter(snapshot.evidence.values()))
            foreign = base.model_copy(
                update={"evidence_id": "foreign", "candidate_id": "other"}
            )
            snapshot = snapshot.model_copy(
                update={
                    "evidence_ids": [*snapshot.evidence_ids, "foreign"],
                    "evidence": {**snapshot.evidence, "foreign": foreign},
                }
            )
            eid = "foreign"
        criterion.update(
            status="observed", rating=3, evidence_ids=[eid], missing_reason=None
        )
    llm = FakeLLM(
        [LLMError(ErrorCode.TOOL_TIMEOUT, "timeout")] if kind == "timeout" else [output]
    )
    result = evaluate_moat(
        snapshot,
        rubric=RUBRIC,
        policy=POLICY,
        llm=llm,
        clock=FakeClock(datetime(2026, 9, 30, tzinfo=UTC)),
        schema_version="fixture-1",
        verify_observation=lambda criterion, evidence: True,
        verified_patents={},
        independent_comparisons={},
    )
    assert result.status == ("success" if kind == "missing" else "failure")
    if kind == "missing":
        assert set(result.evaluations) == {"moat"}
        assert all(c.status == "missing" for c in result.evaluations["moat"].criteria)
    else:
        assert result.evaluations is None
    assert len(llm.calls) == 1
