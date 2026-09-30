"""Re-extract local approved PDFs and persist an explicitly approved text scope."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from pypdf import PdfReader

from skala_rag.contracts import Source
from skala_rag.rag.corpus import CorpusManifest, ManifestStore, check_corpus
from skala_rag.rag.extraction import PageChunkSettings, extract_local_document
from skala_rag.rag.reviewed_extraction import verify_text_review
from skala_rag.rag.text_review import TextIndexReview, text_hash


def review_local_corpus(
    *,
    root,
    manifest,
    sources,
    settings,
    corpus_version,
    approved_by,
    approved_on,
    approval_record,
    output_dir,
):
    """No OCR, inference, embedding or source status promotion. Approval is explicit."""
    root = Path(root).resolve()
    target = (root / output_dir).resolve()
    if not target.is_relative_to(root / "outputs"):
        raise ValueError("extracted text must remain inside outputs")
    if corpus_version == manifest.corpus_version:
        raise ValueError("text scope needs a new corpus version")
    corpus = CorpusManifest(
        schema_version=manifest.schema_version,
        corpus_version=corpus_version,
        previous_corpus_version=manifest.corpus_version,
        documents=manifest.documents,
    )
    documents, results = [], {}
    for doc in corpus.documents:
        source = sources[doc.source_id]
        arguments = dict(
            root=root,
            schema_version=corpus.schema_version,
            settings=settings,
            sections_by_page={},
            embedding_model="not-embedded",
            embedding_revision="not-embedded",
        )
        result = extract_local_document(corpus, doc.document_id, source, **arguments)
        again = extract_local_document(corpus, doc.document_id, source, **arguments)
        if result != again:
            raise ValueError("extraction is not reproducible")
        if result.status != "partial":
            raise ValueError("text review expects partial documents")
        for issue in result.issues:
            if issue.code == "TEXT_LAYOUT_WARNING":
                if issue.page != 1:
                    raise ValueError("layout warning outside inspected first page")
                rotated = []

                def visitor(text, cm, tm, font, size):
                    if any(
                        abs(matrix[i]) > 0.001 for matrix in (cm, tm) for i in (1, 2)
                    ):
                        if text.strip():
                            rotated.append(text.strip())

                PdfReader(root / doc.local_path).pages[0].extract_text(
                    visitor_text=visitor
                )
                if not rotated or any(
                    not text.startswith("arXiv:" + doc.document_id + " ")
                    for text in rotated
                ):
                    raise ValueError(
                        "rotated text is not the inspected arXiv identifier"
                    )
        visual_note = "Visual content excluded; no values inferred."
        layout_note = "Rotated arXiv identifier checked; layout warning retained."
        columns = (
            "Column reading order and mathematical glyphs are not fully certified."
        )
        explanation = {
            "VISUAL_CONTENT_NOT_EXTRACTED": visual_note,
            "TEXT_LAYOUT_WARNING": layout_note,
        }
        review = TextIndexReview.model_validate(
            dict(
                schema_version=corpus.schema_version,
                indexing_scope="text_only",
                source_content_hash=result.content_hash,
                approved_by=approved_by,
                approved_on=approved_on,
                approval_record=approval_record,
                page_count=result.page_count,
                page_text_hashes={
                    str(c.page_start): text_hash(c.text) for c in result.chunks
                },
                extraction_settings=asdict(settings),
                omissions=[
                    dict(
                        schema_version=corpus.schema_version,
                        code=i.code,
                        page=i.page,
                        explanation=explanation[i.code],
                    )
                    for i in result.issues
                ],
                limitations=[
                    "Images/graphs were not extracted; their values are not Evidence.",
                    columns,
                ],
            )
        )
        verify_text_review(result, review)
        documents.append(doc.model_copy(update={"text_index_review": review}))
        results[doc.document_id] = result
    reviewed = corpus.model_copy(update={"documents": tuple(documents)})
    reviewed = CorpusManifest.model_validate_json(reviewed.model_dump_json())
    if not check_corpus(reviewed).passed:
        raise ValueError("text corpus gate rejected")
    target.mkdir(parents=True, exist_ok=False)
    for document_id, result in results.items():
        payload = dict(
            status=result.status,
            indexing_scope="text_only",
            content_hash=result.content_hash,
            page_count=result.page_count,
            settings=asdict(result.settings),
            issues=[asdict(i) for i in result.issues],
            chunks=[c.model_dump(mode="json") for c in result.chunks],
        )
        (target / f"{document_id}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False)
        )
    # All documents have been verified before the immutable metadata is persisted.
    ManifestStore(root / "data/manifests").save(reviewed)
    return reviewed, results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "manifest", "sources", "settings", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("corpus-version", "approved-by", "approved-on", "approval-record"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    manifest = CorpusManifest.model_validate_json(args.manifest.read_text())
    sources = {
        key: Source.model_validate(value)
        for key, value in json.loads(args.sources.read_text()).items()
    }
    settings = PageChunkSettings(**json.loads(args.settings.read_text()))
    reviewed, results = review_local_corpus(
        root=args.root,
        manifest=manifest,
        sources=sources,
        settings=settings,
        corpus_version=args.corpus_version,
        approved_by=args.approved_by,
        approved_on=args.approved_on,
        approval_record=args.approval_record,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            dict(
                corpus_version=reviewed.corpus_version,
                text_scope_gate_passed=check_corpus(reviewed).passed,
                chunks=sum(len(r.chunks) for r in results.values()),
                full_document_statuses=[r.status for r in results.values()],
            )
        )
    )


if __name__ == "__main__":
    main()
