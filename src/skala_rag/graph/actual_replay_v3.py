"""Code-owned nondeterminism scopes and externally pinned replay closure."""

import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

from skala_rag.contracts import ReportJudgement, RunManifest
from skala_rag.graph.actual_inputs_v3 import (
    ActualInputError,
    ActualInputsV3,
    canonical,
    digest,
    file_digest,
)
from skala_rag.rag import adapter as retrieval_module
from skala_rag.rag.dense import QueryEncoder, QueryVector
from skala_rag.rag.index_v3 import IndexSettings
from skala_rag.rag.local_bge_validation import LocalEncoder
from skala_rag.rag.query_local import LocalQueryEncoder
from skala_rag.tools import runtime as runtime_module
from skala_rag.tools.actual_wire_capture import ActualWireCapture

_SCOPE: ContextVar[str] = ContextVar("actual_v3_scope", default="controller")
_JOURNAL: ContextVar["ReplayInputs | None"] = ContextVar(
    "actual_v3_journal", default=None
)
_INSTALL_LOCK = RLock()


@contextmanager
def role_scope(name: str) -> Iterator[None]:
    token = _SCOPE.set(name)
    try:
        yield
    finally:
        _SCOPE.reset(token)


class ReplayInputs:
    """Record exact clock/UUID/vector sequences independently for each role.

    Role keys are assigned by this composition, never by model response arrival.
    Only the two original module UUID imports and ReportLab's deterministic PDF
    setting are scoped; there is no arbitrary patch target or plugin import.
    """

    def __init__(self, saved: Mapping[str, list[JsonValue]] | None = None):
        self.values = {} if saved is None else dict(saved)
        self.replay = saved is not None
        self.positions: dict[str, int] = {}
        self._lock = RLock()

    def take(self, kind: str, produce: Callable[[], JsonValue]) -> JsonValue:
        key = f"{_SCOPE.get()}/{kind}"
        with self._lock:
            position = self.positions.get(key, 0)
            if self.replay:
                if key not in self.values or position >= len(self.values[key]):
                    raise ActualInputError("REPLAY_INPUT_EXHAUSTED")
                value = self.values[key][position]
            else:
                value = produce()
                self.values.setdefault(key, []).append(value)
            self.positions[key] = position + 1
            return value

    def now(self) -> datetime:
        value = self.take("clock", lambda: datetime.now(UTC).isoformat())
        if not isinstance(value, str):
            raise ActualInputError("REPLAY_CLOCK_INVALID")
        return datetime.fromisoformat(value)

    def uuid(self) -> UUID:
        value = self.take("uuid", lambda: uuid4().hex)
        if not isinstance(value, str):
            raise ActualInputError("REPLAY_UUID_INVALID")
        return UUID(hex=value)

    def assert_consumed(self) -> None:
        if self.replay and any(
            self.positions.get(key, 0) != len(values)
            for key, values in self.values.items()
        ):
            raise ActualInputError("REPLAY_INPUT_UNCONSUMED")

    @contextmanager
    def installed(self) -> Iterator[None]:
        import reportlab.rl_config

        with _INSTALL_LOCK:
            runtime_uuid = runtime_module.uuid4
            retrieval_uuid = retrieval_module.uuid4
            invariant = getattr(reportlab.rl_config, "invariant")
            token = _JOURNAL.set(self)

            def scoped_uuid() -> UUID:
                journal = _JOURNAL.get()
                return uuid4() if journal is None else journal.uuid()

            setattr(runtime_module, "uuid4", scoped_uuid)
            setattr(retrieval_module, "uuid4", scoped_uuid)
            setattr(reportlab.rl_config, "invariant", 1)
            try:
                yield
            finally:
                setattr(runtime_module, "uuid4", runtime_uuid)
                setattr(retrieval_module, "uuid4", retrieval_uuid)
                setattr(reportlab.rl_config, "invariant", invariant)
                _JOURNAL.reset(token)


