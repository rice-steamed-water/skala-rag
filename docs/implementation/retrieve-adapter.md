# 고정 index 검색 adapter — #54

`rag.adapter.IndexedRetriever`는 기존 `Retrieve` Protocol의
`RetrievalRequest → ToolResult[RetrievalBundle]` 경계를 구현한다.
현재 제공하는 것은 주입형 backend와 synthetic index를 사용한 오프라인 검증이다.
실제 BGE-M3 로딩·vector store·index 재오픈·검색 품질을 검증한 것은 아니다.

## 구성과 호출

- `IndexSnapshot`: 외부 index 소유자가 검증한 corpus/index version, corpus hash,
  embedding model/revision, 검색 설정, 활성 Chunk 및 Source 전체 payload.
  metadata만으로 corpus 승인·실바이트 hash·모델 readiness를 증명하지 않는다.
- `DenseSearch.search_once(request, *, snapshot, allowed_chunk_ids, timeout_seconds)`:
  한 번의 검색 시도. 허용 Chunk를 **ranking/Top-K 전에** 제한하고 동일 모델/revision과
  설정으로 query를 인코딩한 뒤 순위대로 Bundle을 반환한다. `retry_owner="runtime"`을
  명시한다. whole-attempt timeout 준수·취소는 backend 책임이다.
- `AdapterRuntime`, `Readiness`, `ToolBudget`, `Allowance`: #45 공통 예산·deadline·
  retry·오류 경계를 그대로 주입한다. index/model 필수 readiness를 요구하고,
  미준비·미승인 live·예산 소진은 실제 호출 전에 거절한다.
- `run_id`, `schema_version`, `tool_name`: 호출 이력과 공통 ledger 식별자.

별도 검색 DTO·정책 기본값·provider SDK·hidden retry는 추가하지 않는다.
backend 구현과 다운로드·store 선택은 선행 #52 및 승인 범위의 책임이다.
Backend는 similarity를 Evidence confidence/rating/사실 수치로 반환하지 않는다.
사용량은 현재 Bundle 인터페이스에서 실측되지 않으므로 `None`이며,
runtime은 주입된 요청 최대 예산을 보수적으로 유지한다. 실제 query encoder 비용을
사용하는 연결에서는 그 최대 비용을 allowance에 포함해야 한다.

### 병합된 #52 plan과 dense backend 연결

`rag.dense.snapshot_from_plan(plan, manifest=..., reopened_metadata=...,
search_settings={"metric": "cosine"}, execution_mode=...)`은 #52 `IndexPlan`의
Source/Chunk snapshot과 settings로 `build_index_plan`을 재실행한다. 현재 manifest
hash·승인 gate·설정·index version·Chunk ID 전체를 대조하고, store에서 읽어온
metadata가 plan과 정확히 같은지 확인한 뒤 `IndexSnapshot`을 만든다. partial
manifest는 별도 text-only 승인이 없는 경우 거절한다. 승인된 partial에는
`extraction_results`와 승인 주석을 붙이기 전 원래 `source_inputs`를 함께 주입해
원문·페이지·텍스트·설정·누락 범위를 다시 검증한다. 전체 문서 partial과 Source의
text-only 제한을 반환에서도 보존한다. 변경된 plan/metadata는 거절한다.
이 함수는 원문 바이트를 읽거나 실제
store transaction/reopen이 일어났음을 증명하지 않는다.

`DenseVectorSearch(snapshot=..., vectors=..., encoder=..., execution_mode=...)`는
#52의 `EmbeddingVector`와 명시적 `QueryEncoder.encode_once`를 소비한다. vector의
ID 집합·차원·모델/revision·유한값·0벡터를 검사하고 허용 Chunk만 대상으로 exact
cosine 순위를 계산한다. 동점은 Chunk ID 순서로 결정한다. cosine은 이 연결에서
명시적으로 주입해야 하는 지원 metric이며 product store/검색 정책 기본값이 아니다.
query encoder에는 index plan의 tokenizer·전처리·embedding 설정 전체와 runtime
timeout을 전달한다. 반환 `QueryVector`의 모델/revision·차원·유한값을 다시 검사한다.
실제 BGE-M3 encoder 구현·다운로드·API·store 선택은 포함하지 않는다.

