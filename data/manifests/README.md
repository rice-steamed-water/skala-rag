# 코퍼스 후보 초안 — #13

`corpus-candidates.draft.json`은 실제 공개 자료의 metadata를 정리한 **검토 초안**이다.
가상 fixture나 승인된 실행 manifest가 아니다. #44의 runtime schema를 대체하지 않는다.
모든 문서는 `approved=false`, reviewer는 미지정이며 D13은 OPEN이다.
`counted_pages`는 아래 제안 산정법을 적용한 값이지 승인된 예산 소비 기록이 아니다.

| document_id | 자료 / 버전 | 원본 페이지 | 포함 구간 제안 | counted_pages (제안) | 승인 |
| --- | --- | --- | --- | --- | --- |
| pi-pi0-v4 | Physical Intelligence π0 / arXiv v4 | 17 | 1–17 전체 | 17 | 대기 |
| pi-pi05-v1 | Physical Intelligence π0.5 / arXiv v1 | 19 | 1–19 전체 | 19 | 대기 |
| skild-brain-20250729 | Skild AI 기술 소개 / HTML | 미상 | 미확정 | 미상 | 대기 |

확인된 PDF 후보 소계는 **36페이지**, 한도와의 산술 차이는 **164페이지**다.
HTML 후보 1건이 미상이므로 세 문서의 전체 합계는 **미확정**이다.
미상을 0이나 1로 바꾸어 전체 ≤200 준수를 선언하지 않는다.
이 표는 모든 회사/Agent/run이 공유하는 코퍼스의 출발점이며 기업마다 200페이지가 아니다.

## 문서 선택과 귀속

- 두 논문은 표지에서 Physical Intelligence를 확인했다. 기술 구조·평가 조건을
  검토하는 후보로, 회사 적격성·투자라운드·재무·독립 성능 검증을 대신하지 않는다.
- Skild AI 공식 글은 회사 기술 설명 후보다. 자기주장과 독립 근거를 구분한다.
- 실제 Candidate 정규화 전이므로 `candidate_ids=[]`다. `proposed_company`는 검토용
  회사 표기이며 runtime ID가 아니다. 승인 단계에서 실제 ID를 매핑하기 전에는
  회사 문서로 검색하지 않는다. 다른 회사나 industry 공통 문서로 자동 승격하지 않는다.
- 현재 모두 영문·해외 기업 관련 자료다. 국내 후보나 한/영 비교셋의 완성본이 아니다.
  한국어 문서 및 실제 승인 평가 후보 선정은 #43의 지원 범위와 맞춰 보완한다.

## 관측과 사용 권한

2026-09-30에 고정 arXiv PDF를 임시 경로에 내려받아 pypdf 6.19.0으로
실제 파일 페이지 수를 세고 SHA-256을 계산했다. HTML도 원문 bytes의 해시를
계산했지만 PDF snapshot은 렌더링하지 않았다. 임시 원문은 커밋하지 않는다.
해시는 그때 받은 파일의 관측이며 미래 다운로드의 동일성이나 추출 품질을 보장하지 않는다.

공개 열람 가능성과 재배포·RAG 처리 권한은 별개다. metadata에 `permission_note`로
미확인 사항을 남겼으며 사용 권한 확인/포함 승인은 아직 없다. 논문의 arXiv license와
회사 페이지 이용조건을 검토한 뒤 승인자가 허용 범위를 기록한다.
원문 PDF/HTML, index, 모델 weights는 저장소에 넣지 않는다.

## 후속 인계

[D13 산정안과 모델 점검](../../docs/implementation/corpus-page-proposal.md)을 먼저 읽는다.
#44에서 승인·전체 합·미상 거절 및 runtime 변환을, #49에서 실제 추출 품질을 확인한다.
새 corpus_version으로 승인할 때 실제 source/candidate ID, 원문 보관 경로, reviewer,
사용 권한, 추출 상태, D13 승인 근거를 채운다. 이 초안을 그대로 인덱싱하지 않는다.
