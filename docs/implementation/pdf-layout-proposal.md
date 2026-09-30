# D09 PDF renderer·layout 제안 — #95

상태: **APPROVED — 2026-09-30 사용자 확인 UI 「제안한 설정·의존성·오류 처리 승인」**.
정식 구현/검증은 [PDF rendering](pdf-rendering.md)과 D09 승인 기록을 따른다.
[기존 D09 기록](decisions.md)의 PDF OPEN 항목에 대한 하나의 최소 구현안이다.

## 제안

- Renderer: ReportLab. 프로젝트 의존성을 uv로 추가하고 4.4.9로 고정한다.
- Font: NanumGothic Regular/Bold, SIL OFL 1.1 원문을 함께 보존한다.
  [배포 license](https://raw.githubusercontent.com/google/fonts/main/ofl/nanumgothic/OFL.txt).
- Paper: A4(595.28 × 841.89 pt), 사방 여백 51 pt(약 18mm).
- Body: 10.5 pt / leading 15 pt. 한글 줄바꿈 및 긴 URL 줄바꿈을 지원한다.
- Heading: 14 pt / leading 19 pt. v3 다섯 섹션과 모든 원래 점수/label/인용/REFERENCE를 보존한다.
- SUMMARY 측정: heading부터 마지막 SUMMARY block까지 실제 draw bounding box 높이를
  전체 A4 페이지 높이로 나눈다. heading도 포함하며 여러 페이지에 걸치면 실패다.
- 전체 PDF는 표지/REFERENCE 포함 최대 5페이지, SUMMARY fraction은 최대 0.5다.
- 외부 URL/이미지를 fetch하지 않고 원문 HTML/명령을 실행하지 않는다. 긴 본문을
  임의 요약/삭제하거나 점수·판정을 바꿔 분량을 맞추지 않는다.

## 제안한 실패 회계

layout 초과는 기존 공유 보고서 수정 예산(최대2회)을 소비하는 revise feedback이다.
별도 renderer 재시도 loop를 만들지 않는다. 수정된 문장은 새 draft의 구조·의미 검증을
다시 거쳐야 한다. 렌더 예외는 즉시 실패다. 예산 소진/미검증 PDF는 진단 artifact이며
제출용 final로 승격하지 않는다. v3 completed Warning 인계는 기존 #82 규칙을 따른다.

## 검토용 fixture preview

2026-09-30 기존 Codex bundled ReportLab(4.4.9)을 사용해 outputs/issue95-proposal에
가상 PDF를 생성했다. 프로젝트 패키지를 설치하거나 renderer를 승인으로 표시하지 않았다.
가상 회사·점수·판정·인용이며 실제 기업 조사 결과가 아니다.

- 실제 PDF 페이지: 1
- SUMMARY: heading 포함 실제 배치 bbox 높이/페이지 높이 약0.1093
- PDF 페이지 재열람과 PNG 렌더로 한글/표/인용/REFERENCE/URL 잘림을 확인했다.
- 최소 preview의 측정은 renderer draw 좌표다. 정식 구현에서는 저장 PDF 페이지·
  artifact hash 및 인용/REFERENCE 보존을 추가 검사하고 T17 경계 테스트를 만든다.

실제 PDF는 outputs(커밋 제외)에 있다. 이 fixture preview는 #94 실제 보고서의
Semantic Judge 통과나 M3 live PDF 생성 성공을 뜻하지 않는다. 같은 이슈에서
renderer·fixed profile·measurement·validation·단위/fixture 테스트를 구현한다.
