# 승인 provenance/content registry — #192

## 제공하는 것과 제공하지 않는 것

`skala_rag.scoring.approval_registry.pinned_approval_registry(root)`는 신뢰된
controller 코드가 선택하는 **비실행, 로컬 content-binding resolver**다. 운영
#82/#35, Core #59, Finance #61의 이미 승인된 범위를 정확한 역사 artifact와
연결한다. 새 승인이나 정책 값을 만들지 않는다. 입력 JSON, LLM/provider 응답,
caller의 `True`가 registry row를 정의하지 못한다.

이 registry의 신뢰 경계는 **설치된 코드와 코드 리뷰**다. GitHub 서명 검증,
실시간 GitHub 승인 조회, 원문 사실의 semantic/applicability review 또는 실행
capability가 아니다. 기록의 `observed_at`은 아래 감사에서 GitHub를 조회한
시각이며 매 호출의 네트워크 freshness를 뜻하지 않는다. GitHub comment를
수정해도 로컬 pin이 자동 변경되지 않는다. 변경 승인은 별도 감사·코드 리뷰로
처리해야 한다.

- `verify_policy(evidence, policy)`는 기존 `ApprovalVerifier` signature다.
  supplied DTO를 다시 검증하고 현재 운영 파일과 scope artifact를 매번 재개봉한다.
  운영 파일은 호출당 한 번 읽고, **같은 parsed content snapshot**으로 canonical
  digest와 `V3Policy`를 검증하여 supplied DTO와 비교한다. 후속 path reload로
  다른 세대의 policy를 승인하지 않는다. policy version이 같아도 content가
  바뀌면 거절한다. 이는 각 호출의 content binding이며 여러 파일의 원자적
  snapshot이나 전역 filesystem race 방지를 보장하지 않는다.
- `core_approval()` / `verify_core(approval)`는 기존 `CoreArtifactApproval` /
  `validate_core_artifact` 접점에 대응한다. 공급된 Core mapping의 비교는 기존
  `validate_core_artifact`가 수행한다. registry verifier도 로컬 Core 파일을 새로 읽는다.
  `verify_core`는 approval/digest만 받으므로 실제 runtime이 소비한 bytes나
  semantic review의 증명이 아니다. 그 경계는 기존 consumer/controller에 남는다.
- `pins`는 frozen record의 복사본 tuple이다. registry에는 row injection/setter가
  없다. `verify_pin(claim)`은 repo·issue/comment·author·scope/version·정확 reference,
  source commit/path/blob/bytes hash, canonical digest, comment body hash 및
  created/updated/observed timestamp의 **모든 값과 타입**을 비교한다. 모르는 pin은
  거절하며 claimant가 제공한 digest로 기준을 재정의하지 않는다.
- JSON/YAML duplicate key, malformed/non-object artifact, resolver 예외, forged
  DTO, unknown field, 변경된 anchor·최소근거는 fail closed다. positive cache는 없다.

**항상 별도인 gate:** 원문에 대한 Core anchor/인물/독립성/부정 사실 review,
Finance 사실의 metric-role·기간·회계 주체·round/pre-post review, 실제 Source/Chunk/RAG
폐쇄성·readiness, 현재 campaign의 shared ledger 및 비용·호출 승인. 이 모듈은 이를
검증하지 않고 provider 호출·model/index 준비·semantic review·reserve/settle/reset을
수행하지 않는다. 새 budget, max_candidates, RNG/seed, minimum Evidence 기본값도
없다. D05/D06/D08의 남은 OPEN만 보존하며 이미 승인된 rubric 전체를 OPEN이라고
재분류하지 않는다. 네 excluded provider와 paid fallback 금지는 기존 gate 그대로다.

## 역사 pin 감사

감사 기준: `rice-steamed-water/skala-rag`, base
`b9b96a65eda3dc5ec4bab4c896fad4cb7daf4774`, 2026-10-04 UTC.
`gh api repos/rice-steamed-water/skala-rag/issues/comments/<ID>`로 원문 body,
author, 정확 html_url, created_at/updated_at을 다시 읽었다. 세 comment의
updated_at은 created_at과 동일했다. SHA-256은 조회한 **body UTF-8 bytes**의
hash다. 승인자는 comment에 digest를 직접 서명/기록하지 않았다. 아래 mapping은
승인 문구와 역사 config/docs 변경을 대조한 code-owned commitment다.

