"""Injected five-branch v3 evaluation, atomic promotion and failure-only handoff.

Not a v3 candidate selector/runner. Never adapts v3 observations to baseline DTOs.
"""

from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import datetime

from langgraph.graph import END, START, StateGraph

from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.ids import evaluation_key
from skala_rag.contracts.reports import CandidateOutcome
from skala_rag.contracts.state_v3 import EvaluationStateV3
from skala_rag.contracts.v3 import (
    BRANCH_DIMENSIONS,
    CriterionAssessment,
    EvaluationBranchResult,
    EvaluationSnapshot,
)
from skala_rag.graph.reducers import merge_errors, merge_result_maps
from skala_rag.graph.reducers_v3 import branch_key, merge_branch_results_v3
from skala_rag.scoring.catalog import Criterion

IDENTITY = (
    "run_id",
    "candidate_id",
    "evaluation_round",
    "snapshot_id",
    "evidence_revision",
    "policy_version",
)
ApplicabilityValidator = Callable[[CriterionAssessment, EvaluationSnapshot], bool]


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_branch_v3(
    result, snapshot, *, criteria, industry_evidence_dimensions, applicability_validator
):
    """Pure gate: complete catalog and actual frozen Evidence attribution."""
    _require(
        all(getattr(result, f) == getattr(snapshot, f) for f in IDENTITY),
        "Branch generation identity mismatch",
    )
    if result.status == "failure":
        return
    for dimension, evaluation in result.evaluations.items():
        expected = {c.criterion_id for c in criteria if c.dimension == dimension}
        got = [c.criterion_id for c in evaluation.criteria]
        _require(
            len(got) == len(set(got)) and set(got) == expected,
            "Incomplete dimension criterion set",
        )
        for criterion in evaluation.criteria:
            ids = list(criterion.evidence_ids)
            if criterion.status == "not_applicable":
                ids += criterion.applicability_evidence_ids
                _require(
                    applicability_validator is not None,
                    "N/A requires supplied approved applicability validator",
                )
            for eid in ids:
                evidence = snapshot.evidence.get(eid)
                _require(
                    evidence is not None and eid in snapshot.evidence_ids,
                    "Evidence outside snapshot",
                )
                _require(evidence.evidence_id == eid, "Evidence map key mismatch")
                _require(
                    criterion.criterion_id in evidence.criterion_ids,
                    "Evidence criterion attribution mismatch",
                )
                if evidence.scope == "company":
                    _require(
                        evidence.candidate_id == snapshot.candidate_id,
                        "Evidence candidate attribution mismatch",
                    )
                else:
                    _require(
                        dimension in industry_evidence_dimensions,
                        "Industry evidence dimension not approved",
                    )
            if criterion.status == "not_applicable":
                _require(
                    applicability_validator(
                        criterion.model_copy(deep=True), snapshot.model_copy(deep=True)
                    )
                    is True,
                    "Applicability rule/reason/evidence rejected",
                )
        _require(
            all(
                g.candidate_id == snapshot.candidate_id and g.criterion_id in expected
                for g in evaluation.research_gaps
            ),
            "Research gap attribution mismatch",
        )


def join_evaluation_v3(
    snapshot,
    results,
    *,
    criteria,
    industry_evidence_dimensions,
    applicability_validator,
):
    """Return complete six dimensions or terminal errors, never partial promotion.

    Stored envelopes of another candidate/round are ignored. A matching
    candidate/round with corrupt run/snapshot/revision/policy is rejected.
    """
    active = {}
    for key, payload in results.items():
        result = EvaluationBranchResult.model_validate(payload)
        _require(key == branch_key(result), "Branch key mismatch")
        if (result.candidate_id, result.evaluation_round) != (
            snapshot.candidate_id,
            snapshot.evaluation_round,
        ):
            continue
        validate_branch_v3(
            result,
            snapshot,
            criteria=criteria,
            industry_evidence_dimensions=industry_evidence_dimensions,
            applicability_validator=applicability_validator,
        )
        _require(result.branch_id not in active, "Duplicate current branch")
        active[result.branch_id] = result
    _require(set(active) == set(BRANCH_DIMENSIONS), "Missing terminal branch")
    errors = [
        e.model_dump(mode="json") for result in active.values() for e in result.errors
    ]
    if errors:
        return {
            "evaluation_status_v3": "failure",
            "evaluations_v3": {},
            "errors": errors,
        }
    evaluations = {
        evaluation_key(e.candidate_id, e.evaluation_round, dim): e.model_dump(
            mode="json"
        )
        for branch in BRANCH_DIMENSIONS
        for dim, e in active[branch].evaluations.items()
    }
    return {"evaluation_status_v3": "success", "evaluations_v3": evaluations}