```python
snapshot = snapshot_from_plan(
    plan,
    manifest=manifest,
    reopened_metadata=metadata_from_store,
    search_settings={"metric": "cosine"},
    execution_mode="fixture",
)
backend = DenseVectorSearch(
    snapshot=snapshot,
    vectors=vectors_from_store,
    encoder=injected_query_encoder,
    execution_mode="fixture",
)
# IndexedRetriever에 snapshot, backend, runtime/readiness/budget/allowance를 주입한다.
```

이 예시의 fixture mode나 synthetic model revision을 live 성공으로 바꾸지 않는다.
query encoder는 hidden retry·모델 자동 다운로드 없이 whole-attempt timeout을 지켜야
한다. 문서 후보가 없거나 top_k=0인 요청은 로컬 empty 이력을 남기며 encoder 호출이나
물리 요청 예산 차감이 없다. 그 외 miss는 runtime의 한 번의 시도에 한 번 인코딩한다.

### HF HTTP 및 SQLite 연결

`rag.query_hf.HFQueryEncoder`는 #52 `HFEmbeddingEncoder`를 재사용해 query 1개를
한 번만 HTTP embedding한다. 명시적 deployment/token/client를 공급하며 timeout은
encoder 설정과 runtime의 남은 timeout 중 작은 값이다. HTTP 인증·429·5xx·timeout은
runtime이 분류하고 raw body/URL/token을 이력에 넣지 않는다. 응답 schema·모델 설정
오류는 TOOL_RESPONSE_INVALID다. 기존 문서 embedding 호출의 오류 계약은 유지하고
query 경로에서만 transport 예외 전달을 opt-in한다. HTTPX interval timeout의 한계와
전체 시간 초과 후 결과 거절은 #45 runtime의 기존 책임 구분을 따른다.

`rag.sqlite_retrieve.SQLiteDenseSearch`는 명시적으로 선택된 SQLiteIndexStore를
새로 열어 읽은 metadata와 snapshot에 연결한다. 매 호출(cache hit 포함) 전에 로컬
read-back을 검사해 index 부재/교체/손상에서 query HTTP 및 예산 차감 없이 거절한다.
store의 현재 API에는 필터가 없으므로 **전체 순위**를 읽고 허용 Chunk를 제한한 뒤
request.top_k를 적용한다. 전역 Top-K 일부만 가져와 필터링하지 않는다. 대규모
index의 최적화/새 product store 선택은 포함하지 않으며 similarity는 평가에 전달하지
않는다. SQLite 구현 연결이나 mock HTTP 성공은 실제 저장소 사용/유료 호출 승인이 아니다.

## 반환 및 cache 격리

