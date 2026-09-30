# Fixture 점수·Coverage 모듈

`catalog.load_policy(path, execution_mode="fixture")`는 현재 main의 draft 정책을
명시적으로 읽는다. 승인 문서나 v3 설계 변경을 자동 채택하지 않는다.

`coverage.check_coverage(candidate_id, evidence, catalog, ...)`는 CoverageResult를
반환한다. schema_version·evidence_revision·execution_mode·support_check·
unresolved_conflict_ids를 모두 호출자가 명시한다. live 모드는 거절한다.

support_check는 criterion과 관련 Evidence tuple을 받아 **bool**로 충분성을
판정한다. 단위·기간·주체·산업 관련성 같은 관측 인정 규칙은 이 경계에서
주입하며 criterion_ids가 붙었다는 사실만으로 충분성을 인정하지 않는다.
검사 실패는 예외를 그대로 전달해 기술 실패를 결측으로 숨기지 않는다.
다른 기업 근거는 제외하고 관련 미해결 상충이 있으면 해당 항목을 결측으로
남긴다. Source 참조·as_of·허용 출처·정정/파생값 유효성은 upstream 수집
경계에서 검증해야 한다. 실제 판단·도구·LLM을 연결한 구현이 아니다.

covered/missing 항목은 catalog 순서로 반환한다. missing_weight는 결측 비중
합계, coverage_pct는 100 - missing_weight다. research_ready는 정책의
missing_weight 임계값보다 **작을 때만** True다. 재정규화하거나 점수를
보정하지 않는다.

`coverage.build_research_gaps(coverage, catalog, templates)`는 모든 missing
criterion에 대해 호출자가 명시한 ResearchGap 템플릿을 정확히 한 번 요구한다.
ID·missing_fields·reason·suggested_queries·이력은 템플릿에 보존하고
priority_weight만 catalog에서 다시 계산한다. 같은 후보의 open gap만 허용하며
비중 내림차순, 동률 catalog 순서로 정렬한다. 실제 검색과 재조사 loop는 #25에서
연결한다.

baseline 테스트는 #5의 가상 DTO fixture와 #9 draft catalog를 명시적으로 사용한다.

## 별도 v3 경계 (#20)

`coverage_v3.check_coverage_v3`는 병합된 `contracts.v3.CoverageResult`를 반환한다.
23항목 catalog, v3 policy_version, 실제 근거 충분성 검사와 승인 rule 적용 검사를
호출자가 명시적으로 공급한다. 검증된 N/A만 분모에서 제외하고 Missing은 포함한다.
Decimal 비율과 정확한 30% readiness 경계, 후보별 분모0 예외,
`build_research_gaps_v3`의 Missing 전용 caller gap 보존을 제공한다.

실제 병합된 #12 `tests.fixtures.loader.load_common_fixtures`로 회귀를 검증한다.
API·책임·live 차단과 미연결 경계는
[`docs/implementation/v3-coverage.md`](../../../docs/implementation/v3-coverage.md)에 있다.
이 구현은 baseline을 변경하거나 D14 rule 목록·rubric/live를 승인하지 않는다.
