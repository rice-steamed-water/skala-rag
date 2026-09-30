# v3 구조 계약 — 이슈 #73

명시적 import: `from skala_rag.contracts.v3 import CriterionAssessment, Evaluation,
EvaluationBranchResult, ApplicabilityAssessment, DimensionScore, ScoreSummary,
InvestmentDecision, CoverageResult`. Source, Chunk, Evidence, EvaluationSnapshot,
WorkflowError는 baseline 실제 타입을 재사용한다. 기존 `contracts` 공개 API,
State/reducer, DTO 및 정책 파일은 변경하지 않는다. v3 payload를 baseline
payload로 조용히 대체하지 않는다. 모든 중첩 schema_version은 필수이며
호출자가 버전을 공급한다. extra forbid, strict nonblank ID, instance 재검증,
aware timestamp, 명시적 fixture context 규칙은 공통 Contract에서 상속한다.

## 제공 구조

- observed: strict rating 1..5, 근거 ID와 rationale 필수.
- missing: rating=null, missing_reason 필수. 근거 부재를 N/A로 바꾸지 않는다.
- not_applicable: rating=null, applicability_reason, applicability_rule_id,
  applicability_evidence_ids 필수. 적용성 근거 목록은 중복 없이 비어 있지 않아야
  한다. 일반 evidence_ids와 별도이며 승인 여부나 실제 근거를 생성하지 않는다.
- founder/market/technology/moat branch는 각각 해당 dimension 하나,
  business_deal은 traction/deal_terms 두 payload가 정확히 있어야 success.
  map key와 nested dimension, run/candidate/round/snapshot/revision/policy를
  대조한다. success에는 errors가 없어야 한다. failure는 evaluations=null과
  오류 하나 이상이며 오류 run/알려진 candidate를 대조한다. 일부 성공 승격 금지.
- DimensionScore: observed_score/applicable_weight/not_applicable_weight/
  missing_weight/dimension_score_pct. ScoreSummary는 criterion_points,
  dimension_scores, 위 비중, normalized_score/weighted_missing_pct/coverage_pct와
  핵심 market/technology low_score_dimensions, hold_reasons를 담는다.
- CoverageResult는 covered/missing/not_applicable ID의 중복 없는 분할과
  N/A ID를 정확히 해소하는 applicability_assessments map을 검사한다.
- InvestmentDecision은 RECOMMEND_PRIORITY/RECOMMEND/WATCHLIST/PASS를 기록한다.

숫자는 기존 exact Decimal 검증 타입을 재사용하고 JSON 문자열로 보존한다.
미상 숫자/비율과 research_ready는 None을 유지한다. caller가 공급한 관측을
검증할 뿐 계산이나 보정은 없다. 분모가 0인 구조에도 비율 None을 보존하며
그 결과의 보류/실패/계속 정책을 정하지 않는다. 가중치 합, 공식 일치, 전체
여섯 차원 완전성, 판단 threshold, 정밀도/반올림, ranking/selector, rubric,
Warning/workflow enum, live 예산/readiness 기본값은 구현하지 않는다.

## Controller 경계

원 catalog 23개 완전성·차원 귀속, 실제 Evidence 존재/품질, rule 승인,
freeze·참조 폐쇄성·Source/Chunk/provenance lineage, 조립된 최종 세대의 연결,
join의 다섯 branch→여섯 dimension 원자적 저장은 후속 controller 책임이다.
구조 객체는 불변 snapshot 구현이나 scoring/decision 실행이 아니다.

`tests/fixtures/v3_contracts.json`은 새로 작성한 가상 offline 구조 관측이다.
synthetic rule/version/hash/점수는 정책 승인, 실제 기업 실측 또는 hash 계산이
아니다. 미병합 선행 구현이나 fixture를 복사하지 않는다. 읽기 전용
v3 설계 snapshot의 과거 approval-state 문구는 사용자 v3 전환 승인과
구별하며 운영 미정 선택을 코드 기본값으로 만들지 않는다.

추가로 명시 승인된 v3 운영 규칙은 #82의 별도 정책 범위다. 이 구조 모듈은
분모 0 처리, selector, 재조사/보고서 수정 예산 또는 Warning 종료를 실행하지 않는다.

## 검증과 인계

`uv run pytest tests/contract/test_v3_contracts.py -q`로 신규 계약만 검증한다.
baseline 회귀는 이 파일의 명시 API 분리/reuse 검사와 부모의 최종 gate에서
기존 suite를 실행하여 확인한다. 대상 두 Python 파일의 ruff만 실행한다.
GitHub 공통 계약 영향 알림(#8/#12/#20/#21/#22/#24/#35/#43), PR 기록,
최종 전체 gate/검토/commit/push/merge는 부모 작업이다. 이 구현의 offline
통과는 CI·merge·live 완료 증거가 아니다.
