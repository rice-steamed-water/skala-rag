# v3 source-only 직접 실행 (#209, #215, #221)

## 범위와 승인 경계

`skala_rag.source_only_v3`는 기존 v3 outer의 `discover → normalize → research → eligibility → archive → advance → selector`를 소비한다. 별도 graph, 평가 stub, CLI는 만들지 않는다. 기본 fixture controller와 일반 live selector의 거절 조건은 그대로다.

이 경로의 `RunInput.execution_mode`와 terminal `execution_mode`는 `live`다. 이는 Source/Candidate를 live context로 재검증한다는 뜻이며 실제 HTTP 호출, 기업 사실 확인, 유료 실행 승인을 증명하지 않는다. 기존 `prepare_source_only_v3`의 `replay_scope="discovery_bundle_replay"`로 받은 Discovery는 캡처 재생이다. 새 주제 발견 실측으로 기록하지 않는다. 신규 Discovery producer는 연결하지 않았으며 기존 API의 다른 replay scope는 provider 구성 전에 거절한다.

신규 CompanyResearch 호출에는 기존 `LiveResearchCompany`, `OfficialHomepage(extractor=None)`, `SafeFetcher`만 쓴다. 사실 추출 모델을 구성하지 않으므로 홈페이지 조회가 성공해도 기업 사실이나 평가 Evidence를 새로 만들지 않는다. `research_replays`를 전달하면 해당 후보의 기존 `ToolResult[CompanyResearchBundle]`을 재생한다. 신규 provider 호출과 캡처 재생은 산출물에서 구분한다.

운영 수치는 기존 code-owned approval registry의 pinned content로 확인한다. `V3Policy`의 fixture-only 구조, 일반 `select_best_v3`/approved selector의 live 경계는 바꾸지 않는다. 정책 pin, DTO 검증, 입력 hash, Source metadata는 의미 검토나 실행 승인 근거가 아니다. `eligible` 후보도 evaluator를 만들지 않고 `SOURCE_ONLY_EVALUATION_NOT_READY`로 실패 처리한 뒤 archive/advance한다. collect/freeze, 평가 다섯 branch, scoring, Decision, 보고서 생성은 시작하지 않는다.

## Python 호출 계약

다음 두 callable을 직접 호출한다. 기존 fixture `skala_rag.cli.run()`과는 반환 계약이 다르다.

```python
from skala_rag.source_only_v3 import prepare_source_only_v3, run_source_only_v3

boundary = prepare_source_only_v3(**source_only_inputs)
result = run_source_only_v3(boundary, output_dir=private_new_directory)
```

`source_only_inputs`와 새 output directory는 호출자가 준비한다. 이 예시는 실제 입력, HTTP 권한 또는 factory를 생성하는 실행 명령이 아니다.

| 인자 | 호출자가 제공할 값 |
| --- | --- |
| `run_id` | RunProfile 및 원본 retrieval/error의 run ID와 같은 값 |
| `run_input` | `execution_mode="live"`인 `RunInput`; theme, countries/languages, as_of, corpus/policy/schema를 명시 |
| `discovery_result` | 원본 Source closure, Candidate, RetrievalRecord, WorkflowError를 포함한 캡처 `ToolResult[DiscoveryBundle]` |
| `run_profile` | 기존 운영 프로필: 최대 5개, seed 42, 초기 research 1회, unknown 재시도 0, refill 없음, 유료 allowance/cost 0 |
| `budget` | 후보별 `LiveResearchCompany` 호출에 적용하는 `ToolBudget`; 같은 schema, `max_calls ≥ 1`, `max_retries=0`, 양수 timeout 및 선택적 deadline |
| `provider` | `official-homepage`만 허용 |
| `replay_scope` | `discovery_bundle_replay`만 허용 |
| `fetcher_factory` | 호출 시 기존 `SafeFetcher`를 반환하는 factory; HTTP/transport 구성과 권한은 호출자 책임 |
| `clock` | 기존 Clock 계약 |
| `research_replays` | 선택적 candidate-ID별 원본 `ToolResult[CompanyResearchBundle]`; 새 provider 호출 없이 재생 |

