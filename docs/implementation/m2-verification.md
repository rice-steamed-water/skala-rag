# M2 검증 현황 — #62

## 구현과 검증 범위

`agents.m2_trace.run_technology_trace`는 호출자가 제공한 적격성 조사 State와
검색 결과를 사용해 RAG segment → LLM Evidence 추출 → 이력 연결 → snapshot 동결 →
Technology 평가를 연결한다. 적격성을 만들어 넣거나 전체 M2 완료를 선언하지 않는다.

- 유료 요청 전에 기존 admission/provenance, 검색 run/candidate, 중복 retrieval ID,
  반환 Chunk 선택 및 기존 Source/Chunk 대체 여부를 검사한다.
- live 모드는 준비된 RuntimeStructuredLLM/OpenAIResponsesAttempt와 공유 요청 ledger를
  요구한다. fixture LLM을 live로 재표기할 수 없다. 모델·요금·timeout 기본값은 없다.
- 추출 Evidence가 실제 Technology 인용에 사용됐는지 확인하고 criterion/evidence/
  retrieval/chunk/source/page/snapshot 참조 폐쇄성과 snapshot hash를 검증한다.
- 입력 State는 복제하여 처리하며 실패 시 성공 receipt를 반환하지 않는다.
  receipt에는 원문·prompt·credential을 넣지 않는다. `whole_m2_verified=False`다.

`tests/integration/test_m2_trace_boundaries.py`는 네트워크를 차단한 synthetic fixture로
이 경로와 기존 평가 wrapper를 검증한다. 13개 테스트는 정상 연결, snapshot 불변성,
적격성 부재, 중복 이력, 원문 대체, 조작 Chunk/인용, 다른 기업·미래 자료,
추출 prompt injection 및 가상 환경변수 비전달을 포함한다.
실제 적격성 판정, provider 응답, v3 전체 평가 성공의 증거가 아니다.

```bash
uv run pytest tests/integration/test_m2_trace_boundaries.py -q
```

## 실제 증거와 남은 조건

| 경로 | 상태 및 필요한 증거 |
| --- | --- |
| corpus/index/retrieval | #52/#145/#54 완료. PR #137의 실제 로컬 BGE-M3 검색 검증은 재사용 가능하다. 이번 fixture 실행은 해당 실측의 재실행이 아니다 |
| Evidence → snapshot → Technology | 연결 함수와 offline 경계 검증 완료. 실제 적격성 Evidence가 포함된 Company Research State 및 실제 LLM 성공 trace는 아직 없다 |
| Discovery / Company Research (#48/#51) | required provider별 실사용 smoke와 기업·출처 ID가 필요하다. 일부 구현/이슈 종료만으로 전체 live 성공을 선언하지 않는다 |
| 다섯 평가 branch (#57–#61) | 실제 smoke 및 원자적 여섯 차원 결과가 필요하다. Technology component 성공만으로 대체하지 않는다 |
| runtime | required/optional readiness, 계정 요금·잔여 credit, 승인 범위·시간/호출/비용/retry 기록이 필요하다 |

OpenAI credential의 존재만 확인했으며 값은 기록하지 않았다. 실제 API 요청은 실행하지
않았다. 사용자 후속 승인으로 [승인 요청안 §3](m2-live-approval-proposal.md)의
#62 LLM timeout은 30초, 추가 transport 재시도는 0회로 확정되었다.
실행에는 적격성 근거를 포함한 실제 조사 State의 경로와 계정 요금·잔여 credit 확인이
필요하다. 로컬 산출물에는 해당 State가 없으며, #51의 공개 live 기록은
레인보우로보틱스의 `ineligible`/`LISTED` 결과여서 적격 후보 State로 사용할 수 없다.

실제 실행 기록에는 run/retrieval/chunk/source/page/evidence/snapshot/evaluation ID와
model/prompt/policy/corpus/index version, 공유 예산 사용량을 남긴다.
필수 live 경로의 skip/실패를 성공 또는 missing으로 바꾸지 않는다.

#62의 오래된 200페이지 요구는 #91의 적용 제외와 승인 manifest gate(#44)를 따른다.
임베딩은 BGE-M3 직접 선정 및 #56 Not planned 종료 기록을 따른다.
상충 재무 단위·private URL·v3 전체 snapshot/평가의 통합 부정 시나리오는 여전히
추가 검증이 필요하다. 기존 단위 테스트를 전체 M2 통합 성공으로 재표기하지 않는다.
최종 보고서 citation, Generator/Judge, 실제 PDF 및 전체 live runner는 #94/#95/#96의
M3 범위다. 필수 실사용 trace가 준비될 때까지 #62와 PR #123은 blocked/Draft로 유지한다.
