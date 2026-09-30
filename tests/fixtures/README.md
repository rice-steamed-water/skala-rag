# 공통 가상 fixture

`common.json`은 #12의 **가상 기업·근거·검색 이력·평가 예시**다.
실제 기업 데이터, LLM 출력, 검색·embedding 실측 또는 투자 판단이 아니다.
기존 `contracts.json` / `evaluation_contracts.json`은 개별 DTO 구조 테스트용이며
이 공통 세트가 대체하거나 변경하지 않는다.

| case | 의미 |
| --- | --- |
| eligible | 비상장 Seed 기업을 가정한 가상 적격 입력 |
| ineligible | 상장된 기업을 가정한 가상 부적격 입력 |
| unknown | 상장 여부·투자 단계 미상 입력; None 보존 |
| same_name | eligible과 이름이 같지만 국가·홈페이지·법인 식별자가 다른 기업 |

후보 4개, Source/Chunk/검색 이력 각 4개, Evidence 26개, snapshot 1개,
여섯 영역 Evaluation 6개(criterion 23개)가 들어 있다.
traction.rule_of_40은 적용조건 미확정 예시로 rating=None을 유지한다.
적격성 label과 rating은 테스트 입력으로 명시한 값이며 실행된 판정이 아니다.
모든 자료 locator·후보 홈페이지는 fixture://를 사용한다. embedding_model은
fixture-no-model로, 실제 모델을 다운로드하거나 인덱스를 만들지 않는다.
Source hash는 연결된 가상 Chunk 문자열의 UTF-8 SHA-256이다.

저장소 루트에서 다음처럼 읽는다:

```python
from skala_rag.scoring.catalog import load_policy
from tests.fixtures.loader import load_common_fixtures

policy = load_policy("configs/scoring.draft.json", execution_mode="fixture")
fixtures = load_common_fixtures(policy)
candidate = fixtures.candidates[fixtures.cases["eligible"]]
evaluations = list(fixtures.evaluations.values())
state_payload = fixtures.model_dump(mode="json")
```

로더는 DTO schema, ID/map key, Source·Chunk·Evidence·검색 기록·snapshot 참조,
평가 세대와 catalog 완전성을 검증한다. 매번 독립된 객체를 반환한다.
실제 근거 품질/적격성 판정·snapshot controller·M1 E2E 구현은 후속 이슈 범위다.
현재 main catalog를 사용하며, v3 설계 정합화가 병합되면 catalog·fixture·소비자
테스트를 같은 후속 변경에서 갱신해야 한다. 테스트 helper는 테스트 패키지에만
있으며 배포되는 런타임 모듈에는 포함하지 않는다.
