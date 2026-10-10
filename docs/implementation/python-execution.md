# Python 직접 실행 — 최종 실행 인터페이스 (#166)

## 결정과 현재 구현의 경계

사용자는 2026-09-30 작업 대화에서 “cli 관련 부분은 더이상 진행하지 말자. python을 직접 실행하는 것으로 끝내자.”, “issue로 만들어서 진행해. 설계 문서 업데이트부터”라고 지시했다. [#166](https://github.com/rice-steamed-water/skala-rag/issues/166)과 [EXECUTION-PYTHON-DIRECT 승인 기록](decisions.md#execution-python-direct)에 따라 **Python에서 기존 callable/Graph를 직접 호출하는 방식으로 마무리한다.** 신규 CLI·flag·subcommand·console-script 패키징·CLI UX/배포 개선은 더 진행하지 않는다. 기존 CLI/parser·테스트는 삭제하지 않고 호환성으로 보존한다.

구현 확인 기준은 `5bebad436ec5fe032f23b0c5bf4b503bbc4a7a27`이다. #29/PR #118에서 병합된 [`skala_rag.cli.run`](../../src/skala_rag/cli.py)은 이미 존재하며 `pathlib.Path`(해당 실행의 산출물 디렉터리)를 반환한다. 아래 예시는 모듈 이름에 `cli`가 남아 있는 **기존 Python callable 재사용**이지 `main()`/argparse 실행이 아니다. 새 `app.py`/`run.py`나 `RunResult` 반환 API를 구현했다고 주장하지 않는다. `python -m skala_rag.cli`는 [기존 CLI 호환 경로](fixture-cli.md)다.

#166 당시 변경은 문서뿐이었다. 아래 #211의 fixture 연결은 별도 코드 변경이다. #166에는 runner 추출/이동, runtime schema·정책 필드 변경, #96 live 통합, 평가 정책·provider 변경·유료 실행 승인·모델 다운로드가 포함되지 않았다. `pyproject.toml`과 `uv.lock`은 위 기준 tree에 추적되어 있으며 설치 절차는 그대로 유지한다.

## 저장소 루트에서 실행하는 offline fixture

[README 설치 절차](../../README.md#usage)에 따라 Python 3.11+와 uv를 준비하고 저장소 루트에서 `uv sync`로 환경을 구성한다. 의존성 설치에는 네트워크가 필요할 수 있지만, 아래 fixture 호출은 이미 설치된 환경에서 외부 API·유료 모델·모델 다운로드 없이 실행한다. `--no-sync --offline`은 **uv 환경 실행 옵션**이며 프로젝트 CLI를 개발하거나 실행하는 옵션이 아니다.

다음 명령은 파일을 새로 만들 필요 없이 Python 코드를 직접 실행한다. 출력은 git에서 제외된 `outputs/` 아래에 저장된다.

```bash
uv run --no-sync --offline python - <<'PY'
import hashlib
import json
from pathlib import Path

from skala_rag.cli import run

output = run(
    "Physical AI robotics",
    output_dir="outputs",
    policy_path="configs/scoring.v3.json",
    catalog_path="configs/scoring.draft.json",
    config_path="tests/fixtures/cli-input.json",
    pdf_profile="configs/pdf.layout.v1.json",
    fixture_ratings=(5, 4),
    fixture_judge_verdict="pass",
)
assert isinstance(output, Path)
receipt = json.loads((output / "run-result.json").read_text(encoding="utf-8"))
manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
print("fixture artifacts:", output)
print(json.dumps(receipt, ensure_ascii=False, indent=2))
assert receipt["execution_mode"] == "fixture"
assert receipt["workflow_status"] == "completed"
assert receipt["acceptance"] == "fixture_only"
assert receipt["warnings"] == []
assert receipt["publication_allowed"] is False
assert receipt["reason"] == "FIXTURE_PDF_VERIFIED"
assert manifest["workflow_status"] == receipt["workflow_status"]
assert manifest["run_outcome"] == receipt["run_outcome"]
for name, artifact in manifest["artifacts"].items():
    path = Path(artifact["artifact_path"])
    assert path.is_file(), name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["artifact_hash"], name
assert not (output / "report.md").exists()
print("fixture receipt/artifact hashes verified; no validated final")
PY
```

고정 가상 후보 두 개를 v3 후보 controller·다섯 평가 branch·결정적 selector로 처리한다. 주제는 manifest 입력에 기록할 뿐 실제 검색·주제별 기업 발견을 하지 않는다. 기본 Generator/Judge는 deterministic fixture stub이며 #94 보고서 경로와 #95 실제 PDF renderer를 사용한다. 정상 경로는 후보 결과·trace·context·draft·pipeline·검증 결과·PDF·receipt·manifest를 남긴다. 실패 경로에는 없는 산출물이 있을 수 있으므로 파일 존재만으로 성공을 추정하지 않는다.

## Python 호출의 결과를 읽는 기준

`run()` 반환값은 결과 DTO가 아니라 디렉터리다. 이 경로의 `run-result.json`에 있는 `workflow_status`, `run_outcome`, `warnings`, `acceptance`, `publication_allowed`, `reason`을 함께 확인한다. draft/findings는 해당 실행의 `draft.md`, `report-draft.json`, `report-pipeline.json`, `validation-results.json` 등 실제 생성된 파일과 연결해 읽는다. 일반 계약의 `RunResult`는 목표 제안이며 이 callable의 반환 shape로 오인하지 않는다.

| 경로 | receipt·산출물 의미 | 발행 경계 |
| --- | --- | --- |
| 정상 fixture | `workflow_status=completed`, `acceptance=fixture_only`, `warnings=[]`; 기본 PDF 성공은 `reason=FIXTURE_PDF_VERIFIED` | fixture 검증일 뿐 실모델 의미 검증·M3 성공 아님 |
| 구조·의미·PDF layout 공유 수정 2회 소진 | `workflow_status=completed`, `acceptance=warning`, Warning 목록과 현재 draft/findings 보존 | 검증 실패/미실행을 pass로 바꾸지 않음 |
| context/upstream 파손·stale proof·보고서 fatal | `workflow_status=failed`, `run_outcome=technical_failure`, `acceptance=rejected`, 진단 reason | 투자 비추천 또는 Warning 품질 소진으로 바꾸지 않음 |
| 입력/정책 거절·파일 I/O 등 예외 | `run()`이 예외를 전파할 수 있고 receipt가 없을 수 있음 | 경로 반환/receipt 생성을 보장하지 않음; 오류를 숨기지 않음 |

**모든 fixture의 `publication_allowed`는 false다.** 검증된 PDF가 있어도 제출용 final/`report.md`를 발행하지 않는다. 상태 completed, 투자 추천, 검증 수용, 최종 발행은 서로 다른 사실이다.

직접 호출은 receipt의 `exit_code`를 Python 프로세스 종료 코드로 자동 변환하지 않는다. fatal receipt를 받은 뒤 Python이 정상 종료할 수도 있으므로 **프로세스 exit 0만으로 보고서 성공을 판단하지 않는다.** 위 assertion은 명시된 정상 fixture 시나리오용이며 Warning/fatal을 테스트할 때 그 기대 상태를 별도로 검사한다. 공유 수정 소진은 같은 호출에서 `fixture_judge_verdict="revise"`로 검증할 수 있다.

기존 `exit_code=0/2/1`과 policy의 CLI 관련 필드명은 호환성 구현 사실로 보존한다([기존 매핑](fixture-cli.md#기존-exit-code-매핑)). #166은 이를 삭제/이름 변경하거나 새로운 enum·스키마를 승인하지 않는다. 신규 실행 완료 조건은 CLI 기능이나 종료 코드가 아니라 Python 상태/receipt 및 근거 산출물이다.

## v3 actual·replay 직접 호출 (#168)

작업 브랜치의 `examples/v3_actual_run.py`의 `preflight_sources`·`actual`·`replay`(뒤 둘은 `skala_rag.graph.actual_runner_v3.run_actual`·`run_replay` wrapper)는 이 결정대로 CLI 없이 Python으로만 호출한다. `preflight_sources`는 권위 없이 보존 Source·index·model만 확인하고 항상 `preflight_blocked`로 끝난다. `actual`의 기본은 provider 호출 없는 preflight이고, 실제 실행에는 `execute=True`, 명시적 key, 저장소에 없는 외부 `ActualAuthorityV3`가 필요하다. 인자·입력·권위·예산 경계는 [actual-v3 실행과 replay](actual-execution-v3.md)에 있다. 실제 유료 scored 실행 기록은 없다.

## v3 source-only 직접 호출 (#209)

[Source-only v3 계약](source-only-v3.md)은 기존 outer의 Discovery 캡처 재생, CompanyResearch, Eligibility, archive/advance, no-selection handoff를 별도로 연결한다. 기본 fixture callable은 바꾸지 않는다. 입력의 `execution_mode="live"`는 live DTO 검증 경계이며 실제 HTTP, 새 주제 Discovery, 기업 사실 확인, 평가/점수/보고서 발행 승인을 뜻하지 않는다. source-only는 `eligible`도 명시적 not-ready 실패로 보존하고 full scoring/live evaluation의 기존 승인 gate를 열지 않는다.

## v3 EvidenceResearch artifact fixture 호출 (#211)

[EvidenceResearch → snapshot v3 계약](evidence-snapshot-v3.md)은 기존 Python oracle와 public outer Graph에 선택적 `CandidateStagesV3.evidence_research` binding을 연결한다. CompanyResearch/Eligibility의 원래 근거와 전체 ResearchOutcome을 후보별 JSON State로 검증·보존한 뒤 기존 `freeze_snapshot`, fixture Technology callable, adapter, five-way join을 실행한다. binding이 없으면 기존 callback 경로를 유지한다. 이 연결은 fixture 검증이며 source-only eligible-not-ready, actual 평가/점수 승인 gate와 full-live 선행 조건은 바꾸지 않는다.

## Offline outer → 보고서 직접 인계 (#213)

[Offline 실행 범위와 예시](offline-execution.md)의 `run_candidate_report_v3(stages, evaluators, *, run_input, generate, judge, check_pdf=None, graph_events=None, run_profile=None, **options)`는 기존 public outer를 한 번 실행하고 원래 scored State snapshot을 기존 context와 보고서 pipeline으로 넘긴다. 반환은 `(CandidateRunV3, ReportContextV3, ReportRunV3)`다. 기존 fixture callable/CLI, explicit-snapshot API와 #211 artifact 경로는 보존한다. 이 연결의 검증은 가상 자료와 명시적 mock이며 실제 평가 authority, 유료 모델, source-only scoring, 정책이나 발행 gate를 열지 않는다. PDF callback 생략은 PDF 검증 성공이 아니다.

## 임의 기업 보고서 직접 호출 (#233)

`skala_rag.company_report.run_company_report(company_name, *, config_path, output_dir, research=None, ..., actual_admission=None, authority=None)`가 공개 진입점이다. 기본은 저장 자료와 로컬 BGE-M3만 쓰고, 대상 기업 조사는 `research=True`와 한도가 있을 때만 한다. 설정 우선순위, 거절 영수증, 실제 실행 authority와 승인된 조사 후 재승인은 [기업 보고서 실행](company-report.md)에 있다. 운영자 소유 `readmit_after_research` callback이 정확한 최종 자료와 새 review를 인증해야 하며, 원래 admission과 누적 예산은 유지한다. 실제 API·보고서 실행 증거는 아직 없다.

## 유지하는 검증·안전 gate

- source/context/artifact hash, 실제 로드한 설정·policy·corpus·모델/prompt 버전, 코드 revision/uncommitted, trace·manifest·예산/사용량을 보존한다. manifest 자체는 자기 hash 목록에 포함하지 않는다.
- 동일 context·현재 draft에 묶인 구조·Judge·PDF proof만 사용한다. 새 draft에 이전 proof를 재사용하거나 미검증 파일을 final로 승격하지 않는다.
- 정상·Warning·fatal·입력 거절을 분리하고 실제 PDF 분량/인용과 저장 byte hash를 확인한다. fixture/stub을 실제 기업 조사·RAG/LLM 품질 실측으로 보고하지 않는다.
- live는 승인된 정책·provider·corpus·credential/readiness와 시간/token/비용 예산을 충족해야 한다. Python 직접 호출은 기존 gate를 우회하지 않으며 현 fixture callable은 live 설정을 거절한다.
- #96의 live 통합은 해당 담당 범위다. Python 호출/결과 검증 계약으로 정합화하되 추가 CLI 구현을 요구하지 않는다. #111의 도식은 이 결정과 연결할 후속 대상이며 원본 HTML·raws·생성 `.archify/`를 이번 문서 변경에서 수정하지 않는다.
- #62/PR #123에서 병합된 `skala_rag.agents.m2_component_live`와 `m2_research_live`도 키워드 인자 `run(*, root, input_path, output_dir, live=...)` callable을 제공한다. `m2_component_live.run`은 `live=False` 기본값에서 외부 요청 없이 사전 점검만 하고, `m2_research_live.run`은 `live`가 필수이며 `live=False`이면 `ValueError`를 낸다. 해당 문서의 `python -m ...` 예시는 기존 호환 entrypoint로 보존하며, 그 모듈에 flag·subcommand를 더 추가하지 않는다. live 호출·비용·receipt 해석은 [M2 검증 기록](m2-verification.md)의 승인·예산 gate를 그대로 따르며 이 문서가 live 실행을 승인하지 않는다.
