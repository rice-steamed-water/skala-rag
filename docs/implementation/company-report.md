# 기업 보고서 - 단일 v2 Python API (#235)

## 호출과 결과

`run_company_report(company_name, *, config_path, output_dir, research=None, homepage_url=None, as_of=None, research_limits=None) -> Path`가 유일한 보고서 진입점이다. config/output은 `Path`, as_of는 `date`다. 출력 디렉터리가 이미 있으면 FileExistsError다. public authority/actual_admission/legal_identifiers/runtime_document 인자는 제거됐다.

```python
from datetime import date
from pathlib import Path
from skala_rag.company_report import run_company_report

out = run_company_report(
    "Dexory",
    config_path=Path("data/local/company-report-v2.json"),
    output_dir=Path("outputs/company-report-new"),
    research=False,
    as_of=date(2026, 10, 10),
)
```

반환은 영수증 디렉터리다. `run-result.json`의 `schema_version=company-report-2`를 읽는다.

| outcome | completeness / validation | 의미 |
| --- | --- | --- |
| completed | full / passed | 기업 원문이 있고 18질문 모두 answered 또는 근거 있는 N/A; 인용·Judge·PDF 통과 |
| partial | partial / passed | 기업 원문이 있고 일부 partial/unknown; 같은 검증 통과, 한계·확인 과제 명시 |
| empty | none / not_run | 허용된 로컬/승인 조사 기회 후 기업 근거 없음; 실질 본문/PDF 없음 |
| ambiguous | none / not_run | 대상이 하나로 특정되지 않음; 법적 identity를 추정하지 않음 |
| failed | none / not_run 또는 failed | provider/원문 무결성/모델/출력/검증 기술 실패; 성공 보고서 없음 |

잘못된 설정은 출력 전에 예외다. `company-report-1`은 Pydantic `ValidationError`의 `errors()[0]["ctx"]["error"].code`가 `COMPANY_REPORT_V1_MIGRATION_REQUIRED`인 거절이며 success enum을 재해석하지 않는다. `tests/fixtures/company-report.v1.json`은 거절 검증용 역사 fixture다.

## 설정과 준비 자산

[configs/company-report.v2.json](../../configs/company-report.v2.json)은 경로 템플릿이지 준비된 corpus/model이 아니다. 필수 필드는 schema_version/runtime_path/store_dir/model_path/model_receipt_path, 선택 필드는 issuer_receipt_path/acquisition_receipt_path/research_enabled/research_limits다. 상대 경로는 config 위치 기준으로 해석한다. scoring policy/catalog는 없다. runtime snapshot과 credentials는 runtime 소유다.

CompanyStore의 원본·chunk·검색 lineage와 진짜 로컬 오픈소스 임베딩 자산/revision/hash receipt가 필요하다. 검토한 issuer receipt는 `issuer-association-1`, subject_id/name/evidence_hashes를 저장한다. 원본 fingerprint와 대상에 결합된 private 검토이며 동명이름으로 다른 dataset 사실을 바꾸거나 법적 신원·적격성을 인증하지 않는다. stale/foreign/altered receipt는 거절한다.

## 분석과 인용

technology_product, market_customer, business_model, competition_moat, team_execution, finance_funding_deal 각 3질문, 총 18개다. answered/partial/unknown/not_applicable을 구별한다. partial/unknown에는 limitation과 verification_task, N/A에는 자료에 근거한 적용 제외 이유가 필요하다. 실제 0·통화·단위·기준일을 보존한다.

writer와 모든 feedback/Judge에는 original evidence ID와 source-neutral 내용만 전달한다. source ID/URL/경로/publisher/page/retrieval/provider/corpus/authority는 private citation registry에만 남는다. context_id는 대상·cutoff·질문·writer projection·registry hash에 결합된다. 인용은 검증된 원본/지원 ID를 정확한 Source/locator로 연결하며 생성 보고서를 독립 사실로 승격하지 않는다.

## 유한 조사와 수정

