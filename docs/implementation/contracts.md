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
| `value`, `unit`, `currency` | 숫자가 있을 때만 사용. 값 미상은 null |
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
| 식별 core | source_id, locator, 정규화 claim, candidate_id, scope, value, unit, currency, period, geography, event_date, evidence_kind, supporting_evidence_ids, derivation, supersedes | 모두 같아야 함. 다르면 ID 충돌 오류 |
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
| `CoverageResult` | candidate_id, evidence_revision, policy_version, covered_criterion_ids, missing_criterion_ids, missing_weight, coverage_pct, research_ready, unresolved_conflicts |
| `CriterionAssessment` | criterion_id, status (`observed/missing`), rating (`1..5` 정수 또는 null), evidence_ids, rationale, missing_reason?, applicability_note? |
| `EvaluationSnapshot` | snapshot_id, run_id, candidate_id, evaluation_round, evidence_revision, policy_version, corpus_version, index_version, as_of, evidence_ids, evidence (`dict[evidence_id, Evidence]`), sources (`dict[source_id, Source]`), chunks (`dict[chunk_id, Chunk]`), retrieval_records (`dict[retrieval_id, RetrievalRecord]`) |
| `Evaluation` | run_id, candidate_id, dimension, evaluation_round, snapshot_id, evidence_revision, policy_version, rubric_version, criteria, research_gaps, caveats |
| `EvaluationResult` | run_id, candidate_id, dimension, evaluation_round, snapshot_id, evidence_revision, policy_version, status (`success/failure`), evaluation?, errors (`WorkflowError[]`) |
| `ScoreSummary` | score_summary_id, run_id, candidate_id, evaluation_round, snapshot_id, evidence_revision, policy_version, criterion_points, dimension_ratings, observed_score, missing_weight, coverage_pct, low_score_dimensions, hold_reasons |
| `InvestmentDecision` | decision_id, run_id, candidate_id, label (`RECOMMEND/WATCHLIST/PASS`), report_grade, score_summary_id, reason_codes, evidence_ids, rationale, risks, limitations |

`research_ready`는 조사량 기준이고 `observed`는 평가 가능한 근거의 질까지 검증한 결과다. 최종 ScoreSummary의 coverage는 최종 평가 기준으로 다시 계산한다.

### 불변 평가 snapshot

Freeze controller는 `(run_id, candidate_id, evaluation_round, evidence_revision, policy_version)`에 유일한 snapshot_id를 만들고 `snapshots[snapshot_id]`에 **payload 복사본**을 저장한다. 단순한 현재 State 참조나 revision 숫자만 전달하지 않는다.

- 포함 근거: 해당 기업 또는 collector가 관련성을 확인한 industry 근거 중 실행 as_of·허용 출처 조건을 통과한 항목. `evidence_ids`와 evidence map의 key 집합은 같아야 한다.
- 정정 처리: 명시적으로 정정·승계된 superseded Evidence는 새 평가의 active 근거에서 제외한다. 파생값의 supporting_evidence_ids가 무효화됐으면 파생값도 재검증 전까지 제외한다. 단순히 다른 주장이 발견됐다는 이유만으로 이전 사실을 삭제하지 않으며, 미해결 conflicts는 함께 보존하고 해당 criterion은 missing 처리한다.
- 참조 폐쇄성: 포함 Evidence의 Source, 파생값 입력 Evidence, provenance의 RetrievalRecord와 RAG Chunk를 snapshot 안에서 모두 해소한다. provenance가 가리키는 검색 기록에 실제 해당 chunk_id가 있는지도 검증한다. 하나라도 없으면 `SNAPSHOT_INVALID` 오류를 기록하고 해당 후보만 failed → archive → advance한다. LLM에 불완전한 snapshot을 보내지 않는다.
- 적격성 재확인: 최종 EligibilityResult의 evidence_ids가 정정·승계로 무효화됐으면 같은 오류로 평가하지 않는다. 무효 근거를 보고서 단계까지 가져가 전체 workflow를 실패시키지 않으며, 적격성 자동 재판정은 MVP 범위 밖 제안이다.
- 수집 controller는 RetrievalBundle의 Source/Chunk와 이력을 저장한 후 Evidence를 병합한다. 새 근거·정정·새 provenance 등 snapshot에서 보이는 변경마다 해당 후보 evidence_revision을 증가시킨다.
- 이후 State.evidence에 근거나 provenance를 추가해도 기존 snapshot은 불변이다. 재평가는 새 evaluation_round와 snapshot으로 수행하고 모든 branch에 같은 객체 내용을 전달한다.

