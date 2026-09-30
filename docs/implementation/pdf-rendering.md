# 실제 PDF renderer와 layout 검증 — #95 / D09 승인

2026-09-30 사용자 확인 UI 「제안한 설정·의존성·오류 처리 승인」에 따라
ReportLab4.4.9·NanumGothic(OFL 포함)·A4·18mm 여백·본문10.5pt/행간15pt·제목14pt를
고정했다. 구현은 reporting.pdf, profile은 configs/pdf.layout.v1.json이다.
renderer가 원격 이미지/URL을 fetch하거나 원문 HTML을 실행하지 않는다.

## 입출력

`PDFRenderer(profile, output_dir, proof, execution_mode)`는 RenderPdf Protocol의
(draft, template) 호출 경계다. proof(draft)는 최신 ValidationResult/ReportJudgement를
반환해야 한다. 양쪽 pass, context_id와 원래 artifact_hash 일치 전에는 렌더하지 않는다.
PDF는 문장/점수/label/인용을 수정하지 않는다. v3 다섯 섹션 순서를 확인하고 한글·표·
URL·Evidence/Source token을 보존한다. #94는 이 경계에 실제 검증 결과를 주입한다.

출력 RenderResult에는 실제 artifact path/page_count와 PDF bytes SHA-256,
context/report ID·draft revision/hash·renderer/template 버전·실제 draw bbox·SUMMARY
height/fraction·layout checks가 있다. fixture 또는 stub 의미 검증은 제출용 final로
승격하지 않는다. 그 결과가 실제 기업/모델 품질 검증을 의미하지 않는다.

`PDFLayoutValidator(draft, context, render)`는 저장 파일을 다시 열어 hash/페이지 수/
A4 크기/배치 경계/인용/section 보존을 확인한다. SUMMARY는 제목부터 마지막 block까지
실제 draw bbox의 총 높이를 **전체 A4 페이지 높이**로 나누며 0.5 이하, 단일 페이지여야
한다. 표지/REFERENCE도 전체 PDF 최대5페이지에 포함한다. Markdown 길이로 분량을
추정하거나 stub 수치를 쓰지 않는다. title/subtitle의 문장 내용은 바꾸지 않는다.

## 실패 회계

분량·배치·인용 위반은 action=revise로 기존 공유 보고서 수정 예산(최대2회)에 넘긴다.
renderer 자체에는 retry loop가 없다. 입력 문장을 수정하면 새 draft의 구조/의미 검증을
다시 거쳐야 한다. renderer 예외는 artifact_path=None/action=fail이며 즉시 실패한다.
저장 PDF hash·draft/context mismatch는 PDFArtifactError로 fatal 처리한다.
예산 소진/미검증 PDF는 진단 artifact일 뿐 final이 아니다. v3 Warning 경로는 기존
#82/#94 runner/controller에서 처리하고 이 모듈이 새로운 재시도를 추가하지 않는다.

## 재현과 검증

`uv sync --locked`, `uv run pytest tests/unit/test_pdf_renderer.py`.
가상 ReportDraft·주입 proof로 실제 PDF를 만든 테스트이며 실제 LLM/Judge는 호출하지
않는다. 정상 fixture PDF, SUMMARY>0.5·총5페이지 초과·누락 인용·HTML/원격 이미지
비실행·렌더 예외·stale proof·기존 파일 보호·저장 후 tamper를 검사한다. 정상 PDF의
한글/표/긴 URL/인용을 PNG로 짧게 육안 대조한다. 광범위한 시각 회귀나 모델 비교는 없다.

원래 artifact와 같은 경로가 이미 있으면 덮어쓰지 않으므로 실행마다 새 output_dir을
명시한다. outputs는 커밋하지 않는다. 실제 자료 보고서의 성공 증거는 #94/#96의 동일
최종 artifact로 확인하며 renderer fixture 통과를 M3 live 완료로 표시하지 않는다.

## 시각 디자인 (#173)

ReportLab renderer 위에 절제된 navy/teal 투자 보고서 스타일을 적용한다(A4·NanumGothic·
18mm 여백·≤5페이지·SUMMARY ≤0.5페이지·proof/hash/final 게이트는 그대로). 구현은
`reporting/pdf_presentation.py`(private)와 `pdf.py`다.

- 타이포그래피: 제목/섹션(navy)·소제목(teal)·본문(ink), 표 헤더는 navy 배경·흰 글씨,
  줄무늬 행. 모든 페이지에 헤더(보고서명)와 푸터(모드 FIXTURE/LIVE, PAGE n).
- 시각 요소: 후보 비교 표, 후보별 점수 카드(normalized_score·label·coverage_pct·
  weighted_missing_pct), 영역별 `dimension_score_pct` 막대(고정 0–100 트랙).
- 데이터 출처: v3 구조 검증(`validate_report_v3`)이 통과한 뒤 같은 proof의
  `checks["pdf_presentation"]`에 담은 검증된 `ScoreSummary`/`InvestmentDecision`/
  `CandidateOutcome`만 쓴다. renderer가 context_id·draft_hash·execution_mode·정확한
  draft 블록 일치를 다시 확인하며 불일치는 렌더 실패(fail-closed)다. 본문 prose에서
  숫자를 추출하지 않으며, proof가 없으면 시각 요소는 0개다.
- 값 표기: Decimal 원문 그대로 표시(반올림 없음). None은 "미상"으로 표시하고 막대/채움을
  그리지 않으며 0으로 렌더하지 않는다. 선택 없음(no-selection) 모드는 그대로 유지된다.
- 측정: `layout_measurements`에 `presentation_version`과 `visualizations` 개수를 기록한다.
- 검증: `tests/unit/test_pdf_design.py`가 실제 PDF를 만들어 텍스트 추출로 확인한다.
  시각(육안·래스터) 검증은 수행하지 않았다.