기본 research=false. 인자 research는 파일 값을 덮어쓰며 실행 전에 고정된다. true에는 max_calls/max_cost_usd/deadline_seconds가 필요하고 runtime 상한을 넘을 수 없다. 누락은 `RESEARCH_CAPS_REQUIRED`, 초과는 `RESEARCH_CAPS_EXCEED_RUNTIME`으로 출력 전 거절한다.

opt-in·homepage_url·한도는 수집 권한이 아니다. 현재 acquisition은 private reviewed receipt의 정확한 target/hash에 결합된 official-homepage만 허용한다. 임의 new web/경쟁사 수집을 승인하지 않는다. 권한 없음은 not_authorized, 실패는 acquisition_failed로 기록하고 빈 성공으로 바꾸지 않는다. optional 조사 실패는 기존 유효 근거를 보존한다.

정보 요청은 한 번, 최대 6개 need다. 제공은 질문 답변 완료를 뜻하지 않는다. need 없으면 첫 분석을 재사용하고 analysis/composition/Judge 3호출이다. 보강하면 final analysis를 한 번 추가한다. 구성·의미·layout 수정은 총 2회 공유하며 acquisition을 다시 열거나 누적 ledger/deadline을 초기화하지 않는다. 불확실한 외부 dispatch를 자동 재시도하지 않는다.

## 산출물

request.json/effective-config.json/provider-trace.json/writer-input.json/analysis.json/composition.json/judgment.json/references.json/pdf-validation.json/run-result.json 및 성공 보고서 report.md/report.pdf를 확인한다. 실패·empty·ambiguous는 validated PDF로 취급하지 않는다. run-result의 validation_receipt_hashes는 저장된 judgment/PDF validation/references에 결합된다. 개인정보/credential은 기록하지 않는다.

## 실행 가능한 controlled 로컬 예제

`examples/company_report_run.py`는 public API만 호출한다. 직접 실행에는 SKALA_COMPANY_REPORT_CONFIG/SKALA_COMPANY_REPORT_OUTPUT/SKALA_COMPANY_NAME을 설정한다. 예제 cutoff는 2026-10-10이고 research=false다. 실제 자산 설정이면 실제 모델 비용이 발생하므로 아래 검증은 별도 QA hook만 사용했다.

기존 test Harness를 쓰는 ignored QA hook `.omo/evidence/report-focused-consolidation/task15-hook/sitecustomize.py`는 정확히 합성 local encoder/HTTP transport를 준비한다. 네트워크 connect를 차단하고 fixture의 v2 config를 SKALA_COMPANY_REPORT_CONFIG에 넣는다. 생산 graph·DTO·인용·PDF는 대체하지 않는다. hook과 검증 증거는 개발 작업물이며 배포 API나 제품 fallback이 아니다.

```bash
PYTHONPATH="$PWD/.omo/evidence/report-focused-consolidation/task15-hook:$PWD" \
  uv run python examples/company_report_run.py
```

결과는 `outputs/report-focused-consolidation/task15-example`에 남는다. 새 출력 경로와 새 fixture 자산 경로를 위해 재실행 시 SKALA_COMPANY_REPORT_OUTPUT/SKALA_QA_EXAMPLE_ASSETS를 지정한다. 이 예제는 controlled partial/한글 PDF 연결 증거이며 실제 모델/기업 품질은 증명하지 않는다. malformed v1은 `task-15-migration.json`에 정확한 거절과 무출력으로 기록한다.

## 실제와 controlled의 경계

[실제 상태](actual-v3-status.md)를 따른다. 실제 Dexory는 저장 원문·BGE-M3·real analysis/composition/Judge, cutoff 2026-10-10, 수집 0회로 partial PDF를 생성했다. full real-company와 live 신규 조사 성공은 입증하지 않았다. 현재 문서/예제 검증은 paid call·download·source collection을 추가하지 않는다.

## Historical / 보존 이력 (현재 실행 안내 아님)

아래 전체는 통합 이전의 요구·승인·구현 snapshot이다. 아래의 "현재", CLI/scored/demo 명령과 링크는 당시 기준이며 퇴역한 파일의 실행을 안내하지 않는다. 현재 사용은 위 v2 안내를 따른다. 역사적 과제 요구나 승인 자체를 삭제·완료 처리하지 않는다.

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