provider 제외, paid0, budget, profile, run/schema/policy/corpus/as_of, Source closure, fixture URI 혼입은 fetcher factory 호출 전에 검사한다. 입력은 JSON/DTO로 분리해 pin하며 hash는 변경 감지에만 쓴다. 재생 결과가 세대 정보를 선언했다면 현재 입력과 일치해야 한다. 명시한 `canonical_name`/`homepage_url`, 기존 CompanyResearch 요약의 이름 query, OfficialHomepage의 URL 선언은 원본 후보와 대조하고 소비 시 정규화된 후보와도 다시 대조한다. 이름은 canonical name/alias, URL은 기존 host/site 비교만 사용한다. 불투명한 query나 알 수 없는 추가 metadata는 identity 증명으로 승격하지 않는다. 선언하지 않은 필드를 새 승인이나 원본 실행 세대의 증명으로 채우지 않는다.

선정 뒤 후보를 다시 채우거나 reselection하지 않는다. dedup merged ID와 제외 후보는 연구하지 않는다. 선정 후보는 순서대로 한 번씩 처리하고 마지막에 selector를 한 번 호출한다. 전용 selector guard는 점수/결정 없는 noneligible/failed terminal row만 받고 기존 deterministic no-selection 함수를 사용한다. 원본 Eligibility를 row에 유지하며 `eligible`은 `status="failed"`일 때만 통과한다. evaluated row와 점수/결정은 계속 거절한다.

신규 홈페이지 Source에는 발행일이 없다. 선정 뒤 신규 호출이 필요한 후보가 있으면 `clock.now().date() ≤ as_of`와 미만료 deadline을 factory 전에 검사한다. factory 구성 직전과 각 신규 CompanyResearch 호출 직전에도 다시 검사한다. 최초 거절은 `ValueError`로 전파하며 완성 산출물을 만들지 않는다. 실행 중 만료하면 해당 후보를 기술 실패로 archive/advance하고 새 요청 수를 올리지 않는다. 이는 실행 시각의 admission 오류이며 비상장, Exit 없음 등 기업 사실을 뜻하지 않는다.

캡처 재생은 현재 clock이나 신규 fetch deadline을 소비하지 않는다. 원본 Source의 발행일/확보일 cutoff 검사는 그대로다. 늦은 시각에 실행해도 기준일 전에 확보한 캡처는 재생할 수 있지만, 과거 발행일만 있는 기준일 이후 편집본을 허용하지는 않는다. factory 구성이 실패하면 실패 여부만 메모리에 남기고 후속 후보에서 재구성하지 않는다. 재생 후보는 계속 소비하고 모든 처리된 후보를 한 번씩 archive/advance한다. raw exception은 저장하지 않는다.

## 고정 후보와 원본 캡처만 재생 (#215)

새 수집 없이 실행하려면 `prepare_offline_source_only_v3`를 호출한다. `candidate_bundle`은 기존 `DiscoveryBundle` DTO에 담은 고정 Candidate/Source 입력이다. Discovery 성공 ToolResult, 검색 기록이나 과거 HTTP 성공을 만들지 않는다. `research_captures`는 호출자가 확보한 원본 `ToolResult[CompanyResearchBundle]`의 candidate-ID map이다.

```python
from skala_rag.source_only_v3 import prepare_offline_source_only_v3, run_source_only_v3

boundary = prepare_offline_source_only_v3(
    run_id=run_id,
    run_input=run_input,
    candidate_bundle=candidate_bundle,
    research_captures=research_captures,
    run_profile=run_profile,
    budget=budget,
    clock=clock,
)
result = run_source_only_v3(boundary, output_dir=private_new_directory)
```