### 평가 성공과 기술적 실패

각 평가 node의 wrapper는 **terminal result**인 `EvaluationResult`를 반환한다. LLM은 `Evaluation` 내용만 생성하며, wrapper가 transport·timeout·schema 검증과 허용된 재시도를 처리한다.

- `success`: evaluation 필수, errors는 빈 배열. envelope와 Evaluation의 후보·세대·snapshot·policy가 모두 일치해야 한다. 자료 부족은 유효한 Evaluation 내부의 missing이다.
- `failure`: evaluation=null, errors는 하나 이상의 WorkflowError. 기술적 실패를 missing이나 0점으로 변환하지 않는다.
- 병렬 branch는 `evaluation_results`의 자기 key 하나만 반환한다. join controller는 이번 세대의 다섯 terminal result를 수집한다. 실패가 하나라도 있으면 오류를 State.errors에 옮기고 후보 failed → archive → advance; 다섯 성공일 때만 `evaluations`에 검증된 값과 gap을 저장한다.
- 직렬 Deal Terms도 같은 wrapper/결과 계약을 사용한다. 성공을 저장한 뒤 여섯 영역을 집계하며, 실패하면 해당 후보를 archive → advance한다.
- 프로세스 중단·전체 실행 취소로 wrapper가 결과를 돌려주지 못한 경우는 후보 결측이 아니다. 전체 실행 timeout/오류 controller가 workflow failed로 종료한다.

### ID와 참조

ID 생성은 controller의 공통 함수가 소유하며 LLM이 만들지 않는다. 아래 튜플은 **유일성 범위**다. 실제 문자열 인코딩은 M0 공통 구현에서 고정하고 각 WP가 임의로 연결하지 않는다.

| ID | 결정적 생성 key | 참조 규칙 |
| --- | --- | --- |
| eligibility_result_id | `(run_id, candidate_id, evidence_revision, policy_version, "eligibility")` | 최종 적격성 결과를 CandidateOutcome에서 참조 |
| score_summary_id | `(run_id, candidate_id, evaluation_round, policy_version, "score")` | 해당 세대의 snapshot_id·evidence_revision을 보존 |
| decision_id | `(score_summary_id, "decision")` | 동일 점수 결과에 하나의 최종 판단; 설명 재시도 동안 ID를 바꾸지 않음 |

재실행은 새로운 run_id를 사용한다. 같은 실행·key의 동일 결과 재기록은 idempotent, 다른 결과로 덮어쓰기는 오류다. State의 `eligibility_results`, `score_summaries`, `investment_decisions`는 원문대로 candidate_id → 현재 최종 DTO map을 유지하고, 각 DTO 내부에 위 ID를 넣는다. 보고서 controller가 이 값을 ID → DTO map으로 변환하여 참조를 해소한다. `CandidateOutcome.failure_ids`는 State.errors에 실제 존재하는 error_id만 허용한다.

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
| `ReportInput` | run_id, mode (`single_candidate/no_recommendation`), selected_candidate_id?, candidate_outcomes, permitted_evidence_ids, as_of, corpus_version, policy_version |
| `ReportContext` | context_id, input (`ReportInput`), snapshots, eligibility_results, evaluations, score_summaries, decisions, evidence, sources, chunks, retrieval_records, errors; 아래 payload 계약 적용 |
| `ReportDraft` | report_id, context_id, revision, markdown, cited_evidence_ids, reference_source_ids, limitations |
| `ValidationResult` | valid, context_id, checks, errors (`code/location/message`), artifact_hash |
| `ReportJudgement` | verdict (`pass/revise/fail`), context_id, findings (`severity/claim_location/evidence_ids/reason`), revision_instructions, judged_artifact_hash |
| `WorkflowError` | error_id, run_id, candidate_id?, node, error_code, message_redacted, retryable, attempt, timestamp |
| `RunManifest` | 실행 입력, 코드 revision 또는 uncommitted 표시, schema/policy/prompt/model 버전, corpus hash, 도구 상태, 예산/사용량, artifact 경로·hash, 검증 결과, workflow_status, run_outcome |

