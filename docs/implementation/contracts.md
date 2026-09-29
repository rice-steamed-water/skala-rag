# 공통 데이터 계약

[문서 홈](../README.md) · [아키텍처](architecture.md) · [점수 정책](scoring.md)

근거: 원문 §7의 StageInfo, §8의 InvestmentState. 원문은 대부분 타입 이름만 정의하므로, 아래 DTO 필드와 검증 규칙은 **구현 제안**이다. M0에서 schema를 고정한 뒤 각 담당자가 별도로 같은 타입을 재정의하지 않는다.

## 1. 공통 규칙

- Graph 컨테이너는 원문대로 `InvestmentState(TypedDict, total=False)` 방향을 유지한다. 외부/LLM 경계의 DTO는 Pydantic으로 검증한다.
- 누락값은 `null`/`None`. 숫자 0, 빈 문자열, 추측한 값으로 대체하지 않는다. 0은 실제 관측값일 수 있다.
- 날짜는 ISO 8601, 시각은 시간대 포함. 발행일·사건일·수집일을 분리한다.
- 금액은 값·통화·단위·기준일을 함께 보존한다. KRW로 바꾸려면 별도 환율 근거와 변환식을 남긴다.
- DTO에 `schema_version`을 두고, 정책·모델·prompt 버전은 실행 manifest에 기록한다.
- JSON에 직렬화되지 않는 모델 객체, API client, DB connection, API key는 State에 넣지 않는다.
- `candidate_id`, `source_id`, `evidence_id`, `criterion_id`는 서로 다른 식별자다. 회사 이름 문자열로 join하지 않는다.

## 2. 입력과 후보

| DTO | 필드 계약 | 검증 |
| --- | --- | --- |
| `RunInput` | investment_theme, countries, languages, as_of, policy_version, corpus_version, execution_mode (`fixture/live`) | 공백 주제 거절; live는 승인 정책·예산·필수 도구 readiness 확인 |
| `Candidate` | candidate_id, canonical_name, aliases, country, homepage_url?, legal_identifiers, discovery_source_ids | 동명 기업을 도메인·법인 식별자·국가로 구별; 병합 근거 기록 |
| `StageInfo` | raw_label?, normalized_round, bucket (`early/late/unknown`), last_round_date?, cumulative_funding_krw?, method (`explicit/estimated/unknown`), source_ids, confidence (`high/medium/low/unknown`), rationale | `normalized_round`: `seed/series_a/series_b/series_c/out_of_scope/unknown`; 추정값을 explicit로 승격 금지 |
| `CompanyProfile` | candidate_id, domain_match?, is_listed?, exit_completed?, stage, as_of, field_evidence_ids | boolean 세 값: true/false/null. null을 false로 변환하지 않음 |
| `EligibilityResult` | candidate_id, status (`eligible/ineligible/unknown`), checks, reason_codes, evidence_ids | 모든 필수 조건 근거 확인 시 eligible; 하나라도 명백히 불일치하면 ineligible; 나머지는 unknown |

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
| `evidence_id` | stable ID; source snapshot + locator + 정규화 claim을 기준으로 중복 방지 |
| `candidate_id` | 회사 근거일 때 ID, 산업 공통 근거는 null |
| `scope` | `company` / `industry`; 산업 근거를 기업의 실적으로 인용하지 않음 |
| `criterion_ids` | 이 근거가 지원할 수 있는 세부항목 ID 목록 |
| `claim` | 원문을 벗어나지 않은 주장 |
| `value`, `unit`, `currency` | 숫자가 있을 때만 사용. 값 미상은 null |
| `period`, `geography`, `event_date` | 지표 해석에 필요한 맥락; 없는 필드는 null |
| `source_id`, `chunk_id` | Source 필수; RAG라면 Chunk도 필수 |
| `locator`, `excerpt` | 원문으로 되돌아갈 위치와 필요한 최소 발췌 |
| `method` | `web/api/rag/manual` |
| `evidence_kind` | `reported/derived/estimated` |
| `confidence`, `limitations` | 해석의 확실성과 한계. retrieval similarity와 별개 |
| `supporting_evidence_ids`, `derivation` | derived/estimated의 입력 근거와 계산식·가정 |
| `conflicts_with`, `supersedes` | 충돌·정정 관계; 기존 사실을 조용히 덮어쓰지 않음 |

