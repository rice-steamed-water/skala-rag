"""Python-only actual/replay entrypoints; importing this module performs no work.

The caller supplies a pinned packet and independently configured authority.
Authority must use a stable ``campaign_id`` and common absolute
``campaign_directory`` outside individual outputs. Reuse both across runs;
changing the output path or packet does not authorize a fresh paid ledger.
There is no paid reset/resume option. Replay leaves the original marker intact.
Source-only accepted captures do not establish eligibility or rating admissions.
For an actual request, call ``actual(..., execute=True, api_key=...)`` explicitly.
Keep the key in Python memory; never include it in the packet or saved artifacts.
Replay's external pin is the original ``capture.json`` SHA-256, not a sibling
self-declared checksum. Controlled wires are tests, not actual execution proof.
"""

from pathlib import Path

from skala_rag.graph.actual_inputs_v3 import (
    ActualAuthorityV3,
    ActualInputError,
    RetainedSourceInputsV3,
    canonical,
    prepare_retained_sources,
)
from skala_rag.graph.actual_runner_v3 import run_actual, run_replay


def preflight_sources(output_dir: Path, *, sources: RetainedSourceInputsV3) -> Path:
    """Prepare supplied archive/index/model roots and pins without authority.

    Supply canonical candidate identities, the October 7 RunInput, archive index
    pins, SQLite/reopen pins, original index Source paths/pins, and 15 retained
    model-file pins in ``RetainedSourceInputsV3``. No callback placeholders are
    needed. The result always blocks actual execution; inspect the source-only
    compositions and missing requirements before configuring ``ActualAuthorityV3``.
    """
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=False)
    receipt = {
        "execution_scope": "source_preparation",
        "status": "preflight_blocked",
        "actual_provider_calls": 0,
        "publication_allowed": False,
        "final_allowed": False,
        "reason": "SOURCE_CLOSURE_REJECTED",
    }
    try:
        prepared = prepare_retained_sources(sources)
        (out / "source-inputs.json").write_bytes(
            canonical(sources.model_dump(mode="json"))
        )
        (out / "source-preparation.json").write_bytes(
            canonical(prepared.model_dump(mode="json"))
        )
        receipt.update(
            reason=prepared.reason,
            missing_requirements=list(prepared.missing_requirements),
        )
    except (ValueError, TypeError, KeyError, OSError) as exc:
        if isinstance(exc, ActualInputError):
            receipt["reason"] = exc.code
    (out / "receipt.json").write_bytes(canonical(receipt))
    return out


def actual(
    output_dir: Path,
    *,
    inputs: Path,
    inputs_sha256: str,
    authority: ActualAuthorityV3,
    execute: bool = False,
    api_key: str | None = None,
) -> Path:
    """Default to zero-provider-call preflight; retain its explicit receipt."""
    return run_actual(
        output_dir,
        inputs=inputs,
        inputs_sha256=inputs_sha256,
        authority=authority,
        execute=execute,
        api_key=api_key,
    )


def replay(
    output_dir: Path,
    *,
    inputs: Path,
    inputs_sha256: str,
    authority: ActualAuthorityV3,
    original_capture: Path,
    original_capture_sha256: str,
) -> Path:
    """Reuse the original composition without credentials, encoder or network."""
    return run_replay(
        output_dir,
        inputs=inputs,
        inputs_sha256=inputs_sha256,
        authority=authority,
        original_capture=original_capture,
        original_capture_sha256=original_capture_sha256,
    )