### ReportContext — 보고서 단계에 전달할 실제 내용

보고서 controller가 `ReportInput`과 완료된 State로부터 context를 조립·검증하고 고정한다. 각 필드는 ID만 나열한 목록이 아니라 **해소된 DTO payload map**이다.

- snapshots는 snapshot_id, eligibility_results는 eligibility_result_id, score_summaries는 score_summary_id, decisions는 decision_id, errors는 error_id로 접근한다. evaluations는 후보·세대·dimension key를 사용한다.
- evidence/sources/chunks/retrieval_records는 각각 해당 DTO ID로 접근한다. 평가된 후보마다 최종 ScoreSummary가 참조하는 세대의 snapshot·여섯 Evaluation만 넣고 이전 세대는 넣지 않는다. 그 snapshot과 적격성/제외 사유에 필요한 근거만 복사한다. ReportInput.permitted_evidence_ids와 context.evidence의 key는 일치해야 한다.
- CandidateOutcome의 모든 참조, 여섯 최종 성공 Evaluation, ScoreSummary의 세대·snapshot, Decision의 원래 점수·정책·적격성 결과를 대조한다. 평가하지 않은/실패한 후보는 판정·점수 map 항목 없이 사유·오류만 전달하며 빈 평가를 만들지 않는다.
- 포함 근거의 Source 서지정보와 provenance를 모두 해소한다. 정정으로 무효화된 적격성 근거, 다른 세대의 점수, 누락된 Source 등은 `CONTEXT_INVALID`로 실패 처리한다.
- Generator·Structural Validator·Semantic Judge는 **동일한 고정 ReportContext**를 받는다. 허용되지 않은 State/전역 저장소/인터넷을 추가 조회하여 사실을 보충하지 않는다. 생성에는 excerpt·서지정보, 구조 검증에는 원래 점수·판정, 의미 검증에는 실제 근거·평가 내용이 모두 제공된다.
- 보고서 문장·형식 오류만 같은 context에서 revise한다. upstream 평가·점수·근거 자체가 잘못됐으면 보고서 LLM이 고치지 않고 workflow failed로 종료한다. 이를 수정하려면 별도 실행에서 해당 단계를 다시 수행한다.

출력 문장을 수정하면 구조·의미 검증 결과를 다시 생성한다. 이전 draft의 통과 결과를 새 draft에 붙이지 않는다. context_id와 artifact_hash가 일치하는 검증 결과만 사용한다. `ReportJudgement.fail`은 회복 불가능한 context/upstream 오류 또는 이 실행에서 신뢰할 수 있는 보고서를 만들 수 없다는 판정이며, 즉시 실패한다. 수정 가능한 문장/구성 문제는 `revise`다.

## 6. InvestmentState 계약과 단독 writer

원문 필드 이름은 유지하되 `sources`, `chunks`, `evaluation_results`, `evaluation_rounds`, `evidence_revisions`, `snapshots`, `candidate_outcomes`, `report_context`, `report_draft`, `pdf_validation`, 실행 metadata를 추가하는 제안이다. 성공 평가와 실패 envelope를 분리하므로 evaluations의 writer는 아래처럼 controller로 한정한다.

| State 묶음 | 필드 | 갱신 규칙 |
| --- | --- | --- |
| 실행 입력 | investment_theme, search_queries, run_input, run_manifest | controller 작성, 실행 중 정책 불변 |
| 후보 | candidates, current_candidate_id, candidate_index, candidate_status, selected_candidate_id | 후보 controller만 변경 |
| 기본 조사 | company_profiles, eligibility_results | 해당 후보 조사/판정 노드 단독 writer |
| 출처/근거 | sources, chunks, evidence, retrieval_history | ID 기반 merge; Source/Evidence의 허용된 병합은 §3 규칙 적용 |
| 조사 제어 | coverage_results, research_gaps, research_retry_count, evidence_revisions | coverage/controller만 변경 |
| 평가 | evaluation_results, evaluations, evaluation_rounds, snapshots | evaluation_results만 병렬 merge; 성공 evaluations는 join/직렬 평가 controller, rounds·snapshots는 Freeze controller |
| 판정/이력 | score_summaries, investment_decisions, candidate_outcomes | 단계별 단독 writer; 후보 결과 덮어쓰기 금지 |
| 보고서 | report_context, report_draft, report, report_validation, report_judgement, pdf_validation, report_revision_count | context는 controller가 최초 고정, 나머지는 순차 갱신 |
| 오류/종료 | errors, workflow_status, run_outcome | 오류는 ID 병합; 종료 상태는 controller만 변경 |

