# Python 직접 live 탐색 — #96의 제한된 인계

`skala_rag.live_exploration.run`은 기존 승인 BGE-M3/index와 공식 출처를
읽고 실제 OpenAI 다섯 영역 검토·Generator·독립 Semantic Judge·PDF 검증을
실행한다. CLI나 새 provider를 추가하지 않는다. `run_live_exploration.py`는
저장소 루트를 현재 작업 디렉터리로 두고 직접 실행하는 이번 승인 실행 예시다.
`root`에는 `.env`와 기존 로컬 artifact, `artifacts_root`에는 코드/config가 있다.

2026-09-30 사용자 대화 승인: 전체 20분, 외부 요청 50회, LLM 요청 30회,
US$3. 승인 참조가 같은 실패/계속 실행도 누적해 한도에서 차감한다.
단일 동시성, transport retry 없음, 기존 adapter 출력 상한 2,000 tokens를
유지한다. OpenAI snapshot은 `gpt-4.1-mini-2025-04-14`이며 신규 모델 다운로드나
유료 fallback을 실행하지 않는다. Tavily/KIPRIS/KRX/중기부는 조립하지 않는다.

현재 탐색 모집단은 승인 논문 corpus의 Physical Intelligence와 기존 공식
기술자료의 Skild AI다. 전체 웹에서 새로운 후보를 발견한 것으로 표시하지 않는다.
공식 원문 조회 실패는 실패로 보존하며, 공개된 사실이 없다는 뜻으로 해석하지 않는다.
RAG 원문 locator·Chunk와 Evidence ID·공식 원문 hash·실제 LLM usage·동일
context/hash에 대한 구조/Judge/PDF proof를 `outputs/`에 저장한다.
계속 실행은 hash가 일치하는 기존 source/context와 실제 검토 결과를 재사용한다.
폰트에 없는 `π`는 표시 제목에서 `pi`로 전사하고 원래 제목은 metadata에 보존한다.

**완료 경계:** 투자 적격성, 23 criterion의 실제 semantic verifier,
live 점수 집계와 최종 selector의 통합은 이 경로가 구현하지 않는다.
기존 fixture scoring/controller의 gate를 변경하거나 fake 성공 결과를 만들지 않는다.
보고서·PDF 검증을 통과해도 `acceptance=Warning`, `publication_allowed=false`,
`whole_m3_verified=false`로 보존하며 정식 투자 추천/제출 final로 승격하지 않는다.
실패는 `workflow_status=failed`, `acceptance=failed`로 보존한다.
이는 #96 전체 완료가 아니므로 PR은 Draft로 남긴다.

최초 실제 전달 artifact: `outputs/live-exploration-57153549d334/`.
실제 BGE 검색과 회사 공식 자료, 실제 five-role review/Generator/Judge 사용.
PDF 2페이지, SUMMARY/A4 높이 0.3480265619154507, 구조/Judge/PDF 통과.
보고서 범위가 제한되어 있으므로 후보 투자 점수나 투자 추천은 없다.
accounted 비용은 공시 가격을 사용한 보수적 예약값이며 실제 청구액은 미확인이다.

원문·`.env`·생성 index·보고서 산출물은 커밋하지 않는다. 필요한 로컬 자료가 없는
환경에서는 외부 호출 전에 거절한다. 신규 clone용 전체 환경 설치나 M3 재현
완료를 주장하지 않는다.