| scope/version | 승인 근거 / author / created_at (UTC) | source commit / path / Git blob |
| --- | --- | --- |
| operational / v3-operational-1.0.0 | #35 comment5903505208, #82 본문 / luk0715 / 2026-09-30T03:31:40Z | `a2f2983717c5d927637b9840294124ef6683b258` / `configs/scoring.v3.json` / `1a27742a626b00412a94db1b206eed9a924bc796` |
| core / core-0.1.0 | #59 comment5904859865 / heojiwon2 / 2026-09-30T05:39:38Z | `865f3b146504c61de9dc925b14fc0983a7ad7c8f` / `configs/rubrics/core.yaml` / `97cc05837474823ae9618afc67168e28e3b30d04` |
| finance / finance-0.1.0 | #61 comment5906253348 / luk0715 / 2026-09-30T07:22:02Z | `6e45c6a44f9de7c36bab1da53bdf2ab9a2cafe3e` / `configs/rubrics/finance.yaml` / `06039fd0845ecc0f50611b5a86a6a1a3dc0d027b` |

Canonical content SHA-256:

```text
operational c99052ef50ad65fc5e38373410aafe7122c8a24994144835944202e44945d594
core        24345add802de1f58ff06c6097ce3341e62a39d2f2e56f06912056ae581786a0
finance     6fea9bd99a2c2a81ecfad72a0f7c15b133ab1176a08b32cb8c69bf3ea8c43d93
```

운영/Finance digest는 전체 parsed JSON/YAML을 `sort_keys=True`,
`ensure_ascii=False`, `allow_nan=False`, `separators=(",", ":")` JSON UTF-8로
직렬화한 SHA-256이다. Core만 기존 `core_artifact_digest` 그대로 **최상위 status만
제외**한다. nested status, unknown field, 타 차원, anchor, band, 최소근거는 포함한다.
공백·주석·key ordering은 semantic content가 아니며 역사 raw bytes는 별도
`source_sha256`/Git blob으로 추적한다. 이 raw hash는 현재 파일의 승인 status를
강제하기 위한 비교값이 아니다.

### Core: 승인 전과 승인 반영 후의 역사 blob 비교

승인 반영 commit 시각은 **2026-09-30T05:41:37Z**다. parent
`f1fe430d05c813c3f98902ec4d6e786455a4c24d`의 `core.yaml`과 위 commit의 blob을
직접 읽어 비교했다. diff는 주석과 최상위 `status: proposed → approved`뿐이며 두
canonical digest는 모두 위 `24345…786a0`다. 현재 PR134 Draft head를 비교한 결과를
approval-time pin이라고 사용하지 않았다.

승인 comment는 14 criterion 및 `rubric-core.md` §6 Q1–Q5 제안 그대로를 승인한다:
SAM/CAGR/특허 구간, 자기주장 최대4/5 독립 교차확인, Core 14개 N/A 금지,
핵심 창업팀 대표 수준, CAGR 1구간 차이 낮은 쪽/2구간 이상 missing.
역사 docs §6도 before/after 그대로였다:

```text
before doc blob ced2225f7b0b171609cd81c1777893d855424a2f
after doc blob  386a4a220b1dc1e9116fcc81444e1d85cfcdffcb
§6 suffix hash  6f12234d5fba76a82a8ea51f03f3242c4a2ba5330a2610e152e3a35375c21bd4
```

### Finance: 승인된 override 반영을 대조

commit 시각은 **2026-09-30T07:28:00Z**다. 이전 artifact가 그대로 승인됐다고
가정하지 않았다. 이 commit은 승인된 override를 구현하므로 before/after digest는
다르다. `rubric-finance.md` §1–§3의 구간·최소근거와 9 criterion의 기존
bands/anchors/minimum_evidence/formula/metric/weight는 유지됨을 비교했다.
새 승인 요약 및 config diff는 comment의 다음 값을 반영한다:

- Q1 작은 매출 기저 limitations 필수, 새 기준액/rating cap 없음. ownership x<5%→2 유지.
- Q2 pre/post 미표기 valuation missing, ownership 계산 불가.
- Q3 직전 대비3배/동종 중앙값 대비2배, 동종 근거2건 유지.
- Q5 CAPEX burn 제외.
- Q4 확인된 pre-revenue Rule40 N/A, Q6 같은 기간/주체 OCF>=0 확인 runway N/A.
  두 rule은 명시 ID·reason·snapshot Evidence 필요; 그 외 N/A 없음. 미확인=missing,
  runway N/A가 burn=5의 실제 재무 근거를 대신하지 않는다.

