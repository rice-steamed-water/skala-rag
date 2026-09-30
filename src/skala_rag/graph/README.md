# State reducer

`InvestmentState`는 sources·chunks·evidence·evaluation_results·errors에
ID 기반 reducer를 지정한다. DTO는 수집/평가 경계에서 검증한 뒤
`model_dump(mode="json")` payload로 전달한다. reducer는 입력을 수정하지 않고
새 JSON payload를 반환하며 같은 ID의 식별 core나 결과가 다르면
`MergeConflict`를 발생시킨다. 오류 메시지에 근거 원문을 출력하지 않는다.

- `merge_result_maps`: 같은 key·같은 payload는 멱등 삽입이다.
- `merge_sources`: retrieved_at 이외 필드가 일치할 때 가장 이른 수집 시각을 유지한다.
- `merge_evidence`: 식별 core는 그대로 두고 provenance와 해석 집합을 합친다.
  excerpt는 최초값, confidence는 unknown < low < medium < high 순서의 낮은 값이다.
- `merge_errors`: error_id로 오류 목록을 병합한다.

수집 controller는 기존 evidence와 새 batch를
`merge_evidence_with_changes(existing, incoming)`에 전달해
`(merged_payload, changed_evidence_ids)`를 받는다. 변경 ID를 해당 후보 또는
industry 근거 적용 후보에 대응시켜 후보별 evidence_revision을 갱신한다.
신규 근거·새 provenance·해석 변화는 변경이며 동일 payload 재삽입이나
무시되는 excerpt 변화는 변경이 아니다. revision 갱신과 Graph wiring은
후속 controller 이슈에서 연결한다. 기존 snapshot의 payload는 바뀌지 않는다.

claim/locator의 원문 대응, 정규화와 참조 폐쇄성은 수집 경계 책임이다.
reducer는 문자열을 임의 정규화하거나 다른 출처를 같은 사실로 추론하지 않는다.
retrieval_history 등 단독 writer 필드는 기존 타입과 갱신 규칙을 유지한다.

## 평가 snapshot — #21

`skala_rag.graph.snapshot.freeze_snapshot(candidate_id, state, run_input, *,
run_id, index_version, schema_version, allowed_source_ids,
industry_evidence_ids, clock)`은 외부 호출 없이 `EvaluationSnapshot`을 반환한다.
run_input은 검증된 RunInput이며 State의 JSON run_input과 일치해야 한다.
허용 Source는 실행 manifest에서, 관련 산업 Evidence ID는 collector에서 명시적으로
전달한다. 빈 집합을 전체 허용으로 해석하지 않는다. 버전·clock에는 기본값이 없다.

- 해당 기업 근거와 명시적으로 관련성을 확인한 산업 근거만 포함한다.
  Source 발행일이 있으면 발행일, 없으면 snapshot 수집일을 as_of와 비교한다.
  날짜 비교는 원래 시간대의 달력 날짜를 사용한다. 미래 사건/금액 기준일도 제외한다.
  수집일을 보고서 발행일로 바꾸거나 날짜 미상 자료의 과거 존재를 추정하지 않는다.
- 포함 가능한 정정의 supersedes만 적용한다. 미래/미허용 정정은 과거 근거를
  무효화하지 않는다. 정정된 근거의 파생/추정 후속 값도 연쇄 제외한다.
- Source·supporting Evidence·RetrievalRecord·Chunk 참조와 실제 검색 기록의
  Source/Evidence/Chunk 귀속을 검증한다. RAG의 corpus·기업/scope·source·locator·
  발췌 대응을 확인한다. M1에서는 Chunk와 Evidence locator의 정확한 일치 및
  excerpt의 chunk.text 포함을 요구하며, 의미적 원문 검증을 대신하지 않는다.
- 미해결 충돌은 양쪽 근거와 conflicts_with로 보존한다. 해당 criterion의 missing
  판단은 평가 wrapper 책임이다. 최종 eligible 근거가 제외/정정되거나 참조가
  끊기면 자동 적격성 재판정 없이 실패한다.
- 성공 시 해당 후보 evaluation_round만 증가시키고 새 snapshot의 JSON 복사본을
  저장한다. 반환 DTO·저장 payload·현재 State의 중첩 객체는 공유하지 않는다.
  이전 snapshot은 갱신하지 않는다. 소비자는 저장된 snapshot을 직접 수정하지 않는다.
- 실패 시 redacted WorkflowError를 errors에 기록하고 `SnapshotInvalid`를 발생시킨다.
  오류의 error_code는 SNAPSHOT_INVALID이며 rounds·snapshots는 변경하지 않는다.
  Graph caller가 이를 잡아 해당 후보 failed → archive → advance를 연결한다(#23).

`tests/unit/test_snapshot.py`는 가상 자료로 T25와 참조 실패·정정·귀속 경계를
검증한다. live 검색/LLM 품질이나 정책 승인 검증 결과가 아니다. #35 v3 정합화는
별도 이슈이며 현재 main DTO와 승인 baseline을 재사용한다.
