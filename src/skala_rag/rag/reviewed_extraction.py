"""Bind an approved text-only review to the exact observed extraction result."""

from dataclasses import asdict

from skala_rag.rag.extraction import ExtractionResult
from skala_rag.rag.text_review import TextIndexReview, text_hash


def verify_text_review(result: ExtractionResult, review: TextIndexReview) -> None:
    """Reject new/missing issues, altered text, settings or incomplete page coverage."""
    review = TextIndexReview.model_validate_json(review.model_dump_json())
    if (
        result.status != "partial"
        or result.content_hash != review.source_content_hash
        or result.page_count != review.page_count
        or asdict(result.settings) != review.extraction_settings
    ):
        raise ValueError("extraction differs from approved text scope")
    observed = {(item.code, item.page) for item in result.issues}
    approved = {(item.code, item.page) for item in review.omissions}
    if len(observed) != len(result.issues) or observed != approved:
        raise ValueError("extraction issues differ from approved omissions")
    pages = {}
    for chunk in result.chunks:
        if (
            chunk.page_start != chunk.page_end
            or chunk.page_start in pages
            or chunk.page_start is None
        ):
            raise ValueError("review requires one atomic text chunk per page")
        pages[str(chunk.page_start)] = text_hash(chunk.text)
    if pages != review.page_text_hashes:
        raise ValueError("extraction text differs from reviewed pages")
