"""Immutable v3 report context assembled from #89 terminal results and snapshots."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date

from skala_rag.contracts.evaluation import EvaluationSnapshot
from skala_rag.contracts.reports import CandidateOutcome
from skala_rag.contracts.v3 import InvestmentDecision, ScoreSummary
from skala_rag.graph.candidates_v3 import CandidateRunV3
from skala_rag.rag.retrieval import source_date


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@dataclass(frozen=True)
class ReportContextV3:
    context_id: str
    payload: str

    def snapshot(self):
        if (
            self.context_id
            != "sha256:" + hashlib.sha256(self.payload.encode()).hexdigest()
        ):
            raise ValueError("context integrity mismatch")
        return json.loads(self.payload)


def build_report_context_v3(
    result: CandidateRunV3,
    snapshots: Mapping[str, EvaluationSnapshot],
    *,
    as_of: date,
    corpus_version: str,
    execution_mode: str,
) -> ReportContextV3:
    if execution_mode not in ("fixture", "live") or type(as_of) is not date:
        raise ValueError("explicit mode and date required")
    if (
        result.status
        not in (
            "ready_for_v3_reporting",
            "no_candidates",
            "no_eligible_candidates",
            "all_eligible_failed",
            "eligible_failed_no_success",
        )
        or not corpus_version.strip()
    ):
        raise ValueError("terminal candidate result required")
    selection = result.selection
    if (selection.run_id, selection.schema_version, selection.policy_version) != (
        result.run_id,
        result.schema_version,
        result.policy_version,
    ):
        raise ValueError("selector identity mismatch")
    scores = {cid: ScoreSummary.model_validate(s) for cid, s in result.scores.items()}
    decisions = {
        cid: InvestmentDecision.model_validate(d) for cid, d in result.decisions.items()
    }
    outcomes = {
        cid: CandidateOutcome.model_validate(o) for cid, o in result.outcomes.items()
    }
    if (
        set(scores) != set(decisions)
        or not set(scores) <= set(outcomes)
        or set(snapshots) != set(scores)
    ):
        raise ValueError("candidate/snapshot closure mismatch")
    if set(selection.considered_candidate_ids) != set(scores) or len(
        selection.considered_candidate_ids
    ) != len(scores):
        raise ValueError("selector candidate closure mismatch")
    if (
        tuple(
            scores[cid].score_summary_id for cid in selection.considered_candidate_ids
        )
        != selection.compared_score_summary_ids
    ):
        raise ValueError("selector score closure mismatch")
    if selection.selected_candidate_id is not None and (
        selection.selected_candidate_id not in scores
        or decisions[selection.selected_candidate_id].label
        not in ("RECOMMEND", "RECOMMEND_PRIORITY")
        or selection.reason != "SELECTED"
    ):
        raise ValueError("selection mismatch")
    if selection.selected_candidate_id is None and (
        selection.reason != ("NO_RECOMMENDATION" if scores else "NO_ELIGIBLE_RESULTS")
        or any(
            d.label in ("RECOMMEND", "RECOMMEND_PRIORITY") for d in decisions.values()
        )
    ):
        raise ValueError("no-selection mismatch")
    evidence, sources, frozen = {}, {}, {}
    for cid, outcome in outcomes.items():
        if (
            cid != outcome.candidate_id
            or outcome.schema_version != result.schema_version
        ):
            raise ValueError("outcome identity mismatch")
    for cid, score in scores.items():
        decision = decisions[cid]
        snap = EvaluationSnapshot.model_validate(
            snapshots[cid], context={"execution_mode": execution_mode}
        )
        for field in (
            "schema_version",
            "run_id",
            "candidate_id",
            "policy_version",
            "snapshot_id",
            "evaluation_round",
            "evidence_revision",
        ):
            if getattr(score, field) != getattr(snap, field):
                raise ValueError("score/snapshot generation mismatch")
        if (score.run_id, score.policy_version, score.candidate_id) != (
            result.run_id,
            result.policy_version,
            cid,
        ):
            raise ValueError("score run identity mismatch")
        if (
            decision.schema_version,
            decision.run_id,
            decision.candidate_id,
            decision.score_summary_id,
        ) != (
            result.schema_version,
            result.run_id,
            cid,
            score.score_summary_id,
        ) or outcomes[cid].decision_id != decision.decision_id:
            raise ValueError("decision identity mismatch")
        expected_status = (
            "recommend"
            if decision.label.startswith("RECOMMEND")
            else decision.label.lower()
        )
        if outcomes[cid].status != expected_status:
            raise ValueError("outcome decision mismatch")
        if snap.as_of != as_of or snap.corpus_version != corpus_version:
            raise ValueError("snapshot corpus/date mismatch")
        if not set(decision.evidence_ids) <= set(snap.evidence):
            raise ValueError("decision evidence missing")
        for eid, item in snap.evidence.items():
            if (
                item.scope == "company" and item.candidate_id != cid
            ) or item.source_id not in snap.sources:
                raise ValueError("evidence attribution/source mismatch")
            if item.scope == "industry" and item.candidate_id is not None:
                raise ValueError("industry evidence attribution mismatch")
            if any(
                d is not None and d > as_of for d in (item.event_date, item.value_as_of)
            ):
                raise ValueError("future evidence in fixed context")
            if not set(item.supporting_evidence_ids) <= set(snap.evidence):
                raise ValueError("supporting evidence missing")
            for provenance in item.provenance:
                if provenance.retrieval_id not in snap.retrieval_records:
                    raise ValueError("retrieval provenance missing")
                if provenance.method == "rag" and (
                    provenance.chunk_id not in snap.chunks
                    or snap.chunks[provenance.chunk_id].source_id != item.source_id
                    or snap.chunks[provenance.chunk_id].corpus_version != corpus_version
                ):
                    raise ValueError("rag provenance mismatch")
            payload = item.model_dump(mode="json")
            if eid in evidence and evidence[eid] != payload:
                raise ValueError("conflicting evidence snapshots")
            evidence[eid] = payload
        for sid, item in snap.sources.items():
            if source_date(item) > as_of:
                raise ValueError("future source in fixed context")
            payload = item.model_dump(mode="json")
            if sid in sources and sources[sid] != payload:
                raise ValueError("conflicting source snapshots")
            sources[sid] = payload
        frozen[cid] = snap.model_dump(mode="json")
    payload = canonical(
        {
            "schema_version": result.schema_version,
            "run_id": result.run_id,
            "policy_version": result.policy_version,
            "as_of": as_of.isoformat(),
            "corpus_version": corpus_version,
            "execution_mode": execution_mode,
            "mode": "single_candidate"
            if selection.selected_candidate_id
            else "no_recommendation",
            "selection": asdict(selection),
            "outcomes": {cid: o.model_dump(mode="json") for cid, o in outcomes.items()},
            "scores": {cid: s.model_dump(mode="json") for cid, s in scores.items()},
            "decisions": {
                cid: d.model_dump(mode="json") for cid, d in decisions.items()
            },
            "snapshots": frozen,
            "evidence": evidence,
            "sources": sources,
            "errors": [e.model_dump(mode="json") for e in result.errors],
        }
    )
    return ReportContextV3(
        "sha256:" + hashlib.sha256(payload.encode()).hexdigest(), payload
    )
