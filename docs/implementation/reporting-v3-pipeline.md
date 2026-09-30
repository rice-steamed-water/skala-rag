# #94 v3 보고서 생성·Judge·수정 흐름

2026-09-30 xxhigh가 [두 mode의 다섯 섹션·모델 사용 범위를 승인](https://github.com/rice-steamed-water/skala-rag/issues/94#issuecomment-5906219758)했다.
Generator/Judge는 기존 OpenAI `gpt-4.1-mini-2025-04-14` adapter를 주입한다.
SUMMARY, COMPANY & TEAM, TECHNOLOGY & MARKET,
INVESTMENT ASSESSMENT & RISKS, REFERENCE를 두 mode 모두 유지한다.
무선택 SUMMARY에는 selector의 이유와 후보 결과를 보존한다. 과거 baseline의
첫 추천·고정100 점수·세 label·7개 섹션을 이 경로에 넣지 않는다.

## 인계 API

```python
from skala_rag.reporting.v3_context import build_report_context_v3
from skala_rag.reporting.v3_runtime import build_report_nodes_v3
from skala_rag.reporting.v3_pipeline import run_report_v3

context = build_report_context_v3(
    candidate_result, snapshots_by_candidate,
    as_of=run_input.as_of, corpus_version=run_input.corpus_version,
    execution_mode=run_input.execution_mode,
)
generate, judge = build_report_nodes_v3(
    runtime=shared_runtime, generator_call=generator_call, judge_call=judge_call,
    budget=tool_budget, readiness=readiness,
    generator_transport=generator_attempt, judge_transport=judge_attempt,
    allowance_for=verified_allowance,
)
result = run_report_v3(context, generate=generate, judge=judge)
```

`candidate_result`는 #89의 `CandidateRunV3`다. 같은 성공 후보 집합의 최종
`EvaluationSnapshot`을 caller가 넘긴다. 기존 후보 controller가 snapshot을 결과에
보존하지 않으므로 freeze 단계에서 저장해야 한다. baseline ReportInput으로 변환하지 않는다.
selector의 run/schema/policy·후보/score 집합, score/snapshot 세대, decision/outcome,
Evidence→Source/지원 Evidence/retrieval/RAG Chunk, corpus·as_of를 대조한다.
충돌하는 Evidence/Source ID의 다른 payload와 미래 자료를 거절한다.
스코어/판정·selector를 재계산하거나 없는 근거를 보충하지 않는다.

context payload는 canonical JSON 문자열과 SHA256이다. 입력 변경이 전파되지 않으며
Generator/Judge는 같은 고정 payload를 받는다. raw snapshot/Source의 text-only 한계도
보존한다. model 객체·API key를 payload에 넣지 않는다. 원문·추출 텍스트를 포함할 수
있으므로 context/draft/prompt 실행 산출물은 outputs/에만 보존한다.

## 검증·회계

Generator 출력 schema는 네 narrative section과 limitations다. wrapper가 다섯 heading,
원래 점수·N/A·관측/정규화 점수·여섯 차원·판정/위험/한계와 후보 비교를 결정적으로
추가하고, 본문에서 실제 인용한 Evidence의 Source만 REFERENCE에 넣는다.
Decimal은 context의 정확한 문자열을 그대로 표시하고 null은 미상으로 표시한다.
추가 검색·없는 Evidence·Source 생성·문서 지시 실행·점수 변경은 허용하지 않는 prompt다.

`validate_report_v3`는 목차·빈 섹션·fence·인용/REFERENCE 폐쇄성, context/hash,
고정 점수·선택 block 보존과 fixture 표시를 검사한다. 의미·사실성은
`SemanticJudgeV3`가 고정 context와 정확한 draft를 받아 별도로 판단한다.
Judge의 schema·context/artifact hash·Evidence 참조도 확인한다.
새 draft마다 이전 structural/Judge/PDF proof를 무효화한다.

최초 생성 제외 구조·의미·PDF layout 수정이 **공유 2회**를 소비한다.
소진 시 status=completed, warning=True, 마지막 draft/검증/findings를 반환한다.
Judge fail, context/upstream 파손, stale proof, transport/schema 오류와 PDF artifact 오류는
failed다. 오류 원문·token·prompt를 오류 메시지에 넣지 않는다.
별도 retry/schema 보정 loop를 만들지 않는다. 물리 요청 retry는 #45 runtime이 소유하고
Generator/Judge/수정 호출이 caller의 같은 ledger를 소비한다.

PDF 연동은 `check_pdf(draft, context, structural, judgement)`를 주입한다.
callback이 #95 `PDFRenderer`에 최신 proof를 bind하고 `PDFLayoutValidator` 결과를
반환한다. layout revise는 같은 예산을 소비하며 렌더/손상 오류는 예외 또는 action=fail이다.
PDF callback 생략은 PDF 성공이 아니다. `ReportRunV3.final_allowed`는 항상 False이며,
fixture/stub/Warning 또는 미검증 Markdown을 final PDF로 승격하지 않는다.
최종 live 발행·CLI2/manifest/Graph 연결은 #29/#96 runner가 current proofs를 소비한다.

## Runtime·실측 한계

각 role의 `generator_attempt`/`judge_attempt`는 #51의 `OpenAIResponsesAttempt`이고
공유 runtime은 #45 `AdapterRuntime`이다. schema/prompt version, Clock, role별 CallContext,
credential·모델/요금 readiness, timeout/deadline, token·cost 상한을 caller가 명시한다.
`byte_bound_allowance` 등의 검증된 allowance로 실제 요청을 제한한다. 전체 context와
strict schema의 입력이 한도를 넘으면 호출을 거절하며 임의 자르기·예산 확대를 하지 않는다.
모델·prompt/schema hash·usage는 attempt.llm_calls, 물리 요청·예산·오류는 runtime과
role의 retrieval_records에서 인계한다. context_id와 artifact_hash로 산출물을 연결한다.

자동 검증은 합성 upstream·stub Judge와 실제 adapter의 HTTPX MockTransport다.
fixture PDF 렌더·재읽기를 실행하지만 실모델 사실성/M3 품질 성공이 아니다.
실제 Generator/Judge 성공 증거는 #96 동일 최종 live 실행에서 확인한다.
모델 다운로드·API 호출·새 모델 비교·유료 실행은 이번 PR에서 하지 않는다.
