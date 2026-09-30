# 승인 PDF 페이지 보존 추출 — #49

`rag.extraction.extract_pdf`는 원문 bytes, ManifestDocument, Source를 받아
ExtractionResult를 반환한다. corpus_version·schema_version·PageChunkSettings·
sections_by_page·embedding_model/revision·execution_mode를 명시적으로 전달한다.
section_mode=pdf-outline이면 PDF bookmark 계층과 페이지 간 상속을 보존하며,
검토자가 제공한 page 제목이 우선한다. provided는 기존 호출 호환 모드다.
문서 approved/reviewer·Source ID/언어/저장 경로와 양쪽 실제 bytes hash를 확인한다.
fixture:// 문서는 live 모드에서 거절한다. extraction_status=pending은 추출 입력으로
가능하며, 완료 후 검토한 결과를 새 manifest에 기록하고 #44 gate를 통과해야 한다.
기존 manifest나 승인 상태를 자동 수정하지 않는다.

첫 경로는 텍스트를 포함한 기술 백서·제품 문서 PDF다. pypdf layout 추출을 사용하며
페이지 전체를 atomic Chunk로 유지해 표 제목·단위·열/행·주석을 고정 길이 경계로
분리하지 않는다. 표 의미나 읽기 순서의 완전성을 보장하는 parser는 아니다.
검토자가 확인한 heading/section을 page → title mapping으로 주입하고,
자동으로 heading을 추측하지 않는다. IR/PPTX·HTML·OCR·멀티모달은 미지원이다.

PageChunkSettings에는 max_characters, overlap=0, tokenizer=none-page-atomic,
document_kind, 설정 version을 명시한다. max_characters는 soft 경계다. 초과 페이지를
자르거나 수치를 보간하지 않고 PAGE_EXCEEDS_CHARACTER_LIMIT + partial로 표시한다.
Image XObject와 Form 내부 이미지는 VISUAL_CONTENT_NOT_EXTRACTED로 partial이다.
텍스트만 담긴 Form은 이미지 누락으로 처리하지 않는다. 회전 텍스트는 보존하지만
pypdf layout 경고가 있으면 TEXT_LAYOUT_WARNING으로 partial을 기록한다.
스캔 PDF·빈 페이지는 NO_EXTRACTABLE_TEXT, parser 오류는 TEXT_EXTRACTION_FAILED,
읽을 수 없거나 암호화된 PDF는 PDF_UNREADABLE로 기록한다. 정상 Chunk가 없으면 failed다.
부분 실패의 읽힌 페이지도 원래 페이지 번호를 유지한다. 정상 추출이나 OCR 성공으로
표시하지 않으며 partial/failed 결과는 #44의 extraction_status=ok gate를 통과하지 못한다.

Chunk는 source_id·corpus_version·원래 page_start/end·section·locator·candidate_ids·
scope·language를 보존한다. locator는 Source 위치의 #page=N이다. ID에는 원문 hash,
Source/corpus, 페이지, 텍스트·section, chunk 설정과 embedding metadata를 포함한다.
동일 입력·설정에서는 같은 ID를 반환한다. embedding은 실행하지 않는다. 호출자는
미구축 모델을 not-embedded 같은 명시적 표시로 기록하고 실제 embedding 단계에서
정식 모델/revision metadata를 관리해야 한다.

자동 테스트는 직접 만든 가상 PDF를 메모리에서만 생성한다. 재배포 불가 PDF나
실제 원문을 커밋하지 않는다. 실제 문서 검증은 [#49 검증 기록](../../../docs/implementation/extraction-validation.md)에
기록했다. 사용자 승인에 따라 π0 v4(17페이지)·π0.5 v1(19페이지)를 로컬에서 추출하고
페이지·실제 표·locator·결정적 ID·#50 rag_segment를 대조했다. 두 문서는 시각 자료를
읽지 않았으므로 partial이며 인덱싱 gate는 계속 거절한다. partial을 ok로 바꾸지 않는다.

`extract_local_document`는 승인 CorpusManifest의 data/local 원문만 읽는다.
외부로 연결된 symlink와 미승인 문서를 거절하고 bytes hash를 다시 확인한다.
`python -m skala_rag.rag.extraction_runner`로 다음 필수 인자를 명시해 실행한다:

```text
--root <project-root> --manifest <manifest.json> --document-id <id>
--source <source.json> --settings <settings.json> --sections <page-sections.json>
--output <project-root>/outputs/<run>/extraction.json
--embedding-model not-embedded --embedding-revision not-embedded
```

실행기는 settings/누락/page·Chunk payload를 outputs에만 저장하며 기존 파일을
덮어쓰지 않는다. 종료 코드는 ok=0, partial=2, failed=3이다. 실제 원문·추출 내용은
커밋하지 않는다. clean clone에서는 승인 원문이 없는 실제 PDF 테스트 2개를 건너뛰며
그 사유가 명시된다. 공개 metadata와 가상 PDF 테스트는 언제나 검사할 수 있다.
