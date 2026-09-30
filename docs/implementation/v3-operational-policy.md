# v3 운영 정책 — #82

## 범위와 승인

승인 근거는 `rice-steamed-water/skala-rag#82`의 본문(2026-09-30)이다.
`configs/scoring.v3.json` / `v3-operational-1.0.0`은 운영 규칙의 별도 버전이다.
기존 `configs/scoring.draft.json`, rubric, 공통 DTO/State는 변경하지 않는다.
D14 rubric의 품질·실제 applicability rule 목록, 전체 live readiness·예산은
이 승인에 포함되지 않는다. `approval.status=operational_approved`와
`rubric_status/live_readiness/live_budget=not_approved`를 구별한다.

## 실제 API

```python
from skala_rag.scoring.v3_policy import V3Policy, load_v3_policy

policy = load_v3_policy("configs/scoring.v3.json", execution_mode="fixture")
serialized = policy.model_dump_json()
restored = V3Policy.model_validate_json(serialized)
assert restored == policy
```

`execution_mode` 인자는 필수이며 live를 거절한다. 파일 내부 모드도 fixture만
허용한다. loader는 지정 파일만 읽고 네트워크·모델·Graph를 실행하지 않는다.
`V3Policy` 및 모든 하위 모델은 frozen/extra-forbid이며 모든 정책 필드는 필수다.
컬렉션은 tuple로 보존한다. JSON duplicate key도 loader가 거절한다.
`model_construct`/`model_copy(update=...)` 같은 Pydantic 검증 우회 API는
신뢰 경계에서 사용하지 않는다.

| 필드 | 후속 소비 계약 |
| --- | --- |
| `policy_version`, `execution_mode`, `approval` | 실행 manifest에 버전·fixture·승인 범위 기록 |
| `criteria` | 기존 23개 ID, dimension, display_name, weight; ID 원문 보존 |
| `dimension_weights` | immutable dimension/weight 레코드 6개; 5/30/25/20/10/10 |
| `numeric` | Decimal 80/70/60, weighted_missing_pct 30, low_dimension_ratio_pct 40; exact_unrounded |
| `numeric.low_dimension_scope` | market/technology만 ratio 저점수 guard 대상 |
| `numeric.guard_order` | missing → low_dimension → normalized score |
| `numeric.labels_descending` | RECOMMEND_PRIORITY, RECOMMEND, WATCHLIST, PASS |
| `applicability` | 근거 부재 missing; N/A는 적용 사유·승인 rule·적용성 근거 모두 필요 |
| `selection` | eligible 정상 평가 후보, label 우선 → normalized_score DESC → weighted_missing_pct ASC → 원본 candidate_id ASC |
| `research` | 후보별 추가 2회, 최초 제외, 요청 전 차감, empty/failure 소비, 평가 후 금지 |
| `report` | 구조/의미 수정 공유 2회, 최초 제외; 소진 시 completed + Warning, draft/findings, validated final 금지, CLI exit 2; context/upstream 파손 failed |

`numeric.missing_comparison`은 `>=`, `low_dimension_comparison`은 `<=`,
`score_comparison`은 `>=`이다. 따라서 결측률 30 이상 또는 market/technology
ratio 40 이하 guard가 점수 등급보다 먼저 적용되어 WATCHLIST가 된다.
나머지는 normalized score ≥80 RECOMMEND_PRIORITY, ≥70 RECOMMEND,
≥60 WATCHLIST, <60 PASS다. loader는 이 수치/순서를 검증하지만 등급을 계산하지 않는다.

Decimal은 유한 문자열/정수/Decimal만 Python 입력으로 허용하고 bool/float를
거절한다. 파일 loader는 JSON 소수 token을 Decimal로 직접 파싱한다.
표시 반올림은 비교에 사용하지 않는다. JSON roundtrip은 Decimal 문자열로 보존한다.

기존 `Criterion` 타입과 `ScoringPolicy.validate_catalog`의 영역/합 검증을
재사용하고, 기존 23개 ID/비중도 정확히 대조한다. 내부 catalog-only 검증은
baseline 모델을 구성해 catalog 검증만 호출하며 draft 정책이나 D14를 승인하지 않는다.

## baseline과 다른 부분

기존 고정 분모 100 대신 missing 포함·승인 N/A 제외 분모를 기록한다.
분모 0은 점수/추천 label을 만들지 않고 명시 후보 오류 → archive → advance다.
기존 모든 영역 rating ≤2 대신 market/technology ratio ≤40% guard를 기록한다.
기존 첫 RECOMMEND 정지 대신 적격 정상 후보 사이 deterministic selector를
기록하며 WATCHLIST/PASS뿐이면 선택 없이 비교 보고서다.
평가 후 재조사는 금지하고, 보고서 소진은 failed가 아닌 completed + Warning이다.
이 변경은 기존 baseline 실행 함수를 자동 변경하지 않는다.

## 아직 구현하지 않은 부분

- 실제 applicability rule 목록/권한 검증은 **외부 controller의 필수 입력**이다.
  이 정책에 목록이나 자동 승인 기본값을 만들지 않는다. loader 통과는 N/A 승인 증명이 아니다.
- v3 aggregation, normalized score/weighted_missing_pct/영역 ratio 계산,
  zero-denominator 오류 처리, 네 label 결정, selector는 별도 구현이 필요하다.
  기존 `src/skala_rag/scoring/aggregate.py`와 `decide.py`는 baseline 구현이다.
- #73 DTO와의 연결, Graph archive/advance·research counter·report revision loop,
  Warning/draft/findings 반환 및 validated-final 억제, CLI exit 2는 미연결이다.
- minimum Evidence gate, Company Research 재조사/후보 상한, PDF layout 회계,
  corpus/model/live 비용·시간 상한은 이 파일에서 새 기본값을 부여하지 않는다.
- 실제 알고리즘 경계/E2E 검증은 후속 작업이다. 현재 tests/unit/test_v3_policy.py는
  가상 config/거절/roundtrip/catalog 회귀만 검증하며 live 평가 실측이 아니다.

공통 config 변경 공지는 부모 작업에서 #20/#23/#24/#25/#27/#28/#29/#30/#35/#43에
수행해야 한다. 이 구현 작업은 GitHub write·commit·push·병합을 수행하지 않는다.
