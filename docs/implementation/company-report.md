# 임의 기업 보고서 실행 (#233)

`skala_rag.company_report.run_company_report`는 기업명 하나로 저장 자료 기반 평가 보고서를 만든다. 결과는 항상 디렉터리이며, 거절도 `run-result.json` 영수증으로 남는다. 잘못된 설정·타입만 출력 전에 예외를 낸다.

## 호출 형태

```python
from pathlib import Path
from skala_rag.company_report import run_company_report

out = run_company_report(
    "Physical Intelligence",
    config_path=Path("configs/company-report.example.json"),
    output_dir=Path("outputs/company-report-pi"),  # 이미 있으면 FileExistsError
    # research=None, homepage_url=None, legal_identifiers=None, as_of=None,
    # research_limits=None, runtime_document=None,
    # actual_admission=None, authority=None,
)
```

`company_name` 외 인자는 모두 keyword-only다. Python 호출 예시는 [examples/company_report_run.py](../../examples/company_report_run.py)에 있다. CLI는 없다.

## 설정

[configs/company-report.example.json](../../configs/company-report.example.json)은 `schema_version: "company-report-1"`이며 `runtime_path`, `store_dir`, `model_path`, `model_receipt_path`, `policy_path`, `catalog_path`, `research_enabled`, `research_limits`를 가진다. 상대 경로는 설정 파일 위치 기준이다. 예시 파일은 `research_enabled: false`이고 `research_limits`가 없다.

인자 `research=True/False`는 파일 값을 덮어쓴다. 명시 `False`는 파일의 `true`보다 우선한다.

## 기본: 저장 자료만 사용

기본 실행은 외부 조사를 하지 않는다. `store_dir`에 보존된 자료와 로컬 BGE-M3 임베딩으로만 검색·평가한다. 모델은 `model_path`의 로컬 자산과 `model_receipt_path` 영수증으로 확인하며, 다운로드나 Ollama는 쓰지 않는다. 예시 설정의 `model_path`는 `../data/local/models/bge-m3`이므로, 로컬 자산을 다른 이름으로 둔 경우 설정 사본에서 경로를 맞춘다. 메인 checkout의 `data/local/models/bge-m3-5617a9f`는 revision `5617a9f61b028005a4858fdac845db406aefb181`과 일치함을 확인했다. 이 자산은 커밋하지 않는다.

## 명시적 대상 조사

`research=True`일 때만 대상 기업 하나를 조사한다. 이때 `research_limits`(`max_calls`, `max_cost_usd`, `deadline_seconds`)가 반드시 필요하다.

- 한도 누락: `research_blocked` / `RESEARCH_CAPS_REQUIRED` 영수증, 수집 없음.
- `configs/runtime.json`의 `actual_v3` 상한 초과: 설정 단계에서 `RESEARCH_CAPS_EXCEED_RUNTIME`.
- authority 허용치 초과: `RESEARCH_CAPS_EXCEED_AUTHORITY`.
- 조사 승인 callback 없음: `RESEARCH_ADMISSION_REQUIRED`.

한도는 요청값이지 승인이 아니다. 실제 수집은 신뢰된 authority와 공유 잔여 ledger에도 통과해야 한다. 경쟁사는 조사하지 않는다. 조사 전 저장소에 이미 있던 chunk만 경쟁 비교에 쓰며, 이 값은 실행 중에 바뀌지 않는다.

## 신원·적격성 결과

| `run-result.json` outcome | 의미 |
| --- | --- |
| `identity_unknown`, `identity_ambiguous` | 저장 자료로 기업을 하나로 특정하지 못함. 신원 조사는 `identity_research`와 `identity_review` authority가 모두 있어야 함(`IDENTITY_RESEARCH_AUTHORITY_REQUIRED`) |
| `ineligible` | 적격성 검사 불통과, 평가하지 않음 |
| `eligibility_unknown` | 판정 근거 부족. 조사가 꺼져 있으면 여기서 멈추고, 켜져 있으면 대상 조사 후 다시 검사하며 여전히 `eligible`이 아니면 멈춤 |
| `research_blocked` | 한도·authority·승인 부재 등으로 조사 또는 실제 실행 불가 |
| `failed` | 평가·검증 실패 |
| `warning`, `completed` | 보고서 생성. `completed`도 게시 승인은 아님 |

## 원본 lineage와 실제 실행 authority

보고서는 보존된 원본 evidence 참조를 그대로 재사용한다. 조사로 새 근거가 생기면 corpus·index 식별자가 바뀌며, 보고서 context는 조사 전 상태(`pre_research`)와 대상 chunk를 구분해 기록한다.

실제 평가는 `actual_admission`과 `authority`(`ActualAuthorityV3`)가 함께 필요하다. 없으면 `ACTUAL_AUTHORITY_REQUIRED`로 거절한다. 실행은 한 번 고정한 snapshot(`snapshot.json`)으로만 진행한다.

