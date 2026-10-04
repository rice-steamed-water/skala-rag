# 실행 한정 권장 설정과 후보 선정 receipt (#199)

[문서 홈](../README.md) · [공통 계약](contracts.md) · [결정 목록](decisions.md)

## 권한과 범위

사용자의 **2026-10-04 “권장 설정 사용”** 지시로 선택한 이번 run의
D05/D06/D08 설정이다. `recommended_profile(...)` 호출에는 `run_id`,
`selection_source`, `authority_reference`, `policy_references`, `code_version`을
모두 명시한다. 앱 전역 기본값이나 전역 APPROVED 결정으로 채택하지 않는다.
출처·권한·정책 참조는 호출자의 선언이며 인증된 승인/실제 사용한 rubric bytes의
검증을 대신하지 않는다. `code_version=None`도 명시적 선택이다.

이 모듈이 **실행하는 것**은 기존 `agents.discovery.normalize_candidates`를
통과하는 후보 dedup·선정과 receipt replay뿐이다. Controller 전체 연결, 실제
fact verifier, research/evaluation/provider 호출, 최종 scoring/selector,
manifest 연결, live admission은 구현 범위 밖이다. 기존 fixture/live gate를
우회하지 않으며 새로운 CLI는 없다.

## 선택한 immutable profile

| 설정 | 값과 의미 |
| --- | --- |
| 조사·평가 대상 상한 / seed | dedup 후 최대 5 / 정수 42 |
| 최초 Company Research | 1회, controller handoff 설정 |
| unknown 추가 eligibility/research retry | 0, controller handoff 설정 |
| unknown 평가 / 보충 선정 | false / false, controller handoff 설정 |
| 허용 유료 호출 / 비용 USD | 0 / 0, controller handoff 설정 |
| 과거 paid ledger | `not_supplied_unverified`; 사용자 보고는 과거 usage=0 감사가 아님 |
| criterion covered | `actual_fact_verifier_approved_minimum_evidence` |
| 추가조사 종료 목표 | 기존 strict `missing_weight*100 < 30*applicable_weight` |

`RunProfile`은 frozen dataclass이고 정책 참조도 strict immutable tuple이다.
고정 설정은 이 **명시적으로 선택한 profile**의 값이지 기존 애플리케이션 설정의
변경이 아니다. bool/int/float 대체와 poisoned profile은 선정 전에 거절한다.
run/candidate ID는 이 profile에서만 printable ASCII, 공백 없는 원문 문자열을
요구한다. trim/lower/재발급하지 않는다. 기존 일반 Candidate DTO의 공백·Unicode
허용범위는 바꾸지 않는다. 권한/출처/정책 문자열도 nonblank strict 문자열로
보존한다.

D06에서 unknown reason/evidence를 보존해 archive/advance하고 collection/evaluation을
하지 않는 동작은 **후속 controller 책임**이다. 최초 research1은 provider의
내부 처리를 HTTP 1회로 제한한다는 의미가 아니다. `enforcement`는 항상
`controller_handoff_only`이며 paid0가 기존 모든 adapter 경로에 연결·강제됐다는
주장은 하지 않는다. 이 API는 adapter/research/evaluation callback을 받지 않으며
유료 ledger를 생성·reset·zero audit하지 않는다.

D05의 `criterion_support`는 승인 rubric의 실제 minimum_evidence와 관측 조건을
source bytes/법인·인물/기간/단위/시장/상충에 대한 **실제 사실 verifier**가
검증해야 한다는 설정이다. 이 모듈에는 semantic verifier도 가짜 `True` callback도
없다. covered 사실이 없으면 coverage 충분성을 주장할 수 없다. 기존 strict 30%
missing 비교만 목표로 전달한다. 최종 Market/Technology **평가 score ratio 40%**를
사전 support gate로 옮기지 않으며 Core N/A나 새로운 Finance N/A 규칙을 추가하지
않는다. 기존 승인 N/A·추가 Evidence 조사2·공유 보고서 수정2·최종 selector는
변경하지 않는다. 실제 verifier/runtime wiring과 별도 paid 허가는 #96 후속이다.