def archive_advance_failure_v3(state):
    """Reusable single controller: archive current failed candidate, advance once.

    This ends the evaluation stage; the subsequent #23 owner selects the next
    candidate. Exact repeat after advance is a no-op, conflicting archive rejects.
    """
    if state.get("current_candidate_id") is None:
        return {}
    cid = state["current_candidate_id"]
    _require(state["evaluation_status_v3"] == "failure", "Not a failed evaluation")
    index = state["candidate_index"]
    _require(
        state["candidates"][index]["candidate_id"] == cid, "Candidate index mismatch"
    )
    failure_ids = state["evaluation_failure_ids_v3"]
    errors = [
        e["error_id"]
        for e in state["errors"]
        if e["error_id"] in failure_ids and e.get("candidate_id") == cid
    ]
    _require(set(errors) == set(failure_ids), "Failure errors not in current candidate")
    _require(bool(errors), "Archive requires attributed errors")
    schema = next(
        e["schema_version"] for e in state["errors"] if e["error_id"] in failure_ids
    )
    outcome = CandidateOutcome(
        schema_version=schema,
        candidate_id=cid,
        status="failed",
        eligibility_result_id=None,
        decision_id=None,
        failure_ids=errors,
        summary_reason="v3 evaluation failed",
    ).model_dump(mode="json")
    outcomes = merge_result_maps(state.get("candidate_outcomes", {}), {cid: outcome})
    return {
        "candidate_outcomes": outcomes,
        "candidate_status": {**state.get("candidate_status", {}), cid: "failed"},
        "candidate_index": index + 1,
        "current_candidate_id": None,
    }


