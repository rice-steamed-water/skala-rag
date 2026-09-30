# #49 페이지·구조 보존 추출 검증

검증일: 2026-09-30. 사용자 「전부 다 허용하고 진행해줘」를 받아 기존 후보
두 PDF의 로컬 추출 검증을 승인 기록에 반영했다. 검토자 GitHub 계정은
wjd990819-ops이며 회사 귀속은 논문 표지·저자 표시의 Physical Intelligence를
확인해 co-physical-intelligence로 명시했다. 적격성/투자/법인 등기 검증은 아니다.

| 원문 / 고정 버전 | bytes | 페이지 / Chunk | 상태 |
| --- | --- | --- | --- |
| [π0 v4](https://arxiv.org/abs/2410.24164v4) | 7,893,845 | 17 / 17 | partial |
| [π0.5 v1](https://arxiv.org/abs/2504.16054v1) | 16,213,397 | 19 / 19 | partial |

실제 SHA-256·Source·최초 확보 시각은 data/manifests/issue49-source-snapshots.json,
승인/문서 경로/추출 상태는 issue49-real-validation-v1.json에 기록했다.
두 논문의 공개 license 링크는 arXiv 비독점 배포 license다. 사용자 승인 범위는
로컬 수업용 추출 검증이며 별도 재배포 권한을 주장하지 않는다. PDF/PNG/추출 텍스트는
커밋하지 않고 data/local/issue49와 outputs/issue49-validation에 보존한다.

## 실행과 확인

- 승인 manifest·Source와 실제 PDF bytes hash를 대조한 extract_local_document 실행.
- 36개 Chunk의 page_start/end와 Source URL #page=N을 원래 PDF 페이지와 전부 대조.
- 같은 입력을 다시 실행해 모든 Chunk ID·텍스트·locator 일치 확인.
- pypdf layout 추출 텍스트와 36개 Chunk 텍스트 전부 일치 확인.
- 두 PDF의 모든 Chunk가 #50 rag_segment의 Source/검색 기록/locator/page 검증을 통과.
  검색 기록은 검증용 로컬 기록이며 실제 embedding 검색을 실행한 기록이 아니다.
- π0 표지·16페이지, π0.5 18페이지를 Poppler PNG로 렌더링해 눈으로 대조.
  π0 16페이지의 실제 추론 시간 표에서 제목·열·ms 단위·행별 수치·합계·caption을
  같은 페이지 Chunk에 보존한 것을 확인했다. 모델 성능을 독립 측정한 결과가 아니다.
- PDF bookmark 계층과 page 상속, 검토자 section override, 텍스트 Form과 실제 이미지
  구분은 가상 PDF/구조 테스트로 검사했다. 실제 π0/π0.5의 전체 heading을 자동으로
  정확히 탐지했다고 주장하지 않는다. 일부 section은 검토한 page metadata로 제공했다.

설정: max_characters=12000, overlap=0, tokenizer=none-page-atomic,
document_kind=technical_whitepaper, version=issue49-real-v2, section_mode=pdf-outline.
이 설정은 이번 명시적 검증 입력이며 범용 corpus/모델 token 기본 정책이 아니다.
embedding_model/revision은 not-embedded다. tokenizer·embedding·index는 실행하지 않았다.

## 누락과 후속 인계

π0는 이미지 누락 9페이지, π0.5는 11페이지다. 두 문서 모두 1페이지에서 회전된
텍스트의 layout 경고가 있어 TEXT_LAYOUT_WARNING을 기록했다. pypdf가 읽지 못한
이미지/그래프의 수치를 생성하지 않았다. Poppler도 Type3 glyph/bounding-box 경고를
출력했으며 대표 페이지의 렌더링은 확인했다. 전체 페이지의 수학 기호·그래프를 완벽히
시각 검증한 결과는 아니다. 2단 column의 의미상 읽기 순서는 후속 추출기 검토 대상이며
layout 문자열을 의미상 단일 문단이라고 표시하지 않는다.

따라서 두 문서는 partial이며 check_corpus().passed=False다. #49의 실제 PDF 추출·
페이지/표/locator 검증은 완료했지만 OCR·멀티모달은 이슈 범위 밖이다. #52/#54의
인덱싱에서는 이 manifest를 자동 ok로 승격하지 말고 partial 해결/사용 범위를 검토해야
한다. 원문/Chunk 재배포와 Skild HTML 추출은 승인·검증 완료로 표시하지 않는다.

검증 테스트는 tests/unit/test_page_extraction.py와
 tests/integration/test_real_pdf_extraction.py다. 실제 PDF 테스트는 로컬 원문이 있을
때만 실행하며 없으면 원문 git 제외 사유로 skip한다. 기본 자동 테스트는 네트워크가 없다.

## #52 후속 — 승인된 텍스트 범위

2026-09-30 xxhigh의 별도 승인으로 두 논문의 검증된 본문·caption·텍스트 표를
text-only scope에 한해 사용할 수 있다. 기존 전체 문서의 partial·기존 manifest 거절은
그대로다. 새로운 corpus_version과 페이지별 text hash·검토된 누락 항목을 묶은
text_index_review가 별도 gate를 통과한다. [승인·실제 검증·한계](issue52-text-scope.md)를
따른다. 이미지/OCR·실제 embedding/index/search 완료를 뜻하지 않는다.
