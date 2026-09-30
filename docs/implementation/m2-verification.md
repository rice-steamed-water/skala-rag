# M2 검증 현황 — #62

## 실행 범위

`tests/integration/test_m2_trace_boundaries.py`는 synthetic 공통 fixture와
GuardedRetriever/fixture_search → rag_segment → extract_evidence(FakeLLM) →
link_record → freeze_snapshot → evaluate_dimension(FakeLLM)을 연결한다.
실제 embedding/index·승인 원문·provider·Technology live evaluator는 사용하지 않는다.
기존 baseline 평가 wrapper의 경계를 검증하며 v3 live 평가 완료 증거가 아니다.
적격성은 가상 admission이고 기술 통합 주장이 실제 적격성을 입증한다는 뜻이 아니다.

```bash
uv run pytest tests/integration/test_m2_trace_boundaries.py -q -rs
```

- 검색 이력의 run/retrieval/chunk/source/page와 추출 Evidence, 동결 snapshot,
  Technology criterion 인용을 대조한다.
- 이력에 Evidence가 없거나 다른 기업 Chunk·미래 확보 Source·변경 원문이면
  snapshot 저장/평가 세대 증가를 거절한다.
- 반환되지 않은 조작 Chunk와 snapshot에 없는 평가 인용을 거절한다.
- State의 후속 변경으로 저장 snapshot이 바뀌지 않는다.
- 해당 offline fixture는 socket 연결을 차단하고, 가상 환경변수가 두 LLM 호출에
  전달되지 않는 것을 검사한다. 이것이 전체 테스트나 실모델의 보안 검증을 뜻하지 않는다.
- live 미연결 테스트는 이유와 함께 skip한다. opt-in 실행 함수가 구현됐다는
  뜻이 아니며 환경변수만으로 fake 경로를 live로 바꿀 수 없다.

## 필수 live 증거 — 현재 미검증

이 문서는 credential이나 원문을 읽거나 실제 유료 요청을 실행하지 않는다.
다음 구현이 연결되면 명시적인 승인·설정·readiness·예산 확인 후 실사용을 검증한다.

| 경로 | 필요한 증거/현재 조건 |
| --- | --- |
| corpus / index (#49/#52/#54) | 승인 manifest, 원문 hash/page, 모델·tokenizer revision/license, index 재오픈 및 실제 검색 ID. fixture index 경계로 대체 불가 |
| Evidence (#50/#55) | 실제 반환 Chunk의 LLM 추출, link_record 및 verify_provenance, snapshot 참조 폐쇄성 |
| Technology (#47/#57) | 실제 structured-output 요청, versioned rubric/policy와 snapshot Evidence의 평가 사용 기록 |
| Discovery / Company Research (#48/#51) | 필수 provider smoke 결과 및 출처/기업 ID; 부분 Source 수집으로 전체 성공 선언 불가 |
| 다섯 평가 branch (#57–#61) | Founder/Market/Technology/Moat/Business & Deal의 실제 smoke와 원자적 여섯 차원 결과 |
| runtime | 도구별 required/optional readiness, 미설정/실패 사유, 승인 범위·시간/호출/비용 및 retry 사용량 |

실제 실행 기록에는 run/retrieval/chunk/source/page/evidence/snapshot/evaluation ID와
model/prompt/policy/corpus/index version을 남긴다. private 원문·credential은 남기지 않는다.
필수 live 경로의 skip/실패를 성공 또는 missing으로 바꾸지 않는다.

#62 본문의 오래된 요구는 최신 승인 기록으로 해석한다.
200페이지 한도는 #91에서 적용 제외되어 승인 manifest gate(#44)를 확인한다.
임베딩은 BGE-M3 직접 선정 및 #56 Not planned 종료 기록을 따르며,
3종 비교를 다시 필수화하지 않는다. 정확한 revision/license와 실제 품질·자원 기록은
선정 승인과 별개의 검증이다. #43 초기 C 보류 기록보다 후속 결정이 우선하나
모델 선정만으로 다운로드·실제 index readiness가 충족되지는 않는다.

상충 재무 단위, private URL, prompt injection 및 v3 snapshot/평가의 경계 간
부정 시나리오 전체는 아직 이 신규 테스트의 범위에 포함되지 않는다.
기존 단위 검증을 #62 전체 통합 성공으로 재표기하지 않는다.
최종 보고서 citation, Generator/Judge, 실제 PDF 및 전체 live runner는
#94/#95/#96의 M3 범위이며 여기서 미검증이다.

필수 실사용 trace가 준비될 때까지 #62와 PR은 blocked/Draft로 유지한다.
