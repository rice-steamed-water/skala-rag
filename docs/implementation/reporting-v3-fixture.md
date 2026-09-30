# #94 v3 score/decision presentation fixture

이 문서는 정책 승인이 아니라 독립 fixture API의 구현 범위 설명이다.
기존 baseline formatter·Generator·Validator·Graph와 연결하지 않는다.

```python
from skala_rag.reporting.v3_format import format_fixture_score_decision

# summary와 decision은 skala_rag.contracts.v3의 가상 DTO다.
presentation = format_fixture_score_decision(summary, decision)
```

반환값은 Markdown/PDF가 아닌 JSON-compatible dict다.

- `execution_mode="fixture"`, `score_basis="normalized_score"`를 명시한다.
- `score_summary`와 `decision`에는 원래 DTO의 모든 필드가 들어간다.
  기존 DTO JSON 규약대로 Decimal은 정확한 문자열, 미상 수치는 null이다.
  표시 반올림·퍼센트 기호·번역·대표 reason 선택을 추가하지 않는다.
- `observed_score`와 `normalized_score`, `missing_weight`와
  `weighted_missing_pct`, N/A 배점과 적용가능 배점을 서로 구별한다.
  `observed_score/100`이나 baseline의 `dimension_ratings`를 만들지 않는다.
- 주어진 dimension map만 보존한다. 없는 차원을 0점이나 N/A로 채우지 않으며,
  `RECOMMEND_PRIORITY/RECOMMEND/WATCHLIST/PASS`와 grade·reason을 변경하지 않는다.
- 변경 가능한 입력 DTO와 중첩 점수를 재검증한다. baseline DTO는 거절한다.
  decision과 summary의 `score_summary_id`, `candidate_id`, `run_id`가 다르면
  `ValueError`다. 반환된 dict/list를 변경해도 입력 객체에 전파되지 않는다.

**검증 범위:** `tests/unit/test_v3_report_format.py`는 합성 관측으로 N/A 배점,
nullable 수치와 실제 0, 네 label, 정확한 Decimal 왕복, 참조 일치, baseline 혼용
거절을 확인한다. fixture의 수치는 upstream 산술·catalog 완전성·실제 근거 검증의
증명이 아니다. 0분모 운영 controller, 후보 selector, v3 보고서 context, live LLM,
본문 구조·의미 검증, PDF, final 발행을 구현하거나 승인하지 않는다.

D09의 표시 반올림·대표 reason·mode별 목차 예외·PDF 선택은 이 API로 결정하지
않는다. 이 payload는 후속 renderer의 손실 없는 입력일 뿐 승인된 보고서 양식이나
#94 전체 완료를 뜻하지 않는다. #47/#89 연결과 남은 D09 결정은 별도 후속 범위다.

## 후속 구현·승인

위 독립 fixture API의 역사적 범위는 유지한다. 2026-09-30 사용자 승인 후
#94의 실제 주입 경계·v3 context·다섯 섹션·공유 수정 controller가
[reporting-v3-pipeline](reporting-v3-pipeline.md)에 추가되었다.
이 후속 구현은 별도 모듈이며 이 formatter의 반환값/테스트를 바꾸지 않는다.