위 변수는 호출자가 준비한다. factory/provider/model 인자나 기본 수집 fallback은 없다. 기존 profile, budget, policy pin과 Source cutoff를 검사하고 기존 outer가 한 번 정규화/선정한다. 선정 후보 캡처가 하나라도 없으면 첫 research/assembler 호출 전에 `ValueError`로 거절하며 산출물을 만들지 않는다. 제외/dedup 후보의 캡처는 없어도 되고, 제공했다면 unused 입력으로 보존한다. 관계없는 후보 ID는 거절한다. 오래된 캡처는 현재 clock이나 fresh-fetch deadline으로 막지 않는다.

저장 `replay_scope`와 후보 입력 `origin`은 `fixed_candidate_input`이고 manifest의 신규 provider는 `null`이다. 원본 캡처와 진단은 바꾸지 않는다. required 오류는 기술 실패, optional 오류는 원래 하위 기록, empty는 unknown으로 남는다. 실제 assembler/Eligibility를 쓰되 eligible도 기존 `SOURCE_ONLY_EVALUATION_NOT_READY`로 종료한다. 저장 상태는 투자 결과가 아니다.

당시 #215 회귀 제어군은 합성 캡처와 MockTransport만 썼으며 실제 PI 기업의 CompanyResearch 적격성 캡처는 확보하지 않았다. 이 이력은 아래 #221의 새 로컬 변환과 구별한다. 승인 PI 논문 Source/Chunk나 raw PDF를 기업 비상장/완료 Seed~C/Exit 미완료 근거로 바꾸지 않는다. actual Coverage/freeze/평가/scoring/report와 유료 호출은 열지 않는다.

## 봉인 기업 archive에서 새 Source-only 결과 생산 (#221)

`skala_rag.tools.company_archive.compose_archive_company_research`는 보유 공개 자료를 읽어 기존 `LiveResearchCompany`를 후보당 한 번 호출한다. 원래 수집 당시의 프로젝트 ToolResult를 복원하는 함수가 아니다. 원본 packet은 `NOT_CompanyResearchBundle_or_Evaluation`이며 그대로 ToolResult에 넣으면 계속 거절된다. 반환값만 새 로컬 변환의 `ToolResult[CompanyResearchBundle]`이다.

```python
from skala_rag.tools.company_archive import compose_archive_company_research
from skala_rag.source_only_v3 import prepare_offline_source_only_v3, run_source_only_v3

research_captures = {
    candidate.candidate_id: compose_archive_company_research(
        archive_root=archive_root,
        expected_index_sha256=external_index_pin,
        candidate=candidate,
        run_input=run_input,
        run_id=run_id,
        budget=budget,
        clock=clock,
    )
    for candidate in candidate_bundle.candidates
}
boundary = prepare_offline_source_only_v3(
    run_id=run_id,
    run_input=run_input,
    candidate_bundle=candidate_bundle,
    research_captures=research_captures,
    run_profile=run_profile,
    budget=budget,
    clock=clock,
)
result = run_source_only_v3(boundary, output_dir=private_new_directory)
```

모든 변수는 호출자가 제공한다. 후보 이름·국가·homepage·선정 입력은 수집 후 고정한 caller 선언이지 이 archive가 확인한 법인 정보나 사전 무작위 모집단이 아니다. `post_collection_selection_context=true`로 구별한다. 내부 `archive-local-sources`는 로컬 Sources port일 뿐 일반 live factory에 추가한 외부 provider가 아니다. provider allowlist, 운영 정책 registry, assembler, as_of, Eligibility, graph와 저장 경계는 기존 구현을 그대로 쓴다.

