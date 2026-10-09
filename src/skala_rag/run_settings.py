"""Explicit recommended run choices; only candidate selection is executed here."""

import hashlib
import json
import platform
import random
import sys
from collections.abc import Sequence
from dataclasses import InitVar, asdict, dataclass, field
from pathlib import Path

from skala_rag.agents import discovery
from skala_rag.contracts import Candidate
from skala_rag.settings import RuntimeDocument, load_runtime_document


def _text(value: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError("expected nonblank text")


def _identifier(value: str) -> None:
    _text(value)
    if not value.isascii() or any(not 33 <= ord(c) <= 126 for c in value):
        raise ValueError("profile IDs require printable ASCII without whitespace")


@dataclass(frozen=True, slots=True)
class RunProfile:
    run_id: str
    selection_source: str
    authority_reference: str
    policy_references: tuple[str, ...]
    code_version: str | None
    max_candidates: int = field(init=False)
    seed: int = field(init=False)
    initial_company_research: int = field(init=False)
    unknown_additional_retries: int = field(init=False)
    evaluate_unknown: bool = field(init=False)
    refill: bool = field(init=False)
    paid_call_allowance: int = field(init=False)
    paid_cost_usd: int = field(init=False)
    past_paid_ledger: str = field(init=False)
    enforcement: str = field(init=False)
    criterion_support: str = field(init=False)
    coverage_target: str = field(init=False)
    profile_version: str = field(init=False)
    runtime_document: InitVar[RuntimeDocument | None] = field(
        default=None, kw_only=True
    )

    def __post_init__(self, runtime_document: RuntimeDocument | None) -> None:
        self._validate_identity()
        document = (
            runtime_document
            if runtime_document is not None
            else load_runtime_document()
        )
        RuntimeDocument.model_validate_json(document.model_dump_json(), strict=True)
        settings = document.profiles.recommended_run
        object.__setattr__(self, "max_candidates", settings.max_candidates)
        object.__setattr__(self, "seed", settings.seed)
        object.__setattr__(
            self, "initial_company_research", settings.initial_company_research
        )
        object.__setattr__(
            self, "unknown_additional_retries", settings.unknown_additional_retries
        )
        object.__setattr__(self, "evaluate_unknown", settings.evaluate_unknown)
        object.__setattr__(self, "refill", settings.refill)
        object.__setattr__(self, "paid_call_allowance", settings.paid_call_allowance)
        object.__setattr__(self, "paid_cost_usd", int(settings.paid_cost_usd))
        object.__setattr__(self, "past_paid_ledger", settings.past_paid_ledger)
        object.__setattr__(self, "enforcement", settings.enforcement)
        object.__setattr__(self, "criterion_support", settings.criterion_support)
        object.__setattr__(self, "coverage_target", settings.coverage_target)
        object.__setattr__(self, "profile_version", settings.profile_version)
        _validate_profile(self)

    def _validate_identity(self) -> None:
        _identifier(self.run_id)
        _text(self.selection_source)
        _text(self.authority_reference)
        if type(self.policy_references) is not tuple or not self.policy_references:
            raise ValueError("policy references require a nonempty immutable tuple")
        for reference in self.policy_references:
            _text(reference)
        if len(set(self.policy_references)) != len(self.policy_references):
            raise ValueError("duplicate policy references")
        if self.code_version is not None:
            _text(self.code_version)


def recommended_profile(
    *,
    run_id: str,
    selection_source: str,
    authority_reference: str,
    policy_references: tuple[str, ...],
    code_version: str | None,
    runtime_document: RuntimeDocument | None = None,
) -> RunProfile:
    """Select this profile explicitly; references are declarations, not attestations."""
    return RunProfile(
        run_id,
        selection_source,
        authority_reference,
        policy_references,
        code_version,
        runtime_document=runtime_document,
    )


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MergeReceipt:
    kept_candidate_id: str
    merged_candidate_id: str
    matched_on: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier(self.kept_candidate_id)
        _identifier(self.merged_candidate_id)
        _texts(self.matched_on)


def _texts(values: tuple[str, ...]) -> None:
    if type(values) is not tuple:
        raise ValueError("receipt collections must be immutable tuples")
    for value in values:
        _text(value)


@dataclass(frozen=True, slots=True)
class SelectionReceipt:
    profile: RunProfile
    execution_mode: str
    discovered_ids: tuple[str, ...]
    input_candidate_json: tuple[str, ...]
    input_fingerprint: str
    normalized_ids: tuple[str, ...]
    normalized_candidate_json: tuple[str, ...]
    population_ids_ascii: tuple[str, ...]
    selected_ids: tuple[str, ...]
    selected_candidate_json: tuple[str, ...]
    excluded_ids: tuple[str, ...]
    merges: tuple[MergeReceipt, ...]
    python_implementation: str
    python_version: str
    algorithm: str
    algorithm_version: str
    selection_code_sha256: str
    normalizer_code_sha256: str
    code_version_status: str
    receipt_version: str

    def __post_init__(self) -> None:
        _validate_profile(self.profile)
        if self.execution_mode not in ("fixture", "live"):
            raise ValueError("invalid receipt execution context")
        for name in (
            "discovered_ids",
            "normalized_ids",
            "population_ids_ascii",
            "selected_ids",
            "excluded_ids",
        ):
            _texts(getattr(self, name))
            for identifier in getattr(self, name):
                _identifier(identifier)
        for name in (
            "input_candidate_json",
            "normalized_candidate_json",
            "selected_candidate_json",
        ):
            _texts(getattr(self, name))
        if type(self.merges) is not tuple or any(
            type(m) is not MergeReceipt for m in self.merges
        ):
            raise ValueError("merges must be immutable MergeReceipt tuples")
        for merge in self.merges:
            merge.__post_init__()
        for name in (
            "execution_mode",
            "input_fingerprint",
            "python_implementation",
            "python_version",
            "algorithm",
            "algorithm_version",
            "selection_code_sha256",
            "normalizer_code_sha256",
            "code_version_status",
            "receipt_version",
        ):
            _text(getattr(self, name))

    def to_json(self) -> str:
        return _json(asdict(self))


@dataclass(frozen=True, slots=True)
class CandidateSelection:
    receipt: SelectionReceipt

    def __post_init__(self) -> None:
        if type(self.receipt) is not SelectionReceipt:
            raise ValueError("expected SelectionReceipt")
        self.receipt.__post_init__()

    @property
    def candidates(self) -> tuple[Candidate, ...]:
        """Fresh validated DTO copies: caller mutation cannot poison the receipt."""
        return tuple(
            Candidate.model_validate_json(
                payload, context={"execution_mode": self.receipt.execution_mode}
            )
            for payload in self.receipt.selected_candidate_json
        )


def _validate_profile(profile: RunProfile) -> None:
    if type(profile) is not RunProfile:
        raise ValueError("an explicit RunProfile is required")
    profile._validate_identity()
    # These approved handoff choices are authority, not construction defaults.
    # Compare without loading new operator settings into an existing receipt.
    expected = {
        "run_id": profile.run_id,
        "selection_source": profile.selection_source,
        "authority_reference": profile.authority_reference,
        "policy_references": profile.policy_references,
        "code_version": profile.code_version,
        "max_candidates": 5,
        "seed": 42,
        "initial_company_research": 1,
        "unknown_additional_retries": 0,
        "evaluate_unknown": False,
        "refill": False,
        "paid_call_allowance": 0,
        "paid_cost_usd": 0,
        "past_paid_ledger": "not_supplied_unverified",
        "enforcement": "controller_handoff_only",
        "criterion_support": "actual_fact_verifier_approved_minimum_evidence",
        "coverage_target": "missing_weight*100 < 30*applicable_weight",
        "profile_version": "recommended-run-profile-v1",
    }
    # JSON comparison is type-sensitive (unlike True == 1).
    if _json(asdict(profile)) != _json(expected):
        raise ValueError("profile differs from the recommended run choices")


def normalize_and_select(
    candidates: Sequence[Candidate], *, profile: RunProfile, execution_mode: str
) -> CandidateSelection:
    """Call the existing normalizer once, recording dedup and selection closure.

    No research/evaluation/provider callbacks are accepted. Other profile fields
    are controller handoff settings, not enforcement of application-wide gates.
    """
    _validate_profile(profile)
    if type(execution_mode) is not str or execution_mode not in ("fixture", "live"):
        raise ValueError("explicit fixture/live DTO validation context required")
    originals = []
    for candidate in candidates:
        if type(candidate) is not Candidate:
            raise ValueError("expected Candidate DTO")
        # Revalidate mutable DTOs/model_copy payloads with explicit context.
        copied = Candidate.model_validate(
            candidate.model_dump(mode="json"),
            context={"execution_mode": execution_mode},
        )
        _identifier(copied.candidate_id)
        originals.append(copied)
    input_json = tuple(_json(c.model_dump(mode="json")) for c in originals)
    population: list[Candidate] = []

    def select(candidates: Sequence[Candidate], max_candidates: int) -> tuple[str, ...]:
        population.extend(candidates)
        return tuple(
            random.Random(profile.seed).sample(
                sorted(c.candidate_id for c in candidates), max_candidates
            )
        )

    limit_policy: discovery.CandidateLimitPolicy = select
    normalized = discovery.normalize_candidates(
        originals, max_candidates=profile.max_candidates, limit_policy=limit_policy
    )
    # The callback is bypassed within the limit: do not create/draw an RNG.
    if not population:
        population = normalized.candidates
    ids = tuple(c.candidate_id for c in population)
    receipt = SelectionReceipt(
        profile=profile,
        execution_mode=execution_mode,
        discovered_ids=tuple(c.candidate_id for c in originals),
        input_candidate_json=input_json,
        input_fingerprint=_hash(_json(input_json)),
        normalized_ids=ids,
        normalized_candidate_json=tuple(
            _json(c.model_dump(mode="json")) for c in population
        ),
        population_ids_ascii=tuple(sorted(ids)),
        selected_ids=tuple(c.candidate_id for c in normalized.candidates),
        selected_candidate_json=tuple(
            _json(c.model_dump(mode="json")) for c in normalized.candidates
        ),
        excluded_ids=tuple(normalized.dropped_candidate_ids),
        merges=tuple(
            MergeReceipt(
                m.kept_candidate_id, m.merged_candidate_id, tuple(m.matched_on)
            )
            for m in normalized.merges
        ),
        python_implementation=platform.python_implementation(),
        python_version=sys.version,
        algorithm="Python stdlib random.Random / MT19937 + random.sample",
        algorithm_version="ascii-dedup-sample-v1",
        selection_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        normalizer_code_sha256=hashlib.sha256(
            Path(discovery.__file__).read_bytes()
        ).hexdigest(),
        code_version_status="caller_declared_unverified"
        if profile.code_version is not None
        else "not_supplied_unverified",
        receipt_version="candidate-selection-receipt-v1",
    )
    return CandidateSelection(receipt)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate receipt JSON key")
        result[key] = value
    return result


def replay_selection(
    receipt_json: str,
    candidates: Sequence[Candidate],
    *,
    profile: RunProfile,
    execution_mode: str,
) -> CandidateSelection:
    """Recompute the entire closure under the current code/Python, reject mismatch.

    Caller must supply the expected profile and original semantic inputs, not
    trust a self-described receipt. This is integrity comparison, not signature
    authentication or semantic source attestation.
    """
    _text(receipt_json)
    supplied = json.loads(receipt_json, object_pairs_hook=_unique_object)
    result = normalize_and_select(
        candidates, profile=profile, execution_mode=execution_mode
    )
    if _json(supplied) != result.receipt.to_json():
        raise ValueError("selection receipt replay binding mismatch")
    return result
