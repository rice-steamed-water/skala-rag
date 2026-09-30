# #29 fixture CLI

> **legacy/호환성 안내:** #166 이후 최종 인터페이스는 [Python 직접 호출](python-execution.md)이다. 이 문서는 병합된 #29 CLI와 종료 코드의 기존 동작을 보존한다. 신규 CLI/옵션/패키징 개발을 요구하지 않으며 parser·테스트도 삭제하지 않는다.

`python -m skala_rag.cli --theme ... --config ...`는 기존 M1 fixture CLI 경로다.
같은 모듈의 `run(...)`을 Python에서 호출하면 argparse 없이 실행하며 반환값은 산출물 `Path`다.
고정 가상 후보 두 개를 #89 v3 controller와 다섯 병렬 평가 branch로 처리한 뒤
병합된 #94 v3 context/다섯 섹션/구조 검증/공유 수정 controller를 호출한다.
Generator/Judge는 deterministic fixture stub이며, #95로 실제 fixture PDF를 렌더·재검증한다.
실제 API·검색·embedding 모델 사용·실제 기업 검증을 수행하지 않는다.

후보별 Evidence ID를 분리하고 최종 freeze snapshots를 보존해 context adapter에 전달한다.
Source는 기존 synthetic metadata이며 서로 다른 후보의 Evidence payload를 같은 ID로 덮어쓰지 않는다.
고정 context의 score/decision/N/A/selector를 그대로 보존한다.

`outputs/<run_id>/`에는 후보 결과, trace, context, draft, structural/Judge/PDF 결과,
pipeline receipt, PDF, RunManifest와 run-result.json을 저장한다.
manifest는 코드 revision/uncommitted, 실제 로드 파일·입력 hash, schema/policy/corpus/index,
report prompt version과 fixture-stub 모델 표시, 예산/사용량 및 artifact byte SHA256을 기록한다.
외부 요청·LLM token·비용은 0이며 fixture branch/Generator/Judge 호출·수정 회수는 실제 trace에서 센다.
manifest.json은 자기 자신을 hash 목록에 넣지 않는다.

trace는 후보 처리 단계와 report_context/report_generate/report_validate/report_judge/report_pdf의
시간·status·입출력 ID만 기록한다. API key·prompt·원문·예외 본문을 넣지 않는다.
출력 context/draft에는 재배포 허용된 가상 원문만 사용하며 outputs/는 git 제외다.

## 기존 exit-code 매핑

아래 0/2/1은 기존 receipt의 `exit_code`와 CLI `main()`의 프로세스 매핑이다.
직접 호출은 이 값을 자동으로 프로세스 종료 코드에 반영하지 않는다. Python exit 0만으로
성공을 판단하지 말고 [receipt 검증 기준](python-execution.md#python-호출의-결과를-읽는-기준)을 따른다.

정상 fixture는 completed/exit0/acceptance=fixture_only,
공유 구조·의미·PDF layout 수정 2회 소진은 completed+Warning/exit2,
context/stale/Judge fail/renderer 오류는 failed/technical_failure/exit1이다.
`publication_allowed=false`이고 `report.md`/validated final을 만들지 않는다.
fixture PDF 검증과 stub 의미 검증을 M3 사실성·품질 성공으로 표시하지 않는다.

live flag/RunInput은 config·policy·runner 실행 전에 거절한다.
승인된 실제 provider·readiness·시간/token/비용 budget과 final publication 통합은 #96 범위다.
이 gate는 Python 호출에도 유지되며 #166이 live 실행을 승인하지 않는다.
`report_adapter`의 기존 fixture receipt seam은 보존하며, 주입 adapter의 알려지지 않은
model/prompt를 기본 stub의 version으로 표시하지 않는다.