def build_evaluation_graph_v3(
    evaluators: Mapping[str, Callable],
    *,
    criteria: Sequence[Criterion],
    policy_version: str,
    run_id: str,
    schema_version: str,
    industry_evidence_dimensions: Collection[str],
    applicability_validator: ApplicabilityValidator | None,
    clock: Callable[[], datetime],
):
    """Build (uncompiled) fixture StateGraph; callbacks receive detached Snapshot.

    Five evaluators return v3 EvaluationBranchResult (or its JSON payload).
    business_deal must return traction AND deal_terms atomically. Exceptions and
    invalid outputs become redacted terminal failures. No provider is supplied.
    """
    _require(
        set(evaluators) == set(BRANCH_DIMENSIONS), "Exactly five evaluators required"
    )
    criteria = tuple(criteria)
    _require(
        len(criteria) == 23 and len({c.criterion_id for c in criteria}) == 23,
        "Explicit complete 23-criterion catalog required",
    )
    _require(
        {c.dimension for c in criteria} == set().union(*BRANCH_DIMENSIONS.values()),
        "Six-dimension catalog required",
    )
    options = dict(
        criteria=criteria,
        industry_evidence_dimensions=frozenset(industry_evidence_dimensions),
        applicability_validator=applicability_validator,
    )

    def snapshot_for(state):
        snapshot = EvaluationSnapshot.model_validate(
            state["snapshot_v3"], context={"execution_mode": "fixture"}
        )
        _require(
            state["run_input"]["execution_mode"] == "fixture",
            "Fixture-only graph",
        )
        _require(
            snapshot.run_id == run_id
            and snapshot.schema_version == schema_version
            and snapshot.candidate_id == state["current_candidate_id"]
            and snapshot.evaluation_round
            == state["evaluation_rounds"][snapshot.candidate_id]
            and snapshot.evidence_revision
            == state["evidence_revisions"][snapshot.candidate_id]
            and snapshot.policy_version
            == state["run_input"]["policy_version"]
            == policy_version,
            "Snapshot/controller generation mismatch",
        )
        _require(
            state["snapshots"].get(snapshot.snapshot_id)
            == snapshot.model_dump(mode="json"),
            "Snapshot differs from frozen controller storage",
        )
        return snapshot

    def error(snapshot, node):
        return WorkflowError(
            schema_version=snapshot.schema_version,
            error_id=f"v3:{snapshot.snapshot_id}:{node}",
            run_id=snapshot.run_id,
            candidate_id=snapshot.candidate_id,
            node=node,
            error_code="UPSTREAM_INVALID",
            message_redacted="Invalid v3 evaluation branch or join",
            retryable=False,
            attempt=1,
            timestamp=clock(),
        )

    def prepare(state):
        try:
            snapshot_for(state)
        except Exception:
            failure = WorkflowError(
                schema_version=schema_version,
                error_id=f"v3:{run_id}:{state.get('candidate_index', 0)}:prepare",
                run_id=run_id,
                candidate_id=state.get("current_candidate_id"),
                node="prepare_v3",
                error_code="UPSTREAM_INVALID",
                message_redacted="Invalid frozen snapshot or controller generation",
                retryable=False,
                attempt=1,
                timestamp=clock(),
            )
            return {
                "evaluations_v3": {},
                "evaluation_status_v3": "failure",
                "evaluation_failure_ids_v3": [failure.error_id],
                "errors": [failure.model_dump(mode="json")],
            }
        return {
            "evaluations_v3": {},
            "evaluation_status_v3": "pending",
            "evaluation_failure_ids_v3": [],
        }

    def branch_node(branch):
        def call(state):
            snapshot = snapshot_for(state)
            try:
                result = EvaluationBranchResult.model_validate(
                    evaluators[branch](snapshot.model_copy(deep=True))
                )
                _require(result.branch_id == branch, "Wrong evaluator branch")
                validate_branch_v3(result, snapshot, **options)
                delta = {branch_key(result): result.model_dump(mode="json")}
                merge_branch_results_v3(state.get("branch_results_v3", {}), delta)
            except Exception:
                result = EvaluationBranchResult(
                    **{f: getattr(snapshot, f) for f in IDENTITY},
                    schema_version=snapshot.schema_version,
                    branch_id=branch,
                    status="failure",
                    evaluations=None,
                    errors=[error(snapshot, branch)],
                )
                delta = {branch_key(result): result.model_dump(mode="json")}
                # Do not overwrite a conflicting existing result through the reducer.
                if branch_key(result) in state.get("branch_results_v3", {}):
                    return {"errors": [error(snapshot, branch).model_dump(mode="json")]}
            return {"branch_results_v3": delta}

        return call

    def join(state):
        snapshot = snapshot_for(state)
        try:
            update = join_evaluation_v3(
                snapshot, state.get("branch_results_v3", {}), **options
            )
            # A replay conflict detected by a branch cannot promote an old success.
            conflicts = [
                e
                for e in state.get("errors", [])
                if e.get("error_id")
                in {f"v3:{snapshot.snapshot_id}:{b}" for b in BRANCH_DIMENSIONS}
            ]
            if conflicts:
                combined = merge_errors(update.get("errors", []), conflicts)
                return {
                    "evaluation_status_v3": "failure",
                    "evaluations_v3": {},
                    "evaluation_failure_ids_v3": [e["error_id"] for e in combined],
                    "errors": combined,
                }
            if update["evaluation_status_v3"] == "failure":
                update["evaluation_failure_ids_v3"] = [
                    e["error_id"] for e in update["errors"]
                ]
            return update
        except Exception:
            failure = error(snapshot, "join")
            return {
                "evaluation_status_v3": "failure",
                "evaluations_v3": {},
                "evaluation_failure_ids_v3": [failure.error_id],
                "errors": [failure.model_dump(mode="json")],
            }

    graph = StateGraph(EvaluationStateV3)
    graph.add_node("prepare_v3", prepare)
    graph.add_node("join_v3", join)
    graph.add_node("archive_advance_v3", archive_advance_failure_v3)
    graph.add_edge(START, "prepare_v3")
    for branch in BRANCH_DIMENSIONS:
        graph.add_node(branch, branch_node(branch))
    graph.add_conditional_edges(
        "prepare_v3",
        lambda state: (
            list(BRANCH_DIMENSIONS)
            if state["evaluation_status_v3"] == "pending"
            else "archive_advance_v3"
        ),
        {
            **{b: b for b in BRANCH_DIMENSIONS},
            "archive_advance_v3": "archive_advance_v3",
        },
    )
    graph.add_edge(list(BRANCH_DIMENSIONS), "join_v3")
    graph.add_conditional_edges(
        "join_v3",
        lambda state: state["evaluation_status_v3"],
        {"success": END, "failure": "archive_advance_v3"},
    )
    graph.add_edge("archive_advance_v3", END)
    return graph
