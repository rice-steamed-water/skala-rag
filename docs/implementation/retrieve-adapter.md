# 고정 index 검색 adapter — #54

`rag.adapter.IndexedRetriever`는 기존 `Retrieve` Protocol의
`RetrievalRequest → ToolResult[RetrievalBundle]` 경계를 구현한다.
현재 제공하는 것은 주입형 backend와 synthetic index를 사용한 오프라인 검증이다.
실제 BGE-M3 로딩·vector store·index 재오픈·검색 품질을 구현하거나 검증한 것은 아니다.

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

## 남은 완료 조건

#52는 아직 Draft/blocked이다. #49의 실제 π0/π0.5 자료는 `partial`이므로 현 index
gate가 거절하며, 본 adapter가 이를 승인/ok로 승격하지 않는다. 실제 승인 corpus,
BGE-M3 revision/LICENSE·query encoder·store·read-back·readiness·실행 승인 이후
실제 index의 검색 Chunk/Source/page trace와 검색 품질을 확인해야 #54를 완료할 수 있다.
doc_type/year schema 확장·fallback 우선순위 등 OPEN 정책은 이번 구현에 넣지 않았다.
모델 다운로드·실제 embedding/index/search·외부 API는 실행하지 않았다.