외부 index SHA-256은 필수다. composer 구성 전에 봉인 목록의 모든 bytes, manifest/receipt/source/candidate/packet join, raw→text 일치, Unicode claim anchor, 중복 ID와 시각을 검증한다. 선택하지 않은 후보도 검사한다. canonical 상대 경로만 허용하고 root부터 각 경로 component의 symlink와 일반 파일이 아닌 입력을 거절한다. 파일 상한은 일반 파일/HTML 8 MiB, 보유 PDF 32 MiB, 전체 64 MiB, 봉인 파일 128개다. PDF 상한은 과거 HTTP 응답 상한을 늘리는 설정이 아니라 기존 로컬 보유본의 무결성 검사 한도다. archive Python은 실행하지 않으며 자료를 고치거나 다시 봉인·수집하지 않는다. 중복 JSON key, 비유한 수치, 과도한 nesting과 이 형식에 없던 프로젝트 run/schema/generation 선언도 거절한다.

성공 HTML만 원래 ID·title·URL·publisher(없으면 null)·raw hash로 Source를 만든다. language가 없으면 `unknown`이다. `retrieved_at`은 원래 receipt의 `finished_at`과 같은 시각이며 `published_at=null`은 그대로 둔다. claim에 관측된 날짜, HTTP Date, 현재 변환 시각이나 논문 `copied_at`를 발행·확보 시각으로 대체하지 않는다. 기존 PDF는 Source를 새로 만들지 않고 `reused_assets` metadata로만 보존한다. claim bytes/anchor 일치는 무결성 검사이지 사실·authority·criterion 의미 검토 통과가 아니다. 기본 호출은 `observations=()`, `calls=()`이며 FieldObservation/Evidence/rating/N/A/점수를 만들지 않는다. boolean None, stage unknown과 빈 field map은 기존 assembler가 만든다. 아래 #227의 명시적 설명 심사 입력만 예외다.

Source의 `bibliographic_metadata`에는 원래 receipt와 그 source의 원래 claims를 보존한다. Source는 원래 HTTPS URL을 유지하고 `local_path=null`로 반환한다. receipt의 `raw_path`는 과거 보관 위치 metadata일 뿐 consumer가 파일을 다시 읽어도 되는 경로가 아니다. 변환 후 원문 파일이 바뀌어도 반환 Source의 hash와 receipt는 바뀌지 않으며 새 변환은 봉인 hash 불일치를 composer 호출 전에 거절한다. 새 composer summary의 `arguments_without_secrets.archive_conversion`에는 index, 세 manifest, candidate packet, 전체 receipts와 검색 crosscheck를 원래 JSON subtree로 둔다. 원래 403/404/429/oversize, 검색 backend 실패·fallback/rescue와 reused assets를 새 WorkflowError나 요청 RetrievalRecord로 바꾸지 않는다. 새 summary의 ID/run/provider/status/started_at/finished_at와 원래 획득 시각은 별개다. JSON/DTO 왕복으로 분리하며 반환 metadata 변경은 archive나 다른 호출에 반영되지 않는다.

현재 `current_composition.scope="local_archive_transformation_only"`의 외부 요청/외부 비용 0과 composer의 `requests_used=0`은 이번 무네트워크 변환에만 해당한다. composer `cost`는 원래 계약의 null을 유지한다. 과거 획득의 물리 HTTP 요청은 `unmeasured`, paid ledger와 프로젝트 ToolRuntime은 `not_supplied_unverified`다. redirect 총수나 캠페인 비용을 실측 0으로 채우지 않는다.

2026-10-07 최초 보유본은 외부 pin `84435b773164e95908165db2787827a983be0c14136a35fba19e64b84db6fe0b`에 묶인다. 봉인 파일 71개, source-bound claims 22개, 성공 HTML PI 7개/Skild 12개와 기존 PDF 2개다. GET attempt 23개와 실패 4개에는 preliminary PI429를 포함한다. 검색 4개의 backend 실패/구조와 rescue도 보존한다. 이 수치는 물리 HTTP 요청 총수나 승인 RunManifest가 아니다.

