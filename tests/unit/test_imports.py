from importlib import import_module
from importlib.metadata import distribution


def test_package_imports() -> None:
    """Import the installed package skeleton and dependencies without API calls."""
    assert distribution("skala-rag").version
    for name in (
        "skala_rag",
        "skala_rag.contracts",
        "skala_rag.graph",
        "skala_rag.agents",
        "skala_rag.tools",
        "skala_rag.rag",
        "skala_rag.scoring",
        "skala_rag.reporting",
        "skala_rag.prompts",
        "langgraph.graph",
        "pydantic",
    ):
        assert import_module(name) is not None