## 실제 normalization/selection 경계

```text
normalize_and_select(candidates, *, profile, execution_mode) -> CandidateSelection
replay_selection(receipt_json, candidates, *, profile, execution_mode) -> CandidateSelection
```

`execution_mode`는 필수 `fixture` 또는 `live` **DTO validation context**다.
`live` 문자열 자체는 실제 실행·출처·정책 admission 허가가 아니다. mutable
Candidate를 context에 맞게 재검증하고 복사한 후 기존 normalizer를 한 번 호출한다.
동명만으로 합치지 않는 기존 법인·country·legal identifier·homepage 비교,
발견 순서의 대표 ID, merge 이유와 source/alias 병합을 그대로 사용한다.
동일 ID를 다른 법인에 재사용한 입력은 기존 normalizer 오류다.

- dedup 결과 **0..5**: 기존 발견/정규화 순서로 전원 처리. 상한 callback도
  `random.Random` 생성·draw도 하지 않는다.
- dedup 결과 **6 이상**: 실제 `CandidateLimitPolicy` callback이 전체 dedup
  후보를 받는다. 원본 `candidate_id`의 ASCII 사전식 ASC 모집단에
  `random.Random(42).sample(population_ids, 5)`를 한 번 적용한다. 비복원 draw
  반환 순서가 처리 순서다. 전역 RNG 상태는 변경하지 않는다.
- 제외 IDs는 기존 normalizer의 발견/정규화 순서다. merged IDs는 제외 집합과
  별도로 기록한다. 선정은 적격성 판정 전에 한 번만 수행한다. 이후 unknown,
  ineligible, 실패를 보충하는 API는 없다. 최종 Best Candidate Selector에는 RNG를
  주입하지 않는다.

## Receipt와 replay

`SelectionReceipt`는 frozen이며 컬렉션은 중첩까지 tuple/`MergeReceipt`다.
`CandidateSelection.candidates`는 매 접근마다 새로운 검증된 DTO tuple을 반환한다.
원본/반환 DTO의 aliases·legal identifiers·source IDs 변경으로 receipt를 바꿀 수 없다.
receipt는 별도 JSON sidecar로 저장할 수 있지만 기존 manifest 결합은 후속 작업이다.

기록 필드:

- profile 전체(run ID, 출처/권한/정책 참조/고정 설정/선언 code version), validation context;
- 발견 원본 IDs와 순서 및 **각 원본 Candidate 전체 JSON**, 입력 SHA-256 fingerprint;
- 전체 dedup population의 발견/normalizer 순서 IDs·병합된 Candidate JSON,
  별도 ASCII ASC population IDs;
- selected IDs·Candidate JSON(**처리 순서**), excluded IDs, kept/merged IDs·merge 이유;
- 실제 `platform.python_implementation()`·전체 `sys.version`, algorithm 이름/버전,
  selection module와 기존 normalizer 소스 파일 SHA-256, receipt/profile version;
- 선언 code version이 없으면 `not_supplied_unverified`, 있으면
  `caller_declared_unverified` (호출자의 문자열을 검증된 repo HEAD로 승격하지 않음).

Candidate JSON은 schema/country/name/aliases/homepage/legal identifiers/
discovery source IDs를 모두 포함한다. UTF-8 JSON의 key는 정렬하고 구분자는 고정하며
list 순서·원문 텍스트는 유지한다. fingerprint는 이 **발견 순서의 전체 JSON tuple**에
대한 SHA-256이다. 동일 IDs여도 dedup에 영향을 주는 legal/homepage 또는 provenance가
달라지면 replay를 거절한다. Source payload/실제 source bytes의 의미 검증은 포함하지
않는다. 소스 파일 hash 역시 전체 dependency/실행 bytecode/semantic authority를
인증하는 서명이 아니다.

