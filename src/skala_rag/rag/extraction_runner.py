"""승인된 로컬 PDF 추출 실행기. 원문·Chunk는 outputs 밖에 저장하지 않는다."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from skala_rag.contracts import Source
from skala_rag.rag.corpus import CorpusManifest
from skala_rag.rag.extraction import PageChunkSettings, extract_local_document


def run_extraction(
    *,
    root: Path,
    manifest_path: Path,
    document_id: str,
    source_path: Path,
    settings_path: Path,
    sections_path: Path,
    output_path: Path,
    embedding_model: str,
    embedding_revision: str,
):
    """실제 승인 파일을 읽고 결과·한계를 저장한다. 정책·승인 기록은 수정하지 않는다."""
    target = output_path.resolve()
    if not target.is_relative_to(
        (root / "outputs").resolve()
    ) or not target.is_relative_to(root.resolve()):
        raise ValueError(
            "실제 추출 결과는 프로젝트 outputs 아래에만 저장할 수 있습니다"
        )
    manifest = CorpusManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    source = Source.model_validate_json(source_path.read_text(encoding="utf-8"))
    settings = PageChunkSettings(
        **json.loads(settings_path.read_text(encoding="utf-8"))
    )
    raw_sections = json.loads(sections_path.read_text(encoding="utf-8"))
    sections = {int(page): title for page, title in raw_sections.items()}
    result = extract_local_document(
        manifest,
        document_id,
        source,
        root=root,
        schema_version=manifest.schema_version,
        settings=settings,
        sections_by_page=sections,
        embedding_model=embedding_model,
        embedding_revision=embedding_revision,
    )
    payload = dict(
        schema_version=manifest.schema_version,
        execution_mode="live",
        document_id=result.document_id,
        source_id=result.source_id,
        content_hash=result.content_hash,
        corpus_version=result.corpus_version,
        status=result.status,
        page_count=result.page_count,
        settings=asdict(result.settings),
        issues=[asdict(issue) for issue in result.issues],
        chunks=[chunk.model_dump(mode="json") for chunk in result.chunks],
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    # 기존 결과를 덮어쓰지 않고 새 run 저장 경로를 요구한다.
    with target.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description="승인된 로컬 PDF의 페이지 보존 추출")
    for name in ["root", "manifest", "source", "settings", "sections", "output"]:
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ["document-id", "embedding-model", "embedding-revision"]:
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    result = run_extraction(
        root=args.root,
        manifest_path=args.manifest,
        document_id=args.document_id,
        source_path=args.source,
        settings_path=args.settings,
        sections_path=args.sections,
        output_path=args.output,
        embedding_model=args.embedding_model,
        embedding_revision=args.embedding_revision,
    )
    print(
        json.dumps(
            dict(
                status=result.status,
                page_count=result.page_count,
                chunk_count=len(result.chunks),
                issue_count=len(result.issues),
            )
        )
    )
    return 0 if result.status == "ok" else 2 if result.status == "partial" else 3


if __name__ == "__main__":
    raise SystemExit(main())
