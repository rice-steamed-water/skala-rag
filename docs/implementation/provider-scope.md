# #158 현재 실행 provider 범위

## 승인과 보류

사용자의 이번 실행 제외 지시에 따라 KIPRIS(#154), KRX(#155), 중기부(#156),
Tavily(#157)를 호출하지 않는다. #158은 이 범위의 구현 작업이다. Tavily 실패는
**사용자 보고**이며 이 작업에서 API 실패를 재현하거나 관측한 것이 아니다.
기존 adapter·fixture·승인/시도 기록은 재활성화와 호환성을 위해 유지한다.

기존 승인된 RAG/공식 출처 경로만 해당 경로의 기존 승인·readiness·corpus·예산
조건을 충족할 때 계속할 수 있다. 이는 새 provider 선정이나 자동 대체가 아니다.
Naver 도입·유료 fallback·예산 확대·M3 실행은 승인되지 않았다.
특허 등 필요한 근거가 없으면 missing을 유지한다. 출처 귀속 가능한 승인 대체
근거만 평가할 수 있으며 evaluator semantic gate, rubric, 점수 분모는 완화하지 않는다.
범위 축소는 전체 M2 성공이나 #48/#55 완료가 아니다.

## 명시적 주입 경계 (자동 연결 아님)

`tools/provider_scope.py::invoke_current_scope`는 전체 adapter 호출 **앞**의 lazy
callback을 감싼다. canonical provider ID는 `kipris`, `krx`, `중기부`, `tavily`다.
호출자는 provider ID를 실제 adapter와 고정 매핑해야 한다. caller allowlist에 있어도
네 ID는 제외되며, allowlist 밖 ID도 차단된다. allowlist는 기존 승인 출처의
식별자만 호출자가 주입한다. 별칭을 승인 ID로 넣어 차단을 우회하면 안 된다.

```python
result = invoke_current_scope(
    provider=canonical_provider_id,
    approved_providers=existing_approved_provider_ids,
    context=call_context,
    retrieval_id=unique_retrieval_id,
    clock=clock,
    invoke=lambda: adapter(request, budget),
)
```

`unavailable / TOOL_NOT_CONFIGURED`, `scope_reason=excluded_current_run`,
`physical_attempts=0`, non-retryable error와 retrieval receipt를 반환한다.
`data=None`이고 Evidence/Source를 만들지 않으며 비용은 unknown(null)이다.
성공 0건(empty), 부정 사실, 실패를 복구한 결과로 표현하지 않는다.
승인 경로는 원래 결과 객체와 예외를 그대로 전달한다. retry/예산/refund/다른
provider 선택은 구현하지 않는다. 모든 차단 receipt를 caller가 실행 기록에 보존한다.

현재 기존 runtime은 이 helper를 자동으로 읽지 않는다. config 로더/provider
selector가 없으므로 소비되지 않는 JSON config는 추가하지 않았다.
이 구현만으로 whole-Graph 호출 차단을 주장할 수 없다. 실제 실행 진입점 담당자는
반드시 위 경계를 주입해야 한다. 기존 직접 호출 경계는
`src/skala_rag/tools/discovery_receipt.py::search_with_receipt`,
`src/skala_rag/tools/discovery_smoke.py::run_discovery_smoke`이며 후자는 Tavily 전용이라
현재 실행에서 호출하지 않는다. 전역 강제가 필요하면 이 owner 파일들 또는 #55/#96
runner의 adapter 조립/호출 지점을 별도 범위 승인 후 수정해야 한다.
공통 `tools/runtime.py::AdapterRuntime.execute`는 이번 작업에서 변경하지 않았다.

## 로컬 검증

`tests/unit/test_provider_scope.py`는 제외4개·미승인·승인 결과 identity·예외 전달을
synthetic callback으로 확인한다. 실제 provider/RAG 검색 성공을 측정하지 않는다.
API·credential·model download·M3·GitHub 쓰기는 수행하지 않았다.
네트워크 금지에 따라 live #158/#48/#55 thread를 재조회하지 않았으며 parent가
전달한 이슈/승인 범위와 로컬 문서만 사용했다.