`replay_selection`에는 신뢰할 수 있는 **기대 profile과 원본 inputs**를 별도로 넘긴다.
receipt 내용을 기대값으로 자동 수용하지 않는다. 현재 Python/코드로 전체 normalization,
선정, immutable closure를 다시 계산하고 구조를 type-sensitive canonical JSON으로
비교한다. run/profile/input/population/normalization/merge/selection/exclusion/
interpreter/algorithm/code hash 변경, bool/int 대체, 누락·추가 필드 및 중복 JSON key는
거절한다. tuple은 JSON에서 array로 표현된다. 순수 JSON 공백/key 순서 차이는 허용한다.
raw receipt 생성자는 replay 검증이나 서명 인증을 대신하지 않는다.

관측된 offline runner는 **CPython 3.12.14**였다. 이는 전역·portable lock이 아니다.
다른 구현/전체 버전/소스 변경에는 새 receipt를 생성해야 한다. seed만으로 Python 버전
간 `sample` 호환성이나 실제 검색/모델 평가의 결정성을 보장하지 않는다.

## 직접 Python 예제 — synthetic, 외부 호출 없음

저장소의 uv 환경에서 다음 코드를 Python으로 실행한다. Candidate constructor는
현재 DTO shape를 사용한다. 가상 후보의 ID/source 참조는 실제 수집·출처 검증 증거가
아니며 BGE retrieval 또는 전체 live flow를 실행하는 예제가 아니다.

```python
from skala_rag.contracts import Candidate
from skala_rag.run_settings import (
    normalize_and_select,
    recommended_profile,
    replay_selection,
)

profile = recommended_profile(
    run_id="run-199-example",
    selection_source="recommended-run-settings-20261004-v1",
    authority_reference="user:2026-10-04:recommended-settings",
    policy_references=("v3-operational-1.0.0", "core-0.1.0", "finance-0.1.0"),
    code_version=None,  # caller has not supplied a verified repository version
)
originals = [
    Candidate(
        schema_version="synthetic-common-1",
        candidate_id=identifier,
        canonical_name=f"Synthetic {identifier}",
        aliases=[],
        country="KR",
        homepage_url=None,
        legal_identifiers={},
        discovery_source_ids=[f"src-{identifier}"],
    )
    for identifier in ("Z", "A", "c", "B", "e", "D")
]
result = normalize_and_select(originals, profile=profile, execution_mode="fixture")
receipt_json = result.receipt.to_json()
replayed = replay_selection(
    receipt_json, originals, profile=profile, execution_mode="fixture"
)
assert replayed.receipt == result.receipt
assert len(result.candidates) == 5
print(result.receipt.selected_ids)  # processing order; no eligibility/evaluation
```

## Offline 검증

```bash
uv sync --frozen --offline
uv run --no-sync --offline pytest -q tests/unit/test_run_settings.py tests/integration/test_run_settings.py tests/unit/test_discovery.py tests/unit/test_v3_coverage.py tests/unit/test_select_v3.py tests/unit/test_approved_scoring_consumers.py
uv run --no-sync --offline ruff check src/skala_rag/run_settings.py tests/unit/test_run_settings.py tests/integration/test_run_settings.py
uv run --no-sync --offline ruff format --check src/skala_rag/run_settings.py tests/unit/test_run_settings.py tests/integration/test_run_settings.py
```

신규 API 부재 RED와 실제 normalizer callthrough GREEN, 0/1/5/6+·dedup/callback bypass·
원본 ID·global RNG·immutable receipt·DTO poisoning·각 receipt 필드/semantic 입력/
interpreter/profile mismatch 회귀를 분리한다. 이는 offline synthetic normalization
증거이며 실제 기업 eligibility/semantic support/provider 호출 또는 전체 actual admission
검증 증거가 아니다. 전체 테스트/build와 별도 리뷰는 부모의 통합 gate에서 수행한다.
