# Offline v3 데모 패키지 (#219)

[문서 홈](../README.md) · [Python 직접 실행](python-execution.md) · [Offline 연결 계약](offline-execution.md) · [Source-only v3](source-only-v3.md) · [PDF 렌더링](pdf-rendering.md)

## 이번 완료 목표

사용자가 승인한 목표 재설정은 **offline 합성 데모 산출물과 재현 안내 완성**이다. 실제 프로젝트 전체, 기업 투자평가, M3 live 완료를 뜻하지 않는다. README의 기존 로컬 데모와 과거 검증 기록은 이력으로 유지한다.

`examples/v3_offline_demo.py`는 기존 `setup_case(count=2)` 테스트 helper를 명시적으로 재사용한다. 기존 public `run_candidate_report_v3`를 한 번 호출하고 CompanyResearch seed → EvidenceResearch 원래 outcome → freeze/snapshot → atomic join → pinned registry의 승인 산술/판정/selector → 원래 context → 보고서 → 실제 ReportLab PDF로 연결한다. 점수, ID, generation, source, snapshot을 재작성하거나 다시 계산하지 않는다. 새 production runner, CLI, DTO 계약은 없다.

기업, 검색 backend, evaluator의 관측과 Generator/Judge 응답은 합성이다. Graph, 원래 artifact 소비, 승인된 산술과 ReportLab 렌더링은 실제 코드 실행이다. Judge의 pass는 stub이며 실제 기업 사실성이나 semantic authority의 증거가 아니다. `final_allowed=false`, `publication_allowed=false`를 유지한다.

## 재현

Python 3.11+와 uv를 준비한 **source checkout + 개발 의존성 환경**만 지원한다. wheel 단독 실행을 보장하지 않는다. 저장소 루트에서:

```bash
uv sync --frozen --offline
uv run --offline --frozen python examples/v3_offline_demo.py
```

첫 명령은 의존성이 로컬 캐시에 있을 때만 offline 설치된다. 캐시가 없다면 기존 [설치 안내](../../README.md#usage)에 따라 환경을 먼저 준비한다. 실행 중에는 API, 유료 모델, 새 모델 다운로드, 새 corpus/index를 호출하지 않는다. Chromium도 필요 없다.

직접 실행은 `outputs/v3-offline-demo-<uuid>/`를 만들고 경로를 출력한다. 프로젝트 환경의 Python으로 스크립트 절대 경로를 실행해도 설정은 checkout 루트에서 찾는다. 명시적 출력 경로가 필요하면 Python 함수를 직접 호출한다:

```bash
uv run --offline --frozen python -c 'from pathlib import Path; from examples.v3_offline_demo import run_offline_demo; print(run_offline_demo(Path("outputs/v3-offline-demo-manual")))'
```

출력 디렉터리는 새 경로여야 한다. 이미 존재하면 비어 있어도 `FileExistsError`로 거절한다. 예제는 기존 helper의 상대 설정 경로를 위해 잠깐 작업 디렉터리를 바꾸고 복원하므로 단일 프로세스로 실행한다. 병렬 호출용 API가 아니다.

## 산출물 확인

- `report-demo.md`: 원래 현재 draft의 Markdown. final 보고서가 아니다.
- 실제 `*.pdf`: 기존 profile/font로 렌더한 PDF. `renders.json`에 원래 render 결과, bytes hash, 실제 페이지 수와 SUMMARY 비율을 보존한다.
- `candidates.json`: 원래 CandidateRunV3, 두 후보의 scores/decisions/selection, research outcomes/state/snapshots와 오류.
- `context-dto.json`, `context.json`: 원래 ReportContextV3의 ID/payload와 읽기용 snapshot.
- `report.json`: 원래 ReportRunV3의 draft, 구조/Judge/PDF 검증, revision, 오류와 final 금지.
- `graph-events.json`, `trace.json`: 기존 graph event와 trace.
- `demo-manifest.json`: demo 전용 버전/한계/상태/파일 상대 경로/SHA-256. 공식 RunManifest나 서명된 actual-runtime receipt가 아니다. manifest 자체는 자기 hash 목록에서 제외한다.

정상 fixture에서는 두 후보 모두 처리된 `ready_for_v3_reporting`, 보고서 `completed`, `warning=false`, 구조/PDF 검증 `valid=true`를 확인한다. PDF는 실제 저장 파일을 다시 읽는 `PDFLayoutValidator`를 통과해야 한다. [#223 승인 목차](reporting.md#현재-v3-목차-본문-5절과-마지막-reference-223)는 SUMMARY → COMPANY & TEAM → TECHNOLOGY → MARKET → INVESTMENT ASSESSMENT & RISKS → REFERENCE다. 기존 Generator·fixture 응답·renderer를 사용해 본문 5절과 마지막 REFERENCE를 만들며 별도 runner를 추가하지 않는다. 기술과 시장 본문·역할 출력·인용은 분리한다. 최대 5페이지, SUMMARY 반 페이지, 인용 보존을 완화하지 않는다. `pdf.verified=true`여도 최종 발행 권한은 false다. 실패하면 원래 진단 JSON과 manifest를 남기고 예외를 반환한다.

manifest의 `observed_calls`는 fixture backend/evaluator/Generator/Judge 호출 수다. `product=0`은 이 예제에 주입된 구현이 모두 fixture라는 경계이며 실제 provider 사용량 실측으로 해석하지 않는다. 미제공 token/cost 사용량은 `usage=null`로 유지한다. UUID와 PDF 생성 metadata가 달라질 수 있어 실행 간 bytes 동일성을 보장하지 않는다. hash는 해당 실행에서 저장된 파일의 무결성 확인용이다.

## 검증과 남은 작업

개발 중에는 신규 `tests/integration/test_v3_offline_demo.py`만 실행한다. 정상 연결, 원래 두 후보 join/snapshot 보존, 실제 PDF/분량/인용/hash/final 금지, network/DNS 차단, 기존 출력 디렉터리 거절을 검사한다. 최종 전체 pytest/Ruff/format/build와 AST graphify는 부모가 한 번 실행하고 독립 정적 리뷰 한 명으로 마무리한다. 기존 테스트를 삭제하거나 완화하지 않는다.

[로컬 BGE 검증](local-bge-validation.md), 기존 PDF/index source-proof와 [source-only 경계](source-only-v3.md)는 별도 증거다. 이 예제가 그 실제 자료를 입력으로 소비한 것은 아니다. #222의 실제 기업 Source 생산·public consumer 연결은 완료했고 #223은 승인 목차를 정합화한다. 실제 적격성·D05/D06 semantic/applicability authority, Market 인계, actual 평가/모델·공유 budget/campaign, 실제 투자 보고서 재현은 남아 있다. #96/#168이나 교수님 필수 요구사항을 이 데모로 완료·면제 처리하지 않는다.
