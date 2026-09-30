# #52 — 두 논문의 승인된 텍스트 범위

2026-09-30 xxhigh가 [텍스트 전용 범위를 승인](https://github.com/rice-steamed-water/skala-rag/issues/52#issuecomment-5905563007)했다.
본문·caption·텍스트 표를 인덱싱할 수 있으며, 이미지/그래프 미추출 정보는 사용하지 않는다.
이 승인은 모델 다운로드·embedding 실행·vector store 선택 승인이 아니다.

## 실제 원문 검증

기존 고정 버전 PDF를 다시 확보해 #49의 Source content_hash와 일치함을 확인했다.
각 PDF를 동일 설정으로 두 번 추출했으며 전체 결과·페이지 텍스트·Chunk ID가 일치했다.

| 문서 | PDF 페이지 / 텍스트 Chunk | 이미지 누락 페이지 | layout 경고 |
| --- | --- | --- | --- |
| π0 2410.24164v4 | 17 / 17 | 1, 2, 4, 5, 6, 7, 8, 9, 11 | 1 |
| π0.5 2504.16054v1 | 19 / 19 | 1, 2, 4, 6, 7, 8, 9, 10, 11, 18, 19 | 1 |

두 문서의 회전된 텍스트가 첫 페이지 arXiv 식별 문구임을 pypdf text visitor로 확인했다.
원래 layout 텍스트를 변경하거나 이 경고를 삭제하지 않는다. 두 column의 의미상 순서와
모든 수학 기호를 완벽히 검증했다는 뜻이 아니다. 이미지에만 있는 수치·축·추세는
추출·검증되지 않았으므로 평가 근거로 보충하지 않는다. 기존 π0 16페이지 텍스트 표의
제목·ms 단위·행 수치·caption 보존 회귀 테스트도 통과했다.

## Gate와 버전

- 기존 `issue49-real-validation-v1`은 그대로이며 전체 문서의 `partial`과 gate 거절을 보존한다.
- 새 `data/manifests/issue52-reviewed-text-v1.json`도 두 문서의 `extraction_status=partial`을 유지한다.
  추가 `text_index_review`는 사용자 승인, 원문 hash, 모든 페이지 텍스트 hash,
  추출 설정, 확인된 누락 항목과 한계를 저장한다. 텍스트 범위 corpus gate만 통과한다.
- 검토가 없는 partial·pending·failed 문서는 이전처럼 거절한다. 이 예외가 원문 전체의
  추출 성공이나 다른 partial 문서 사용을 승인하지 않는다.
- `build_index_plan(..., extraction_results=...)`는 검토가 있는 문서마다 실제 추출 결과를 요구한다.
  원문/문서/Source/corpus, 설정, 누락 목록, 전체 페이지 텍스트 hash를 대조하며 추가 오류·
  바뀐 텍스트·빠진 페이지·페이지 중복·다른 chunk 설정을 거절한다. 모델/revision 확인은 기존 gate를 따른다.
- index Source snapshot의 access_notes와 bibliographic_metadata에 text-only scope와
  검토 한계를 함께 넣는다. 원래 Source 객체·원문 bytes는 바꾸지 않는다.
- review가 없는 기존 manifest는 새로운 null 필드를 직렬화하지 않아 기존 hash 입력을 보존한다.
  텍스트 review를 추가하는 경우에는 새 corpus_version과 hash/index_version을 사용한다.

PDF와 추출 텍스트는 `data/local/issue49`·`outputs/issue52-reviewed-text-v1`에만 보존한다.
커밋에는 검토 metadata와 페이지 hash만 넣고 원문·추출 텍스트를 재배포하지 않는다.
본문 텍스트를 embedding한 실행·index 재오픈·실제 검색은 아직 수행하지 않았다.

## 재현

설정은 #49와 같은 `max_characters=12000`, `overlap=0`,
`tokenizer=none-page-atomic`, `document_kind=technical_whitepaper`,
`version=issue49-real-v2`, `section_mode=pdf-outline`이다.

```bash
uv run python -m skala_rag.rag.text_review_runner \
  --root . \
  --manifest data/manifests/issue49-real-validation-v1.json \
  --sources data/manifests/issue49-source-snapshots.json \
  --settings outputs/issue52-document-review/settings.json \
  --output-dir outputs/issue52-reviewed-text-v1 \
  --corpus-version issue52-reviewed-text-v1 \
  --approved-by xxhigh --approved-on 2026-09-30 \
  --approval-record https://github.com/rice-steamed-water/skala-rag/issues/52#issuecomment-5905563007
```

출력 폴더가 있으면 덮어쓰지 않는다. 다른 corpus_version을 임의로 승인하지 않는다.
위 설정 파일은 명시적 검토 입력이고 모델 token/default 정책이 아니다.
실제 PDF가 없는 환경의 원문 테스트는 로컬 자료 git 제외 사유로 skip하며,
synthetic gate 테스트를 실제 문서 성공으로 표시하지 않는다.

## #52 범위 변경과 Hugging Face API

2026-09-30 xxhigh는 실제 embedding/index 성공 검증을 후속 작업으로 옮기고
#52의 blocked를 제거하도록 요청했다. 모델 사용 방식은 로컬 다운로드 대신
Hugging Face API이며, 주소는 이후 설정하고 지금은 인터페이스·테스트를 진행한다.
기존 모델/tokenizer 다운로드 보류는 유지한다. 실제 API 호출도 이번 검증에 포함하지 않는다.

`HFEmbeddingEncoder`는 기존 httpx 의존성을 사용한다. 호출자는 HTTPS endpoint,
배포 model/tokenizer revision과 배포 확인 기록, token, HTTP client, timeout을 공급한다.
명시적 설정은 `tokenizer_settings={"truncate": false}`,
`preprocessing_settings={"prefix": ""}`이며 embedding_settings에는
`mode=dense`, `normalization=l2`, `runtime=hf-http`, endpoint, deployment_record를 넣는다.
이 설정은 index_version 입력에 포함된다. query도 같은 `embed_texts` 경로를 사용한다.
응답 count·차원·유한값·0벡터를 검사하고 L2 정규화한다. 자동 retry·redirect·
prefix·truncation·모델 다운로드는 없다. HTTP 실패 메시지에 token/응답 본문을 넣지 않는다.

API 요청에 임의 revision 필드를 넣어 revision 고정을 주장하지 않는다.
[HF endpoint 설정](https://huggingface.co/docs/inference-endpoints/guides/configuration)의
Commit Revision과 배포 확인은 운영자가 제공해야 한다. 공용 router의 BGE-M3 지원 여부와
revision 확인도 후속 실제 검증 대상이다. 응답 형식은
[feature-extraction API](https://huggingface.co/docs/inference-providers/tasks/feature-extraction)의
문서별 벡터 배열이며 token-level 배열은 거절한다. 특정 backend의 추가 payload가 필요하면
그 adapter를 별도로 검증해야 한다.

`SQLiteIndexStore(path, plan=plan)`은 명시적 경로의 선택 가능한 저장소 구현이다.
운영 저장소 기본값/선택 승인이 아니다. `write_index`가 검증한 vectors와 Chunk/Source,
설정을 한 transaction에 저장하고 동일 index_version 덮어쓰기를 거절한다.
새 객체로 재오픈해 metadata와 DTO를 복원하며 예상 metadata와 query 공간을 대조한다.
정확 cosine 검색과 chunk_id tie-break를 제공하고 payload hash 손상을 거절한다.
이 검색 점수는 confidence가 아니다. 후보/날짜 필터와 Evidence 연결은 #54 범위다.

mock HTTP → synthetic vectors → SQLite 저장·재오픈·검색 검증만 수행했다.
실제 36개 Chunk의 API embedding·저장·검색은 [후속 #145](https://github.com/rice-steamed-water/skala-rag/issues/145)이며, 과제의 실제 RAG 성공
요구사항 자체를 없앤 것이 아니다. 이번 #52는 구현 및 offline 검증 범위로 완료한다.
