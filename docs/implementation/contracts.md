# 공통 데이터 계약

[문서 홈](../README.md) · [아키텍처](architecture.md) · [점수 정책](scoring.md)

근거: [v3](../design/design-v3.html) B-1, C-1–C-4, D-1–D-3, E 및 이전 원문 §7의 StageInfo. v3의 5 branch/6 dimension·수집 책임·점수·종료 목표를 구체화한 **DTO 필드와 검증 규칙은 구현 제안**이다. 현재 main에는 #5의 구조 DTO와 #7의 `InvestmentState`/`create_initial_state`가 있으며, 그 import·검증 범위와 v3 목표는 구별한다([정합화 기록](design-v3-alignment.md)). #3의 기록된 baseline 승인과 v3 대체안도 별개이므로, D04·D05·D08 등의 v3 세부 선택을 이 문서가 자동 승인하지 않는다.

**기존 baseline 승인 범위:** 다음 네 bullet은 2026-09-30 xxhigh가 승인한 D01–D06·D08의 요약이다([승인 기록](decisions.md#m0-승인-검토-기록--이슈-3)). 별도 대체 승인 전까지 baseline의 승인 상태는 유지된다. §1 이후의 v3 목표 계약, 특히 N/A 분모·Business & Deal·전체 후보 selector·Warning에 이 승인을 전이하지 않는다. DTO의 전체 필드나 모든 구현 선택을 승인한 것도 아니다.

- StageInfo/EligibilityResult: 직접 확인된 Seed~C만 단계 조건 통과. TIPS만으로 Seed 확정 금지, 명시적 프리시드·엔젤은 out_of_scope, 프리·브릿지는 직전 완료 라운드 근거로 판정. 추정/unknown만으로 eligible 처리 금지(D06).
- CriterionAssessment/ScoreSummary: 1..5 정수 rating 또는 null, 비중 `5/30/25/20/10/10`, 총 분모 100 고정. 상위 영역 관측 가중평균 rating ≤2와 최종 결측 비중 ≥30은 WATCHLIST(D01·D02·D05).
- EvaluationResult/InvestmentDecision: 다섯 병렬 평가 성공 후 동일 snapshot의 투자조건 직렬 평가까지 여섯 성공 결과를 집계. WATCHLIST/PASS는 다음 후보, 첫 RECOMMEND는 단일 기업 보고서(D03·D04).
- State count: 후보 최대 5개, 후보별 추가 조사 총 2회, 실행별 보고서 수정 총 2회. 구조·의미·layout 수정은 같은 예산을 공유한다. 도구 batch 호출 8회·추가 재시도 2회·시도별 30초이며 live 총시간·LLM 호출·비용 상한은 별도 승인 전까지 미정(D08).

## 1. 공통 규칙

- Graph 컨테이너는 원문대로 `InvestmentState(TypedDict, total=False)` 방향을 유지한다. 외부/LLM 경계의 DTO는 Pydantic으로 검증한다.
- 누락값은 `null`/`None`. 숫자 0, 빈 문자열, 추측한 값으로 대체하지 않는다. 0은 실제 관측값일 수 있다.
- 날짜는 ISO 8601, 시각은 시간대 포함. 발행일·사건일·수집일을 분리한다.
- 금액은 값·통화·단위·기준일을 함께 보존한다. KRW로 바꾸려면 별도 환율 근거와 변환식을 남긴다.
- DTO에 `schema_version`을 두고, 정책·모델·prompt 버전은 실행 manifest에 기록한다.
- JSON에 직렬화되지 않는 모델 객체, API client, DB connection, API key는 State에 넣지 않는다.
- `candidate_id`, `source_id`, `evidence_id`, `criterion_id`는 서로 다른 식별자다. 회사 이름 문자열로 join하지 않는다.

### #5 구조 DTO 구현 범위 — 정책 승인이 아님

`skala_rag.contracts`에서 §§2–4의 `RunInput`, `Candidate`, `StageInfo`,
`CompanyProfile`, `EligibilityResult`, `Source`, `Chunk`, `Evidence`,
`EvidenceProvenance`, `DiscoveryBundle`, `RetrievalRequest`, `RetrievalBundle`,
`RetrievalRecord`, `ResearchGap`, `CoverageResult`와 보조 `MonetaryObservation`을
import할 수 있다. 이는 이 문서의 **구현 제안 중 #5가 제공한 구조 검증 범위**다. #3의
D01–D06·D08 baseline은 기록상 승인되었지만, v3가 바꾸는 selector·N/A/Warning 등
세부 선택은 별도 `v3-OPEN`으로 남아 있다. D09 후속 부분 승인을 포함한 정책 승인
상태는 [결정 목록](decisions.md)을 따른다. `eligible`, `research_ready`, 단계·bucket·confidence·status는
호출자가 공급한 관측을 저장할 뿐, 서로를 계산하거나 정당화하지 않는다.
`execution_mode="live"`의 schema 통과도 정책·예산·도구 readiness 승인과 무관하다.
State, reducer, stable ID 생성, snapshot/controller 참조 검증, 평가·점수·보고서·
manifest, 수집·환율·근거 병합 함수는 #5 구현에 포함하지 않는다. State와 초기화는 별도로 병합된 #7이 제공한다. 이후 #15의 reducer/State 연결, #16 baseline 집계·판정, #53 재무 helper 추가 범위는 §7과 정합화 기록을 따른다.

**타입과 결측**

- 모든 DTO와 중첩 DTO의 `schema_version`은 호출자가 명시하는 필수 nonblank
  문자열이다. 기본 버전은 없고 미지 필드는 거절한다. 필수 텍스트·ID·컬렉션
  안의 ID는 strict 문자열이며 공백만 있는 값과 문자열 아닌 값은
  `pydantic.ValidationError`다. 유효한 문자열의 앞뒤 공백·대소문자를 정규화하지
  않는다. optional 텍스트는 `None` 또는 nonblank 문자열이다.
- `?` 관측과 `Evidence`의 수치·맥락, `Source.published_at`, profile의 세 boolean,
  `ResearchGap.priority_weight`, Coverage의 `missing_weight/coverage_pct/research_ready`는
  생략 시 `None`이다. 관측 boolean은 strict `true/false/null`; 문자열·정수에서
  변환하지 않는다. 필수 list/map은 호출자가 명시하며 빈 list/map은 허용한다.
- 수치는 strict 정수 또는 유한 실수다. 실제 0과 음수 측정은 유지한다.
  `evidence_revision`, `top_k`, `page_start/page_end`는 0 이상 strict 정수,
  `priority_weight/missing_weight`는 알려진 경우 0 이상,
  `coverage_pct`는 알려진 경우 0..100이다. weight 단위·합·threshold·priority·
  page 구간 순서·시작/종료 시각 순서·정책 관계는 추론하거나 강제하지 않는다.
  문서의 literal enum만 허용하고 enum 간 round/bucket/eligibility 관계를 만들지 않는다.
- `as_of`, `last_round_date`, `event_date`, 금액 기준일은 Python `date` 또는 정확한
  `YYYY-MM-DD` 문자열이다. timestamp·epoch 숫자를 date로 바꾸지 않는다.
  `retrieved_at/started_at/finished_at`은 timezone-aware `datetime` 또는 `T`와
  시간대를 포함한 ISO timestamp다. naive timestamp·epoch 숫자/숫자 문자열은
  거절한다. `Source.published_at`은 `date | aware datetime | None`: 알려진
  날짜만 있으면 date로 보존하고 임의 자정·시간대·수집시각을 발행시각으로 넣지 않는다.
  JSON roundtrip은 date/date-time 구분과 실제 offset을 보존한다.

**금액의 손실 없는 맥락**

- `MonetaryObservation`은 `schema_version`, `value`, `currency`, `unit`, `as_of`를
  모두 필수로 가진다. `value`는 유한 수치이며 통화·단위는 관측 그대로의 nonblank
  문자열이다. 임의 ISO 통화 목록이나 환산 규칙은 도입하지 않는다.
- 기존 이름 `StageInfo.cumulative_funding_krw`는 유지하되 타입을
  `MonetaryObservation | None`으로 구체화했다. 이 필드의 currency는 이름과
  일치하는 정확한 `KRW`여야 한다. KRW payload 검증은 환율 변환의 정확성 증명이
  아니다. 맥락 없는 scalar를 받아 기준일·단위·환율을 보충하지 않는다.
  `RetrievalRecord.cost`도 같은 neutral observation 또는 `None`이다.
- `Evidence.value/unit/currency`의 원래 이름을 보존한다. **추가 optional 필드
  `value_as_of: date | None`**는 금액 자체의 기준일이다. currency를 공급하면
  value·unit·value_as_of가 모두 필요하고, value_as_of를 공급하면 currency도
  필요하다. currency가 없는 수치는 비금액 관측으로 보존하며 통화를 추정하지
  않는다. 금액 미상은 관련 필드를 모두 `None`으로 전달한다. event_date·period·
  published_at·retrieved_at로 금액 기준일을 대신하지 않는다. `value_as_of`는
  금액 해석의 식별 core에 추가되는 맥락이며, 이후 merger 구현에서도 차이를
  조용히 제거하면 안 된다. 이 DTO는 계산·환율 provenance를 생성하지 않는다.

**컬렉션과 별도 필드**

| 필드 묶음 | 구현 shape |
| --- | --- |
| countries, languages, aliases, 각종 `*_ids`, reason_codes, missing_fields, suggested_queries, limitations, conflicts_with | `list[nonblank str]` |
| Candidate.legal_identifiers | `dict[nonblank str, nonblank str]`: 관측 식별체계 → 원래 식별자 |
| CompanyProfile.field_evidence_ids | `dict[nonblank str, list[nonblank str]]`: 필드 → Evidence ID 목록 |
| Source.bibliographic_metadata, EligibilityResult.checks, RetrievalRecord.arguments_without_secrets | `dict[nonblank str, JSON value]`; nested JSON key도 문자열 |
| Evidence.provenance | 실제 `EvidenceProvenance` payload의 list |
| DiscoveryBundle.sources, RetrievalBundle.sources | `dict[nonblank source_id, Source]`: ID가 아닌 실제 payload |
| CoverageResult.unresolved_conflicts | 충돌 관련 Evidence ID의 `list[nonblank str]`; 정책 판정 없음 |

JSON value는 null/boolean/string/유한 number/list/string-keyed object만 허용한다.
Python client·connection·date·tuple·set·비유한 nested 수치는 metadata로 넣을 수
없다. 이 검증은 비밀 탐지나 redaction 기능이 아니므로 호출자는 비밀을 제거한
arguments만 전달해야 한다. fixture에는 credentials가 없다.
`RetrievalRecord.query`는 별도의 optional nonblank 문자열,
`arguments_without_secrets`는 별도의 필수 JSON map이다. 검색문 없는 API 기록도
query를 `None`으로 보존하며 검색문을 arguments에서 만들어 넣지 않는다.
`Evidence.derivation`은 optional nonblank 설명 문자열이고 `supersedes`는
optional 이전 Evidence ID다. `Source.publisher`와 `author`는 별도의 optional
nonblank 문자열이며 알려지지 않은 저자/기관은 생성하지 않는다.
`ResearchGap.criterion_id`와 `eligibility_field`는 별도의 optional nonblank
문자열로 구현하고 정확히 하나만 공급한다. eligibility 필드 catalog·우선순위·
재시도 횟수는 결정하지 않는다.

**출처·locator·중첩 검증**

- Source에는 nonblank `url` 또는 `local_path`가 하나 이상 필요하다. 둘 다
  허용하며 optional locator를 빈 문자열로 표시하면 거절한다. locator는 원문
  문자열을 유지한다. URL 접근·파일 읽기·domain allowlist·content_hash 실제
  계산/검증·embedding 선택은 하지 않는다. Chunk의 embedding 필드는 호출자가
  기록한 nonblank 이름/revision이며 product 기본값이 아니다.
- `fixture://`는 Source url/local_path, Chunk/Evidence locator,
  Candidate.homepage_url에서 명시적 `context={"execution_mode": "fixture"}`일
  때만 허용한다. validation context는 DTO 필드나 기본 실행모드가 아니다.
  `Model.model_validate(payload, context=...)`와
  `Model.model_validate_json(json, context=...)`를 사용하면 Pydantic이 nested
  Source/Chunk/Evidence 검증에도 같은 context를 전달한다. context 없는 생성이나
  live context는 fixture URI를 거절한다. context 허용은 live 출처의 승인 증거가 아니다.
- nested DTO instance도 `revalidate_instances="always"`로 다시 검증하므로 이전
  fixture context에서 만든 Source나 수정된 instance가 현재 검증을 우회하지 않는다.
  DTO는 immutable snapshot이 아니며 schema 검증 후의 무결성·freeze는 controller 책임이다.
- RAG provenance는 nonblank chunk_id가 필수다. web/api/manual의 optional
  chunk_id는 그대로 유지하며 자동 생성하지 않는다. 실제 반환 record/source/locator
  일치 및 RAG 사용 증명은 #21/controller의 snapshot 범위다.
- DiscoveryBundle은 모든 candidate.discovery_source_ids의 Source payload를,
  RetrievalBundle은 모든 chunk.source_id의 Source payload를 포함해야 한다.
  sources map key와 Source.source_id가 일치해야 하며 존재하는 문자열만으로
  closure를 충족할 수 없다. 빈 성공 bundle과 참조되지 않은 유효한 Source는
  허용한다. 후보 동일성·출처 승인·검색 cutoff 등은 schema 밖의 책임이다.

가상 예제는 `tests/fixtures/contracts.json`, 구조·거절·JSON roundtrip 검증은
`tests/contract/`에 있다.

**v3 B-2 metadata filter 확장 제안 — 현재 #5 DTO에는 없음:** `Source.source_kind`는
기존 broad kind이고 `bibliographic_metadata`는 확보한 서지 JSON을 보존할 뿐이다. v3의
문서 우선순위는 이를 중복한 Evidence `source_type`으로 만들지 않고, Source에서 유도할
`document_class`(예: official_technical/ir_or_pitch/product_material/paper/public_or_market/news_or_interview)와
그 근거를 `bibliographic_metadata`의 versioned shape로 추가하는 안이다. `Chunk`에는
`document_class`, `year`, `slide_no`, `patent_no`/`ipc`/`claim_no` 같은 source-derived metadata를
복사·참조하는 shape, `RetrievalRequest`에는 `document_classes?`, `published_years?`의 허용 filter를
추가하는 안을 검토한다. unknown은 추정 class나 year를 만들지 않는다. priority는 1→2→3의
검색 fallback 정책이며 hard exclusion은 승인된 request filter만 적용한다. 현재 DTO는 `extra=forbid`라
새 필드를 받지 않으며, schema 확장과 T12 fixture가 선행되기 전 metadata filter를 구현됐다고 말하지 않는다. `fixture.invalid`/`fixture://`, synthetic hash/model/version은
실제 수집·hash 계산·모델 선택·정책 승인 또는 실측 결과가 아니다. 아래 §3의
JSON 설명 예제는 명시적 fixture validation context로 검증한다.

### #6 평가 이후 DTO 구현 범위 — 구조 계약

**현재 baseline shape와 v3 제안의 경계:** PR #37의 DTO는 dimension 기반 `EvaluationResult.evaluation` 단건과 observed/missing, 세 label을 검증한다. 아래 §4의 `branch_id`·`evaluations` 복합 결과, `not_applicable`, 적용가능 분모·정규화 점수, 네 label 및 §5의 `ReportInput.selection_result`는 별도 v3 확장 제안이며 현재 DTO에 공급할 수 없다. `SelectionResult`·`RunResult` 역시 현재 public DTO가 아니다. 같은 이름의 아래 목표 표를 현재 생성자 인자로 복사하지 않는다.

`skala_rag.contracts`에서 `CriterionAssessment`, `EvaluationSnapshot`, `Evaluation`,
`EvaluationResult`, `ScoreSummary`, `InvestmentDecision`, `CandidateOutcome`,
`ReportInput`, `ReportContext`, `ReportDraft`, `ValidationResult`, `ReportJudgement`,
`WorkflowError`, `RunManifest`를 import할 수 있다. 보조 payload인
`ValidationErrorDetail`, `ReportFinding`, `ArtifactMetadata`도 같은 public API로 제공한다.
모든 중첩 DTO는 #5의 `Contract`를 사용하여 schema_version을 명시하고, strict
nonblank 문자열·유한 수치·aware timestamp·ISO date·미지 필드 거절·instance
재검증·명시적 fixture context 규칙을 공유한다. 앞뒤 공백은 보존한다.

이 구조 검증은 정책 승인, 실제 근거 유효성 또는 snapshot 동결의 증명이 아니다.
criterion catalog 완전성·중복/영역 귀속·근거 존재/품질은 #22 wrapper,
Source/Chunk/검색 provenance 폐쇄성·as_of·정정 처리·불변 freeze는 #21,
최종 세대·판정·점수 참조를 검증하는 context 조립은 #26의 책임이다.
DTO는 변경 가능한 경계 객체이므로 controller가 검증된 복사본을 고정하여
운영해야 한다. DTO 자체는 정책·점수·보류 threshold·비용 예산을 생성하지 않는다.

ScoreSummary의 수치는 #16의 Decimal 계산 결과를 손실 없이 보존한다.
int·float·Decimal 또는 명시적 유한 decimal 문자열을 받아 Decimal로 저장한다.
bool·객체·NaN/Infinity·공백/underscore가 들어간 문자열은 거절한다. JSON/State
payload에서는 Decimal을 문자열로 직렬화하고, model_validate/model_validate_json
모두 해당 문자열을 정확히 복원한다. rating의 strict 정수 규칙과 #5 수치 타입은
변경하지 않는다. 합산과 표시 반올림은 여전히 #16의 책임이다.

| 필드 묶음 | #6 구현 shape / 구조 검증 |
| --- | --- |
| Evaluation.dimension, ScoreSummary dimension map/list | `founder/market/technology/moat/traction/deal_terms` |
| evaluation_round, evidence_revision, revision, attempt | 0 이상 strict 정수; 실행 세대·재시도 증가는 controller 담당 |
| EvaluationSnapshot payload maps | `dict[ID, 실제 DTO]`; map key와 payload ID 일치, evidence_ids는 중복 없이 evidence key 집합과 일치 |
| Evaluation.criteria, research_gaps | `list[CriterionAssessment]`, `list[ResearchGap]`; catalog 완전성이나 조사 우선순위는 계산하지 않음 |
| EvaluationResult | success는 evaluation 필수·errors 빈 list, failure는 evaluation=null·errors 하나 이상; run/candidate/dimension/round/snapshot/revision/policy가 중첩 Evaluation과 일치; 오류의 run 및 알려진 candidate 일치 |
| ScoreSummary.criterion_points | `dict[criterion_id, 유한 비음수 수치 또는 null]`; missing 기여를 0으로 바꾸지 않음 |
| ScoreSummary.dimension_ratings | `dict[dimension, 유한 1..5 수치 또는 null]`; 영역 평균은 소수 허용 |
| observed_score, missing_weight, coverage_pct | 앞의 두 값은 유한 비음수 수치, coverage는 0..100; 합산·분모·반올림·label은 별도 정책 계산 |
| InvestmentDecision.report_grade, ReportFinding.severity | 호출자가 공급한 nonblank 문자열; DTO가 grade나 severity를 판단하거나 생성하지 않음 |
| CandidateOutcome.status | 종료 상태 `ineligible/eligibility_unknown/recommend/watchlist/pass/failed/not_evaluated`; optional 결과 ID와 failure_ids의 해소는 controller 담당 |
| ReportInput.candidate_outcomes | `list[CandidateOutcome]`, candidate_id 중복 거절; single_candidate는 해당 outcome의 selected ID 필수, no_recommendation은 selected=null |
| ReportContext | 실제 DTO map; 각각 payload ID와 key 일치, evaluations key는 공통 evaluation_key와 일치, permitted_evidence_ids와 evidence key 집합 일치; 내용 조립/세대 참조는 #26 |
| ReportDraft.cited_evidence_ids, reference_source_ids | 중복 없는 nonblank ID list; 실제 Markdown 인용/REFERENCE와의 집합 대조는 #27 ([D09 계약](reporting.md#정확한-집합-관계)) |
| ValidationResult | checks는 JSON map, errors는 `list[ValidationErrorDetail]`; valid=true는 errors 빈 list, false는 오류 하나 이상 |
| ReportJudgement | findings는 `list[ReportFinding]`, revision_instructions는 nonblank 문자열 list; verdict는 pass/revise/fail. 의미·severity에 따른 verdict 판단은 Judge 담당 |

**RunManifest의 구체적 필드:** 필수 `run_id`, `run_input: RunInput`,
`uncommitted: strict bool`, `policy_version`, `corpus_version`, `prompt_versions`,
`model_versions`, `corpus_hash`, `tool_status`, `budgets`, `usage`, `artifacts`,
`validation_results`, `workflow_status`를 가진다. `code_revision`과 `run_outcome`은
optional이며 미상 값은 null이다. policy/corpus version은 run_input과 일치해야
한다. code_revision이 없으면 uncommitted=true가 필요하다. running은
run_outcome=null, completed/failed는 명시적 RunOutcome을 요구한다.
workflow_status/RunOutcome 값은 #7 State enum을 재사용한다.

- prompt_versions/model_versions는 `dict[nonblank 이름, nonblank 버전]`이다.
  실행에 사용하지 않은 모델/prompt는 빈 map으로 명시할 수 있다.
- tool_status/budgets/usage는 JSON map이다. 도구 상태·예산 단위·실측 값은
  runner가 공급하며 DTO가 readiness나 예산 준수를 판단하지 않는다.
- artifacts는 `dict[산출물 이름, ArtifactMetadata]`이고 payload 필드는
  schema_version·artifact_path·artifact_hash다. 파일 읽기나 실제 hash 계산은
  runner의 책임이다. synthetic fixture의 hash는 실제 측정 hash가 아니다.
- validation_results는 `dict[검증 이름, ValidationResult | ReportJudgement]`다.
  같은 context/실제 artifact hash인지의 대조는 보고서 controller 담당이다.
- WorkflowError.message_redacted는 호출자가 비밀을 제거한 nonblank 문자열이다.
  DTO schema 검증은 자동 redaction이나 비밀 탐지가 아니다.

`tests/fixtures/evaluation_contracts.json`은 모두 가상 구조 예제다. 정상·거절·
공통 회귀·JSON 왕복은 `tests/contract/`에서 검증한다. ID 인코딩은 아래 §4의
규칙과 `contracts.ids`를 사용한다. DTO는 ID를 자동 생성하거나 재계산하지 않는다.

## 2. 입력과 후보

| DTO | 필드 계약 | 검증 |
| --- | --- | --- |
| `RunInput` | investment_theme, countries, languages, as_of, policy_version, corpus_version, execution_mode (`fixture/live`) | 공백 주제 거절; live는 승인 정책·예산·필수 도구 readiness 확인 |
| `Candidate` | candidate_id, canonical_name, aliases, country, homepage_url?, legal_identifiers, discovery_source_ids | 동명 기업을 도메인·법인 식별자·국가로 구별; 병합 근거 기록 |
| `StageInfo` | raw_label?, normalized_round, bucket (`early/late/unknown`), last_round_date?, cumulative_funding_krw?, method (`explicit/estimated/unknown`), source_ids, confidence (`high/medium/low/unknown`), rationale | `normalized_round`: `seed/series_a/series_b/series_c/out_of_scope/unknown`; 추정값을 explicit로 승격 금지 |
| `CompanyProfile` | candidate_id, domain_match?, is_listed?, exit_completed?, stage, as_of, field_evidence_ids | boolean 세 값: true/false/null. null을 false로 변환하지 않음 |
| `EligibilityResult` | eligibility_result_id, run_id, candidate_id, evidence_revision, policy_version, as_of, status (`eligible/ineligible/unknown`), checks, reason_codes, evidence_ids | 모든 필수 조건 근거 확인 시 eligible; 하나라도 명백히 불일치하면 ineligible; 나머지는 unknown |

`last_round_date`는 가능하면 실제 라운드 사건일이다. 최신 게시물이 과거 라운드를 회고한 것인지 구별하고, 사건일 미상은 `null`로 둔다. `source_ids`는 Source 테이블 참조, `field_evidence_ids`는 Evidence 참조다.

## 3. Source → Chunk → Evidence

### Source와 Chunk

| DTO | 최소 필드 |
| --- | --- |
| `Source` | source_id, title, publisher/author, source_kind (`web/report/paper/filing/patent`), url 또는 local_path, published_at?, retrieved_at, content_hash, language, access_notes, bibliographic_metadata |
| `Chunk` | chunk_id, source_id, corpus_version, text, page_start?, page_end?, section?, locator, candidate_ids, scope (`company/industry`), language, embedding_model, embedding_revision |

Source의 bibliographic metadata에는 논문 학술지·권호·페이지 등 확보한 값만 넣는다. `content_hash`는 실제 내용에서 계산한다. 정정된 원문은 새 Source snapshot으로 관리한다.

### Evidence

한 건은 **검증 가능한 한 주장** 또는 하나의 수치 관측이다. 단순 검색 결과 URL은 Evidence가 아니다.

| 필드 | 타입 / 의미 |
| --- | --- |
| `evidence_id` | source snapshot + locator + 정규화 claim·주체·값 등 불변 core 기준 stable ID. 수집 경로는 ID에 포함하지 않음 |
| `candidate_id` | 회사 근거일 때 ID, 산업 공통 근거는 null |
| `scope` | `company` / `industry`; 산업 근거를 기업의 실적으로 인용하지 않음 |
| `criterion_ids` | 이 근거가 지원할 수 있는 세부항목 ID 목록 |
| `claim` | 원문을 벗어나지 않은 주장 |
| `value`, `unit`, `currency` | 숫자가 있을 때만 사용. 값 미상은 null. currency를 공급하면 value·unit·value_as_of가 모두 필요 |
| `value_as_of` | optional ISO 날짜 (`YYYY-MM-DD`); 금액 자체의 기준일. 미상은 null. 공급하면 currency도 필요하며 event_date·period·published_at·retrieved_at로 대신하지 않음 |
| `period`, `geography`, `event_date` | 지표 해석에 필요한 맥락; 없는 필드는 null |
| `source_id` | 원문 Source snapshot ID 필수 |
| `locator`, `excerpt` | 원문으로 되돌아갈 위치와 필요한 최소 발췌 |
| `provenance` | `EvidenceProvenance[]`; 해당 주장을 실제로 얻은 수집 경로 목록 |
| `evidence_kind` | `reported/derived/estimated` |
| `confidence`, `limitations` | 해석의 확실성과 한계. retrieval similarity와 별개 |
| `supporting_evidence_ids`, `derivation` | derived/estimated의 입력 근거와 계산식·가정 |
| `conflicts_with`, `supersedes` | 충돌·정정 관계; 기존 사실을 조용히 덮어쓰지 않음 |

기업의 자기 주장도 `reported`일 수 있지만, 이를 독립 검증 결과로 서술해서는 안 된다. 출처 성격과 검증 수준은 따로 기록한다.

| DTO | 필드 / 제약 |
| --- | --- |
| `EvidenceProvenance` | retrieval_id, method (`web/api/rag/manual`), chunk_id?; rag는 실제 반환된 chunk_id 필수, 그 Chunk의 source_id와 원문 위치가 Evidence에 부합해야 함 |

**중복 병합 계약:** 필드를 세 종류로 나눈다.

| 종류 | 필드 | 동일 evidence_id 재발견 시 |
| --- | --- | --- |
| 식별 core | source_id, locator, 정규화 claim, candidate_id, scope, value, unit, currency, value_as_of, period, geography, event_date, evidence_kind, supporting_evidence_ids, derivation, supersedes | 모두 같아야 함. 다르면 ID 충돌 오류 |
| 수집 경로 | provenance, excerpt | provenance는 `(retrieval_id, method, chunk_id)` key 집합 합. excerpt는 같은 locator의 원문 발췌여야 하며 최초 값 유지 |
| 해석 | criterion_ids, conflicts_with, confidence, limitations | criterion_ids·conflicts_with·limitations는 합집합, confidence는 더 낮은 값. 값이 바뀌면 evidence_revision 증가 |

같은 경로의 재삽입은 idempotent다. 동일 경로 key에 다른 chunk/내용이 오거나 식별 core가 다르면 오류다. Web과 RAG가 같은 주장을 서로 다르게 해석했다는 이유만으로 정상 재발견을 실패시키지 않는다. 의도적인 내용 정정은 새 evidence_id와 supersedes 관계로 기록한다. source snapshot/원문 위치 대응을 검증할 수 없는 Web·RAG 결과는 억지로 동일 Evidence로 합치지 않는다.

Source 중복은 source_id·content_hash·서지 core가 같을 때만 허용한다. `retrieved_at`은 해당 snapshot의 최초 수집 시각으로 가장 이른 값을 유지하고, 각각의 새 수집 시각은 RetrievalRecord에 남긴다. 다른 Source core는 조용히 덮어쓰지 않는다.

Web으로 먼저 얻은 근거를 RAG로 재검색해도 근거는 하나이고 경로는 둘이다. RAG 사용 증명은 rag provenance의 retrieval_id/chunk_id → 실제 검색 결과 → 해당 근거를 포함한 평가 snapshot → 평가/보고서 인용으로 확인한다. 이 trace가 없는 기존 Web 근거에 method만 rag로 바꾸는 것은 허용하지 않는다.

### 구조 예시 — 가상 테스트 데이터, 실제 기업 자료 아님

아래 예시는 JSON 형태 설명용이며 실사용 근거가 아니다. `fixture://` 주소는 fixture 모드에서만 허용한다.

```json
{
  "schema_version": "draft-2",
  "evidence_id": "ev-fixture-001",
  "candidate_id": "co-fixture-001",
  "scope": "company",
  "criterion_ids": ["technology.integration"],
  "claim": "가상 문서는 센서, 제어 소프트웨어, 로봇 하드웨어 연결 구조를 설명한다.",
  "value": null,
  "unit": null,
  "currency": null,
  "period": null,
  "geography": null,
  "event_date": null,
  "source_id": "src-fixture-001",
  "locator": "fixture://robotics-whitepaper#page=3",
  "excerpt": "센서 → 제어 소프트웨어 → 로봇 하드웨어",
  "provenance": [
    {
      "schema_version": "draft-2",
      "retrieval_id": "retrieval-fixture-001",
      "method": "rag",
      "chunk_id": "chunk-fixture-001-p3"
    }
  ],
  "evidence_kind": "reported",
  "confidence": "medium",
  "limitations": ["가상 데이터이며 실제 성능 검증이 아니다."],
  "supporting_evidence_ids": [],
  "derivation": null,
  "conflicts_with": [],
  "supersedes": null
}
```

## 4. 수집·Coverage·평가

| DTO | 최소 필드 / 규칙 |
| --- | --- |
| `DiscoveryBundle` | candidates (`Candidate[]`), sources (`dict[source_id, Source]`); 모든 discovery_source_ids를 해소하는 payload 포함 |
| `RetrievalRequest` | query, candidate_id, corpus_version, index_version, as_of, top_k, allowed_source_ids; collector가 RunInput·승인 manifest에서 생성 |
| `RetrievalBundle` | chunks (`Chunk[]`), sources (`dict[source_id, Source]`); 반환된 Chunk의 모든 Source payload 포함 |
| `RetrievalRecord` | retrieval_id, run_id, candidate_id?, tool_name, query/arguments_without_secrets, started_at, finished_at, status (`ok/empty/unavailable/failed`), source_ids, chunk_ids, evidence_ids, error_id?, cost?, cache_hit |
| `ResearchGap` | gap_id, candidate_id, criterion_id 또는 eligibility_field, missing_fields, reason, priority_weight, suggested_queries, attempted_retrieval_ids, status (`open/resolved/exhausted`) |
| `CoverageResult` | candidate_id, evidence_revision, policy_version, covered_criterion_ids, missing_criterion_ids, not_applicable_criterion_ids, applicability_assessments, applicable_weight, not_applicable_weight, missing_weight, weighted_missing_pct?, coverage_pct?, research_ready?, unresolved_conflicts; 0분모 guard는 D05 |
| `CriterionAssessment` | criterion_id, status (`observed/missing/not_applicable`), rating (`1..5` 정수 또는 null), evidence_ids, rationale, missing_reason?, applicability_reason?, applicability_rule_id?, applicability_note? |
| `EvaluationSnapshot` | snapshot_id, run_id, candidate_id, evaluation_round, evidence_revision, policy_version, corpus_version, index_version, as_of, evidence_ids, evidence (`dict[evidence_id, Evidence]`), sources (`dict[source_id, Source]`), chunks (`dict[chunk_id, Chunk]`), retrieval_records (`dict[retrieval_id, RetrievalRecord]`) |
| `Evaluation` | run_id, candidate_id, dimension, evaluation_round, snapshot_id, evidence_revision, policy_version, rubric_version, criteria, research_gaps, caveats |
| `EvaluationResult` | run_id, candidate_id, branch_id, evaluation_round, snapshot_id, evidence_revision, policy_version, status (`success/failure`), evaluations (`dict[dimension, Evaluation]` 또는 null), errors (`WorkflowError[]`); branch terminal envelope |
| `ScoreSummary` | score_summary_id, run_id, candidate_id, evaluation_round, snapshot_id, evidence_revision, policy_version, criterion_points, dimension_scores, observed_score, applicable_weight, not_applicable_weight, missing_weight, normalized_score?, weighted_missing_pct?, coverage_pct?, low_score_dimensions, hold_reasons |
| `InvestmentDecision` | decision_id, run_id, candidate_id, label (`RECOMMEND_PRIORITY/RECOMMEND/WATCHLIST/PASS`), report_grade, score_summary_id, reason_codes, evidence_ids, rationale, risks, limitations |
| `SelectionResult` | run_id, policy_version, considered_candidate_ids, selected_candidate_id?, reason_codes, rationale, compared_score_summary_ids; selector 전용 결과, 순위·동점·전부 비추천 처리 규칙은 D03 OPEN |

`research_ready`는 평가 전 조사 충분성 정책이며 Eligibility의 최소 Evidence와 같지 않다(D05·D06). covered/missing/not_applicable ID는 catalog를 중복 없이 분할한다. applicability_assessments는 criterion_id별 applicability_reason·applicability_rule_id·evidence_ids의 map 제안으로 N/A의 근거를 해소한다. Coverage의 covered도 최종 observed를 보장하지 않는다. 최종 ScoreSummary의 분모·결측률은 최종 평가에서 다시 계산하며 사전 Coverage는 별도로 보존한다.

`dimension_scores[d]` 제안은 observed_score, applicable_weight, missing_weight, not_applicable_weight, dimension_score_pct?를 담는다. 원배점 missing_weight와 정규화 weighted_missing_pct는 다른 수다. Missing은 분모에 남고 not_applicable만 제외한다. 핵심 보류 대상 low_score_dimensions에는 market/technology만 들어간다. 분모 0의 nullable 숫자는 값 미정의 표시이지 자동 보류/통과 승인 규칙이 아니다. 점수 공식·소수 정책·reason 구별은 [scoring §3–§5](scoring.md)에 따른다.

### 불변 평가 snapshot

Freeze controller는 `(run_id, candidate_id, evaluation_round, evidence_revision, policy_version)`에 유일한 snapshot_id를 만들고 `snapshots[snapshot_id]`에 **payload 복사본**을 저장한다. 단순한 현재 State 참조나 revision 숫자만 전달하지 않는다.

- 포함 근거: 해당 기업 또는 collector가 관련성을 확인한 industry 근거 중 실행 as_of·허용 출처 조건을 통과한 항목. `evidence_ids`와 evidence map의 key 집합은 같아야 한다.
- 정정 처리: 명시적으로 정정·승계된 superseded Evidence는 새 평가의 active 근거에서 제외한다. 파생값의 supporting_evidence_ids가 무효화됐으면 파생값도 재검증 전까지 제외한다. 단순히 다른 주장이 발견됐다는 이유만으로 이전 사실을 삭제하지 않으며, 미해결 conflicts는 함께 보존하고 해당 criterion은 missing 처리한다.
- 참조 폐쇄성: 포함 Evidence의 Source, 파생값 입력 Evidence, provenance의 RetrievalRecord와 RAG Chunk를 snapshot 안에서 모두 해소한다. provenance가 가리키는 검색 기록에 실제 해당 chunk_id가 있는지도 검증한다. 하나라도 없으면 `SNAPSHOT_INVALID` 오류를 기록하고 해당 후보만 failed → archive → advance한다. LLM에 불완전한 snapshot을 보내지 않는다.
- 적격성 재확인: 최종 EligibilityResult의 evidence_ids가 정정·승계로 무효화됐으면 같은 오류로 평가하지 않는다. 무효 근거를 보고서 단계까지 가져가 전체 workflow를 실패시키지 않으며, 적격성 자동 재판정은 MVP 범위 밖 제안이다.
- 수집 controller는 RetrievalBundle의 Source/Chunk와 이력을 저장한 후 Evidence를 병합한다. 새 근거·정정·새 provenance 등 snapshot에서 보이는 변경마다 해당 후보 evidence_revision을 증가시킨다.
- 이후 State.evidence에 근거나 provenance를 추가해도 기존 snapshot은 불변이다. 향후 별도 승인된 재평가 기능을 추가하더라도 새 evaluation_round와 snapshot을 사용하고 모든 branch에 같은 객체 내용을 전달해야 한다. 이 세대 보호 규칙이 v3에 평가 후 재조사 loop를 추가하는 것은 아니다.

### 평가 성공과 기술적 실패

각 평가 branch wrapper가 **terminal result**인 `EvaluationResult`를 반환한다. LLM은 차원별 `Evaluation` 내용만 만들고 wrapper가 transport·timeout·schema·허용 재시도를 처리한다. 다음 branch→dimension 매핑과 atomic envelope는 D04 구현 제안이며 5개 branch/6개 차원 자체는 v3 목표다.

| branch_id (fan-out/result key) | success payload의 dimension key 집합 |
| --- | --- |
| `founder` | `{founder}` |
| `market` | `{market}` |
| `technology` | `{technology}` |
| `moat` | `{moat}` |
| `business_deal` | `{traction, deal_terms}` |

- `success`: evaluations map이 위 집합과 정확히 같고 errors는 빈 배열. 각 map key는 Evaluation.dimension과 같으며 envelope와 모든 payload의 후보·세대·snapshot·revision·policy가 일치한다. 자료 부족은 유효한 Evaluation 내부의 missing이다.
- `failure`: evaluations=null, errors는 하나 이상의 WorkflowError. 기술적 실패를 missing이나 0점으로 바꾸지 않는다. Business & Deal의 한 차원만 누락·schema 실패여도 **branch 전체 실패**이며 성공한 차원만 점수에 넣지 않는다. 원시 진단 출력은 성공 State와 분리한다.
- 병렬 branch는 `evaluation_results`에 자기 branch key 하나만 반환한다. join은 이번 세대의 다섯 terminal result를 검증하고 모두 성공일 때만 여섯 dimension payload를 `evaluations`에 **함께 승격**한다. 실패가 하나라도 있으면 errors로 옮기고 후보 failed → archive → advance한다. 별도의 직렬 투자조건 노드는 없다.
- evaluation의 research_gaps는 최종 결측 설명/감사용이다. Coverage가 만든 actionable research_gaps와 혼동하거나 평가 뒤 Evidence Research를 재호출하지 않는다.
- 프로세스 중단·전체 실행 취소로 wrapper가 결과를 돌려주지 못하면 후보 결측이 아니다. 전체 timeout/오류 controller가 workflow failed로 종료한다.

예시(가상 구조 설명, 완전한 DTO JSON 아님): `evaluation_results["co-fixture-001:1:business_deal"]`은 `branch_id="business_deal"`, `status="success"`, `evaluations={"traction": Evaluation(dimension="traction", ...), "deal_terms": Evaluation(dimension="deal_terms", ...)}`를 담는다. join 후에는 `evaluations["co-fixture-001:1:traction"]`과 `evaluations["co-fixture-001:1:deal_terms"]`로 분리한다. `Evaluation.dimension="business_deal"` 또는 여섯 번째 branch key `deal_terms`는 허용하지 않는다. 새 schema_version 값은 M0에서 고정한다.

### ID와 참조

ID 생성은 controller의 공통 함수가 소유하며 LLM이 만들지 않는다. 아래 튜플은 **유일성 범위**다. 현재 문자열 인코딩은 아래 #6 구현 규칙을 사용하고 각 WP가 임의로 연결하지 않는다. v3 branch 결과 key 확장은 별도 계약 변경이다.

| ID | 결정적 생성 key | 참조 규칙 |
| --- | --- | --- |
| eligibility_result_id | `(run_id, candidate_id, evidence_revision, policy_version, "eligibility")` | 최종 적격성 결과를 CandidateOutcome에서 참조 |
| score_summary_id | `(run_id, candidate_id, evaluation_round, policy_version, "score")` | 해당 세대의 snapshot_id·evidence_revision을 보존 |
| decision_id | `(score_summary_id, "decision")` | 동일 점수 결과에 하나의 최종 판단; 설명 재시도 동안 ID를 바꾸지 않음 |

재실행은 새로운 run_id를 사용한다. 같은 실행·key의 동일 결과 재기록은 idempotent, 다른 결과로 덮어쓰기는 오류다. State의 `eligibility_results`, `score_summaries`, `investment_decisions`는 원문대로 candidate_id → 현재 최종 DTO map을 유지하고, 각 DTO 내부에 위 ID를 넣는다. 보고서 controller가 이 값을 ID → DTO map으로 변환하여 참조를 해소한다. `CandidateOutcome.failure_ids`는 State.errors에 실제 존재하는 error_id만 허용한다.

**#6 ID 문자열 인코딩:** `contracts.ids`의 함수는
`["skala-rag-id-v1", kind, *key_components]` 배열을 UTF-8 JSON으로 직렬화한다
(`ensure_ascii=False`, 구분자 `,`와 `:`, 추가 공백 없음). SHA-256 전체 hex를
`{kind}-v1-{digest}` 형태로 반환한다. kind는 `snapshot/eligibility/score/decision`이다.
snapshot key는 위 Freeze 튜플, 나머지는 위 표의 튜플을 그대로 사용한다.
문자열은 공백뿐인 값을 거절하되 입력 자체를 정규화하지 않는다. 세대·revision은
bool을 제외한 음이 아닌 정수다. 평가 map key는 `{candidate_id}:{evaluation_round}:{dimension}`을
유지하며 모호한 분리를 막기 위해 candidate_id와 dimension의 `:`를 거절한다.
이는 식별자 인코딩 규칙이며 OPEN 정책 승인이나 평가 척도 승인에 해당하지 않는다.

**#6 CriterionAssessment 구조 검증:** schema_version은 호출자가 명시한다.
observed는 1..5의 strict 정수 rating, 공백 아닌 rationale, 중복 없는 근거 ID를
하나 이상 요구하며 missing_reason을 허용하지 않는다. missing은 rating=null과
missing_reason을 요구한다. 실제 근거 존재·snapshot 포함 여부와 영역 criterion
완전성은 평가 wrapper의 책임이며 이 DTO만으로 검증되었다고 표현하지 않는다.

**검증 불변식**

- Evaluation에는 해당 영역의 모든 criterion이 정확히 한 번 나타나야 한다. 누락은 schema 오류이지 자동 결측 처리 아님.
- `observed`이면 rating·rationale·실존 Evidence가 필요하다. `missing`이면 rating=null, missing_reason 필수. `not_applicable`이면 rating=null, applicability_reason·applicability_rule_id·적용성을 지지하는 Evidence가 필요하다는 제안이다. C-2/C-3 N/A 용어 충돌과 승인 rubric(D05·D14) 없이 근거 부재를 N/A로 승격하지 않는다.
- 근거 ID가 존재하고, 같은 후보 또는 적용 가능한 산업 scope이며, 평가 snapshot에 포함되어야 한다.
- 금융 숫자에 단위·기간이 없거나 상충 근거가 미해결이면 해당 criterion은 missing으로 남긴다.
- 점수·비중은 모델 출력값을 신뢰하지 않고 승인된 catalog와 rating에서 재계산한다.
- summary는 같은 후보·평가 세대·policy·evidence_revision의 여섯 영역만 집계한다.

## 5. 보고서와 오류

| DTO | 최소 필드 |
| --- | --- |
| `CandidateOutcome` | candidate_id, status, eligibility_result_id?, decision_id?, failure_ids, summary_reason |
| `ReportInput` | run_id, mode (`single_candidate/no_recommendation` 제안), selection_result, selected_candidate_id?, candidate_outcomes, permitted_evidence_ids, as_of, corpus_version, policy_version; no_recommendation 명칭이 전부 WATCHLIST/PASS의 선택 정책을 결정하지 않음 |
| `ReportContext` | context_id, input (`ReportInput`), snapshots, eligibility_results, evaluations, score_summaries, decisions, evidence, sources, chunks, retrieval_records, errors; 아래 payload 계약 적용 |
| `ReportDraft` | report_id, context_id, revision, markdown, cited_evidence_ids, reference_source_ids, limitations |
| `ValidationResult` | valid, context_id, checks, errors (`code/location/message`), artifact_hash |
| `ReportJudgement` | verdict (`pass/revise/fail`), context_id, findings (`severity/claim_location/evidence_ids/reason`), revision_instructions, judged_artifact_hash |
| `WorkflowError` | error_id, run_id, candidate_id?, node, error_code, message_redacted, retryable, attempt, timestamp |
| `RunManifest` | 실행 입력, 코드 revision 또는 uncommitted 표시, schema/policy/prompt/model 버전, corpus hash, 도구 상태, 예산/사용량, artifact 경로·hash, 검증 결과, workflow_status, run_outcome; Warning·acceptance·publication 필드/값 매핑은 D08 OPEN |
| `RunResult` | 제안: run_id, execution_terminated, current_draft?, validated_report?, validation_findings, warnings, acceptance, publication_allowed; 구체적 enum·CLI exit·manifest 매핑은 D08 OPEN |

### ReportContext — 보고서 단계에 전달할 실제 내용

보고서 controller가 전 후보 처리가 끝난 `SelectionResult`, `ReportInput`과 완료된 State로 context를 조립·검증하고 고정한다. candidate_outcomes는 정규화된 모든 후보의 최종 결과를 포함한다. selected ID·selection policy·비교한 score ID가 State와 일치해야 한다. 적격 후보가 없으면 selected=None과 사유를 제공한다(v3 D-3). all-WATCHLIST/PASS 또는 성공 평가 없음의 mode는 D03 승인 정책에 따르며 임의 선택하지 않는다. 각 context 필드는 ID 목록이 아닌 **해소된 DTO payload map**이다.

- snapshots는 snapshot_id, eligibility_results는 eligibility_result_id, score_summaries는 score_summary_id, decisions는 decision_id, errors는 error_id로 접근한다. evaluations는 후보·세대·dimension key를 사용한다.
- evidence/sources/chunks/retrieval_records는 각각 해당 DTO ID로 접근한다. 평가된 후보마다 최종 ScoreSummary가 참조하는 세대의 snapshot·여섯 Evaluation만 넣고 이전 세대는 넣지 않는다. 그 snapshot과 적격성/제외 사유에 필요한 근거만 복사한다. ReportInput.permitted_evidence_ids와 context.evidence의 key는 일치해야 한다.
- CandidateOutcome의 모든 참조, 여섯 최종 성공 Evaluation, ScoreSummary의 세대·snapshot, Decision의 원래 점수·정책·적격성 결과를 대조한다. 평가하지 않은/실패한 후보는 판정·점수 map 항목 없이 사유·오류만 전달하며 빈 평가를 만들지 않는다.
- 포함 근거의 Source 서지정보와 provenance를 모두 해소한다. 정정으로 무효화된 적격성 근거, 다른 세대의 점수, 누락된 Source 등은 `CONTEXT_INVALID`로 실패 처리한다.
- Generator·Structural Validator·Semantic Judge는 **동일한 고정 ReportContext**를 받는다. 허용되지 않은 State/전역 저장소/인터넷을 추가 조회하여 사실을 보충하지 않는다. 생성에는 excerpt·서지정보, 구조 검증에는 원래 점수·판정, 의미 검증에는 실제 근거·평가 내용이 모두 제공된다.
- 보고서 문장·형식 오류만 같은 context에서 revise한다. upstream 평가·점수·근거 자체가 잘못됐으면 보고서 LLM이 고치지 않고 workflow failed로 종료한다. 이를 수정하려면 별도 실행에서 해당 단계를 다시 수행한다.

출력 문장을 수정하면 구조·의미 검증 결과를 다시 생성한다. 이전 draft의 통과 결과를 새 draft에 붙이지 않는다. context_id와 artifact_hash가 일치하는 검증 결과만 사용한다. `ReportJudgement.fail`은 회복 불가능한 context/upstream 오류로 한정하는 제안이며 즉시 실패한다. 수정 가능한 문장/구성/근거 설명 문제는 `revise`로 공유 수정 예산을 사용한다. 품질 문제가 끝내 해결되지 않은 상태를 fatal fail로 재분류하여 v3 Warning 반환을 우회하지 않는다.

### Warning 반환과 final 발행 — D08/D09 OPEN

v3 D-3의 구조·의미 수정 최대 2회 후 Warning과 현재 결과 반환은 목표다. **실행 종료, 결과 반환, 검증 수용(acceptance), 최종 발행(publication)은 별개**다. 제안하는 RunResult는 현재 draft와 그 hash에 묶인 findings·Warning을 반환하되, 실패/미검증 draft를 validated_report나 State.report로 승격하지 않는다.

| 경로 | 반환/보존 제안 | 검증·발행 경계 |
| --- | --- | --- |
| 구조·의미·실제 PDF 모두 통과 | 동일 context/hash의 validated_report와 manifest | 이 산출물만 validated final 후보 |
| 구조/의미 revise 한도 소진 | current_draft, 기존 검증 결과·미실행 검사 표시, warnings | failed/not-run 검사를 pass로 바꾸지 않음; final 발행 불가 |
| context/upstream 참조 파손·실행 오류 | 오류·진단·있다면 draft 보존, fatal 종료 | Warning 품질 경로로 복구하거나 투자 결론을 지어내지 않음 |

`workflow_status`는 v3 D-1에 `running/completed/failed`만 있다. Warning 반환을 어느 값에 매핑할지, acceptance 값/CLI exit code/manifest/파일명을 무엇으로 할지, PDF layout 수정이 공유 예산인지 별도 실패인지 승인 전이다. 이 문서가 `completed_with_warning` 같은 enum을 추가 승인하지 않는다. `report_revision_count=0`으로 최초 생성, 각 재작성 직전에 +1, 구조·의미 합산 2회, 두 번째 수정도 실패하면 세 번째 수정 없이 반환하는 안을 테스트한다. 실제 renderer 실패·깨진 context는 별도 fatal로 다루며 그 구체적 경계도 D08·D09에 기록한다.

## 6. InvestmentState 계약과 단독 writer

v3 필드 이름을 유지하되 `sources`, `chunks`, `evaluation_results`, `evaluation_rounds`, `evidence_revisions`, `snapshots`, `candidate_outcomes`, `selection_result`, `report_context`, `report_draft`, `pdf_validation`, `run_result`, 실행 metadata를 추가하는 제안이다. 성공 평가와 실패 envelope를 분리하므로 evaluations의 writer는 controller로 한정한다.

| State 묶음 | 필드 | 갱신 규칙 |
| --- | --- | --- |
| 실행 입력 | investment_theme, search_queries, run_input, run_manifest | controller 작성, 실행 중 정책 불변 |
| 후보 | candidates, current_candidate_id, candidate_index, candidate_status | Normalize/Iterator/Archive 등 단계별 단독 writer; Iterator가 현재 후보 지정 |
| 최종 선택 | selection_result, selected_candidate_id | 모든 후보 처리 뒤 Best Candidate Selector만 작성; Iterator/Decision은 선택 불가 |
| 기본 조사 | company_profiles, eligibility_results | 해당 후보 조사/판정 노드 단독 writer |
| 출처/근거 | sources, chunks, evidence, retrieval_history | ID 기반 merge; Source/Evidence의 허용된 병합은 §3 규칙 적용 |
| 조사 제어 | coverage_results, research_gaps, research_retry_count, evidence_revisions | Coverage가 사전 gap/충족 기록, retry controller가 횟수, Evidence Research merge controller가 revision; 평가 후 조사 재진입 없음 |
| 평가 | evaluation_results, evaluations, evaluation_rounds, snapshots | branch-key evaluation_results만 병렬 merge; dimension-key 성공 evaluations는 Join 단독 writer, rounds·snapshots는 Freeze controller |
| 판정/이력 | score_summaries, investment_decisions, candidate_outcomes | 단계별 단독 writer; 후보 결과 덮어쓰기 금지 |
| 보고서 | report_context, report_draft, report, report_validation, report_judgement, pdf_validation, report_revision_count | context는 controller가 최초 고정, 나머지는 순차 갱신 |
| 오류/종료 | errors, workflow_status, run_outcome, run_result | 오류는 ID 병합; 종료/acceptance/publication은 controller만 변경, Warning 매핑 OPEN |

- `evaluation_results` key는 `{candidate_id}:{evaluation_round}:{branch_id}`(5개), 성공 `evaluations` key는 `{candidate_id}:{evaluation_round}:{dimension}`(6개)다. 각각 envelope/payload와 일치해야 한다. 원문의 후보+차원 key에 세대를 더하는 것은 혼입 방지용이며 사후 재평가 loop를 새로 요구하지 않는다.
- candidate_status 제안: `discovered/researching/ineligible/eligibility_unknown/evaluating/recommend_priority/recommend/watchlist/pass/failed`. 실행 중단 시 미처리 후보의 `not_evaluated` 필요 여부는 D03·D08에서 결정하며 정상 첫 추천의 결과로 사용하지 않는다. 상태와 네 대문자 투자 label은 별개 필드다.
- workflow_status는 v3의 `running/completed/failed`를 보존한다. Warning 매핑은 OPEN이다.
- run_outcome의 과거 제안은 `recommended/no_recommendation/no_candidates/insufficient_evidence/technical_failure`였다. 네 label·selection·Warning/acceptance와의 매핑을 D03·D08에서 확정해야 하며 이 목록을 승인 enum으로 설치하지 않는다. 완료와 투자 추천은 별개다.
- 실행 시작 시 `research_retry_count={}`, `evaluation_rounds={}`, `evidence_revisions={}`, `snapshots={}`와 나머지 map/list를 비운다. `candidate_index=0`, `report_revision_count=0`; current/selected ID, selection_result, report_context, report_draft, report, run_result는 null이다.
- 후보 최초 선택 시 후보별 map인 `research_retry_count`, `evaluation_rounds`, `evidence_revisions`에 각각 `setdefault(candidate_id, 0)`을 적용한다. 다른 후보로 이동해도 기존 후보의 count를 지우지 않는다. Freeze마다 해당 후보 evaluation_round를 증가시키며, 보고서 수정 횟수만 실행 단위 scalar다.
- 구조·의미·PDF 검증을 모두 통과한 현재 artifact만 `report`에 넣는다. Warning 반환 또는 fatal 실패 시 `report=null`, draft·findings·오류를 별도 보존한다. Warning=검증 통과로 해석하지 않는다.
- `candidate_index`는 처리 순서이지 기업 ID가 아니다. 후보 변경 시 평가 controller의 현재 세대 참조도 바꾼다.

## 7. 팀 사이의 함수 경계 — 구현할 인터페이스

```text
search_candidates(request, budget) -> ToolResult[DiscoveryBundle]
research_company(candidate, budget) -> ToolResult[CompanyResearchBundle]
retrieve(request: RetrievalRequest) -> ToolResult[RetrievalBundle]
collect_evidence(candidate, gaps, budget) -> ToolResult[EvidenceBundle]
check_eligibility(profile, evidence, policy) -> EligibilityResult
check_coverage(candidate_id, evidence, catalog, policy) -> CoverageResult
freeze_snapshot(candidate_id, state, run_input) -> EvaluationSnapshot
evaluate_branch(branch_id, snapshot, rubrics, policy) -> EvaluationResult
join_evaluations(branch_results, snapshot, catalog) -> dict[dimension, Evaluation]
aggregate_scores(evaluations, policy) -> ScoreSummary
decide(score_summary, eligibility, policy) -> DecisionPolicyResult
select_best_candidate(candidate_outcomes, decisions, score_summaries, policy) -> SelectionResult
build_report_context(report_input, state) -> ReportContext
generate_report(context, feedback) -> ReportDraft
validate_report(draft, context, policy) -> ValidationResult
judge_report(draft, context) -> ReportJudgement
render_pdf(draft, template) -> RenderResult
finalize_run(current_artifacts, validations, warnings, policy) -> RunResult
```

ToolResult는 `status`, typed `data`, `retrieval_records`, `errors`를 가진다. Discovery controller는 `DiscoveryBundle.sources`를 먼저 검증·저장하고 각 후보의 discovery_source_ids가 모두 해소되는지 확인한 뒤 Normalize로 넘긴다. Company Research가 실패해도 발견 출처는 남아야 한다. 검색은 `RetrievalRequest.as_of`를 반드시 사용하고, query·기업·corpus/index·allowed_source_ids·as_of를 모두 cache key에 포함한다. returned Chunk가 요청 밖의 기업/출처/기준일을 위반하면 반환을 거절한다. `CompanyResearchBundle`은 profile+sources+evidence, `EvidenceBundle`은 sources+evidence, `DecisionPolicyResult`는 label+grade+reason_codes, `RenderResult`는 artifact_path+page_count+layout_measurements+errors를 가진다.

`collect_evidence`는 Evidence Research의 초기·gap 조사 공통 경계다. gaps가 비어 있으면 최초 수집, 있으면 Coverage의 부족자료 조사라는 제안이며 별도 Targeted Research API를 요구하지 않는다. `EvidenceBundle`에 RAG Chunk가 필요하면 RetrievalBundle에서 먼저 Source/Chunk를 저장하고 Evidence 참조를 해소한다. branch 내부 차원 해석 helper를 둘 수 있어도 외부 terminal boundary는 evaluate_branch이며 business_deal의 두 차원을 원자적으로 검증한다.

통합 기준 `c5a30f3`에는 #5 구조 DTO, #7 State/factory, #9 fixture 전용 draft catalog 외에 #15/PR #42의 ID reducer와 State Annotated 연결, #16/PR #64의 baseline `aggregate_scores`·`decide`, #53/PR #66의 재무 단위·기간·파생값 helper가 있다. 집계는 고정100·observed/missing·여섯 영역 관측 rating 저점수·세 label baseline이며 v3 N/A 분모·핵심차원40%·네 label 구현이 아니다. 업무 Graph wiring·CLI·live RAG·평가 agent·보고서 출력은 여전히 구현 목표다. 실제 #16 API는 `aggregate_scores(evaluations, policy) -> ScoreBreakdown`, `decide(observed_score, missing_weight, dimension_ratings, thresholds) -> Decision`이다. 위 제안의 ScoreSummary/eligibility 입력 API와 같지 않다. 현재 State의 평가 key는 dimension 기반이며 v3 branch-key/atomic Business & Deal 승격은 별도 전환 대상이다. #53 helper는 `Derived | Unavailable` 결과로 단위·기간·provenance를 검증하며 N/A나 rating을 결정하지 않는다. 나머지 제안 이름은 사용 가능한 API로 읽지 않는다. selector·0분모·Warning 정책 부재는 v3 live 시작 전에 차단해야 하며 fixture 정책 주입을 팀 승인으로 표시하지 않는다.
