"""Versioned terminal branch storage; identical replay is idempotent."""

from skala_rag.contracts.v3 import EvaluationBranchResult
from skala_rag.graph.reducers import MergeConflict, merge_result_maps


def branch_key(result: EvaluationBranchResult) -> str:
    """Snapshot ID additionally isolates generations from baseline result keys."""
    import json

    return json.dumps(
        [
            result.run_id,
            result.candidate_id,
            result.evaluation_round,
            result.snapshot_id,
            result.branch_id,
        ],
        separators=(",", ":"),
    )


def merge_branch_results_v3(left: dict, right: dict) -> dict:
    for key, payload in [*left.items(), *right.items()]:
        result = EvaluationBranchResult.model_validate(payload)
        if key != branch_key(result):
            raise MergeConflict("v3 branch key mismatch")
    return merge_result_maps(left, right)