- 평가 key: `{candidate_id}:{evaluation_round}:{dimension}`. 원문의 `{candidate_id}:{dimension}`을 확장한 이유는 재평가 세대 혼입 방지다. `evaluation_results`도 같은 key를 쓰며 key와 envelope 필드가 일치해야 한다.
- candidate_status: `discovered/researching/ineligible/eligibility_unknown/evaluating/recommend/watchlist/pass/failed/not_evaluated`.
- workflow_status는 원문대로 `running/completed/failed`.
- run_outcome은 `recommended/no_recommendation/no_candidates/insufficient_evidence/technical_failure` 중 하나. 완료와 투자 추천은 별개다.
- 실행 시작 시 `research_retry_count={}`, `evaluation_rounds={}`, `evidence_revisions={}`, `snapshots={}`와 나머지 map/list를 비운다. `candidate_index=0`, `report_revision_count=0`; current/selected ID, report_context, report_draft, report는 null이다.
- 후보 최초 선택 시 후보별 map인 `research_retry_count`, `evaluation_rounds`, `evidence_revisions`에 각각 `setdefault(candidate_id, 0)`을 적용한다. 다른 후보로 이동해도 기존 후보의 count를 지우지 않는다. Freeze마다 해당 후보 evaluation_round를 증가시키며, 보고서 수정 횟수만 실행 단위 scalar다.
- 완료 시 검증된 Markdown을 `report`에 넣는다. 실패 시 `report=null`, `report_draft`와 오류를 보존한다.
- `candidate_index`는 처리 순서이지 기업 ID가 아니다. 후보 변경 시 평가 controller의 현재 세대 참조도 바꾼다.

## 7. 팀 사이의 함수 경계 — 구현할 인터페이스

```text
search_candidates(request, budget) -> ToolResult[DiscoveryBundle]
research_company(candidate, budget) -> ToolResult[CompanyResearchBundle]
retrieve(request: RetrievalRequest) -> ToolResult[RetrievalBundle]
collect_evidence(candidate, gaps, budget) -> ToolResult[EvidenceBundle]
check_eligibility(profile, evidence, policy) -> EligibilityResult
check_coverage(candidate_id, evidence, catalog) -> CoverageResult
freeze_snapshot(candidate_id, state, run_input) -> EvaluationSnapshot
evaluate_dimension(dimension, snapshot, rubric) -> EvaluationResult
aggregate_scores(evaluations, policy) -> ScoreSummary
decide(score_summary, eligibility, policy) -> DecisionPolicyResult
build_report_context(report_input, state) -> ReportContext
generate_report(context, feedback) -> ReportDraft
validate_report(draft, context, policy) -> ValidationResult
judge_report(draft, context) -> ReportJudgement
render_pdf(draft, template) -> RenderResult
```

ToolResult는 `status`, typed `data`, `retrieval_records`, `errors`를 가진다. Discovery controller는 `DiscoveryBundle.sources`를 먼저 검증·저장하고 각 후보의 discovery_source_ids가 모두 해소되는지 확인한 뒤 Normalize로 넘긴다. Company Research가 실패해도 발견 출처는 남아야 한다. 검색은 `RetrievalRequest.as_of`를 반드시 사용하고, query·기업·corpus/index·allowed_source_ids·as_of를 모두 cache key에 포함한다. returned Chunk가 요청 밖의 기업/출처/기준일을 위반하면 반환을 거절한다. `CompanyResearchBundle`은 profile+sources+evidence, `EvidenceBundle`은 sources+evidence, `DecisionPolicyResult`는 label+grade+reason_codes, `RenderResult`는 artifact_path+page_count+layout_measurements+errors를 가진다.

이름은 설계 계약이지 사용 가능한 import가 아니다. M0에서 schema와 fixture, M1에서 adapter stub, M2 이후 실제 구현을 연결한다. 각 기능은 주입된 Tool/LLM/clock을 사용해 외부 호출 없이 테스트할 수 있어야 한다.
