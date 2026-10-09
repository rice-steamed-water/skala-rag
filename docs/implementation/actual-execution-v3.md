# actual-v3 Python 실행과 replay (#168)

[문서 홈](../README.md) · [Python 직접 실행](python-execution.md) · [actual-v3 현재 상태](actual-v3-status.md) · [연결 점검](connection-check.md)

작업 브랜치 `feat/168-completion-connections`의 미병합·미커밋 소스 기준이다. [#166](python-execution.md) 결정대로 CLI·flag·console script·plugin 설정은 없고 Python callable만 있다. 이 문서는 export된 callable의 정확한 인자와, 호출자가 직접 공급해야 하는 입력·권위를 적는다. 실제 유료 scored 실행은 한 번도 일어나지 않았다.

## export된 callable

[`examples/v3_actual_run.py`](../../examples/v3_actual_run.py)는 import만으로는 아무 일도 하지 않는다. 세 함수가 있다.

```python
from skala_rag.graph.actual_inputs_v3 import ActualAuthorityV3, RetainedSourceInputsV3


# examples/v3_actual_run.py
def preflight_sources(output_dir: Path, *, sources: RetainedSourceInputsV3) -> Path: ...
def actual(
    output_dir: Path,
    *,
    inputs: Path,
    inputs_sha256: str,
    authority: ActualAuthorityV3,
    execute: bool = False,
    api_key: str | None = None,
) -> Path: ...
def replay(
    output_dir: Path,
    *,
    inputs: Path,
    inputs_sha256: str,
    authority: ActualAuthorityV3,
    original_capture: Path,
    original_capture_sha256: str,
) -> Path: ...
```

`actual`과 `replay`는 [`skala_rag.graph.actual_runner_v3`](../../src/skala_rag/graph/actual_runner_v3.py)의 `run_actual`·`run_replay`를 그대로 부른다. `run_actual`에는 테스트 전용 인자 `execution_scope: Literal["actual", "controlled_response"] = "actual"`과 `transport_for: Callable[[str], httpx.MockTransport] | None = None`이 더 있다. `preflight_sources`는 [`prepare_retained_sources`](../../src/skala_rag/graph/actual_inputs_v3.py)를 감싼다. `examples/`에는 `__init__.py`가 없으니 아래 `from examples.v3_actual_run import ...`는 저장소 루트를 현재 디렉터리로 두고 `uv run --offline --no-sync python`으로 실행할 때 namespace package로 import된다.

세 함수 모두 새 산출물 디렉터리를 반환한다. `output_dir`가 이미 있으면 `mkdir(exist_ok=False)`에서 실패한다. 결과는 그 디렉터리의 `receipt.json`에서 읽는다. `publication_allowed`와 `final_allowed`는 어느 경로에서도 `false`다.

## 1단계: 보존 Source 준비 (권위 없음)

`preflight_sources`는 callback도 packet도 요구하지 않는 유일한 단계다. archive composer(`compose_archive_company_research`), `assemble_bundle`, 원래 `check_eligibility`를 semantic review 없이 다시 돌리고, index·reopen 파일과 index Source 원본, BGE-M3 파일 15개를 pin에 대조한다. encoder·runtime·자격증명·campaign marker·provider는 만들지 않는다.

입력은 [`RetainedSourceInputsV3`](../../src/skala_rag/graph/actual_inputs_v3.py)다. `extra="forbid"`, `strict=True`이고 필드는 다음과 같다.

| 필드 | 타입 | 넣을 값 |
| --- | --- | --- |
| `run_id` | `str` | 이 준비 실행의 ID |
| `run_input` | `RunInput` | `execution_mode="live"`, `as_of=2026-10-07`. 다르면 `RUN_SCOPE_MISMATCH`. `corpus_version`은 index의 `issue52-reviewed-text-v1`과 같아야 한다 |
| `candidates` | `tuple[Candidate, ...]` (1~40) | PI와 Skild의 canonical 후보. 호출자 데이터일 뿐 승인된 discovery receipt가 아니다 |
| `archives` | `tuple[ArchiveInput, ...]` (1~8) | `ArchiveInput(root=..., index_sha256=...)` |
| `index`, `reopen_input` | `PinnedFile(path=..., sha256=...)` | SQLite index와 `reopen-input.json`, 각각 외부에서 기록된 SHA-256 |
| `index_originals` | `dict[str, PinnedFile]` | index Source ID별 원본 PDF 경로와 pin. index Source 집합과 정확히 같아야 한다 |
| `model_root`, `model_files` | `Path`, `dict[str, str]` | 보존된 BGE-M3 revision 디렉터리와 그 안의 상대 경로 15개별 SHA-256 |

아래는 실제 로컬 자산을 가리키는 호출 형태다. 부모가 실행한 구체 호출은 로컬 `outputs/mulw-actual-composition-20261009/parent-source-preflight.py`, 실제 입력과 파일 commitment는 `outputs/parent-retained-source-preflight-20261009/source-inputs.json`에 보존했다. 모델 파일 pin은 기존 `validation.json`, 원본 PDF pin은 봉인 archive와 index Source에 대조했다. index·reopen 파일의 현재 hash는 같은 실행에서 전체 SQLite metadata·36개 Chunk·2개 Source closure와 함께 검증했다. 이 byte 검증은 의미 심사나 실행 권위가 아니다.

```python
from pathlib import Path

from examples.v3_actual_run import preflight_sources
from skala_rag.graph.actual_inputs_v3 import (
    ArchiveInput,
    PinnedFile,
    RetainedSourceInputsV3,
)

MAIN = Path("/Users/luk/workspace/skala-raga-project")
INDEX_DIR = MAIN / "outputs/issue145-local-bge-final"

sources = RetainedSourceInputsV3(
    run_id="actual-v3-source-prep-2026-10-07",
    run_input=run_input,  # 원래 #222 source-only 실행의 live RunInput, as_of 2026-10-07
    candidates=(pi, skild),  # 같은 실행의 정규화된 Candidate 두 개
    archives=(
        ArchiveInput(
            root=MAIN / "data/local/company-research/2026-10-07-initial",
            index_sha256="84435b773164e95908165db2787827a983be0c14136a35fba19e64b84db6fe0b",
        ),
    ),
    index=PinnedFile(path=INDEX_DIR / "index.sqlite", sha256=...),
    reopen_input=PinnedFile(path=INDEX_DIR / "reopen-input.json", sha256=...),
    index_originals={
        ...
    },  # PI 기술 PDF 2개(2410.24164v4, 2504.16054v1)의 Source ID → PinnedFile
    model_root=MAIN / "data/local/models/bge-m3-5617a9f",
    model_files={...},  # 상대 경로 15개 → SHA-256
)
out = preflight_sources(Path("outputs/actual-v3-source-prep-1"), sources=sources)
```

`receipt.json`은 항상 `status="preflight_blocked"`, `execution_scope="source_preparation"`, `actual_provider_calls=0`이다. 성공해도 `reason="EXTERNAL_AUTHORITY_REQUIRED"`, `missing_requirements=["EXTERNAL_AUTHORITY_REQUIRED", "ELIGIBILITY_FACTS_MISSING", "RATING_REVIEW_MISSING"]`으로 끝난다. 이 세 값은 [`RetainedSourcePreparationV3`](../../src/skala_rag/graph/actual_inputs_v3.py)의 고정 기본값이다. 입력이 틀리면 `FILE_PIN_MISMATCH`, `INDEX_CLOSURE_MISMATCH`, `INDEX_ORIGINAL_CLOSURE_MISMATCH`, `MODEL_CLOSURE_MISSING`, `ARCHIVE_COMPOSITION_FAILED` 같은 코드가 남는다. 성공하면 `source-inputs.json`과 `source-preparation.json`(후보별 composition, eligibility, index Source·Chunk, 검증된 model 파일 수)도 생긴다.

부모가 socket을 차단하고 실제 자산으로 이 단계를 실행했다. Source는 PI 7개·Skild 12개, index는 PI PDF 2개·Chunk 36개, 모델 파일은 15개가 검증됐고 exit 0이었다. 두 후보의 eligibility는 실제 반환값도 `unknown`이다. receipt는 `EXTERNAL_AUTHORITY_REQUIRED`, 유료/provider 호출 0, 발행 금지로 끝났다. 결과는 `outputs/parent-retained-source-preflight-20261009/receipt.json`과 `source-preparation.json`이다. 이는 actual scored 실행이 아니다.

## 2단계: actual packet과 권위

`actual`·`replay`의 `inputs`는 [`ActualInputsV3`](../../src/skala_rag/graph/actual_inputs_v3.py) JSON이고, `inputs_sha256`은 그 파일 bytes의 외부 pin이다. [`load_inputs`](../../src/skala_rag/graph/actual_inputs_v3.py)는 hash 불일치에 `INPUT_PIN_MISMATCH`를, live가 아니거나 `as_of`가 2026-10-07이 아니거나 후보가 비면 `RUN_SCOPE_MISMATCH`를 낸다. packet에는 관찰값과 commitment만 있고 승인·reviewer·plugin 필드는 없다.

필드는 `RetainedRetrievalV3`에서 물려받은 `run_input`, `index`, `reopen_input`, `model_root`, `model_files`에 더해 `run_id`, `discovery`(`ToolResult[DiscoveryBundle]`), `archives`, `candidates`(후보별 `observations`, `sources`, `chunks`, `records`, `evidence`, `initial_gaps`), `allowed_source_ids`, `industry_evidence_ids`, `industry_evidence_dimensions`, `approval_reference`다. `observations`의 각 항목은 `FieldObservation`과 `review_request`, `review_subject`를 담는다. 실제 자료를 연결한 로컬 packet은 `outputs/actual-execution-inputs-20261009/inputs.json`에 있고 SHA-256은 `0dc9422c9dae06b71e616c9ece1dcdab66c3c466e42bb9a34c710c6145f7f8c0`다. 원문 심사 보고서 pin도 `input-receipt.json`에 연결했지만 관찰·rating 승인 0개, research plan 미확립으로 실행 준비가 끝난 것은 아니다.

`authority`는 packet에서 읽지 않는 [`ActualAuthorityV3`](../../src/skala_rag/graph/actual_inputs_v3.py) frozen dataclass다. 타입이 정확히 이 클래스가 아니거나 callback이 callable이 아니면 `EXTERNAL_AUTHORITY_REQUIRED`다. 저장소에는 이 callback의 실제 구현이 없다. 테스트의 controlled 구현을 실제 권위로 옮겨 쓰면 안 된다.

| 필드 | 타입 | 호출자가 책임지는 것 |
| --- | --- | --- |
| `campaign_id` | `str` | 승인된 누적 campaign 이름. 앞뒤 공백이 있으면 `STABLE_CAMPAIGN_ID_REQUIRED` |
| `campaign_directory` | `Path` | 출력 밖의 절대 경로. 아니면 `PERSISTENT_CAMPAIGN_DIRECTORY_REQUIRED` |
| `authenticate_inputs` | `Callable[[ActualInputsV3], bool]` | discovery, field observation, criterion 귀속, model commitment를 독립 기록으로 인증. byte hash 비교만으로는 구현이 아니다 |
| `sources` | `Mapping[str, TrustedSource \| TrustedCapture]` | Source ID별 원본 경로·corpus·승인 Chunk |
| `live_gate_verifier` | `LiveGateVerifier` | run의 live gate 참조 확인 |
| `reviews_for` | `Callable[[EvaluationSnapshot, Mapping[str, JsonValue]], tuple[SourceBoundReview, ...]]` | 그 frozen snapshot·rubric에 결속된 독립 인증 review. 모델 제안이나 seed 승인 복사는 안 된다 |
| `evaluation_inputs_for` | `Callable[[EvaluationSnapshot], EvaluationInputsV3]` | 인물 귀속, Founder·Technology·Moat anchor, Market target·link·review, 기간이 맞는 Finance fact |
| `support_check`, `applicability_assessments`, `applicability_check`, `applicability_verifier` | 기존 타입 | D05/D06 support·applicability의 run 단위 판단 |
| `verify_replay_origin` | `Callable[[Mapping[str, JsonValue]], bool]` | 외부 pin된 actual 원본 capture를 인정할지. 리터럴 `True`만 통과 |

권위는 [`ActualAdmissionV3`](../../src/skala_rag/scoring/approved_consumers.py)로 이어진다. runner는 `review_resolver_for=`에 snapshot·rubric digest로 캐시하는 resolver factory를 넘긴다. replay에서만 `replay_verifier=`에 `capture.verify()`와 `verify_replay_origin(saved)`가 둘 다 `True`일 때만 `True`인 함수를 넘긴다. 빠진 review는 `missing-review-requests.json`에 정확한 요청으로 남고, 다음 유료 dispatch 직전에 `SNAPSHOT_REVIEW_MISSING`으로 멈춘다.

### 지금 없는 것

다음은 소프트웨어 이름이 아니라 아직 존재하지 않는 외부 사실과 심사다. USD1 승인은 이 중 어느 것도 대신하지 않는다.

1. 기존 packet에 연결할 인증된 field observation·criterion review·research plan. packet bytes 자체는 이미 생성·pin됐지만 권위를 주지 않는다.
2. PI·Skild 각각의 같은 회사 근거로 된 비상장·Seed~C 단계·Exit 미완료 사실. PI의 Series C 날짜는 출처끼리 충돌하고, Skild의 Zebra 인수는 Skild 자신의 Exit가 아니다. Source에 없는 사실과 미상 Exit는 계속 unknown이다.
3. 신뢰된 reviewer가 frozen snapshot·rubric·request/receipt에 결속해 낸 `SourceBoundReview` 기록. `outputs/issue168-cutoff-original-review/report.json`은 이런 operational 기록을 만들지 않았다.
4. 5개 branch의 `EvaluationInputsV3`와 D05/D06 판단.
5. 위를 독립 기록으로 확인하는 `authenticate_inputs`, `live_gate_verifier`, `verify_replay_origin` 구현.

이것들이 없으면 `actual`은 시작하자마자 `EXTERNAL_AUTHORITY_REQUIRED`로 끝난다. 자동으로 권한이 주어지는 경로는 없다.

### 호출 형태

위 입력이 갖춰졌다고 가정한 non-synthetic 호출이다. `authority`, `packet_path`, `packet_sha256`, `capture_sha256`은 앞 절의 외부 자료로만 만들 수 있다.

```python
from pathlib import Path

from examples.v3_actual_run import actual, replay

# preflight: provider·encoder 호출 0
pre = actual(
    Path("outputs/actual-v3-preflight-1"),
    inputs=packet_path,
    inputs_sha256=packet_sha256,
    authority=authority,
)

# 실제 실행: preflight_ready를 확인한 뒤 한 번만. key는 Python 메모리에서만 넘긴다
run = actual(
    Path("outputs/actual-v3-run-1"),
    inputs=packet_path,
    inputs_sha256=packet_sha256,
    authority=authority,
    execute=True,
    api_key=api_key_in_memory,
)

# replay: key·encoder·network 없음
again = replay(
    Path("outputs/actual-v3-replay-1"),
    inputs=packet_path,
    inputs_sha256=packet_sha256,
    authority=authority,
    original_capture=run / "capture.json",
    original_capture_sha256=capture_sha256,
)
```

- `execute=False`(기본)는 pin, Source 원본, index, model 파일, eligibility, 관찰값별 review를 확인한다. 통과하면 `status="preflight_ready"`, `reason="execution_not_requested"`다. 현재 보존본은 eligibility에서 막힌다. 그 경우 `eligibility.json`을 남기고 `ELIGIBILITY_FACTS_MISSING`으로 끝난다.
- `execute=True`는 `bool`이어야 한다(`EXPLICIT_EXECUTION_BOOLEAN_REQUIRED`). actual은 비어 있지 않은 `api_key`가 필요하고(`EXPLICIT_CREDENTIAL_REQUIRED`), `transport_for`가 `None`이어야 한다. 그래서 [`OpenAIResponsesAttempt`](../../src/skala_rag/tools/openai_attempt.py)는 native httpx transport를 쓴다. 환경변수·`.env` 조회는 없다.
- `capture_sha256`은 원본 `capture.json` bytes의 외부 기록 SHA-256이다. 같은 디렉터리의 자기 선언 값이 아니다. replay는 옆의 `campaign.json`·`reviews.json`도 읽고, `campaign_id`나 `approval_reference`가 다르면 `REPLAY_CAMPAIGN_MISMATCH`다.

## 예산과 campaign

한도는 runner에 고정돼 있고 `AdapterRuntime`·`BudgetLedger` 하나를 모든 역할이 공유한다.

- 누적 USD1, 호출 40회(`openai` 40, `retrieve` 40), 시작 시각부터 60분 deadline, 입력 200만·출력 12만 토큰. `retry_delays_seconds=()`, `max_retries=0`이라 transport 재시도는 없다. 보고서 revision은 재시도가 아니라 같은 한도를 쓰는 별도 호출이다.
- 모델 `gpt-4.1-mini-2025-04-14`, 요청당 출력 2000 토큰(`_allowance`의 `byte_bound_allowance`). 예약 단가는 캐시 없는 입력 `0.0000004`, 출력 `0.0000016` USD/token이다. 두 토큰 상한을 다 써도 USD0.992다.
- invoice가 없으니 실제 청구액은 모른다. ledger는 보수적 예약 금액을 그대로 남기고, 그 값을 확정 비용이나 0원으로 바꾸지 않는다.
- `execute=True`의 actual·controlled 실행은 encoder·provider보다 먼저 `authority.campaign_marker`를 exclusive create하고 fsync한다. marker 이름은 `campaign_id`에서만 나온다. 성공이든 실패든 지우지 않는다. marker가 있으면 `CAMPAIGN_ALREADY_STARTED`다. reset·resume 옵션은 없고, 다시 하려면 운영자 쪽 정산과 새 승인이 필요하다. replay는 marker를 건드리지 않는다.
- 유료 dispatch는 runner lock으로 직렬화되고 graph 병렬성은 그대로다.

## receipt 읽는 법

| 키 | 의미 |
| --- | --- |
| `observed_native_responses` | native post가 실제 응답(상태 코드 포함)을 돌려준 횟수. 저장 실패 전에 센다 |
| `uncertain_transport_dispatch_failures` | transport 실패인데 응답이 관찰되지 않은 횟수. 요청이 provider에 닿았는지 알 수 없다 |
| `reserved_model_attempts` | ledger의 `openai` 예약 횟수. 실제 호출 수가 아니다 |
| `actual_provider_calls` | actual에서 uncertain이 0이면 `observed_native_responses`, 하나라도 있으면 `null`. controlled·replay는 0 |
| `replayed_responses`, `observed_controlled_responses` | replay·테스트 wire 응답 수 |

uncertain이 있으면 provider 호출 수를 0이나 예약 수로 추정하지 않고 `null`로 둔다.

`status="completed"`는 다음이 모두 참일 때만이다. 보고서 `completed`, Warning 없음, Judge verdict `pass`, `severity == "stub"`인 Judge finding 없음, PDF layout 검증 valid, 점수 존재, `observed` criterion 1개 이상, 남은 review 요청 없음, 오류 없음, 적격 후보마다 6개 차원 평가. stub Judge finding이 있으면 `reason="STUB_JUDGE_FORBIDDEN"`이고, 그 밖의 미완료는 `execution_blocked`/`original_consumers_not_complete`다.

`draft.md`와 `draft.json`은 draft가 있으면 저장되지만 최종 보고서가 아니다. `report.md`나 final 파일은 만들지 않는다. PDF가 렌더되면 `render.json`에 `PDFRenderer`가 측정한 결과를 runner가 손대지 않고 저장한다. 렌더러의 `final_allowed` 측정값도 그대로이고, 발행 여부는 receipt·manifest의 `false`만 따른다. `manifest.json`은 기존 `RunManifest`(`uncommitted=true`)이고, `completed`도 발행 승인이 아니다.

## replay 조건

`capture.json`은 actual 또는 controlled 실행이 wire를 남겼을 때 생긴다. `execution_scope`, `inputs_sha256`, 구현 commitment, 파일 hash, nondeterminism 기록, `states.json`·`context.json`·`draft.json`·PDF의 안정 hash를 담는다. 구현 commitment([`implementation_commitments`](../../src/skala_rag/graph/actual_replay_v3.py))는 `src/skala_rag`의 모든 `.py`, `configs`의 `.json`·`.yaml`, `src/skala_rag/reporting/fonts`의 `.ttf`, `uv.lock`이다. controlled capture는 actual replay 원본으로 거절된다.

replay는 Source·index·model closure를 callback보다 먼저 다시 확인한다. 요청 bytes가 저장된 wire와 정확히 같아야 하고, 추가·누락·미소비 요청이나 산출물 hash 차이는 `REPLAY_WIRE_CHANGED`·`REPLAY_ARTIFACT_MISMATCH` 등으로 거절된다. fallback은 없다. 검색은 저장된 query vector(`CapturedLocalEncoder`)와 읽기 전용 index를 쓴다. controlled 원본은 replay 원본이 될 수 없다.

replay에도 같은 로컬 자산이 읽기 전용으로 있어야 한다. 10월 7일 archive, PI PDF 원본, SQLite index와 `reopen-input.json`, BGE-M3 파일 15개, 같은 commitment의 코드·설정·폰트다. fresh clone만으로는 안 되고, 팀원 배포 권한도 확인되지 않았다.

## 보존 자산의 한계

| 자산 | 확인된 값 | 한계 |
| --- | --- | --- |
| 10월 7일 archive | `/Users/luk/workspace/skala-raga-project/data/local/company-research/2026-10-07-initial`, pin `84435b773164e95908165db2787827a983be0c14136a35fba19e64b84db6fe0b` | Source-only 수용 19개, operational eligibility·rating admission 0개 |
| SQLite index | `/Users/luk/workspace/skala-raga-project/outputs/issue145-local-bge-final/index.sqlite`, `reopen-input.json` | PI 기술 PDF 2개의 Chunk 36개뿐. Skild나 다른 회사 근거로 이름을 바꾸지 않는다 |
| BGE-M3 | revision `5617a9f61b028005a4858fdac845db406aefb181`, 파일 15개 hash 검증 | 다운로드·reindex 없음 |

## 증거 구분

| 종류 | 현재 상태 |
| --- | --- |
| 구현된 preflight | 실제 Source 준비는 Source 19개·Chunk 36개·모델 파일 15개 검증, exit 0. 권위·자격·rating 미확립으로 blocked, provider 0. actual 실행 preflight_ready 증거는 아님 |
| HTTP200 접근 | 10월 9일 `GET /v1/models/gpt-4.1-mini-2025-04-14` 1회(`outputs/issue168-actual-preflight-20261009/receipt.json`). 생성이나 reserve/settle 증거가 아니다 |
| 승인된 USD1 | 사용자 승인. 위 한도로 코드에 반영. 사실·권위를 공급하지 않는다 |
| 실제 유료 graph | 없음. actual `capture.json`, scored replay, scored PDF 모두 없다 |
| controlled 테스트 | `MockTransport` 응답으로 runner·admission·capture·replay 연결을 검증. provider 호출 0, actual 실행 증명 아님 |

eligibility가 0인 지금은 최종 scored 완료를 주장할 수 없다. 기준일은 계속 2026-10-07이다. 출처에 없는 사실과 미상 Exit는 unknown으로 남는다.
