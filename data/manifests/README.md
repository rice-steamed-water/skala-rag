# 코퍼스 후보 초안 — #13

`corpus-candidates.draft.json`은 실제 공개 자료의 metadata를 정리한 **검토 초안**이다.
가상 fixture나 승인된 실행 manifest가 아니며 #44 runtime schema를 대체하지 않는다.
모든 문서는 `approved=false`, reviewer 미지정, `runtime_ready=false`다.

[병합된 PR #92](https://github.com/rice-steamed-water/skala-rag/pull/92)에 따라
코퍼스 200페이지 한도와 D13 페이지 산정은 적용 제외다. 페이지 예산 필드와 합계 표를
제거했으며 페이지 수 미상이나 HTML PDF snapshot 미생성은 코퍼스 차단 사유가 아니다.

| document_id | 자료 / 버전 | 원문 형식 | 준비 상태 |
| --- | --- | --- | --- |
| pi-pi0-v4 | Physical Intelligence π0 / arXiv v4 | PDF | 원문 hash·metadata 관측, 권한/추출/승인 대기 |
| pi-pi05-v1 | Physical Intelligence π0.5 / arXiv v1 | PDF | 원문 hash·metadata 관측, 권한/추출/승인 대기 |
| skild-brain-20250729 | Skild AI 기술 소개 | HTML | 원문 hash·metadata 관측, 권한/추출/승인 대기 |

## 문서 선택과 귀속

- 두 논문은 표지에서 Physical Intelligence를 확인했다. 기술 구조·평가 조건 검토
  후보이며 회사 적격성·투자라운드·재무·독립 성능 검증을 대신하지 않는다.
- Skild AI 공식 글은 회사 기술 설명 후보다. 자기주장과 독립 근거를 구분한다.
- 실제 Candidate 정규화 전이므로 `candidate_ids=[]`다. `proposed_company`는 검토용
  표기이며 runtime ID가 아니다. 실제 ID를 매핑하기 전에는 회사 문서로 검색하지 않는다.
  다른 회사나 industry 공통 문서로 자동 승격하지 않는다.
- 현재 영문·해외 기업 자료만 있다. 국내 후보나 한/영 비교셋의 완성본이 아니며
  한국어 문서와 실제 평가 후보는 #43의 지원 범위에 맞춰 보완한다.

## 관측과 사용 권한

2026-09-30에 고정 arXiv PDF 2건과 회사 HTML을 임시 경로에서 확인해
원문 bytes와 SHA-256을 기록했다. 임시 원문은 커밋하지 않는다. hash는 당시 관측이며
미래 다운로드 동일성·추출 품질·historical snapshot 가용성을 보장하지 않는다.

공개 열람 가능성과 재배포·RAG 처리 권한은 별개다. `permission_note`에 미확인 사항을
남겼으며 사용 권한/포함 승인은 아직 없다. arXiv license·회사 이용조건을 검토해
승인자가 허용 범위를 기록한다. 원문 PDF/HTML, index, weights는 저장소에 넣지 않는다.

## 후속 인계

[코퍼스 준비 상태·모델 점검](../../docs/implementation/corpus-readiness.md)을 읽는다.
#44는 승인된 runtime manifest 변환·문서 승인·version/hash·index 입력 대조를,
#49는 실제 추출 품질과 원본 페이지/슬라이드/section 인용 위치를 확인한다.
페이지 총량 검사 없이도 인용 provenance는 보존한다.
새 corpus_version 승인 시 실제 source/candidate ID, 원문 보관 경로, reviewer,
사용 권한과 추출 상태를 채운다. 이 초안을 그대로 인덱싱하지 않는다.

## #49 로컬 추출 검증용 승인 기록

2026-09-30 사용자 「전부 다 허용하고 진행해줘」에 따라 π0/π0.5 두 PDF를
로컬 추출 검증에 사용했다. `issue49-real-validation-v1.json`은 해당 승인·hash·귀속과
실제 추출 상태(partial)를 담은 별도 runtime manifest이며 기존 후보 초안을 승인
문서로 바꾸지 않는다. `issue49-source-snapshots.json`은 실제 Source metadata다.
원문·추출 텍스트는 data/local 및 outputs에만 있고 커밋하지 않는다.

두 문서의 이미지/OCR·회전 텍스트 layout 경고가 남아 있으므로 #44 인덱싱 gate는
통과하지 못한다. 이 검증은 embedding/index 준비 완료가 아니다. 세부 결과와 범위는
[추출 검증 기록](../../docs/implementation/extraction-validation.md)을 참고한다.