- `authority.evaluation_inputs_for(snapshot)`이 평가 입력을 공급한다.
- 검토(review)는 바로 그 snapshot에 묶여 인증된다. core·finance rubric 모두 동일 payload를 검증한 뒤에만 평가가 시작된다. 다른 snapshot이 들어오면 `EVALUATION_SNAPSHOT_MISMATCH`.
- 기업별 입력은 `authority.company_report`(`CompanyReportAuthorityV3`: `profile_for`, `research_for`, `identity_research`, `identity_review`, `readmit_after_research`)로 받는다. 파일에서 역직렬화하거나 추론하지 않는다.

실제 QA에서는 운영자가 소유한 외부 모듈의 `company_report_qa_authority.build_runtime(*, config_path: str, research: bool) -> dict`가 대상 실행용 `authority`와 `actual_admission` 두 객체를 돌려준다(계획 결정 17). 이 모듈은 저장소에 없고 제품 파일도 아니다. 문서는 그 내부나 승인 데이터를 정의하지 않으며, 승인 기록을 이 문서나 코드 기본값으로 대신 만들지 않는다. 모듈이 없거나 유효한 입력을 주지 못하면 실제 QA는 API 호출 없이 BLOCKED다.

조사 한도는 실행 하나에 대한 요청값이고, 실제 호출과 비용은 authority의 공유 잔여 ledger에 누적 차감된다. 검토 영수증은 snapshot별로 남고 같은 snapshot에서만 재사용된다.

## 검증 후 새 자료 반영 (task 9)

사용자가 승인한 “검증 후 새 자료 반영”은 운영자 Python callback
`authority.company_report.readmit_after_research(retained, snapshot)`으로 연결한다.
명시적 조사(신원 조사 포함)가 corpus/index를 바꾸면, 최종 보존 자료로 검색과
적격성 확인을 마친 뒤 평가 dispatch 전에 한 번 호출한다. 미래 corpus를
미리 계산하거나 원래 `ActualAdmissionV3`를 변경하지 않는다.

callback은 거절 시 `None`, 승인 시 `CompanyReportReadmissionV3`를 반환한다.
이 객체에는 `digest(retained.payload)`인 `retained_sha256`,
`digest(canonical(snapshot.model_dump(mode="json")))`인 `snapshot_sha256`,
독립 인증된 `sources`, `reviews_for`, `evaluation_inputs_for`만 들어간다.
`canonical`과 `digest`는 `graph.actual_inputs_v3`의 함수다. hash 일치만으로
승인하지 않으며, 공급한 원본 bytes와 core/finance rubric의 정확한 snapshot
review를 기존 verifier로 다시 검증한다. callback에는 분리 복사한 평가
snapshot을 주며, 이를 변경한 응답은 평가할 snapshot의 승인이 아니다.

검증 후 controller가 평가 단계 전용 admission을 만든다. 변경되는 값은
최종 corpus/index와 새 review resolver뿐이다. 원래 admission은 그대로
보존하고 **동일 runtime binding·누적 ledger·호출/비용 상한·deadline·기업·
cutoff·policy**를 유지한다. callback 응답은 새 예산이나 runtime을 받을 수
없고, callback/review 중 실행 context나 누적 사용량을 바꾸면 거절한다.
callback은 모델 출력이나 JSON에서 생성하지 않는 운영자 소유 코드여야 한다.
임의의 동기 Python callback을 sandbox하거나 강제 중단하는 기능은 아니다.

- callback 없음: 기존 `CORPUS_ADMISSION_MISMATCH` 거절 유지.
- 거절·잘못된 반환 타입: `READMISSION_DENIED`.
- 보존 자료 또는 평가 snapshot commitment 불일치: `READMISSION_SNAPSHOT_MISMATCH`.
- review callback 누락: `READMISSION_REVIEWS_REQUIRED`.
- 오래된 원본/review: 기존 source 또는 `SNAPSHOT_REVIEW_MISSING` 거절.
- 실행 context·예산·누적 사용량 변경: `READMISSION_RUNTIME_CHANGED`.

거절 시 evaluator는 호출하지 않으며 이미 적법하게 수집·보존한 자료는
남긴다. `readmission.json`은 성공한 단계 전환의 원래/최종 버전, commitment,
공유 binding/ledger 확인, review 전후 사용량과 상한을 기록한다.
`snapshot.json`, `reviews.json`, `ledger.json`, `run-result.json`과 함께
확인한다. 다음 실행의 보고서 재사용은 별도 실행의 적법한 admission으로
수행하며 이전 보고서를 독립적인 사실 근거로 승격하지 않는다.

## 검증 범위

- 통합 테스트(`tests/integration/test_company_report.py`)는 controlled fixture와 합성 API 응답을 쓴다. 실제 보고서 품질의 증거가 아니다.
- 새 사실 추출 → 정확한 재승인 → 검증 보고서 → 원본 lineage 재사용도 합성 응답 검증이며 실제 API QA를 대신하지 않는다.
- 로컬 임베딩은 실제 BGE-M3 자산으로 동작한다.
- 운영자 authority(`company_report_qa_authority`)와 `233-qa/company-report.json`이 없어 실제 조사·실제 보고서 QA는 실행하지 않았다.