실제 보유본 테스트는 아래 두 환경변수가 모두 있을 때만 실행한다. 둘 다 없으면 명시 skip하며 actual PASS로 세지 않는다. 하나만 있거나 지정 경로·pin이 잘못됐으면 실패한다. 원문 경로나 자료는 Git에 넣지 않는다.

```sh
SKALA_COMPANY_ARCHIVE_ROOT=/path/to/2026-10-07-initial \
SKALA_COMPANY_ARCHIVE_PIN=84435b773164e95908165db2787827a983be0c14136a35fba19e64b84db6fe0b \
uv run --offline --no-sync pytest -q -s tests/integration/test_company_archive_source_only.py
```

실제 두 후보의 새 composer 결과는 위 public consumer를 통과해 각각 `eligibility_unknown`, 전체 `no_eligible_candidates`/`NO_ELIGIBLE_RESULTS`로 끝난다. 정상 cutoff에서는 19 Sources를 보존하고 2026-10-06 cutoff에서는 전부 제외한다. 더 늦은 consumer clock도 원래 확보 시각을 덮어쓰지 않는다. private JSON readback/hash, metadata 보존과 callback 0을 테스트한다. 별도의 합성 sealed archive 테스트는 hash·anchor·candidate·경로·시각·JSON 공격 입력의 composer 전 거절과 반복 호출 분리를 검사한다.

기업 facts와 authority의 전체 범위는 여전히 미검토다. PI Series C 날짜 충돌·Acq - Rumored와 Skild의 Zebra division 매수자 역할을 법적 Exit 또는 단계 확정으로 승격하지 않는다. Skild `$1.4 billion` literal, `currency_normalized=null`, `closing_date=null`도 packet metadata 그대로다. 이 슬라이스는 Sources 보존과 unknown consumer 연결이며 full actual E2E·Coverage/freeze·평가·scoring·보고서·최종 발행, 신규 corpus/index/model/유료 실행 승인을 대신하지 않는다.

## 사용자 심사한 단일 설명의 원래 소비 경로 (#227)

사용자는 `skild-ai-011`의 출처 귀속 진술과 제한을 이미 수용했고, 별도로 “한정된 설명 Evidence 연결 구현을 승인”을 선택했다. 이 권한은 Sequoia의 소개를 `candidate_id="skild-ai"`, `scope="company"`, `criterion_ids=[]`로 보존하는 데만 적용한다. 나머지 수용 진술 4건의 자동 소비, 법인 동일성, domain/listing/stage/Exit field mapping, rating, 최소 Coverage, 유료 실행과 발행은 포함하지 않는다. 봉인 packet의 역사 `unreviewed_for_production_semantics`와 manifest의 전체 `semantic_review="unreviewed"`를 유지한다.

`prepare_reviewed_archive_description(...) -> str`는 기존 archive loader로 전체 봉인 bytes와 raw/text/claim closure를 확인한 뒤 한정된 기대 snapshot을 immutable JSON 문자열로 분리한다. 필수 인자는 `archive_root`, `expected_index_sha256`, `candidate`, `run_input`, `run_id`, `proposal_bytes`, `expected_proposal_sha256`, `decision_bytes`, `expected_decision_sha256`다. 심사안과 결정 bytes를 외부 기대 hash와 대조하고, 정확한 단일 item, 원래 claim 필드, 후보/기준일/archive pin, 진술/제한, reviewer attribution 및 보충 Unicode excerpt를 검증한다. 새 범용 registry, 승인 DTO, runner 또는 accepting callback은 없다.

