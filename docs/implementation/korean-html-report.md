# 한글 HTML 보고서와 Playwright PDF — #175 / D09 후속

## 승인

2026-09-30 사용자 확인 UI 「제안한 HTML→PDF 경로·의존성 추가 승인」(이슈 #175)에 따라
draft를 정형 한글 HTML로 만들고 Playwright/Chromium으로 PDF화하는 경로와 의존성
`playwright==1.63.0`을 추가한다. 기존 ReportLab `reporting.pdf`(#95)와 테스트는 그대로
보존하며, 이 경로가 실패해도 ReportLab으로 대체하지 않는다. 병합·유료 모델 호출은 승인 범위 밖이다.

## 구성

| 모듈 | 역할 |
| --- | --- |
| `reporting.html_report.render_report_html(draft, context)` | 독립 UTF-8 `<html lang="ko">`. 표시 제목 요약/기업·팀/기술·시장/투자 평가·위험/참고문헌(영문 canonical 섹션명은 `id`·`data-section`). 점수 개요 카드, 후보 비교, 위험, 참고문헌. |
| `reporting.html_pdf.HTMLPDFRenderer` | `(draft, template) -> RenderResult`. proof(구조 valid + Judge pass, context_id·artifact_hash 일치)를 렌더 전에 확인하고 `report-<rev>-<hash8>.html`을 저장한 뒤 **그 저장 bytes**를 Chromium에 넣어 PDF를 만든다. 기존 파일은 덮어쓰지 않는다. |
| `reporting.html_pdf.HTMLPDFLayoutValidator` | 저장 HTML/PDF hash 재계산, draft·context 일치, HTML 재생성 일치, 페이지/A4/인용/섹션/SUMMARY 재검증. hash는 RenderResult가 가리키는 파일의 우발적·렌더 후 수정을 감지할 뿐이며, measurements까지 다시 쓸 수 있는 공격자는 막지 못한다. 불일치는 `PDFArtifactError`. |
| `reporting.korean_report.build_korean_report_pdf` | 위 둘을 묶은 Python 직접 호출 helper. CLI는 없다. |

보안: 모든 텍스트를 `html.escape`하고 raw HTML·이미지·스크립트·외부 링크를 만들지 않는다.
Evidence/Source token은 escape된 문자로 남고 문서 내부 `#src-…` anchor만 건다. CSP
`default-src 'none'`을 넣고, Chromium은 sandbox·JS 비활성·service worker 차단·다운로드 금지이며
모든 네트워크/파일 요청을 `route.abort()`한다.

한글 서술: `GENERATOR_SYSTEM`은 서술 전체를 한국어로, 인용·고유명사·ID·자료 제목은 원문 유지를
요구하고 `JUDGE_SYSTEM`은 한국어 여부와 충실성을 점검한다(`PROMPT_VERSION=report-v3-2`).
실제 모델의 준수 여부는 이 이슈에서 실측하지 않았다.

## 사용

```bash
uv sync
uv run playwright install chromium   # 최초 1회
uv run python examples/korean_report_fixture.py
```

예제는 가상 fixture context를 `FixtureReportLLM`으로 `run_report_v3`에 통과시키고
`outputs/issue-175-demo/<timestamp>/`에 HTML·PDF를 쓰며 경로·hash·page_count·
summary_fraction을 JSON으로 출력한다. 직접 호출은
`build_korean_report_pdf(context, draft, structural, judgement, output_dir, execution_mode)`.

## 검증

- PDF 최대 5페이지, 모든 페이지 A4, 인용/source token과 섹션 제목이 추출 텍스트에 존재.
- SUMMARY: PDF 텍스트 좌표에서 요약 제목과 다음 섹션 제목의 baseline 간격 ÷ A4 높이 ≤ 0.5.
  다음 제목이 다른 페이지이거나 못 찾으면 실패(action=revise). 자르기·축소·숨김으로 통과시키지 않는다.
- 초과는 `action=revise`로 기존 공유 수정 예산(최대 2회)에 넘기고, browser 부재·예외는
  `PDF_RENDER_FAILED`(action=fail, 임시 `.tmp` 파일은 삭제), hash 불일치·stale·execution_mode≠context는
  `PDFArtifactError`/`PDF_UPSTREAM_NOT_VALIDATED`. HTML/PDF는 검증 뒤에만 최종 이름으로 바뀐다.
- 섹션 검사: 다섯 제목이 각각 한 줄 전체로 존재하고 페이지/y 순서가 오름차순이어야 한다.
- Chromium 미설치 시 browser 테스트는 사유와 함께 skip된다.
- 테스트: `tests/unit/test_html_report.py`(브라우저 없음),
  `tests/integration/test_html_pdf_browser.py`(실제 Chromium, marker `browser`).

## 한계

- fixture/stub으로 검증한 보고서는 `final_allowed=False`이며 실제 기업·LLM 검증 완료가 아니다.
  `final_allowed`는 live 모드·stub 아님·검증 통과일 때만 True가 될 수 있고 최종 발행은 #96 소관이다.
- 한글 서술 준수는 LLM 지시·Judge 검증에 의존하며 번역을 이 renderer가 만들지 않는다.
- Chromium 설치가 필요하다(`playwright install chromium`). 실행마다 새 output_dir을 쓴다.
- 시각 검증은 fixture 1건의 육안 확인이며 광범위한 시각 회귀는 없다.
