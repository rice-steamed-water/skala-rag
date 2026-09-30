# Issue #20 — v3 fixture Coverage

구현: `src/skala_rag/scoring/coverage_v3.py`. 기존
`src/skala_rag/scoring/coverage.py`와 baseline 테스트는 변경하지 않는다.
구조 계약은 병합된 `src/skala_rag/contracts/v3.py`를 명시 import한다.

## API / 명시 입력

- `check_coverage_v3(candidate_id, evidence, catalog, *, evidence_revision,
  schema_version, execution_mode, policy_version, support_check,
  applicability_assessments, applicability_check, unresolved_conflict_ids)`
  → 실제 v3 `CoverageResult`.
- catalog는 기존 23항목 `ScoringPolicy`를 호출자가 주입한다. 이 catalog의
  draft 버전과 출력의 명시적 v3 `policy_version`은 서로 다른 역할이다.
  운영 정책 파일을 자동 로드하거나 미병합 #82 코드를 import하지 않는다.
- `support_check(criterion, actual_evidence_tuple) -> bool`은 관측 충분성,
  단위·기간·산업 관련성을 판정한다. 빈 근거나 미해결 상충은 Missing이며
  checker의 오류·bool 아닌 반환을 Missing으로 숨기지 않는다.
- `applicability_assessments`는 criterion ID → 실제 v3
  `ApplicabilityAssessment` map이다. N/A를 요청한 모든 항목에 사유·rule ID·
  중복 없는 실제 Evidence ID가 필요하다.
- `applicability_check(candidate_id, criterion, assessment,
  actual_evidence_tuple) -> bool`은 호출자가 승인받은 rule 목록·버전과 사유,
  실제 근거의 의미·provenance 유효성을 확인한다. 전역 rule 목록이나 기본
  승인 checker는 없다. False는 입력 오류이며 Missing/N/A로 자동 보정하지 않는다.
- N/A 근거는 실제 입력에 존재하고 같은 후보 또는 candidate_id=null인 산업
  scope이며 해당 criterion에 귀속돼야 한다. 비어 있는 provenance, 미해결
  상충, 미지 ID, 중복 ID/criterion, 다른 Evidence·assessment schema는 거절한다. Source/Chunk/
  RetrievalRecord의 실제 폐쇄성·as_of·superseded/derived 유효성은 upstream
  수집 경계와 주입 checker가 검증해야 한다. 이 함수는 외부 출처를 조회하지 않는다.

## 산술 / 오류

세 partition은 catalog 순서로 출력한다. Missing은 적용가능 분모에 포함하고
검증된 N/A만 제외한다. 가중치와 비율은 float 변환 없이 Decimal로 계산한다:

```text
applicable_weight = total_catalog_weight - not_applicable_weight
weighted_missing_pct = missing_weight / applicable_weight * 100
coverage_pct = 100 - weighted_missing_pct
research_ready = missing_weight * 100 < 30 * applicable_weight
```

표시 반올림을 하지 않는다. 유한 Decimal로 끝나지 않는 나눗셈은 Python Decimal
context 정밀도를 따르지만 readiness는 나눗셈 결과가 아니라 교차곱으로 비교해
정확한 30% 경계를 유지한다. `research_ready`는 투자 추천/점수/rubric 승인이 아니다.
분모 0은 `NoApplicableCriteria(ValueError)`이며 `candidate_id`와
`policy_version`을 보존한다. 비율·점수·추천을 만들지 않는다. Graph/controller가
후보 오류를 기록하고 archive → advance해야 하며 그 연결은 이 모듈 범위 밖이다.

`build_research_gaps_v3(coverage, catalog, templates, *, policy_version)`은
전체 catalog partition·중복/겹침·정책 버전과 caller template을 검증한다.
Missing만 정확히 한 번 요구하며 비중 내림차순, 동률 catalog 순서다.
`gap_id`, `reason`, `missing_fields`, `suggested_queries`, 검색 이력을 유지하고
`priority_weight`만 catalog에서 재계산한다. N/A/covered gap이나 외부 후보,
빈 쿼리/필드, non-open template은 거절한다. 입력을 변경하지 않는다.

## 검증·미연결 경계

`tests/unit/test_v3_coverage.py`는 병합된
`tests/fixtures/loader.py:load_common_fixtures`를 실제 호출한다. baseline 회귀는
`tests/unit/test_coverage_scoring.py`, 공통 fixture 회귀는
`tests/contract/test_common_fixtures.py`다. 모든 데이터·checker/rule은 가상이며
실제 기업 평가나 live 검증이 아니다.

fixture만 허용한다. D14 rubric/applicability rule 목록·live provider/예산/corpus
승인을 대체하지 않는다. #24 병렬 평가/State, #23 후속 v3 wiring, #25 실제 조사
loop, #82 정책 loader와의 최종 adapter 연결은 담당 작업에서 수행한다.
