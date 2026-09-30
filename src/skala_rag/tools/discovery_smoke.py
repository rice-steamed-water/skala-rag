"""One explicitly injected discovery invocation; no CLI, config or network defaults."""

import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from skala_rag.contracts.inputs import RunInput
from skala_rag.contracts.tools import ToolBudget
from skala_rag.tools.discovery_live import TavilyDiscovery
from skala_rag.tools.discovery_receipt import DiscoveryReceipt, search_with_receipt
from skala_rag.tools.discovery_runtime import TavilyRuntimeBridge
from skala_rag.tools.runtime import AdapterRuntime


class UnsafeSmokeReceipt(ValueError):
    """Unsafe bytes were not written; detached receipt remains caller-accessible."""

    def __init__(self, receipt: DiscoveryReceipt) -> None:
        super().__init__("unsafe discovery receipt; output refused")
        self.receipt = receipt


class SmokePersistenceError(OSError):
    """Publication failed; caller can recover the exact detached receipt."""

    def __init__(self, receipt: DiscoveryReceipt) -> None:
        super().__init__("discovery receipt publication failed")
        self.receipt = receipt


def _safe(value: object, secret: str) -> bool:
    """Reject known credentials/token patterns, not an arbitrary secret detector."""
    if isinstance(value, dict):
        return all(
            not re.fullmatch(
                r"(?i)api[_-]?key|password|secret|access[_-]?token|authorization",
                str(k),
            )
            and _safe(k, secret)
            and _safe(v, secret)
            for k, v in value.items()
        )
    if isinstance(value, list):
        return all(_safe(v, secret) for v in value)
    if not isinstance(value, str):
        return True
    if (secret and secret in value) or re.search(
        r"(?i)(?:bearer\s+\S+|(?:api[_-]?key|password|secret|access[_-]?token)\s*[:=]\s*\S+|tvly-[\w-]+|sk-[\w-]{12,})",
        value,
    ):
        return False
    if value.startswith(("https://", "http://")):
        parts = urlsplit(value)
        if parts.username or parts.password:
            return False
        if any(
            re.search(r"(?i)key|token|secret|password|signature", key)
            for key, _ in parse_qsl(parts.query)
        ):
            return False
    return True


def run_discovery_smoke(
    *,
    adapter: TavilyDiscovery,
    request: RunInput,
    budget: ToolBudget,
    output_directory: Path,
) -> Path:
    """Persist a detached run receipt, including failed ToolResults and snippets.

    Caller approves and creates a dedicated empty directory for this invocation.
    A reservation remains even on failure: never reuse a directory/run receipt.
    Fixture receipts are synthetic, not proof of live readiness or M2 completion.
    """
    bridge = adapter.runtime
    if type(adapter) is not TavilyDiscovery or type(bridge) is not TavilyRuntimeBridge:
        raise ValueError("requires the actual discovery adapter and shared bridge")
    if type(bridge.runtime) is not AdapterRuntime:
        raise ValueError("requires the actual shared runtime")
    if (
        bridge.runtime.policy.execution_mode != request.execution_mode
        or bridge.context.run_id != adapter.run_id
        or bridge.context.schema_version != adapter.schema_version
        or bridge.context.tool_name != "tavily"
        or bridge.context.node != "discovery"
        or bridge.context.candidate_id is not None
        or request.schema_version != adapter.schema_version
        or budget.schema_version != adapter.schema_version
        or (request.execution_mode == "live" and not adapter.allow_live)
    ):
        raise ValueError("inconsistent invocation context or live opt-in")
    directory = Path(output_directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("output directory must be an existing caller-owned directory")
    if any(directory.iterdir()):
        raise FileExistsError("output directory must be empty and unused")
    reservation = directory / ".discovery-smoke"
    reservation.mkdir(mode=0o700)
    http_requests = []

    def observe_request(outgoing):
        # Observe the actual transport-bound JSON, never headers/client config.
        body = json.loads(outgoing.content)
        allowed = {
            "query",
            "topic",
            "search_depth",
            "max_results",
            "end_date",
            "include_answer",
            "include_raw_content",
            "include_images",
            "auto_parameters",
            "country",
            "language",
        }
        if not isinstance(body, dict) or not set(body) <= allowed:
            raise ValueError("unexpected outgoing request shape")
        observation = {
            "method": outgoing.method,
            "url": str(outgoing.url),
            "json": body,
        }
        if not _safe(observation, bridge.api_key):
            raise ValueError("unsafe outgoing request")
        http_requests.append(observation)

    # Exclusive synchronous use of the caller's client is required. Do not
    # replace its existing hooks; detach ours even if invocation raises.
    hooks = bridge.client.event_hooks["request"]
    hooks.append(observe_request)
    try:
        receipt = search_with_receipt(adapter, request, budget)
    finally:
        hooks.remove(observe_request)
    payload = {
        "run_id": adapter.run_id,
        "execution_mode": request.execution_mode,
        "bridge_version": bridge.bridge_version,
        "runtime_schema_version": bridge.runtime.policy.schema_version,
        "runtime_version": None,  # No implementation version is exposed by runtime.
        "http_requests": http_requests,
        "request": request.model_dump(mode="json"),
        "budget": budget.model_dump(mode="json"),
        "provider": "tavily",
        # The bridge does not expose provider response/request IDs or HTTP bodies.
        "provider_request_id": None,
        "result": receipt.result.model_dump(mode="json"),
        "attempt_errors": [
            bridge.runtime.error_history[record.error_id].model_dump(mode="json")
            for record in receipt.result.retrieval_records
            if record.error_id in bridge.runtime.error_history
        ],
        "observed_sources": {
            sid: source.model_dump(mode="json")
            for sid, source in receipt.observed_sources.items()
        },
    }
    if not _safe(payload, bridge.api_key):
        raise UnsafeSmokeReceipt(receipt)
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temporary = reservation / "receipt.json.tmp"
    target = directory / "receipt.json"
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic publication without replacement; unlike replace(), cannot clobber.
        os.link(temporary, target)
        temporary.unlink()
    except OSError:
        raise SmokePersistenceError(receipt) from None
    return target
