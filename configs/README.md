# Draft 점수 정책

`scoring.draft.json`은 현재 main의 `docs/implementation/scoring.md` §2–6과
D08 제안을 그대로 옮긴 **미승인 fixture 정책**이다. D01·D02·D03·D05·D08은
OPEN이며, v3 정합화 작업 #35 / PR #36의 변경은 반영하지 않았다.
이 파일은 팀 승인이나 실제 투자 평가 결과가 아니다.

`skala_rag.scoring.catalog.load_policy(path, execution_mode="fixture")`로
경로와 모드를 명시해 읽는다. 암묵적 경로나 정책 기본값은 없다.
`execution_mode="live"`는 거절하며 status를 approved로 바꿔도 로더가 거절한다.
후속 설계가 병합되면 별도 버전과 해당 fixture를 함께 변경해야 한다.

`tests/fixtures/scoring.draft.json`에는 가상 계산 7종과 직접 입력 경계값의
기대 점수·label·grade가 있다. 경계값은 라벨 함수 테스트용 입력이므로
23개 정수 rating에서 생성 가능한 평가라고 주장하지 않는다.
실제 aggregate_scores·decide 구현과 세대 검증은 #16 범위다.

검증: `uv run pytest tests/unit/test_scoring_catalog.py`

## rubric (D14 제안)

`rubrics/finance.yaml`(traction·deal_terms)과 `rubrics/core.yaml`(founder·market·technology·moat, PR #40)은
criterion별 rating 1–5 기준·최소 근거·missing 조건을 담은 **미승인 제안**이다. criterion ID·비중·표시명은
`scoring.draft.json`과 같아야 하며 `tests/unit/test_finance_rubric.py`·`test_core_rubric.py`가 확인한다.
설명: `docs/implementation/rubric-finance.md`, `docs/implementation/rubric-core.md`.