class CapturedLocalEncoder:
    """Lazy local-only BGE; replay has no route to instantiate a model."""

    retry_owner = "runtime"

    def __init__(self, packet: ActualInputsV3, journal: ReplayInputs):
        self.packet = packet
        self.journal = journal
        self._encoder: QueryEncoder | None = None

    def encode_once(
        self, query: str, *, settings: IndexSettings, timeout_seconds: float
    ) -> QueryVector:
        commitment = digest(canonical([query, settings.snapshot()]))

        def encode() -> JsonValue:
            if self.journal.replay:
                raise ActualInputError("REPLAY_ENCODER_FORBIDDEN")
            if self._encoder is None:
                from sentence_transformers import SentenceTransformer

                model = SentenceTransformer(
                    str(self.packet.model_root),
                    local_files_only=True,
                    trust_remote_code=False,
                    device=settings.embedding_settings["device"],
                )
                if (
                    model.max_seq_length != 8192
                    or model.get_embedding_dimension() != settings.dimension
                ):
                    raise ActualInputError("MODEL_CONFIGURATION_MISMATCH")
                self._encoder = LocalQueryEncoder(
                    LocalEncoder(model), settings=settings
                )
            vector = self._encoder.encode_once(
                query, settings=settings, timeout_seconds=timeout_seconds
            )
            return json.loads(canonical(asdict(vector)))

        value = self.journal.take(f"vector/{commitment}", encode)
        if not isinstance(value, dict):
            raise ActualInputError("REPLAY_VECTOR_INVALID")
        return TypeAdapter(QueryVector).validate_json(canonical(value))


def implementation_commitments(root: Path) -> dict[str, str]:
    """Commit code, configuration, prompt resources and renderer fonts."""
    paths = sorted(
        [
            *sorted((root / "src/skala_rag").rglob("*.py")),
            *sorted((root / "configs").rglob("*.json")),
            *sorted((root / "configs").rglob("*.yaml")),
            *sorted((root / "src/skala_rag/prompt/text").glob("*.json")),
            *sorted((root / "src/skala_rag/reporting/fonts").glob("*.ttf")),
            root / "uv.lock",
        ],
        key=lambda path: str(path.relative_to(root)),
    )
    return {str(p.relative_to(root)): file_digest(p) for p in paths}


class ReplayDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    execution_scope: Literal["actual", "controlled_response"]
    inputs_sha256: str
    publication_allowed: Literal[False]
    commitments: dict[str, str]
    files: dict[str, str]
    nondeterminism: dict[str, list[JsonValue]]
    stable_hashes: dict[str, str]


def load_replay(
    path: Path,
    expected_sha256: str,
    *,
    inputs_sha256: str,
    commitments: Mapping[str, str],
    packet: ActualInputsV3,
) -> tuple[dict, ActualWireCapture, ReplayInputs]:
    """Check manifest, files, original origin and wire pin BEFORE callbacks."""
    raw = path.read_bytes()
    if digest(raw) != expected_sha256:
        raise ActualInputError("REPLAY_PIN_MISMATCH")
    saved = ReplayDocument.model_validate_json(raw).model_dump(mode="json")
    if (
        saved["execution_scope"] != "actual"
        or saved["inputs_sha256"] != inputs_sha256
        or saved["commitments"] != commitments
        or saved["publication_allowed"] is not False
    ):
        raise ActualInputError("REPLAY_ORIGIN_MISMATCH")
    if (
        not {
            "manifest.json",
            "campaign.json",
            "wire.json",
            "inputs.json",
            "states.json",
            "context.json",
            "draft.json",
            "reviews.json",
            "source-commitments.json",
        }
        <= saved["files"].keys()
    ):
        raise ActualInputError("REPLAY_CLOSURE_MISSING")
    if saved["files"]["inputs.json"] != digest(
        canonical(packet.model_dump(mode="json"))
    ):
        raise ActualInputError("REPLAY_INPUT_MISMATCH")
    for name, pin in saved["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ActualInputError("REPLAY_PATH_INVALID")
        if file_digest(path.parent / relative) != pin:
            raise ActualInputError("REPLAY_FILE_MISMATCH")
    manifest = RunManifest.model_validate_json(
        (path.parent / "manifest.json").read_bytes()
    )
    if (
        manifest.run_input.execution_mode != "live"
        or manifest.run_input != packet.run_input
        or manifest.run_id != packet.run_id
        or manifest.workflow_status != "completed"
        or manifest.tool_status.get("execution_scope") != "actual"
        or manifest.tool_status.get("publication_allowed") is not False
    ):
        raise ActualInputError("REPLAY_MANIFEST_MISMATCH")
    judgement = manifest.validation_results.get("judge")
    if (
        not isinstance(judgement, ReportJudgement)
        or judgement.verdict != "pass"
        or any(finding.severity == "stub" for finding in judgement.findings)
    ):
        raise ActualInputError("REPLAY_JUDGE_REJECTED")
    for artifact in manifest.artifacts.values():
        if artifact.artifact_hash != "sha256:" + saved["files"].get(
            artifact.artifact_path, ""
        ):
            raise ActualInputError("REPLAY_ARTIFACT_COMMITMENT_MISMATCH")
    capture = ActualWireCapture.load(
        path.parent / "wire.json", saved["files"]["wire.json"]
    )
    return saved, capture, ReplayInputs(saved["nondeterminism"])
