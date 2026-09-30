"""Synthetic partial PDFs cannot bypass the text-scope approval bindings."""

from dataclasses import asdict, replace

import pytest
from tests.unit.test_index_v3 import chunk, corpus, document, settings, source

from skala_rag.rag.corpus import check_corpus, manifest_hash
from skala_rag.rag.extraction import (
    ExtractionIssue,
    ExtractionResult,
    PageChunkSettings,
)
from skala_rag.rag.index_v3 import build_index_plan
from skala_rag.rag.reviewed_extraction import verify_text_review
from skala_rag.rag.text_review import TextIndexReview, text_hash


def reviewed():
    doc = document(extraction_status="partial")
    c = chunk(doc)
    extraction_settings = PageChunkSettings(
        10000, 0, "none-page-atomic", "product_document", "synthetic-1"
    )
    extraction = ExtractionResult(
        "partial",
        (c,),
        1,
        (ExtractionIssue("VISUAL_CONTENT_NOT_EXTRACTED", 1),),
        extraction_settings,
        doc.document_id,
        doc.source_id,
        doc.content_hash,
        "synthetic-v1",
    )
    review = TextIndexReview(
        schema_version=doc.schema_version,
        indexing_scope="text_only",
        source_content_hash=doc.content_hash,
        approved_by="fixture-reviewer",
        approved_on="2026-09-30",
        approval_record="synthetic-approval",
        page_count=1,
        page_text_hashes={"1": text_hash(c.text)},
        extraction_settings=asdict(extraction_settings),
        omissions=[
            dict(
                schema_version=doc.schema_version,
                code="VISUAL_CONTENT_NOT_EXTRACTED",
                page=1,
                explanation="Synthetic image excluded",
            )
        ],
        limitations=["Synthetic only; visual information excluded"],
    )
    doc = doc.model_copy(update={"text_index_review": review})
    return doc, c, extraction


def plan(doc, c, extraction=None, **changes):
    options = dict(
        manifest=corpus(doc),
        expected_corpus_hash=manifest_hash(corpus(doc)),
        sources={doc.source_id: source(doc)},
        chunks=[c],
        settings=settings(chunk_settings=doc.text_index_review.extraction_settings),
        extraction_results={doc.document_id: extraction} if extraction else None,
    )
    options.update(changes)
    return build_index_plan(**options)


def test_partial_requires_explicit_review_and_original_status_is_preserved():
    assert not check_corpus(corpus(document(extraction_status="partial"))).passed
    doc, c, extraction = reviewed()
    assert check_corpus(corpus(doc)).passed
    verify_text_review(extraction, doc.text_index_review)
    index_plan = plan(doc, c, extraction)
    assert index_plan.metadata.chunk_ids == (c.chunk_id,)
    import json

    snapshot = json.loads(index_plan.source_snapshots[0])
    assert "text_only" in snapshot["access_notes"]
    assert (
        snapshot["bibliographic_metadata"]["text_index_review"]["indexing_scope"]
        == "text_only"
    )
    assert "text_index_review" not in source(doc).bibliographic_metadata
    assert doc.extraction_status == "partial"
    with pytest.raises(ValueError, match="actual extraction"):
        plan(doc, c)


@pytest.mark.parametrize(
    "change", ["text", "page", "settings", "new_issue", "omitted_issue", "source"]
)
def test_changed_extraction_or_chunks_are_rejected(change):
    doc, c, extraction = reviewed()
    if change == "text":
        c = c.model_copy(update={"text": "Altered text"})
    elif change == "page":
        c = c.model_copy(update={"page_end": 2})
    elif change == "settings":
        extraction = replace(
            extraction, settings=replace(extraction.settings, max_characters=1)
        )
    elif change == "new_issue":
        extraction = replace(
            extraction,
            issues=extraction.issues + (ExtractionIssue("TEXT_EXTRACTION_FAILED", 1),),
        )
    elif change == "omitted_issue":
        extraction = replace(extraction, issues=())
    else:
        extraction = replace(extraction, source_id="wrong")
    with pytest.raises(ValueError):
        plan(doc, c, extraction)


def test_review_cannot_accept_changed_source_hash_missing_pages_or_unknown_issues():
    doc, c, extraction = reviewed()
    review = doc.text_index_review.model_dump(mode="json")
    with pytest.raises(ValueError):
        document(
            extraction_status="partial",
            text_index_review={**review, "source_content_hash": "changed"},
        )
    with pytest.raises(ValueError):
        TextIndexReview.model_validate({**review, "page_count": 2})
    review["omissions"][0]["code"] = "PAGE_EXCEEDS_CHARACTER_LIMIT"
    with pytest.raises(ValueError):
        TextIndexReview.model_validate(review)


def test_unreviewed_manifest_serialization_preserves_previous_hash_input():
    doc = document()
    payload = doc.model_dump(mode="json")
    assert "text_index_review" not in payload
    old = corpus(doc)
    assert manifest_hash(old) == manifest_hash(
        type(old).model_validate(old.model_dump(mode="json"))
    )
