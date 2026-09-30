# 단일 Evidence Research — #55

`agents.evidence_research.EvidenceResearch`는 v3 B-2의 Primary RAG Agent다.
최초 수집과 Coverage 부족 근거 재조사를 **같은 진입점**에서 수행한다. 별도 Targeted
Research Agent는 없다([contracts §7](contracts.md), [data-rag §1](data-rag.md)).

## 호출

```text
EvidenceResearch.run(candidate, gaps, budget) -> ResearchOutcome
EvidenceResearch(candidate, gaps, budget)     -> ToolResult[EvidenceBundle]  # CollectEvidence
evidence_research_stage(research, budget=...) -> CandidateNodes.collect
```

- `gaps=[]`: 최초 수집이다. 생성 시 주입한 `initial_plan(candidate)`이 돌려준 필수 근거
  계획(ResearchGap 형태: 대상 criterion/eligibility_field와 질의)을 조사한다.
- `gaps` 있음: 그 후보의 `open` gap만 조사한다. 다른 후보·resolved·exhausted gap은 무시한다.
- 생성 시 고정: `Retrieve`(#54 `IndexedRetriever` 등), `StructuredLLM`, 선택적 Web/API
  채널, corpus/index version, as_of, top_k, 승인 manifest의 allowed_source_ids.

**주입만 하고 기본값을 두지 않는 OPEN 항목**
([decisions](decisions.md) D05·D06·D08, [architecture §5](architecture.md)):
최초 수집 필수 근거 계획(최소 Evidence gate)과 batch당 도구 호출 한도
(`max_tool_calls_per_research_batch`의 v3 해석). 호출자가 값을 넣지 않으면 동작하지 않는다.

## 경로와 추출

| 도구 | 입력 구간 | provenance |
| --- | --- | --- |
| RAG `Retrieve` | #50 `rag_segment` — 이번 검색 이력의 `chunk_ids`와 같은 bundle이 반환한 Chunk | `rag` + retrieval_id + chunk_id |
| Web/API `WebChannel` | #50 `web_segment` — 실제 받은 `RawSnapshot`과 Source hash·위치·fetch 이력 대조 | `web`/`api` + retrieval_id |

- 구간마다 #50 `extract_evidence`가 LLM 출력을 원문과 대조한다. 거절 사유 코드만
  `ResearchOutcome.rejected`에 남긴다(원문·모델 출력 없음).
- 금액 Evidence(`currency` 있음)는 #53 `to_amount` 단위 해석을 통과해야 남는다
  (`finance:<MissingCode>` 거절). 파생값은 만들지 않는다.
- Web/API Source가 as_of 정책에 걸리면 `as_of:<사유>`로 제외한다.
- 같은 Source·locator·주장을 Web과 RAG로 다시 얻으면 같은 Evidence ID이며 #15
  reducer가 provenance만 합친다(Web→RAG 재발견 trace).
- 반환 전 `verify_provenance`로 모든 Evidence를 이번 이력·Chunk와 다시 대조한다.

## 필수/선택, 상태, 예산

- 도구마다 `required`를 정한다. 필수 도구가 `unavailable`/`failed`면 batch를 멈추고
  그 상태로 끝난다. 그때까지 받은 Source·Chunk·이력은 `ResearchOutcome`에 남는다.
- 선택 도구 실패는 계속 진행한다. 이유는 RetrievalRecord의
  `arguments_without_secrets.research_error_codes`와 `error_id`로 남긴다. 도구가 이력을
  주지 않은 오류(예: index preflight 거절)는 로컬 이력을 합성한다. State `errors`에는
  넣지 않는다(후보 실패 ID로 집계되므로).
- `empty`는 조회 성공·0건이다. 실패나 부정 사실로 바꾸지 않는다.
- LLM 오류는 도구 코드가 아니라서 `ToolResult`에 담지 않는다. `run()`은 `failed`와
  WorkflowError를, `__call__`은 `ResearchFailure`를 낸다.
- 모든 이력에 `research_tool`·`research_required`·`research_gap_id`·
  `research_initial`을 붙인다. gap 복사본의 `attempted_retrieval_ids`에 실제 호출만 더한다.
- batch `ToolBudget.max_calls`를 (gap × 질의 × 도구) 호출이 함께 쓴다. 남은 계획은
  실행하지 않고 `skipped`로 센다. 계획이 있는데 0이면 호출 전 `BUDGET_EXHAUSTED`다.
  LLM·네트워크 retry는 각 wrapper 예산이다.

## 후보 Graph 연결

`evidence_research_stage`를 baseline `CandidateNodes.collect`로 넣는다. Graph 코드는
바꾸지 않았다.

- `research_retry_count[cid] == 0`이면 최초 수집(`gaps=[]`), 아니면
  `research_gaps[cid]`의 open gap으로 재조사한다. 횟수 차감·소진은 #25 `research_gate`다.
- delta: Source·Chunk·검색 이력과 Evidence. State Evidence payload가 실제로 바뀔 때만
  `evidence_revisions[cid]`를 1 올린다. empty 재조사는 revision을 바꾸지 않는다.
- 필수 도구 실패·LLM 오류는 `StageFailure`다. 최초 수집이면 후보 failed → archive,
  재조사면 count를 되돌리지 않고 gap을 missing으로 둔다. 이 경우 실패 batch의 이력은
  Graph writer 규칙상 State에 남지 않고 `errors`만 남는다.
- snapshot은 쓰지 않는다. 이미 freeze한 snapshot은 불변이고 새 근거는 다음 세대에서만
  보인다(T25).

## 검증 범위

`tests/unit/test_evidence_research.py`, `tests/integration/test_evidence_research_graph.py`.
RAG는 #54 `IndexedRetriever`에 가상 backend를, 추출은 가상 LLM을 쓴다. 실제 BGE-M3
index·LLM 호출은 하지 않았으므로 검색 품질·추출 품질이나 live 성공 증거가 아니다.
gap 질의용 실제 Web 검색 adapter는 아직 없다(#48 Tavily는 후보 발견 전용).
