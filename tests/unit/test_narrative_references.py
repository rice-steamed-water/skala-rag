"""Controlled originals, real projection and citation finalization."""

from dataclasses import replace
from datetime import date

import pytest
from pydantic import ValidationError
from tests.fixtures.narrative import narrative_questions
from tests.unit.test_company_store import (
    SyntheticEncoder,
    identity,
    material,
    settings,
)

from skala_rag.contracts.company_report import CompositionReply
from skala_rag.rag.company_store import CompanyStore, StoreSnapshot
from skala_rag.reporting.company_context import (
    CompanyContextError,
    build_writer_context,
)
from skala_rag.reporting.format import NarrativeCitationError, finalize_narrative
from skala_rag.reporting.validator import TOKEN


@pytest.fixture
def citation_context(tmp_path):
    store = CompanyStore(tmp_path, settings=settings(), encoder=SyntheticEncoder())
    stored = store.ingest_sources(
        (material(), material("support", text="Controlled supporting observation.")),
        companies=(identity(),),
    )
    return project(stored)


def project(stored):
    return build_writer_context(
        stored,
        company_name="Synthetic a Robotics",
        as_of=date(2026, 10, 10),
        questions=narrative_questions(),
        evidence_ids=list(stored.manifest.evidence),
    )


def compose(context, *, ids=("ev-a",), body=None):
    return CompositionReply.model_validate(
        {
            "schema_version": "narrative-report-1",
            "context_id": context.context_id,
            "markdown_without_reference": body
            or "## SUMMARY\nObservation. "
            + " ".join(f"[@evidence:{eid}]" for eid in ids),
            "cited_evidence_ids": list(ids),
            "strengths": [],
            "weaknesses": [],
            "verification_tasks": [],
        },
        context={"writer_context": context.writer_context},
    )


def test_exact_citations_exclude_unused_originals(citation_context):
    # Given an admitted but uncited support source.
    context = citation_context
    reply = compose(context)
    original_registry = context.citation_payload
    # When production finalization resolves references.
    result = finalize_narrative(reply, context)
    # Then body, metadata and precise original reference sets agree.
    assert set(TOKEN.findall(result.markdown)) == set(result.cited_evidence_ids)
    assert result.reference_source_ids == ("src-a",)
    assert {ref.evidence_id for ref in result.references} == {"ev-a"}
    assert result.references[0].locator == "https://fixture.invalid/a#claim=1"
    assert result.references[0].url == "https://fixture.invalid/a"
    assert result.references[0].page_start is None
    assert result.references[0].page_end is None
    assert finalize_narrative(reply, context) == result
    assert context.citation_payload == original_registry


@pytest.mark.parametrize(
    ("damage", "code"),
    [
        ({"cited_evidence_ids": ["forged"]}, "INVALID_CITATION"),
        ({"context_id": "stale"}, "STALE_COMPOSITION"),
        ({"cited_evidence_ids": ["ev-a", "ev-support"]}, "BODY_CITATION"),
        ({"markdown_without_reference": "Uncited."}, "BODY_CITATION"),
        ({"markdown_without_reference": "[@evidence:ev-a"}, "MALFORMED"),
        ({"markdown_without_reference": "[@source:forged]"}, "MODEL_REFERENCE"),
    ],
)
def test_forged_stale_unused_and_malformed_rejected(citation_context, damage, code):
    # Given a mutated previously validated model reply.
    reply = compose(citation_context).model_copy(update=damage)
    # When it crosses finalization, contextual parsing still applies.
    with pytest.raises((ValidationError, NarrativeCitationError), match=code):
        finalize_narrative(reply, citation_context)


def test_stale_private_registry_rejected(citation_context):
    # Given registry bytes changed after admission.
    context = replace(citation_context, citation_payload="{}")
    # When finalizing, binding is checked before registry resolution.
    with pytest.raises(CompanyContextError, match="BINDING_MISMATCH"):
        finalize_narrative(compose(citation_context), context)


@pytest.mark.parametrize("pages", [(None, None), (0, 0), (2, 4), (None, 4)])
def test_locator_pages_preserved_or_explicitly_unknown(tmp_path, pages):
    # Given original page coordinates, including measured zero.
    item = material()
    item.chunks[0].page_start, item.chunks[0].page_end = pages
    item.source.source_kind = "paper"
    store = CompanyStore(tmp_path, settings=settings(), encoder=SyntheticEncoder())
    context = project(store.ingest_sources((item,), companies=(identity(),)))
    # When production finalization uses the private registry.
    result = finalize_narrative(compose(context), context)
    # Then page coordinates and the original paper URL survive.
    ref = result.references[0]
    assert (ref.page_start, ref.page_end) == pages
    assert ref.url == item.source.url
    assert item.source.url in result.markdown


def test_derived_support_requires_explicit_body_citations(tmp_path):
    # Given an original-derived observation and original support.
    item = material("support", text="Controlled supporting observation.")
    item.evidence[0].evidence_kind = "derived"
    item.evidence[0].derivation = "Controlled derivation."
    item.evidence[0].supporting_evidence_ids = ["ev-a"]
    store = CompanyStore(tmp_path, settings=settings(), encoder=SyntheticEncoder())
    context = project(store.ingest_sources((material(), item), companies=(identity(),)))
    # When only the derived observation is cited.
    with pytest.raises(NarrativeCitationError, match="UNCITED_SUPPORT"):
        finalize_narrative(compose(context, ids=("ev-support",)), context)


def test_support_cycle_rejected_before_finalization(citation_context):
    # Given an attempted cyclic support projection.
    import json

    data = json.loads(citation_context.writer_payload)
    data["evidence"]["ev-a"]["supporting_evidence_ids"] = ["ev-support"]
    data["evidence"]["ev-support"]["supporting_evidence_ids"] = ["ev-a"]
    context = replace(citation_context, writer_payload=json.dumps(data))
    # When finalizing, the production context rejects the cycle.
    with pytest.raises(ValidationError, match="CIRCULAR"):
        finalize_narrative(compose(citation_context), context)


def test_generated_prose_cannot_enter_registry(tmp_path):
    # Given an original marked as a generated report.
    store = CompanyStore(tmp_path, settings=settings(), encoder=SyntheticEncoder())
    stored = store.ingest_sources((material(),), companies=(identity(),))
    manifest = stored.manifest
    manifest.sources["src-a"].bibliographic_metadata["generated_report"] = True
    stored = StoreSnapshot(stored.root, manifest.model_dump_json().encode())
    # When task 3 prepares the only registry finalization accepts.
    with pytest.raises(CompanyContextError, match="REPORT_CANNOT_CREATE_EVIDENCE"):
        project(stored)
