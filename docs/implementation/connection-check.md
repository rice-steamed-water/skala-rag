# 무과금 v3 연결 점검 (#168)

[문서 홈](../README.md) · [현재 actual-v3 상태](actual-v3-status.md) · [Source 검토 보고서](source-review-report.md)

[`examples/v3_connection_check.py`](../../examples/v3_connection_check.py)는 원래 v3 outer graph(`run_candidate_report_v3`), 5개 approved evaluator, EvidenceResearch, Generator/Judge, PDF renderer/validator를 한 번에 연결해 실행한다. 모델 응답은 `httpx.MockTransport`가 만든 통제 응답(`execution_scope=controlled_response`)이다. 인증정보를 읽지 않고, 외부 요청도 실제 provider 호출도 없다. 실제 기업 평가나 투자 점수는 아니다.

## 무엇을 증명하고 무엇을 증명하지 않는가

증명하는 것:

- 공유 `AdapterRuntime`과 `BudgetLedger` 하나가 evaluator 5개, `evidence_research`, `generator`, `judge`의 호출 8개를 실제로 예약하고 정산한다.
- `ActualAdmissionV3`가 code-owned pin(`pinned_approval_registry`)으로 `configs/scoring.v3.json`과 Core/Finance rubric을 검증한 뒤에만 policy를 연다.
- 원문 capture bytes는 `verify_original_capture`로 hash가 확인된다. 이건 source 무결성일 뿐이고 의미 심사와는 별개다.
- 보고서는 `completed`가 되고 PDF 검증을 통과하지만 `final_allowed=False`, `publication_allowed=False`로 남는다.

증명하지 않는 것:

- 긍정적 semantic review는 없다. `SourceBoundReviewResolver`의 `reviews=()`이고 receipt의 `semantic_reviews`·`financial_facts`도 빈 목록이다. 미상 기준 23개는 모두 `missing`이고 founder·market 신원도 미상이다.
- Judge의 `pass`는 합성 응답이라 의미 권위가 아니다.
- 후보, Eligibility, coverage, source, index, wire, ID 모두 합성 통제값이다(receipt `synthetic_controls`).

## 실행

저장소 루트에서 실행한다. 의존성은 이미 설치돼 있어야 한다.

```sh
# 1. 새 실행: 기본 출력은 outputs/issue168-connection-check
uv run --offline --no-sync python examples/v3_connection_check.py
uv run --offline --no-sync python examples/v3_connection_check.py /tmp/cc-run

# 2. replay: 저장된 capture의 응답 bytes로 같은 graph를 다시 실행
uv run --offline --no-sync python examples/v3_connection_check.py /tmp/cc-replay /tmp/cc-run/capture.json
```

첫 인자는 출력 디렉터리고 두 번째 인자는 replay할 `capture.json`이다. 출력 디렉터리는 아직 없어야 한다(`mkdir(exist_ok=False)`). 성공하면 `receipt.json` 경로를 출력한다. Python에서 직접 부를 때는 `run_connection_check(output_dir, replay_capture=None)`를 쓴다.

출력 디렉터리 내용:

| 파일 | 내용 |
| --- | --- |
| `source.capture`, `source.txt` | 통제 원문 bytes와 추출 텍스트 |
| `capture.json`, `capture.sha256` | 역할 8개의 request SHA-256과 response body, 안정 hash. 옆의 sha256 파일은 무결성 commitment이고 서명은 아니다 |
| `state.json`, `context.json`, `report.json`, `report.md` | graph state, 보고서 context, draft |
| PDF | renderer가 출력 디렉터리에 쓴 파일. 경로는 receipt `pdf.path` |
| `receipt.json` | `actual_provider_calls=0`, `external_requests=0`, `replay`, ledger snapshot, branch별 snapshot hash, `stable_hashes`, PDF 검증 값 |

## Replay와 tamper 거절

replay는 callback이나 request보다 먼저 다음 순서로 검사한다. 하나라도 틀리면 `ConnectionCheckError`를 내고 출력 디렉터리를 만들지 않는다.

1. `capture.json`의 SHA-256이 같은 위치 `capture.sha256`과 일치하는지 (stage `capture hash`)
2. `execution_scope`가 `controlled_response`이고 역할 집합이 정확히 8개인지 (`capture scope/roles`)
3. capture 옆 `source.capture`, `source.txt`의 hash가 저장된 `source_sha256` 및 통제 원문과 같은지 (`source capture`)

실행 중에는 역할별 request hash가 저장값과 달라지면 `replay request`로 멈춘다. 끝난 뒤 state/context/report/PDF와 `scoring.v3.json`, `rubrics/core.yaml`, `rubrics/finance.yaml`의 hash가 달라지면 `replay artifact hashes`로 거절한다. replay는 새 응답을 만들지 않고 저장된 wire만 소비한다.

직접 tamper를 확인하는 예:

```sh
printf tampered >> /tmp/cc-run/source.txt
uv run --offline --no-sync python examples/v3_connection_check.py /tmp/cc-tamper /tmp/cc-run/capture.json
# ConnectionCheckError: connection check rejected: source capture, /tmp/cc-tamper 미생성
```

같은 동작은 [`tests/integration/test_v3_connection_check.py`](../../tests/integration/test_v3_connection_check.py)가 고정한다. 새 실행, replay의 hash·ledger 동일성, `capture.json`/`source.capture`/`source.txt` 변조 시 evaluator 호출 전 거절이 대상이다.

```sh
uv run --offline --no-sync pytest -q tests/integration/test_v3_connection_check.py
```

## 부모 검증 기록 (2026-10-08)

부모가 위 명령으로 `outputs/parent-connection-check`와 `outputs/parent-connection-replay`를 직접 생성했다. state/context/draft/PDF hash가 일치했고, 원문·capture 변조 거절을 포함한 회귀 5개가 통과했다. 누적 ledger는 검색 1회와 통제 모델 요청 8회이며, 실제 provider·외부 요청은 0회다. ledger의 비용은 합성 allowance의 보수적 예약값이지 실제 청구액이 아니다.

PDF 3페이지 전체를 로컬 렌더링해 한글·표·페이지 경계를 확인했다. SUMMARY는 페이지의 약 11.6%이며 clipping·겹침·빈 페이지는 없었다. 실제 사실이 없으므로 인용은 없고 REFERENCE도 그 상태를 표시한다.

전체 회귀는 **5,548 passed, 10 skipped**다. skip은 opt-in live 호출, 별도 준비되지 않은 로컬 원문, 미제공 user review이며 성공으로 세지 않는다. style 수정 후 관련 회귀 74개, Ruff, 380파일 format, offline sdist/wheel build와 공백 검사가 통과했다. 기존 controller의 정적 타입 오류 54개는 원본 main에도 동일하게 존재한다.

## 남은 gate

이 점검이 통과해도 실제 투자 평가는 완료되지 않는다. 남은 것은 실제 provider를 쓰는 유료 scored E2E 실행과, 원문 근거에 대한 독립 reviewer의 positive semantic review다. 둘 다 사용자 비용 승인과 reviewer 권위가 필요하며 지금은 보류 상태다. 자세한 상태는 [actual-v3 상태](actual-v3-status.md)에 있다.
