"""설치된 LangGraph에서 병렬 업데이트의 State reducer 적용을 검증한다."""

import json

from langgraph.graph import END, START, StateGraph

from skala_rag.contracts.state import InvestmentState


def test_parallel_updates_use_id_reducers():
    graph = StateGraph(InvestmentState)
    graph.add_node(
        "first",
        lambda state: {
            "evaluation_results": {"co:1:founder": {"status": "success"}},
            "errors": [{"error_id": "err-fixture", "message_redacted": "가상 오류"}],
        },
    )
    graph.add_node(
        "second",
        lambda state: {
            "evaluation_results": {"co:1:market": {"status": "success"}},
            "errors": [{"error_id": "err-fixture", "message_redacted": "가상 오류"}],
        },
    )
    for node in ["first", "second"]:
        graph.add_edge(START, node)
        graph.add_edge(node, END)
    result = graph.compile().invoke({"evaluation_results": {}, "errors": []})
    assert set(result["evaluation_results"]) == {"co:1:founder", "co:1:market"}
    assert len(result["errors"]) == 1
    json.dumps(result, allow_nan=False)
