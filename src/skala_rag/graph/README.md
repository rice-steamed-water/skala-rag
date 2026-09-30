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
