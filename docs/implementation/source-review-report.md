# Dexory 단일 자료 기반 조사 보고서 (#96)

이 경로는 사용자가 선택한 짧은 조사 범위다. 평가·rating·점수·투자 추천,
CandidateRunV3, 선정 결과, scored snapshot을 만들지 않는다. 기존 actual 투자
gate, Discovery/RAG/Eligibility, corpus adoption을 실행하거나 완화하지 않는다.
`unscored-research-only-1`은 기존 HTML presentation 표식을 재사용한다.

## 입력과 controller 경계

`source_review.build_source_review_context(capsule, expected_input_id=...,
expected_sources=...) -> ReportContextV3`.

capsule은 다음 키만 가진 JSON-compatible dict다.

- schema_version, run_id: 명시적 nonblank 문자열
- as_of: 정확한 YYYY-MM-DD cutoff
- execution_mode: live 또는 fixture. 실제 원문은 live, 합성 제어군은 fixture
- research_subject: 정확히 Dexory
- sources: 실제 Source DTO JSON payload를 source_id로 묶은 map
- source_texts: 같은 source_id 집합의 보관 원문 extraction 문자열
- evidence: Evidence DTO JSON payload를 evidence_id로 묶은 map

Evidence는 candidate_id=dexory, company scope, reported, confidence=unknown이며
criterion_ids/provenance/supporting_evidence_ids/conflicts_with는 빈 목록이다.
claim은 원문 excerpt와 정확히 같고 해당 source_texts의 부분 문자열이어야 한다.
locator는 정확한 Source URL이다. limitations는 필수이며 금액·계산·rating
필드를 만들지 않는다. 사건 날짜는 있을 때 cutoff 이하로 보존한다.
이 Evidence는 인용 위치를 표현할 뿐 의미 admission이나 retrieval 실행의 증거가 아니다.

parent는 모델 응답 밖에 독립 보관한 Source payload들과 전체 capsule의 canonical
JSON SHA-256(`sha256:` prefix)을 전달한다. canonical은 v3_context.canonical이다.
검증 후 문자열로 detach하며 snapshot은 매번 새 dict다. hash는 원문 진실성이나
심사 authority가 아니며, controller pin을 같은 모델 출력에서 만들면 안 된다.
G015/G026 raw body 및 extraction hash 확인은 parent의 별도 offline 책임이다.
새 파일을 다운로드하거나 accepted registry를 생성하지 않는다.

## 호출

`v3_pipeline.run_source_review_report(context, expected_context_id=...,
expected_input_id=..., generate=ReportGeneratorV3(generator_llm),
judge=SemanticJudgeV3(judge_llm), check_pdf=...)`.

context ID와 input ID는 parent가 먼저 고정한다. runtime/LLM은 기존 승인된
StructuredLLM 주입 경계만 사용한다. 이 helper는 provider/API를 선택·호출하지 않는다.
check_pdf(draft, context, structural, judgement)는 기존
`korean_report.build_korean_report_pdf(context, draft, structural, judgement,
output_dir, execution_mode)`의 반환 `(render, layout_validation)`을 받고
layout_validation을 반환하며 render를 parent가 기록한다.

원래 Generator, structural validator, Semantic Judge, run_report_v3의 공유 수정
2회와 fail/Warning lifecycle을 그대로 쓴다. 모델은 한국어 5개 body를 쓰며 기존
REFERENCE는 controller가 인용 Evidence의 Source 집합으로 생성한다. 기존 Korean
HTML renderer의 한국어 목차·안정적 번호·Source ID·URL·발췌 목록을 사용한다.
권장 내용 길이는 2–3쪽이며 기존 PDF 검사 상한 5쪽, SUMMARY 반쪽을 유지한다.

controller는 unscored/unrated·평가 미실행·추천 없음·self-reported·cutoff·현재성
미확인과 역사적 fundraising 한계를 고정 표시한다. 구조 검사는 exact citation/
reference closure, 고정 disclosure와 명백한 score/recommendation 및 원문에 없는
숫자를 검사한다. 이 제한된 검사는 모든 한국어 의미·금액 귀속 오류를 판정하지
않는다. 숫자가 원문에 있어도 매출·valuation으로 바꿔 쓰면 원래 Semantic Judge가
거절해야 한다. synthetic Judge 제어군은 실제 의미 검증 성공이 아니다.

숫자 비교는 영어 million/billion/M과 한국어 억·억/만 조합을 같은 정확한 값으로
정규화한다. 발행일의 한국어 월·일 표기도 Source 날짜와 대조한다. 이 변환은 새
Evidence·점수·재무 추정치를 만들지 않으며, 금액의 의미·통화·주체는 Judge가 검사한다.

