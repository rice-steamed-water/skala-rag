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
- 기업별 입력은 `authority.company_report`(`CompanyReportAuthorityV3`: `profile_for`, `research_for`, `identity_research`, `identity_review`)로 받는다. 파일에서 역직렬화하거나 추론하지 않는다.

실제 QA에서는 운영자가 소유한 외부 모듈의 `company_report_qa_authority.build_runtime(*, config_path: str, research: bool) -> dict`가 대상 실행용 `authority`와 `actual_admission` 두 객체를 돌려준다(계획 결정 17). 이 모듈은 저장소에 없고 제품 파일도 아니다. 문서는 그 내부나 승인 데이터를 정의하지 않으며, 승인 기록을 이 문서나 코드 기본값으로 대신 만들지 않는다. 모듈이 없거나 유효한 입력을 주지 못하면 실제 QA는 API 호출 없이 BLOCKED다.

조사 한도는 실행 하나에 대한 요청값이고, 실제 호출과 비용은 authority의 공유 잔여 ledger에 누적 차감된다. 검토 영수증은 snapshot별로 남고 같은 snapshot에서만 재사용된다.

## 미해결: 조사 후 같은 승인으로 보고서 완료 (task 9)

현재 admission 인터페이스는 **미리 승인된 정확한 최종 snapshot**만 받아들인다. 대상 조사로 실제로 새 사실이 추출되면 corpus가 바뀌고, 고정된 원래 admission은 그 새 snapshot을 인증하지 못해 보고서가 거절된다. 따라서 "예상하지 못한 새 사실을 조사한 뒤 보고서까지 완료"는 지금 보장되지 않는다. 승인 계약을 바꾸는 제안이 사용자 결정을 기다리는 중이며, 결정 전에는 이 경로를 완료로 표시하지 않는다.

## 검증 범위

- 통합 테스트(`tests/integration/test_company_report.py`)는 controlled fixture와 합성 API 응답을 쓴다. 실제 보고서 품질의 증거가 아니다.
- 로컬 임베딩은 실제 BGE-M3 자산으로 동작한다.
- 운영자 authority(`company_report_qa_authority`)와 `233-qa/company-report.json`이 없어 실제 조사·실제 보고서 QA는 실행하지 않았다.
