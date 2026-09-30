"""corpus manifest 승인·버전 고정 gate — #44, data-rag §3, T11.

- 승인·추출 완료된 문서만 인덱스 입력이 된다. 미승인·추출 미완료 문서는 거절한다.
- manifest는 불변이다. 자료 교체·추가는 새 corpus_version을 만들고, 저장소는 같은
  version에 다른 내용을 덮어쓰지 않는다.
- live Web/API Evidence는 manifest에 넣지 않는다. 문서 원문·index는 ``data/local/``에,
  재배포 가능한 metadata만 ``data/manifests/``에 둔다.

페이지 수 한도는 이 프로젝트에서 적용하지 않는다. 실제 추출·embedding·검색은 이
모듈의 범위가 아니다.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from pydantic import ConfigDict, StrictBool, model_validator

from skala_rag.contracts._validation import validate_unique
from skala_rag.contracts.common import Contract, ISODate, Locator, Text

LOCAL_CORPUS_ROOT = PurePosixPath("data/local")


class ManifestDocument(Contract):
    model_config = ConfigDict(frozen=True)
    document_id: Text
    source_id: Text
    origin_url: Locator | None = None
    local_path: Locator
    content_hash: Text
    title: Text
    publisher: Text | None = None
    publication_date: ISODate | None = None
    language: Text
    permission_note: Text
    candidate_ids: tuple[Text, ...]
    scope: Literal["company", "industry"]
    extraction_status: Literal["pending", "ok", "partial", "failed"]
    reviewer: Text | None = None
    approved: StrictBool

    @model_validator(mode="after")
    def validate_document(self) -> Self:
        if self.scope == "company" and not self.candidate_ids:
            raise ValueError("company scope requires candidate_ids")
        if self.scope == "industry" and self.candidate_ids:
            raise ValueError("industry scope must not name candidate_ids")
        validate_unique(list(self.candidate_ids), "candidate_ids")
        if self.approved and self.reviewer is None:
            raise ValueError("approved document requires reviewer")
        validate_local_path(self.local_path)
        return self


def validate_local_path(value: str) -> None:
    """원문 snapshot은 승인된 ``data/local/`` 아래 상대 경로(또는 fixture://)만."""
    if value.lower().startswith("fixture://"):
        return
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("local_path must be a relative path without '..'")
    if path.parts[:2] != LOCAL_CORPUS_ROOT.parts or len(path.parts) <= 2:
        raise ValueError(f"local_path must be under {LOCAL_CORPUS_ROOT}/")


class CorpusManifest(Contract):
    """승인 코퍼스 한 버전. 생성 후 바꾸지 않는다."""

    model_config = ConfigDict(frozen=True)
    corpus_version: Text
    previous_corpus_version: Text | None = None
    documents: tuple[ManifestDocument, ...]

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        validate_unique([d.document_id for d in self.documents], "document_id")
        validate_unique([d.source_id for d in self.documents], "source_id")
        if self.previous_corpus_version == self.corpus_version:
            raise ValueError("previous_corpus_version must differ from corpus_version")
        return self

    def document(self, document_id: str) -> ManifestDocument:
        for item in self.documents:
            if item.document_id == document_id:
                return item
        raise KeyError(document_id)


def manifest_hash(manifest: CorpusManifest) -> str:
    """정규화 JSON의 sha256. RunManifest.corpus_hash로 기록한다."""
    payload = json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


class Rejection(StrEnum):
    DOCUMENT_NOT_APPROVED = "DOCUMENT_NOT_APPROVED"
    EXTRACTION_NOT_OK = "EXTRACTION_NOT_OK"


@dataclass(frozen=True)
class GateIssue:
    code: Rejection
    document_id: str
    detail: str


@dataclass(frozen=True)
class GateResult:
    corpus_version: str
    manifest_hash: str
    indexable_document_ids: tuple[str, ...]
    issues: tuple[GateIssue, ...]

    @property
    def passed(self) -> bool:
        return not self.issues


def check_corpus(manifest: CorpusManifest) -> GateResult:
    """인덱싱 전 gate. 거절된 문서가 하나라도 있으면 ``passed`` 가 False다.

    거절된 문서는 ``indexable_document_ids`` 에서 빠지고 사유는 ``issues`` 에 남는다.
    """
    issues: list[GateIssue] = []
    indexable: list[str] = []
    for item in manifest.documents:
        if not item.approved:
            issues.append(
                GateIssue(
                    Rejection.DOCUMENT_NOT_APPROVED,
                    item.document_id,
                    "document is not approved",
                )
            )
        elif item.extraction_status != "ok":
            issues.append(
                GateIssue(
                    Rejection.EXTRACTION_NOT_OK,
                    item.document_id,
                    item.extraction_status,
                )
            )
        else:
            indexable.append(item.document_id)
    return GateResult(
        corpus_version=manifest.corpus_version,
        manifest_hash=manifest_hash(manifest),
        indexable_document_ids=tuple(indexable),
        issues=tuple(issues),
    )


def require_indexable(result: GateResult) -> tuple[str, ...]:
    """gate를 통과한 경우에만 인덱스 입력 document_id를 돌려준다."""
    if not result.passed:
        codes = ", ".join(sorted({i.code for i in result.issues}))
        raise CorpusGateError(f"corpus {result.corpus_version} rejected: {codes}")
    return result.indexable_document_ids


class CorpusGateError(Exception):
    pass


def next_corpus_version(
    base: CorpusManifest,
    *,
    corpus_version: str,
    add: Iterable[ManifestDocument] = (),
    remove: Iterable[str] = (),
) -> CorpusManifest:
    """자료 교체·추가를 새 corpus_version으로만 표현한다. base는 바뀌지 않는다.

    교체는 이전 document_id를 ``remove`` 하고 새 문서를 ``add`` 한다.
    """
    removed = set(remove)
    unknown = removed - {d.document_id for d in base.documents}
    if unknown:
        raise ValueError(f"unknown document_id: {sorted(unknown)}")
    kept = [d for d in base.documents if d.document_id not in removed]
    return CorpusManifest(
        schema_version=base.schema_version,
        corpus_version=corpus_version,
        previous_corpus_version=base.corpus_version,
        documents=(*kept, *add),
    )


class ManifestStore:
    """``data/manifests/<corpus_version>.json`` 에 불변 manifest를 저장한다."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def path(self, corpus_version: str) -> Path:
        name = PurePosixPath(corpus_version)
        if len(name.parts) != 1 or name.name in {".", ".."}:
            raise ValueError("corpus_version must be a single path segment")
        return self._root / f"{corpus_version}.json"

    def save(self, manifest: CorpusManifest) -> str:
        """저장하고 manifest_hash를 반환한다. 같은 version의 다른 내용은 거절."""
        target = self.path(manifest.corpus_version)
        digest = manifest_hash(manifest)
        if target.exists():
            if manifest_hash(self.load(manifest.corpus_version)) != digest:
                raise FileExistsError(
                    f"corpus_version {manifest.corpus_version} already frozen "
                    "with different content"
                )
            return digest
        self._root.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            manifest.model_dump(mode="json"), ensure_ascii=False, indent=2
        )
        with target.open("x", encoding="utf-8") as handle:
            handle.write(text + "\n")
        return digest

    def load(
        self, corpus_version: str, *, expected_hash: str | None = None
    ) -> CorpusManifest:
        manifest = CorpusManifest.model_validate_json(
            self.path(corpus_version).read_text(encoding="utf-8")
        )
        if expected_hash is not None and manifest_hash(manifest) != expected_hash:
            raise ValueError(f"corpus {corpus_version} hash mismatch")
        return manifest


@dataclass(frozen=True)
class IndexInputDiff:
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]
    hash_mismatch: tuple[str, ...]

    @property
    def matches(self) -> bool:
        return not (self.missing or self.unexpected or self.hash_mismatch)


def compare_index_inputs(
    manifest: CorpusManifest,
    result: GateResult,
    indexed: Mapping[str, str],
) -> IndexInputDiff:
    """gate를 통과한 문서 집합과 실제 인덱스 입력(document_id → content_hash) 대조."""
    if result.manifest_hash != manifest_hash(manifest):
        raise ValueError("gate result belongs to a different manifest")
    expected = {
        doc_id: manifest.document(doc_id).content_hash
        for doc_id in result.indexable_document_ids
    }
    return IndexInputDiff(
        missing=tuple(sorted(expected.keys() - indexed.keys())),
        unexpected=tuple(sorted(indexed.keys() - expected.keys())),
        hash_mismatch=tuple(
            sorted(
                doc_id
                for doc_id in expected.keys() & indexed.keys()
                if expected[doc_id] != indexed[doc_id]
            )
        ),
    )


def allowed_source_ids(
    manifest: CorpusManifest, result: GateResult, candidate_id: str
) -> list[str]:
    """RetrievalRequest.allowed_source_ids — 통과 문서 중 해당 기업·산업 공통 출처."""
    if not result.passed:
        raise CorpusGateError(f"corpus {result.corpus_version} did not pass gate")
    if result.manifest_hash != manifest_hash(manifest):
        raise ValueError("gate result belongs to a different manifest")
    return sorted(
        item.source_id
        for item in map(manifest.document, result.indexable_document_ids)
        if item.scope == "industry" or candidate_id in item.candidate_ids
    )
