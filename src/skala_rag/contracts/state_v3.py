"""Opt-in v3 state extension; baseline state and initial defaults are unchanged."""

from typing import Annotated

from skala_rag.contracts.state import InvestmentState, JsonObject
from skala_rag.graph.reducers_v3 import merge_branch_results_v3


class EvaluationStateV3(InvestmentState, total=False):
    snapshot_v3: JsonObject
    branch_results_v3: Annotated[dict[str, JsonObject], merge_branch_results_v3]
    evaluations_v3: dict[str, JsonObject]
    evaluation_status_v3: str
    evaluation_failure_ids_v3: list[str]
