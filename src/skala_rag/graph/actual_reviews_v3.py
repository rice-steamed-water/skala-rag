"""Exact source/rubric/snapshot review admission shared by actual callers."""

from collections.abc import Mapping
from pathlib import Path
from threading import RLock

from pydantic import JsonValue, TypeAdapter

from skala_rag.agents.source_fact_verification import SourceBoundReviewResolver
from skala_rag.contracts import EvaluationSnapshot
from skala_rag.graph.actual_inputs_v3 import (
    ActualInputError,
    EvaluationInputsV3,
    canonical,
    digest,
    review_resolver,
)


def _write(out: Path, name: str, value) -> None:
    (out / name).write_bytes(canonical(value))


class _Reviews:
    """Resolve exact new snapshots once, with no seed approval transplantation."""

    def __init__(self, authority, registry, out, expected=None):
        self.authority, self.registry, self.out = authority, registry, out
        self.expected = expected
        self.audit = {}
        self._lock = RLock()
        self._resolvers: dict[tuple[str, str], SourceBoundReviewResolver] = {}
        self.inputs: dict[str, EvaluationInputsV3] = {}
        self.requests: dict[str, dict] = {}

    def __call__(
        self, snapshot: EvaluationSnapshot, rubric: Mapping[str, JsonValue]
    ) -> SourceBoundReviewResolver:
        key = digest(canonical(snapshot.model_dump(mode="json")))
        version = str(rubric["rubric_version"])
        with self._lock:
            if key not in self.inputs:
                try:
                    supplied = self.authority.evaluation_inputs_for(
                        snapshot.model_copy(deep=True)
                    )
                    if type(supplied) is not EvaluationInputsV3:
                        raise ActualInputError("EVALUATION_INPUTS_MISSING")
                    selections = {
                        v: review_resolver(
                            snapshot,
                            self.registry.rubric(v),
                            self.authority,
                            self.registry,
                        )
                        for v in ("core-0.1.0", "finance-0.1.0")
                    }
                    resolvers = {v: selected[0] for v, selected in selections.items()}
                    for v, resolver in resolvers.items():
                        resolver.verify_snapshot(snapshot, self.registry.rubric(v))
                        if any(
                            resolver.verify_source(sid) is None
                            for sid in snapshot.sources
                        ):
                            raise ActualInputError("SOURCE_AUTHORITY_MISSING")
                    receipts = (
                        [
                            (
                                "core-0.1.0",
                                supplied.review_request,
                                supplied.review_subject,
                                receipt,
                            )
                            for receipt in (
                                *supplied.founder_anchors.values(),
                                *supplied.technology_anchors.values(),
                                *supplied.moat_anchors.values(),
                            )
                        ]
                        + [
                            ("core-0.1.0", r.request, r.subject, r.receipt)
                            for r in supplied.market_reviews.values()
                        ]
                        + [
                            (
                                "finance-0.1.0",
                                supplied.review_request,
                                supplied.review_subject,
                                receipt,
                            )
                            for receipt in supplied.financial_facts
                        ]
                    )
                    if not receipts:
                        raise ActualInputError("RATING_REVIEW_MISSING")
                    for v, request, subject, receipt in receipts:
                        resolved = resolvers[v].resolve_review(
                            request, receipt, subject=subject
                        )
                        if resolved is None or resolved.decision != "accepted":
                            raise ActualInputError("RATING_REVIEW_MISSING")
                    audit = {
                        "evaluation_inputs": TypeAdapter(
                            EvaluationInputsV3
                        ).dump_python(supplied, mode="json"),
                        "reviews": {
                            v: [r.model_dump(mode="json") for r in selected[1]]
                            for v, selected in selections.items()
                        },
                    }
                    if self.expected is not None and self.expected.get(key) != audit:
                        raise ActualInputError("REPLAY_REVIEW_MISMATCH")
                except (ValueError, TypeError, KeyError):
                    if len(self.requests) < 40:
                        self.requests[key] = {
                            "snapshot": snapshot.model_dump(mode="json"),
                            "rubrics": {
                                v: digest(canonical(self.registry.rubric(v)))
                                for v in ("core-0.1.0", "finance-0.1.0")
                            },
                            "reason": "independently_authenticated_review_required",
                        }
                        _write(self.out, "missing-review-requests.json", self.requests)
                    raise ActualInputError("SNAPSHOT_REVIEW_MISSING") from None
                self.inputs[key] = supplied
                self.audit[key] = audit
                _write(self.out, "reviews.json", self.audit)
                for v, resolver in resolvers.items():
                    self._resolvers[(key, v)] = resolver
            return self._resolvers[(key, version)]

    def for_snapshot(self, snapshot):
        self(snapshot, self.registry.rubric("core-0.1.0"))
        return self.inputs[digest(canonical(snapshot.model_dump(mode="json")))]