```text
before doc blob 20898fd01604f0fb12356b95d6b1a647fdf3a991
after doc blob  4f1423921f701baf7fbf251b472a08d8110925a5
§1–§3 suffix hash 6175963129d93b9ef58a90c9e9b58f6123cbdd5adc57a4e4e3f6d716040dc4f3
```

운영 pin은 #82의 23 criterion/6차원/5·30·25·20·10·10, exact 80/70/60,
missing30%/핵심40%, 정당한 N/A/0분모 오류, deterministic selector,
최초 제외 추가조사2회/공유 보고서수정2회·completed Warning/final 금지에 대응한다.
과거 CLI2 metadata는 보존하며 신규 CLI를 추가하지 않는다.

## 비실행 진단과 기존 접점

```python
from pathlib import Path
from skala_rag.scoring.approval_registry import pinned_approval_registry
from skala_rag.scoring.approved_policy import load_approved_policy

root = Path.cwd()  # trusted controller가 지정하는 저장소 root
registry = pinned_approval_registry(root)
policy = load_approved_policy(
    root / "configs/scoring.v3.json",
    approvals=registry.policy_approvals(),
    approval_verifier=registry.verify_policy,
)  # fixture/offline preflight, live 성공 아님
print(registry.diagnose(approvals=registry.policy_approvals()))
```

`diagnose`는 artifact마다 pinned approval_reference, reference_binding,
content_binding/reason, 관측 artifact_status, owner_dependency를 분리한다.
미지 reference라도 content 일치 여부는 독립적으로 보고하며 승인으로 바꾸지 않는다.
현재 main Core는 content가 일치해도 `proposed`이고 `owner_dependency="PR134"`다.
이 모듈은 파일에 status를 쓰거나 다른 owner의 PR134 코드를 복사하지 않는다.
로컬 status가 approved로 전파되어도 다음 값은 항상 유지된다:

```text
semantic_review=unreviewed
runtime_admission=not_admitted
campaign_approval=unapproved
open_decisions=(D05, D06, D08)
```

readiness/budget boolean, live gate callback, campaign reference 또는 ledger를
입력받지 않는다. 실제 admission controller는 exact run/policy/source/generation,
현재 ledger와 독립 승인 references를 자기 경계에서 검증해야 한다. 이 진단의
문자열은 준비 상태의 기록이지 permission token이 아니다.
`ApprovedPolicySource`도 configuration일 뿐이다. 기존 actual consumer는 **live를
파일·callback·산술·후보 소비 전에 무조건 거절**하며 registry가 이를 변경하지 않는다.

## 재현 및 검증

repo root에서 역사 blob/기록을 재현한다(읽기 전용):

```bash
git show 865f3b146504c61de9dc925b14fc0983a7ad7c8f^:configs/rubrics/core.yaml
git show 865f3b146504c61de9dc925b14fc0983a7ad7c8f:configs/rubrics/core.yaml
git diff 865f3b146504c61de9dc925b14fc0983a7ad7c8f^ 865f3b146504c61de9dc925b14fc0983a7ad7c8f -- configs/rubrics/core.yaml docs/implementation/rubric-core.md
git diff 6e45c6a44f9de7c36bab1da53bdf2ab9a2cafe3e^ 6e45c6a44f9de7c36bab1da53bdf2ab9a2cafe3e -- configs/rubrics/finance.yaml docs/implementation/rubric-finance.md
gh api repos/rice-steamed-water/skala-rag/issues/comments/5904859865
uv run python -c 'from dataclasses import asdict; from pathlib import Path; from skala_rag.scoring.approval_registry import pinned_approval_registry; print([asdict(p) for p in pinned_approval_registry(Path.cwd()).pins])'
uv run pytest tests/unit/test_approval_registry.py tests/unit/test_approved_policy.py tests/unit/test_approved_scoring_consumers.py tests/unit/test_moat_verification.py tests/unit/test_finance_verification.py -q
```

pins 출력에는 각 원문 source bytes SHA-256/comment body SHA-256과 정확한
observed_at도 포함된다. offline 테스트의 긍정은 역사 content/ref 대응이며 mock
reader 실패 검사는 실제 GitHub 인증/semantic review를 흉내 내어 성공시킨 것이 아니다.
전체 suite·독립 review·commit/PR/merge는 부모 작업의 별도 gate다.
