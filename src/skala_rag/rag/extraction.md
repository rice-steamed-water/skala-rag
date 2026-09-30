# 승인 PDF 페이지 보존 추출 — #49

`rag.extraction.extract_pdf`는 원문 bytes, ManifestDocument, Source를 받아
ExtractionResult를 반환한다. corpus_version·schema_version·PageChunkSettings·
sections_by_page·embedding_model/revision·execution_mode를 명시적으로 전달한다.
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
이미지/Form XObject가 있는 페이지는 VISUAL_CONTENT_NOT_EXTRACTED로 partial이다.
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
실제 원문을 커밋하지 않는다. 현재 `corpus-candidates.draft.json`의 실제 후보는 모두
approved=false / runtime_ready=false이고 로컬 승인 원문도 없어 실제 기술 문서의
layout/표 추출·locator 검증은 미실행이다. 승인자·권한·실제 candidate ID·Source/hash와
원문 bytes가 준비되면 실제 PDF 페이지를 렌더링해 추출 문맥을 대조해야 한다.
가상 PDF 테스트 결과를 실제 문서 품질 검증으로 표시하지 않는다.
