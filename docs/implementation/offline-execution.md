# Offline 후보 평가 결과의 보고서 인계 (#213)

이번 범위는 고정 후보와 사전 확보한 로컬 자료에서 시작하는 연결이다. 온라인 Discovery, API/Web 수집, provider 복구는 이 작업의 선행 조건에서 제외한다. 수집을 offline으로 제한해도 실제 근거 평가와 scoring authority, 유료 모델, 정책, corpus, 발행 승인을 대신하지 않는다.

## 지원하는 Python 연결

`skala_rag.graph.candidate_workflow_v3.run_candidate_report_v3`는 기존 public `run_candidate_workflow_v3`를 한 번 호출한 뒤 다음 경로를 잇는다.

```text
기존 outer / EvidenceResearch / freeze / Technology adapter / five-way join / selector
→ research_artifacts[cid]['state']['snapshots'][score.snapshot_id]
→ build_report_context_from_run_v3 / 기존 build_report_context_v3
→ 기존 ReportGeneratorV3 / Validator / SemanticJudgeV3 / 공유 수정 / PDF callback
```

반환값은 `(CandidateRunV3, ReportContextV3, ReportRunV3)`다. 원래 후보 결과의 `status`, `reporting_gap`, 실패와 unknown 진단을 유지한다. scored 후보만 원래 State snapshot에서 읽는다. 실패한 후보의 미사용 snapshot을 보고서 평가 결과로 승격하지 않는다. 별도 snapshot map, 재freeze, 점수나 selector 재계산은 없다. 기존 explicit-snapshot builder와 outer API도 그대로 둔다.

명시적 `run_input`과 outer 옵션을 후보 callback 전에 분리한다. 원래 binding과 입력, 반환된 run, State, scored snapshot의 후보와 세대, schema, policy, corpus, as_of, mode 및 provenance를 대조한다. 누락되거나 파손된 인계는 Generator/Judge/PDF 호출 전에 거절한다. context는 기존 canonical JSON 문자열과 hash이므로 caller나 반환된 State의 후속 변경이 전파되지 않는다.

저장소 루트의 설치된 환경에서 실행할 수 있는 작은 예시는 다음과 같다. `setup_case`와 `Stub`는 테스트 전용 가상 자료와 명시적 mock이다. 실제 기업 근거나 모델 품질을 증명하는 예시가 아니다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. UV_OFFLINE=1 uv run --no-sync python - <<'PY'
from tests.integration.test_v3_evidence_snapshot_consumer import setup_case
from tests.unit.test_v3_report_pipeline import Stub
from skala_rag.graph.candidate_workflow_v3 import run_candidate_report_v3
from skala_rag.reporting.v3_pipeline import ReportGeneratorV3, SemanticJudgeV3

case = setup_case(count=2)
generator_model, judge_model = Stub(), Stub()
candidates, context, report = run_candidate_report_v3(
    case['stages'], case['callbacks'],
    run_input=case['stages'].evidence_research.run_input,
    generate=ReportGeneratorV3(generator_model),
    judge=SemanticJudgeV3(judge_model),
    graph_events=case['events'],
    **case['options'],
)
assert candidates.candidate_index == 2
assert report.status == 'completed' and not report.warning
assert report.validation.valid and report.judgement.verdict == 'pass'
assert report.pdf_validation is None and report.final_allowed is False
print(candidates.status, report.status, context.context_id)
PY
```

호출자는 준비한 기존 `CandidateStagesV3.evidence_research`, evaluator, run 옵션과 `generate(context, feedback)`, `judge(draft, context)`를 명시적으로 넘긴다. 모델 callback에 새 default stub을 설치하지 않는다. `check_pdf(draft, context, structural, judgement)`는 선택 사항이며 기존 PDF renderer/layout validator를 연결할 자리다. callback 생략은 PDF 통과가 아니다.

## 검증 결과를 읽는 기준

이 연결의 통합 회귀는 가상 Source/Chunk, mock retrieval backend와 model callback을 사용한다. EvidenceResearch, 원래 freeze, fixture Technology callable/adapter, five-way join, selector와 기존 보고서 pipeline은 실제 코드로 실행한다. 나머지 evaluator는 synthetic sibling이다. 이 범위에서 actual 평가 성공, 실제 Generator/Judge 성공 또는 실제 보고서 PDF 산출을 주장하지 않는다.

무후보, 무선택, failed, eligibility_unknown은 원래 후보 결과와 함께 읽는다. 보고서 `completed`가 후보 실패를 지우지는 않는다. 구조, 의미, PDF 수정은 기존 공유 2회 예산을 사용하며 소진은 `completed`와 Warning이다. 기술 실패와 stale proof는 `failed`다. 입력 거절은 예외가 될 수 있다. 모든 `ReportRunV3.final_allowed`는 False다.

현재 `SECTIONS`는 REFERENCE를 포함한 총5절이다. 최신 요구의 '5개 section + 마지막 REFERENCE'와의 차이는 미결정으로 남기며 이 작업에서 목차 정책을 바꾸지 않는다. `execution_mode='offline'` 같은 별도 mode나 live 별칭은 없다. 현재 scored artifact 연결은 fixture만 지원한다. actual/scoring gate와 source-only의 eligible-not-ready 경계는 그대로이며 source-only 결과를 이 API의 보고서로 승격하지 않는다.

## 다음 최소 작업과 범위 밖

다음 dependency는 사전 확보한 로컬 자료의 admission, 실제 근거를 사용하는 5개 evaluator/scoring authority, 한 실행의 산출물 저장과 clean 재현, 실제 모델/Judge/PDF 검증이다. 기존 구성의 재사용을 우선한다. #96/#168의 actual authority와 누적 예산 blocker는 유지하되 온라인 수집 readiness를 현 offline 우선 경로의 blocker로 다시 요구하지 않는다.

새 CLI, controller framework, 보고서 DTO, corpus/provider/model 변경이나 semantic/publication gate 개방은 포함하지 않는다. 과거 full-live 조건은 이력으로 보존한다. request-gap, outer double-pin, optional-RAG SegmentError의 기존 한계는 별도 후속 사항이다. 이번 연결은 추가 Discovery/수집/평가/selector 실행으로 그 한계를 보정하지 않는다.

기존 [Python 실행 안내](python-execution.md), [EvidenceResearch snapshot 계약](evidence-snapshot-v3.md), [보고서 pipeline](reporting-v3-pipeline.md), [PDF 계약](pdf-rendering.md)을 함께 읽는다.