기업의 자기 주장도 `reported`일 수 있지만, 이를 독립 검증 결과로 서술해서는 안 된다. 출처 성격과 검증 수준은 따로 기록한다.

### 구조 예시 — 가상 테스트 데이터, 실제 기업 자료 아님

아래 예시는 JSON 형태 설명용이며 실사용 근거가 아니다. `fixture://` 주소는 fixture 모드에서만 허용한다.

```json
{
  "schema_version": "draft-1",
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
  "chunk_id": "chunk-fixture-001-p3",
  "locator": "fixture://robotics-whitepaper#page=3",
  "excerpt": "센서 → 제어 소프트웨어 → 로봇 하드웨어",
  "method": "rag",
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
| `RetrievalRecord` | retrieval_id, run_id, candidate_id?, tool_name, query/arguments_without_secrets, started_at, finished_at, status (`ok/empty/unavailable/failed`), source_ids, chunk_ids, evidence_ids, error_id?, cost?, cache_hit |
| `ResearchGap` | gap_id, candidate_id, criterion_id 또는 eligibility_field, missing_fields, reason, priority_weight, suggested_queries, attempted_retrieval_ids, status (`open/resolved/exhausted`) |
| `CoverageResult` | candidate_id, evidence_revision, policy_version, covered_criterion_ids, missing_criterion_ids, missing_weight, coverage_pct, research_ready, unresolved_conflicts |
| `CriterionAssessment` | criterion_id, status (`observed/missing`), rating (`1..5` 정수 또는 null), evidence_ids, rationale, missing_reason?, applicability_note? |
| `Evaluation` | candidate_id, dimension, evaluation_round, evidence_revision, policy_version, rubric_version, criteria, research_gaps, caveats |
| `ScoreSummary` | candidate_id, evaluation_round, policy_version, criterion_points, dimension_ratings, observed_score, missing_weight, coverage_pct, low_score_dimensions, hold_reasons |
| `InvestmentDecision` | candidate_id, label (`RECOMMEND/WATCHLIST/PASS`), report_grade, score_summary_id, reason_codes, evidence_ids, rationale, risks, limitations |

`research_ready`는 조사량 기준이고 `observed`는 평가 가능한 근거의 질까지 검증한 결과다. 최종 ScoreSummary의 coverage는 최종 평가 기준으로 다시 계산한다.

**검증 불변식**

- Evaluation에는 해당 영역의 모든 criterion이 정확히 한 번 나타나야 한다. 누락은 schema 오류이지 자동 결측 처리 아님.
- `observed`이면 rating·rationale·실존 Evidence가 필요하다. `missing`이면 rating=null, missing_reason 필수.
- 근거 ID가 존재하고, 같은 후보 또는 적용 가능한 산업 scope이며, 평가 snapshot에 포함되어야 한다.
- 금융 숫자에 단위·기간이 없거나 상충 근거가 미해결이면 해당 criterion은 missing으로 남긴다.
- 점수·비중은 모델 출력값을 신뢰하지 않고 승인된 catalog와 rating에서 재계산한다.
- summary는 같은 후보·평가 세대·policy·evidence_revision의 여섯 영역만 집계한다.

## 5. 보고서와 오류

| DTO | 최소 필드 |
| --- | --- |
| `CandidateOutcome` | candidate_id, status, eligibility_result_id?, decision_id?, failure_ids, summary_reason |
| `ReportInput` | mode (`single_candidate/no_recommendation`), selected_candidate_id?, candidate_outcomes, decisions, score_summaries, permitted_evidence_ids, as_of, corpus_version, policy_version |
| `ReportDraft` | report_id, revision, markdown, cited_evidence_ids, reference_source_ids, limitations |
| `ValidationResult` | valid, checks, errors (`code/location/message`), artifact_hash |
| `ReportJudgement` | verdict (`pass/revise/fail`), findings (`severity/claim_location/evidence_ids/reason`), revision_instructions, judged_artifact_hash |
| `WorkflowError` | error_id, run_id, candidate_id?, node, error_code, message_redacted, retryable, attempt, timestamp |
| `RunManifest` | 실행 입력, 코드 revision 또는 uncommitted 표시, schema/policy/prompt/model 버전, corpus hash, 도구 상태, 예산/사용량, artifact 경로·hash, 검증 결과, workflow_status, run_outcome |

출력 문장을 수정하면 구조·의미 검증 결과를 다시 생성한다. 이전 draft의 통과 결과를 새 draft에 붙이지 않는다. `artifact_hash`로 동일 산출물인지 확인한다.

## 6. InvestmentState 계약과 단독 writer

원문 필드는 유지하되 `sources`, `evaluation_rounds`, `evidence_revisions`, `candidate_outcomes`, `report_draft`, `pdf_validation`, 실행 metadata를 추가하는 제안이다.

| State 묶음 | 필드 | 갱신 규칙 |
| --- | --- | --- |
| 실행 입력 | investment_theme, search_queries, run_input, run_manifest | controller 작성, 실행 중 정책 불변 |
| 후보 | candidates, current_candidate_id, candidate_index, candidate_status, selected_candidate_id | 후보 controller만 변경 |
| 기본 조사 | company_profiles, eligibility_results | 해당 후보 조사/판정 노드 단독 writer |
| 출처/근거 | sources, evidence, retrieval_history | ID 기반 merge; 동일 ID 충돌 감지 |
| 조사 제어 | coverage_results, research_gaps, research_retry_count, evidence_revisions | coverage/controller만 변경 |
| 평가 | evaluations, evaluation_rounds | evaluations만 병렬 merge, rounds는 controller |
| 판정/이력 | score_summaries, investment_decisions, candidate_outcomes | 단계별 단독 writer; 후보 결과 덮어쓰기 금지 |
| 보고서 | report_draft, report, report_validation, report_judgement, pdf_validation, report_revision_count | 보고서 controller가 순차 갱신 |
| 오류/종료 | errors, workflow_status, run_outcome | 오류는 ID 병합; 종료 상태는 controller만 변경 |

- 평가 key: `{candidate_id}:{evaluation_round}:{dimension}`. 원문의 `{candidate_id}:{dimension}`을 확장한 이유는 재평가 세대 혼입 방지다.
- candidate_status: `discovered/researching/ineligible/eligibility_unknown/evaluating/recommend/watchlist/pass/failed/not_evaluated`.
- workflow_status는 원문대로 `running/completed/failed`.
- run_outcome은 `recommended/no_recommendation/no_candidates/insufficient_evidence/technical_failure` 중 하나. 완료와 투자 추천은 별개다.
- 시작 시 list/dict는 빈 값, index는 0, selected/current ID와 report는 null, retry/revision은 0으로 초기화한다.
- 완료 시 검증된 Markdown을 `report`에 넣는다. 실패 시 `report=null`, `report_draft`와 오류를 보존한다.
- `candidate_index`는 처리 순서이지 기업 ID가 아니다. 후보 변경 시 평가 controller의 현재 세대 참조도 바꾼다.

## 7. 팀 사이의 함수 경계 — 구현할 인터페이스

```text
search_candidates(request, budget) -> ToolResult[list[Candidate]]
research_company(candidate, budget) -> ToolResult[CompanyResearchBundle]
retrieve(query, candidate_id, corpus_version, top_k) -> ToolResult[list[Chunk]]
collect_evidence(candidate, gaps, budget) -> ToolResult[EvidenceBundle]
check_eligibility(profile, evidence, policy) -> EligibilityResult
check_coverage(candidate_id, evidence, catalog) -> CoverageResult
evaluate_dimension(dimension, snapshot, rubric) -> Evaluation
aggregate_scores(evaluations, policy) -> ScoreSummary
decide(score_summary, eligibility, policy) -> DecisionPolicyResult
generate_report(report_input, feedback) -> ReportDraft
validate_report(draft, evidence, policy) -> ValidationResult
judge_report(draft, evidence) -> ReportJudgement
render_pdf(draft, template) -> RenderResult
```

ToolResult는 `status`, typed `data`, `retrieval_records`, `errors`를 가진다. `CompanyResearchBundle`은 profile+sources+evidence, `EvidenceBundle`은 sources+evidence, `DecisionPolicyResult`는 label+grade+reason_codes, `RenderResult`는 artifact_path+page_count+layout_measurements+errors를 가진다.

이름은 설계 계약이지 사용 가능한 import가 아니다. M0에서 schema와 fixture, M1에서 adapter stub, M2 이후 실제 구현을 연결한다. 각 기능은 주입된 Tool/LLM/clock을 사용해 외부 호출 없이 테스트할 수 있어야 한다.