이미 생성한 실제 응답을 재생할 때 caller는 `initial_revision`(0–2)과
`initial_feedback`을 전달해 원래 수정 상태를 보존할 수 있다. 기본값은 원래의
0회·빈 feedback이다. 2회 상태에서 Judge가 수정 요청을 하면 추가 생성 없이
기존 Warning으로 종료한다. 재생 응답의 원래 입력 hash와 누적 유료 ledger는
caller가 별도로 검증·보존해야 하며, 이 인자는 재시작·예산 초기화 승인이 아니다.

check_pdf가 없으면 완료 상태도 draft일 뿐이다. Warning은 현재 draft/findings를
보존하고 최종 발행을 금지한다. run 결과 final_allowed는 원래대로 false다.
HTML renderer의 기술적 live final_allowed observation도 parent의 실행/발행 승인,
실제 Judge trace 및 페이지별 visual QA를 대체하지 않는다.

## 남은 actual 승인과 검증

실제 Generator/Judge 실행은 finite call/token/time/USD와 runtime/ledger 승인 후
parent가 수행한다. 이 작업은 오프라인 FakeLLM 회귀와 실제 로컬 PDF 렌더만 수행한다.
실제 Dexory 원문에 대한 모델 결과, 최종 PDF 발행, full suite/build/독립 review와
페이지별 실제 콘텐츠 visual QA는 parent 소관이며 완료로 주장하지 않는다.

## 팀원용 실제 결과 재현 (#229)

위 helper 구현의 오프라인 검증과 별개로, parent는 승인된 실제 모델 요청 6회
안에서 Generator와 SemanticJudge를 실행했다. 실제 Judge pass, 원래 수정
2회 상태의 Warning 없는 completed, 최종 한국어 3쪽 PDF와 SUMMARY 21.7368%,
native Preview 모든 페이지 검증 및 정리 증거는
[PR #229](https://github.com/rice-steamed-water/skala-rag/pull/229)의 실행 증거와
첨부를 따른다. 이 성공은 source-review 범위이며 전체 투자 평가·RAG·M3 완료가 아니다.

실제 수집 자료는 Git에 넣지 않는다. 현재 PR에는 화면만 첨부돼 있고
`dexory-replay-bundle.zip`과 PDF는 로컬에 보관 중이다. 공개 release나 ZIP
업로드는 하지 않았다. 따라서 코드 checkout만으로는 실제 보고서를 재현할 수
없다. 재현 자료를 별도로 전달받아 `outputs/` 아래에 풀면 다음 파일을 사용한다.

- 당시 공식 Source 2개의 content-decoded 보관 body와 추출 text, URL·수집일·hash
- 원래 입력 capsule, Source DTO 2개와 정확한 발표문 Evidence 발췌 4개
- 실제 최종 초안과 과거 Judge 결과, 누적 ledger 및 원문/내용/PDF 검증 receipts
- 파일 무결성 manifest와 상대 경로만 사용하는 `replay.py`, 실행 README

```bash
git fetch origin feat/96-minimal-dexory-report
git switch feat/96-minimal-dexory-report
uv sync
uv run playwright install chromium
PYTHONPATH=src:. uv run python outputs/dexory-replay-bundle/replay.py
```

API key와 `.env` 없이 원문 body/text hash, 원래 input/context/초안/Judge 대상의
동일성, 구조·인용 closure를 검사하고 기존 renderer로 PDF를 다시 만든다.
PDF 경로와 검증 결과가 JSON으로 출력된다. 패키지 내부의 `replayed-report-*`에
새 결과를 만들므로 기존 결과를 덮어쓰지 않는다. 설치 이후 replay는 외부
HTTP나 모델 요청을 하지 않는다. 원래 회사 발표와 실제 모델 결과를 재사용하는
cache replay이며 새로운 조사·Generator·의미 Judge 실행이라고 표시하지 않는다.
생성 시각 등으로 새 PDF binary hash는 달라질 수 있지만 원래 context와
초안 hash는 같아야 한다.

원문 저작권은 원출처에 있으며 별도 오픈 데이터 재배포 라이선스는 확인하지
않았다. 사용자가 요청한 팀 내부 재현용 첨부이며 원문·outputs·API key와
생성 index는 커밋하지 않는다. 역사적 receipts의 수집 머신 절대 경로는
메타데이터로 보존하고 replay에서는 열지 않는다. 전체 actual-v3 입력
코퍼스나 검색 index를 추가로 만들지 않는다.

새 유료 모델 실행은 위 명령에 포함되지 않는다. 기존 6회 한도는 소진됐으므로
새 finite call/token/time/USD 승인이 필요하며, 원래 비용 예약 상한
USD 0.0770756은 실제 청구액이 아니다.
