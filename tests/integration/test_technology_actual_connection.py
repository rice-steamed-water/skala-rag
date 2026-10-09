"""Mock wire, SYNTHETIC semantic review and test-only budgets; no provider calls."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from tests.unit.test_actual_admission_v3 import configured as configured
from tests.unit.test_actual_admission_v3 import offline as offline
from tests.unit.test_openai_attempt import body
from tests.unit.test_source_bound_review import setup_review
from tests.unit.test_source_fact_verification import SYSTEM

from skala_rag.agents.evaluation import CriterionOutput, DimensionAssessmentOutput
from skala_rag.agents.moat_verification import (
    _digest,
    core_artifact_digest,
    frozen_snapshot_digest,
)
from skala_rag.agents.source_fact_verification import (
    SourceBoundReview,
    SourceBoundReviewResolver,
    TrustedSource,
    review_receipt_digest,
)
from skala_rag.agents.technology import (
    TECHNOLOGY_CRITERIA,
    TechnologyEvaluation,
    evaluate_technology_approved,
)
from skala_rag.agents.technology_verification import ReviewedTechnologyAnchor
from skala_rag.contracts import EvaluationSnapshot
from skala_rag.scoring.approved_consumers import ActualAdmissionV3
from skala_rag.tools.openai_attempt import OpenAIResponsesAttempt
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM

REQUEST = b'{"criterion":"technology.integration","subject":"Synthetic System S"}'


@dataclass(frozen=True, slots=True)
class Connection:
    admission: ActualAdmissionV3
    snapshot: EvaluationSnapshot
    llm: RuntimeStructuredLLM
    receipt: ReviewedTechnologyAnchor
    review: SourceBoundReview
    sources: Mapping[str, TrustedSource]
    output: DimensionAssessmentOutput
    wire: list[httpx.Request]

    def run(self) -> TechnologyEvaluation:
        return evaluate_technology_approved(
            self.snapshot,
            llm=self.llm,
            actual_admission=self.admission,
            review_request=REQUEST,
            review_subject=SYSTEM,
            receipts={"technology.integration": self.receipt},
        )

    def reviews(self, records: tuple[SourceBoundReview, ...]) -> "Connection":
        rubric = self.admission.registry.rubric("core-0.1.0")
        resolver = SourceBoundReviewResolver(
            self.snapshot, rubric, sources=self.sources, reviews=records
        )
        return replace(
            self,
            admission=replace(
                self.admission,
                review_resolvers={(self.snapshot.snapshot_id, "core-0.1.0"): resolver},
            ),
        )


@pytest.fixture
def connection(request: pytest.FixtureRequest, tmp_path: Path) -> Connection:
    base, _, _, _, _, _ = request.getfixturevalue("configured")
    snapshot, _, sources, receipt, review = setup_review(tmp_path)
    chunk = next(iter(sources.values())).approved_chunks[0]
    evidence = snapshot.evidence["ev-synthetic-a-3"].model_copy(
        update={"locator": chunk.locator}
    )
    record = next(iter(snapshot.retrieval_records.values()))
    record.source_ids = list(sources)
    record.chunk_ids = [chunk.chunk_id]
    record.evidence_ids = [evidence.evidence_id]
    # Controller builds a synthetic snapshot before admission freezes its content.
    snapshot.sources = {sid: trusted.source for sid, trusted in sources.items()}
    snapshot.chunks = {chunk.chunk_id: chunk}
    snapshot.evidence_ids = [evidence.evidence_id]
    snapshot.evidence = {evidence.evidence_id: evidence}
    rubric = base.registry.rubric("core-0.1.0")
    receipt = replace(
        receipt,
        artifact_sha256=core_artifact_digest(rubric),
        snapshot_sha256=frozen_snapshot_digest(snapshot),
    )
    review = review.model_copy(
        update={
            "snapshot_sha256": frozen_snapshot_digest(snapshot),
            "rubric_sha256": _digest(rubric),
            "receipt_sha256": review_receipt_digest(receipt),
        }
    )
    admission = replace(
        base,
        review_resolvers={
            (snapshot.snapshot_id, "core-0.1.0"): SourceBoundReviewResolver(
                snapshot, rubric, sources=sources, reviews=(review,)
            )
        },
    )
    output = DimensionAssessmentOutput(
        criteria=[
            CriterionOutput(
                criterion_id=cid,
                status="observed" if cid == receipt.criterion_id else "missing",
                rating=3 if cid == receipt.criterion_id else None,
                evidence_ids=(
                    [evidence.evidence_id] if cid == receipt.criterion_id else []
                ),
                rationale="Synthetic output",
                missing_reason=None if cid == receipt.criterion_id else "not_disclosed",
            )
            for cid in sorted(TECHNOLOGY_CRITERIA)
        ]
    )
    wire: list[httpx.Request] = []

    def respond(sent: httpx.Request) -> httpx.Response:
        wire.append(sent)
        return httpx.Response(
            200,
            json=body(
                output.model_dump_json(),
                usage={"input_tokens": 10, "output_tokens": 10},
            ),
        )

    binding = admission.runtime_binding
    llm = RuntimeStructuredLLM(
        runtime=binding.runtime,
        call=binding.call.model_copy(
            update={
                "candidate_id": snapshot.candidate_id,
                "node": "technology_evaluation",
            }
        ),
        budget=binding.budget,
        readiness=binding.readiness,
        transport=OpenAIResponsesAttempt(
            api_key="SYNTHETIC-NOT-A-CREDENTIAL",
            prompt_version="synthetic-technology-test",
            schema_version=snapshot.schema_version,
            clock=binding.runtime.clock,
            http_transport=httpx.MockTransport(respond),
        ),
        allowance_for=lambda _system, _user, _schema: binding.allowance,
    )
    return Connection(admission, snapshot, llm, receipt, review, sources, output, wire)


def test_reviewed_rating_returns_caller_trace_when_wire_is_accounted(
    connection: Connection,
) -> None:
    # Given: independently supplied review, not a hash-derived semantic decision.
    original = connection.snapshot.model_dump()
    # When
    result = connection.run()
    # Then: exact original lineage and physical usage survive the real bridge.
    assert result.result.status == "success"
    assert len(result.trace) == 1
    trace = result.trace[0]
    assert trace.evidence_id == connection.receipt.evidence_ids[0]
    assert trace.snapshot_id == connection.snapshot.snapshot_id
    assert trace.chunk_id in connection.snapshot.chunks
    assert trace.retrieval_id in connection.snapshot.retrieval_records
    assert connection.snapshot.model_dump() == original
    ledger = connection.admission.runtime_binding.runtime.ledger.snapshot()
    assert ledger["calls"] == len(connection.wire) == 1
    assert ledger["input_tokens_accounted"] == ledger["output_tokens_accounted"] == 10
    assert Decimal(ledger["cost_usd_accounted"]) == Decimal("0.10")
    assert ledger["unknown_cost_requests"] == 1
    assert not ledger["usage_invalid"]
    assert len(connection.llm.retrieval_records) == 1


@pytest.mark.parametrize("decision", ["absent", "rejected", "unresolved"])
def test_observed_fails_without_independent_acceptance(
    connection: Connection, decision: str
) -> None:
    # Given
    records = (
        ()
        if decision == "absent"
        else (connection.review.model_copy(update={"decision": decision}),)
    )
    connection = connection.reviews(records)
    # When
    result = connection.run()
    # Then
    assert result.result.status == "failure"
    assert result.result.evaluation is None and result.trace == ()
    assert len(connection.wire) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"rating": 4},
        {"artifact_sha256": "0" * 64},
        {"snapshot_sha256": "0" * 64},
        {"evidence_ids": ("other",)},
        {"minimum_evidence_reviewed": False},
    ],
)
def test_receipt_tamper_cannot_transfer_accepted_review(
    connection: Connection, changes: Mapping[str, str | int | bool | tuple[str, ...]]
) -> None:
    # Given
    connection = replace(connection, receipt=replace(connection.receipt, **changes))
    # When
    result = connection.run()
    # Then
    assert result.result.status == "failure" and result.trace == ()
    assert len(connection.wire) == 1


def test_missing_succeeds_without_positive_review(connection: Connection) -> None:
    # Given
    for criterion in connection.output.criteria:
        criterion.status = "missing"
        criterion.rating = None
        criterion.evidence_ids = []
        criterion.missing_reason = "not_disclosed"
    connection = connection.reviews(())
    # When
    result = connection.run()
    # Then
    assert result.result.status == "success" and result.trace == ()
    evaluation = result.result.evaluation
    assert evaluation is not None
    assert all(c.status == "missing" for c in evaluation.criteria)
    assert len(connection.wire) == 1


def test_accepted_review_denied_for_wrong_span(connection: Connection) -> None:
    # Given
    bad = replace(connection.review.spans[0], start=1)
    connection = connection.reviews(
        (connection.review.model_copy(update={"spans": (bad,)}),)
    )
    # When
    result = connection.run()
    # Then
    assert result.result.status == "failure" and result.trace == ()
    assert len(connection.wire) == 1


@pytest.mark.parametrize(
    "field",
    (
        "index_version candidate_id source_hash chunk_text record_chunk source_bytes"
    ).split(),
)
def test_lineage_tamper_denied_before_wire(connection: Connection, field: str) -> None:
    # Given
    snapshot = connection.snapshot
    match field:
        case "index_version" | "candidate_id":
            setattr(snapshot, field, "foreign")
        case "source_hash":
            next(iter(snapshot.sources.values())).content_hash = "sha256:" + "0" * 64
        case "chunk_text":
            next(iter(snapshot.chunks.values())).text += " forged"
        case "record_chunk":
            next(iter(snapshot.retrieval_records.values())).chunk_ids = []
        case "source_bytes":
            next(iter(connection.sources.values())).path.write_bytes(b"tampered")
    # When / Then
    with pytest.raises(ValueError):
        connection.run()
    assert connection.wire == []
    assert connection.admission.runtime_binding.runtime.ledger.snapshot()["calls"] == 0


def test_invalid_model_evidence_fails_without_repair(connection: Connection) -> None:
    # Given
    next(c for c in connection.output.criteria if c.rating).evidence_ids = ["foreign"]
    # When
    result = connection.run()
    # Then
    assert result.result.status == "failure" and result.trace == ()
    assert len(connection.wire) == 1
