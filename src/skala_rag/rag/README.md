# Fixture 검색과 근거 수집

#54의 주입형 index 검색 경계는 `rag.adapter.IndexedRetriever`와
[검색 adapter 가이드](../../../docs/implementation/retrieve-adapter.md)를 따른다.
실제 모델·store·검색 검증은 #52 대기 상태이며 아래 fixture 검색과 구별한다.

`rag.fixture.FixtureRetriever`는 #12 공통 fixture의 RetrievalBundle을 메모리에
복사해 검색한다. Retrieve Protocol을 만족하며 실행 모드는 fixture만 허용한다.
query로 의미 검색하지 않고 fixture Chunk 순서대로 기업·허용 출처·corpus·기준일을
필터링하고 top_k만큼 반환한다. 실제 embedding·index·네트워크는 사용하지 않는다.

index_version·run_id·schema_version·clock·검색 이력 ID 생성 함수를 명시적으로
주입한다. 발행일 미상 자료는 fixture 결과에서 제외하며 날짜를 추측하지 않는다.
캐시는 query·기업·corpus/index_version·허용 Source 집합·as_of·top_k를 포함한다.
조회마다 독립된 payload와 새 RetrievalRecord를 반환하고 cache_hit을 기록한다.

`tools.fixture_collector.FixtureEvidenceCollector`는 CollectEvidence Protocol을
만족한다. retrieve·request_builder·가상 extract 함수·chunk_store·run_id·
schema_version·clock·오류 ID 생성 함수·fixture 모드를 명시한다.
request_builder는 Candidate와 ResearchGap 목록에서 RetrievalRequest 목록을
만든다. 추출기는 **실제 반환된 Chunk 복사본**에서 가상 Evidence 내용을 제공한다.
Collector는 추출 결과의 기존 provenance를 새 검색 이력/반환 Chunk의 rag
provenance로 교체한다. 기존 경로는 검증되지 않은 추출 metadata로 간주한다.
같은 내용의 Evidence ID는 유지하며 이미 State에 있는 Web/RAG 경로와의 합집합은
#15 reducer가 담당한다.

검색 결과의 범위를 다시 검증하고, Evidence의 기업·scope·Source·사건일 및
검색 이력의 run/기업/반환 ID를 대조한다. 검색 한 번에 이력 한 건을 요구한다.
실제 반환 Chunk는 주입한 chunk_store에 복사하며 같은 ID의 다른 내용은 거절한다.
controller는 ToolResult.retrieval_records, EvidenceBundle.sources/evidence와
chunk_store의 DTO를 model_dump(mode="json")으로 State에 저장한다. State 갱신과
revision 증가, snapshot 동결은 이 adapter의 책임이 아니다.

fixture batch 요청 수가 max_calls보다 크면 호출 전에 BUDGET_EXHAUSTED로 거절한다.
실제 timeout/deadline·재시도·비용 예산은 M2 공통 wrapper #45 범위다.
검색 0건/추출 0건은 empty이고, 범위 위반은 TOOL_RESPONSE_INVALID, 추출 기술
실패는 TOOL_FAILED로 구분하며 오류 메시지에 원문이나 예외 내용을 넣지 않는다.
실패 전에 생성된 검색 이력과 반환 Chunk는 보존한다.

구체적 구성과 trace 예시는 `tests/unit/test_fixture_retrieval.py`에 있다.
현재 main 계약 기준이며 v3 DTO/atomic branch 변경(#73)은 별도 후속 대조 대상이다.