기업 Chunk는 request의 candidate에 귀속돼야 한다. industry의 관련성은 승인된
manifest를 읽는 caller가 `allowed_source_ids`로 공급하며 adapter가 추측하지 않는다.
발행일과 snapshot 확보일 모두 as_of 이하여야 한다. 발행일 미상도 기준일 이전
확보한 고정 snapshot만 허용한다(#99의 `source_date` 재사용).

결과는 top_k·Chunk 중복·기업·허용 출처·corpus·기준일을 검증한다.
Chunk의 text/page/locator/모델을 포함한 전체 payload와 Source hash/date/location을
고정한 활성 snapshot과 대조하며, 추가 Source·교체 문서·변조 결과는 전체 거절한다.
잘못된 corpus/index 요청 또는 retry owner는 호출·예산 차감 없이 실패한다.
정상 0건은 `empty`, index unavailable은 `unavailable`, payload/기술 실패는 `failed`다.

cache key는 query·기업·corpus/index·Source 집합·as_of·top_k와 전체 snapshot hash 및
검색 설정을 포함한다. Source 목록의 순서/중복과 Chunk 순서에는 불변이다.
snapshot과 cache의 내부 복사본을 보관하고 반환/주입에도 독립 복사본을 사용하므로
caller/backend의 변경이 다음 호출 결과를 바꾸지 않는다. 실패는 cache하지 않는다.
cache hit은 새로운 retrieval ID와 `cache_hit=true`를 기록하고 물리 요청 예산을
소비하지 않는다. deadline·readiness·live approval gate는 hit에서도 유지한다.

RetrievalRecord에 실제 반환 Source/Chunk ID와 index identity/cache key를 남긴다.
페이지와 locator는 반환 Chunk에서 복원한다. query/prompt/원문/예외 내용은 이력에
넣지 않으며 runtime의 정적 redacted 오류와 개별 시도 이력을 보존한다.
collector는 기존처럼 반환 Chunk를 저장하고 Evidence provenance를 연결한다.

## 실제 로컬 검색 검증 — 2026-09-30

#145의 사용자 승인 로컬 경로와 병합된 PR #149를 소비했다. 기존 로컬 BGE-M3
revision `5617a9f61b028005a4858fdac845db406aefb181` 모델 파일 hash를 #145 receipt와
대조하고 36 Chunk corpus의 실제 PDF 추출/텍스트 승인·plan을 재검증했다. 모델 파일을
추가 다운로드하지 않고 local_files_only/trust_remote_code=false로 읽었다.
`LocalQueryEncoder`는 #145 LocalEncoder를 사용하며 tokenizer/전처리/모델 설정
일치를 요구한다. 초과 길이와 늦게 반환된 query 결과를 거절한다. 동기 모델 연산을
강제로 중단하지는 못한다.

기존 SQLite index를 별도 Python interpreter에서 다시 열고 IndexedRetriever와
SQLiteDenseSearch를 실제 query embedding에 연결했다. 결과는 다음과 같다.

| 요청 | 실제 결과 |
| --- | --- |
| 두 문서 허용, Top-5 | π0.5 2·1·7·10·4페이지, Source/Chunk/locator 일치 |
| 같은 질의 반복 | 같은 결과, cache_hit=true, 추가 query encoding 없음 |
| π0 출처만 허용 | π0 4·15·1·7·2페이지, 다른 Source 제외 |
| 다른 기업 | empty, query encoding 없음 |
| 2025-01-01 historical 기준일 | empty, 늦게 확보된 snapshot 제외 |
| 허용 출처 없음 | empty, query encoding 없음 |

실행 31.30초(모델 파일 hash·로드·재추출·query·검색 포함), query encoding 2회,
각 26 token, 외부 inference API 0회/비용 USD 0이다. 로컬 compute 비용은 미측정이다.
이는 한 질의의 실제 검색·격리 smoke이며 Hit Rate/MRR benchmark나 모델 우위,
Evidence LLM 추출·Technology 평가·보고서 인용·전체 M2 성공은 아니다.
full-document partial과 이미지/그래프 누락·text_only metadata를 보존했다.

결과/이력은 Git 제외 `outputs/issue54-local-retrieve-v1/validation.json`에 있다.
index version은 #145와 같은
`sha256:bc345db2d4ed9c6c4edb46e02ccc3021408ae8aabe1431b376af1fa579ba53c7`이다.
모델·원문·벡터·index·추출 텍스트는 커밋하지 않는다.

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
uv run python -m skala_rag.rag.retrieve_validation \
  --root /path/to/repo --model-path /path/to/repo/data/local/models/bge-m3-5617a9f \
  --store-path /path/to/repo/outputs/issue145-local-bge-final/index.sqlite \
  --receipt-path /path/to/repo/outputs/issue145-local-bge-final/validation.json \
  --output-dir /path/to/repo/outputs/issue54-local-retrieve-rerun --timeout-seconds 60
```

명시적인 60초는 이 로컬 smoke의 기술적 상한이며 유료 provider/M3의
timeout/backoff 정책 승인으로 확대하지 않는다.
doc_type/year schema·fallback OPEN은 추가하지 않고 유료 HF endpoint 검증도 하지 않았다.