```python
from skala_rag.tools.company_archive import (
    compose_archive_company_research,
    prepare_reviewed_archive_description,
)

reviewed_description = prepare_reviewed_archive_description(
    archive_root=archive_root,
    expected_index_sha256=external_index_pin,
    candidate=candidate,
    run_input=run_input,
    run_id=run_id,
    proposal_bytes=proposal_bytes,
    expected_proposal_sha256=trusted_proposal_pin,
    decision_bytes=decision_bytes,
    expected_decision_sha256=trusted_decision_pin,
)
capture = compose_archive_company_research(
    archive_root=archive_root,
    expected_index_sha256=external_index_pin,
    candidate=candidate,
    run_input=run_input,
    run_id=run_id,
    budget=budget,
    clock=clock,
    reviewed_description=reviewed_description,
)
boundary = prepare_offline_source_only_v3(
    run_id=run_id,
    run_input=run_input,
    candidate_bundle=candidate_bundle,
    research_captures={candidate.candidate_id: capture},
    reviewed_descriptions={candidate.candidate_id: reviewed_description},
    run_profile=run_profile,
    budget=budget,
    clock=clock,
)
result = run_source_only_v3(boundary, output_dir=private_new_directory)
```

변수와 최종 고정 Candidate/RunInput은 호출자가 먼저 준비한다. 위 예시는 입력 생성이나 실행 권한을 부여하지 않는다. `compose_archive_company_research`와 원래 `assemble_research_state`는 선택적 `reviewed_description: str | None = None`를 받는다. 두 SourceOnly 준비 함수는 선택적 `reviewed_descriptions: Mapping[str, str] | None = None`를 받으며 capture가 있는 후보만 허용한다. SourceOnly는 이 별도 기대 입력도 기존 binding digest에 포함하고 소비 직전에 다시 변경 여부를 검사한 뒤 **원래 assembler에 전달한다**. 생략하면 기존 Sources-only/임의 manual 거절과 기본 binding 내용은 그대로다.

composer는 자기 호출에서 읽은 새 archive snapshot과 보호된 기대 snapshot을 대조한 뒤 기존 `LiveResearchCompany`의 실제 로컬 summary에 Evidence를 붙인다. 소비자는 source 파일을 다시 열지 않는다. 준비 이후 archive가 바뀌면 새 composition은 거절하지만, 이미 검증·분리한 캡처를 소비할 때 mutable 경로로 다시 연결하지 않는다. Evidence에는 수용한 정확한 귀속 진술·제한과 보충 excerpt만 들어간다. `reported/unknown`, 빈 criterion/supporting/conflict 목록과 모든 수치/사건/derivation/supersedes의 None을 유지한다. provenance는 현재 로컬 summary의 retrieval ID, `method="manual"`, `chunk_id=None`다. HTTP ProviderCall, LLM 응답 또는 RAG Chunk를 만들지 않는다.

assembler는 result/State 밖에서 전달받은 기대 입력과 현재 Candidate/RunInput/run/schema/as_of, 전체 Source/profile, statement/excerpt/locator/limitations, archive/review identity와 현재 성공 summary의 closure를 대조한다. 검증 후에도 기존 `verify_provenance`로 stable Evidence ID와 record/Source 연결을 확인한다. 임의 marker/accepted 문자열만 있는 manual Evidence는 계속 거절한다. `field_evidence_ids={}`, boolean None과 stage unknown은 그대로이고 Eligibility는 unknown이며 평가·점수·선정·보고서는 없다. 심사 metadata는 `arguments_without_secrets.reviewed_archive_description`에 item별로 남기며 전체 Source의 심사를 승격하지 않는다.

**신뢰 책임:** hash 일치와 immutable snapshot은 caller-owned 입력의 무결성 검사이지 심사자의 인증이나 진술의 독립적 사실 증명이 아니다. 호출자는 신뢰된 사용자 심사 기록에서 외부 기대 pin과 범위 권한을 확보하고 보호해야 한다. 같은 mutable 파일이나 ToolResult/State의 자기 선언에서 hash와 기대 context를 함께 가져오면 이 신뢰 전제를 충족하지 않는다. 모든 기대 입력과 결과를 일관되게 다시 만든 공격자나 권한 있는 caller의 허위 재선언을 서명 없이 인증할 수 없다. reviewer 권위·기록 보관, 향후 field/criterion 의미 심사, runtime/spending gate와 재배포·발행 권한은 별도 의무다.

