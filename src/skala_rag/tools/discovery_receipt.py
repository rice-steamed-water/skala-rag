"""Caller-owned discovery receipt, including failures; not a Graph patch."""

from dataclasses import dataclass

from skala_rag.contracts.bundles import DiscoveryBundle
from skala_rag.contracts.inputs import RunInput
from skala_rag.contracts.sources import Source
from skala_rag.contracts.tools import ToolBudget, ToolResult
from skala_rag.tools.discovery_live import TavilyDiscovery


@dataclass(frozen=True)
class DiscoveryReceipt:
    """Detached result and Source snapshots; failure data remains None."""

    result: ToolResult[DiscoveryBundle]
    observed_sources: dict[str, Source]


def search_with_receipt(
    adapter: TavilyDiscovery, request: RunInput, budget: ToolBudget
) -> DiscoveryReceipt:
    """Capture snapshots immediately, before adapter reuse can replace them.

    Retains every physical/normalization retrieval record and redacted error.
    This does not persist a receipt or promote snippets into Evidence.
    """
    result = adapter(request, budget)
    return DiscoveryReceipt(
        result=result.model_copy(deep=True),
        observed_sources=adapter.observed_sources,
    )