실제 한 진술 회귀는 기존 archive opt-in 2개에 아래 4개를 추가해 같은 테스트 파일을 실행한다. 심사 변수 4개가 모두 없으면 해당 새 테스트만 opt-in skip이다. 일부만 지정했거나 paths/pins/bytes가 틀리면 실패한다. 합성 bytes/decisions는 거절·분리 제어군일 뿐 실제 사용자 심사로 세지 않는다.

```sh
SKALA_COMPANY_ARCHIVE_ROOT=/path/to/2026-10-07-initial \
SKALA_COMPANY_ARCHIVE_PIN=84435b773164e95908165db2787827a983be0c14136a35fba19e64b84db6fe0b \
SKALA_COMPANY_REVIEW_PROPOSAL=/path/to/semantic-review.json \
SKALA_COMPANY_REVIEW_PROPOSAL_PIN=4d0026fe09c0de2e5165f65f8ffdba5ebd9563b776721ac6a6dc7acd18f8881d \
SKALA_COMPANY_REVIEW_DECISIONS=/path/to/user-review.json \
SKALA_COMPANY_REVIEW_DECISIONS_PIN=104da926ef282c67701477e4c205a7ef1a1993c9f9aa35cf3a5c5dd48a01ddd0 \
uv run --offline --no-sync pytest -q -s tests/integration/test_company_archive_source_only.py
```

이는 보유 실제 archive·사용자 결정으로 composer → public SourceOnly → 원래 State를 실행하는 로컬 회귀다. saved Evidence/제한/manual record, 빈 field map, unknown/no-selection, archive·artifact hash와 네트워크/모델/encoder/평가/scoring/report callback 0을 확인한다. 새 실제 수집, full actual-v3, 유료 실행과 최종 발행의 완료 증거가 아니다.

## 결과와 산출물

반환값은 기존 `CandidateRunV3`다. `source_only_detail`에 Discovery 원본 결과 또는 origin을 명시한 고정 후보 bundle과 재생 범위, 연구별 ToolResult, 조립 State, Source, RetrievalRecord, 원본 WorkflowError, Eligibility, receipt를 보존한다. 원본 연구 기술 오류는 code/ID/run/candidate를 유지한다. #203의 required-extractor 실패 Source retention도 기존 consumer로 복원한다. retention이 malformed이면 거절된 payload를 사실로 보존하지 않고 `UPSTREAM_INVALID`로 실패 archive/advance한다. 전체 원본 캡처는 `capture_replay_inputs`에 입력으로 따로 보존한다. 이 입력 보존은 State에 사실로 채택했다는 뜻이 아니다. `SOURCE_ONLY_EVALUATION_NOT_READY`는 controller-local WorkflowError 진단 문자열이다. ToolResult ErrorCode enum이나 Enum 재수화 계약을 추가하지 않았으며 #203 원본 typed 오류는 바꾸지 않는다.

| terminal | 의미 |
| --- | --- |
| `no_candidates` | 정상 Discovery 캡처 또는 고정 후보 입력에 후보가 없음 |
| `discovery_failed` | 원본 Discovery의 기술 실패 또는 발견·정규화·선정 단계의 입력/실행 실패 |
| `no_eligible_candidates` | 연구 기술 실패 없이 전 후보가 unknown/ineligible |
| `source_only_partial_failure` | 일부 후보에 연구 오류, 잘못된 retention 또는 아직 열리지 않은 평가 경계 등 기술 실패 발생 |
| `source_only_technical_failure` | 전 후보가 기술 실패로 종료 |

위 상태에서 선정 후보, scores, decisions는 없다. 정상 empty/unknown/ineligible의 selector reason은 `NO_ELIGIBLE_RESULTS`다. Discovery 실패는 `SOURCE_ONLY_DISCOVERY_FAILED`, 전 후보 실패는 `SOURCE_ONLY_TECHNICAL_FAILURE`, 정상 후보와 실패가 섞이면 `SOURCE_ONLY_PARTIAL_FAILURE`로 구분한다. 기술 실패를 투자 비추천 진단이나 정상 결측으로 바꾸지 않는다. 취소 예외는 전파하며 terminal artifact를 만들지 않는다. 일반 입력 거절과 파일 I/O 오류도 전파될 수 있다.

새 directory를 `0700`, JSON 파일을 `0600`으로 만들고 저장 byte를 다시 읽어 확인한다. 기존 directory는 덮어쓰지 않는다.

- `candidate-run.json`: terminal state와 원본/조립 연구 상세
- `trace.json`: 기존 trace와 discover/normalize/research/eligibility 실행 시간. `status="ok"`는 callback이 반환됐다는 뜻이지 Eligibility 성공이나 적격 판정을 뜻하지 않는다.
- `graph-events.json`: 실제 LangGraph node, namespace, candidate/index/route 요약
- `manifest.json`: run input/profile/budget/provider, 입력 binding hash, 신규 CompanyResearch 호출/캡처 재생 시도 수, factory 구성 시도 수, 후보별 호출/재생 시도 수, 캡처 supplied/attempted/unused/excluded/dedup ID와 원본 입력 hash, 나머지 세 파일의 SHA-256

실패로 detail을 조립하지 못한 재생도 시도 수에 포함한다. factory 실패와 admission 거절은 provider 호출 수에 넣지 않는다. budget scope는 `per_candidate`이며 캠페인 전체 요청 한도가 아니다. replay budget scope는 `no_new_network_not_charged_to_fresh_fetch_deadline`, 물리 HTTP 요청 수는 `unmeasured`로 명시한다. 이 usage는 이번 composition 범위이며 실제 HTTP request 수, 과거 유료 호출이나 과거 ledger를 추정하지 않는다. 과거 ledger는 `not_supplied_unverified`다. manifest는 자기 hash를 포함하지 않는다. `semantic_review="unreviewed"`, `evaluation="not_started"`, `scoring="not_started"`, `publication_allowed=false`를 유지한다. private JSON은 보고서 발행 허가가 아니다.

clients/factory/환경 snapshot/raw exception은 State에 넣지 않는다. `run_source_only_v3(..., secret_values=...)`는 호출자가 지정한 secret 문자열의 저장을 거절한다. 이것은 입력 DTO나 원본 Source 전체가 비밀을 포함하지 않는다는 보증이 아니므로 호출자가 캡처를 확인해야 한다. 취소나 I/O 오류로 중단하면 완성 manifest가 없을 수 있다.

## 확인한 범위

고정 입력 경로는 선정 캡처 누락의 첫 research 전 거절, 후보와 Source의 연결·cutoff·세대 검증, 입력 분리와 변경 거절, 실제 assembler/Eligibility 소비와 private JSON hash를 검사한다. 이 경로에서는 budget을 신규 호출에 쓰지 않는다.

통합 테스트는 socket을 거절하고 `httpx.MockTransport`로 기존 source tool, 실제 CompanyResearch/Eligibility consumer, 기존 LangGraph outer와 Python composition을 실행한다. 가상 홈페이지와 기존 합성 fixture는 기업 사실 실측이 아니다. unknown, 원본 LLM_TIMEOUT와 Source retention, no homepage, 정상 empty/failed Discovery, malformed 입력/retention, 제외 provider, paid0, callback 변경, dedup/no-refill, eligible 거절, no-selection guard, 취소, JSON/trace/hash를 검사한다.

이 문서는 actual HTTP, 실제 모델, 새 corporate eligibility 사실, 정책 의미 승인, 유료 캠페인, full scoring/live evaluation을 실행하거나 승인하지 않는다. 그 경계는 [live 정책](live-scoring-policy.md), [run 설정](run-settings.md), [Python 직접 실행](python-execution.md)의 기존 승인 조건을 따른다.
