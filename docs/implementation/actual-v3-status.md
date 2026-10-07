# 현재 actual-v3 구현과 검증 상태 (#224)

[문서 홈](../README.md) · [루트 README](../../README.md) · [Python 직접 실행](python-execution.md) · [Source-only 계약](source-only-v3.md)

기준: 2026-10-07, 현재 main 커밋 `a830c7d7271544eefe4a9447401291e788fdfb74`(#223/PR #225 병합). 이 문서는 해당 소스와 부모 검증 기록을 대조한 상태 기록이다. 본문 5절+REFERENCE 구현·병합·합성 실행은 확인됐지만 전체 actual-v3는 HOLD다. 목표는 실제 자료로 조사부터 평가·선정·의미 검증·PDF·재현까지 연결하는 전체 actual-v3이며, #96/#168은 OPEN/blocked다. #219/#220의 [합성 offline 데모](offline-delivery.md)는 완료 이력으로 보존한다.

## 요구사항별 현재 상태

R01~R12는 [팀 가이드의 필수 요구](../README.md#필수-요구사항과-검증-위치) ID다. 아래 경로는 현재 소스·기존 테스트 위치이며, 테스트 파일의 존재를 이번 실행 PASS로 해석하지 않는다.

| 요구사항 | 현재 소스 / 테스트 / 산출물 | fixture와 actual의 경계 | 남은 작업 |
| --- | --- | --- | --- |
| R01·R08 LangGraph, loop, 후보 순차·내부 5 branch 병렬 | [public outer](../../src/skala_rag/graph/candidate_workflow_v3.py), [5-way join](../../src/skala_rag/graph/evaluation_v3.py), [snapshot 회귀](../../tests/integration/test_v3_evidence_snapshot_consumer.py) | 전체 평가·join은 합성 검증. #222 actual은 두 후보 research/Eligibility/advance와 no-selection만 실행했고 평가 branch는 실행하지 않음 | 동일 actual run/snapshot에서 5개 실제 callable, Business & Deal의 두 차원 원자적 성공, 전 후보 소진·selector 증명 |
| R02·R03 도메인·비상장·Seed~C·Exit 미완료·평가 가능성 | [archive producer](../../src/skala_rag/tools/company_archive.py)의 `compose_archive_company_research`, [Source-only consumer](../../src/skala_rag/source_only_v3.py), [actual 통합 테스트](../../tests/integration/test_company_archive_source_only.py) | 실제 보유 HTML Sources PI 7개/Skild 12개를 새 로컬 `ToolResult[CompanyResearchBundle]`로 변환해 원래 assembler/Eligibility에 연결. 과거 HTTP ToolResult 복원이 아님. 두 후보 `eligibility_unknown`, `NO_ELIGIBLE_RESULTS`, 점수·선정 없음 | 후보 귀속·단계·상장·Exit·충분성의 실제 심사와 field Evidence 연결. caller 이름/국가/homepage를 확인된 법인 사실로 승격하지 않음 |
| R04 실제 RAG 검색→평가→인용 | [EvidenceResearch binding](../../src/skala_rag/graph/research_artifacts_v3.py), [actual source-proof 테스트](../../tests/integration/test_technology_actual_source_slice.py), [BGE 검증 기록](local-bge-validation.md) | 기존 π0/π0.5 PDF·BGE-M3/index 보유 및 실제 source/Chunk/quote closure 증거는 있음. binding은 fixture-only. source-proof PASS는 실제 Technology 평가·semantic-positive PASS가 아님 | 원래 frozen snapshot에 실제 retrieval/chunk/page/Evidence를 묶고 Technology→보고서 인용까지 같은 실행에서 소비 |
| R05 200페이지 / R06 오픈소스 임베딩 | [D13 적용 제외](decisions.md#d13--200페이지-산정-규칙-적용-제외-91), [데이터·RAG](data-rag.md), [BGE 선택·실측 기록](local-bge-validation.md) | 200페이지 제한은 기록된 적용 제외. 기존 오픈소스 BGE-M3 선택과 보유 index는 새 비교 실측이나 전체 actual 성공을 뜻하지 않음 | 선택·비교 근거와 미실측 지표를 구분하고 최종 actual run의 실제 model/index 소비 기록 연결. 임의 다운로드/reindex를 선행 조건으로 추가하지 않음 |
| R07 승인 가중치·Missing/N/A·rating·최종 선정 | [승인 산술 소비자](../../src/skala_rag/scoring/approved_consumers.py), [outer 회귀](../../tests/integration/test_v3_approved_outer.py), [Finance 평가](../../src/skala_rag/agents/business_deal.py) | 승인 산술/판정/selector의 original outer 연결은 구현됨. `_load_fixture`는 live 거절, Finance는 non-fixture 거절. 실제 Sources/claims는 rating·N/A·점수 근거로 승인되지 않음 | actual semantic/applicability와 신뢰된 runtime/registry admission 연결. 기준 main에 Market callable 없음; Draft PR #134의 owner 인계·검토 전 구현으로 세거나 복제하지 않음 |
| R09·R10 본문 5절+REFERENCE, 실제 인용, PDF≤5페이지·SUMMARY≤반 페이지 | [보고서 pipeline](../../src/skala_rag/reporting/v3_pipeline.py), [PDF renderer/검증](../../src/skala_rag/reporting/pdf.py), [합성 데모](../../examples/v3_offline_demo.py) | #223/PR #225로 Generator/Validator/HTML/PDF의 목차 정합화가 구현·병합·검증됨. 합성 예제 PDF 5페이지·SUMMARY 비율 0.1484754956977179, 9개 산출물 readback과 5페이지 시각 확인은 실제 기업 최종 보고서 증거가 아님 | 실제 context/draft에 묶인 Generator/Judge/PDF proof, 실제 사용 인용↔REFERENCE closure, bytes/hash·분량·한글/표/인용 시각 검증 |
| R11·R12 README·개인별 수행·설계/코드/재현 보고서 PDF 제출 | [제출 기준](delivery.md), [Python 상태·산출물 계약](python-execution.md), [원래 scored State 인계](../../src/skala_rag/reporting/v3_context.py) | README 필수 항목과 개인별 실제 역할·과거 데모 안내는 유지. snapshot→보고서 인계는 구현됐지만 fixture-only, `ReportRunV3.final_allowed=False`. 합성 clean replay는 actual clean replay가 아님 | 공식 actual RunManifest, 실제 최종 보고서/PDF와 clean actual replay, 누적 usage·제출 자산/권한 확인. Draft PR #179 본문은 현재 actual 성공 증거가 아님 |

## 보유 자료와 의미 심사의 경계

2026-10-07 archive의 `collection-index.json` 외부 SHA-256 pin은 `84435b773164e95908165db2787827a983be0c14136a35fba19e64b84db6fe0b`다. index와 봉인 71파일의 hash를 다시 확인했다. index의 성공 HTML 19개, source-bound claims 22개, 기존 PDF 2개는 보유 자료·무결성 수치이지 승인된 사실·평가 결과 수가 아니다. 이 index는 `local_company_collection_integrity_index_NOT_approved_RunManifest`이며 공식 actual RunManifest가 아니다. claim anchor 일치와 의미 심사·review authority는 [Source-only 계약](source-only-v3.md)의 별도 경계다.

기존 #222 actual 산출물 `candidate-run.json`, `trace.json`, `graph-events.json`의 저장 hash를 부모 검증 기록과 대조했고 `manifest.json` 상태를 다시 읽었다. `execution_scope=source_only`, `semantic_review=unreviewed`, `evaluation=not_started`, `scoring=not_started`, `publication_allowed=false`다. `scores={}`, `decisions={}`, 선정 후보 없음은 평가하지 않았다는 뜻이지 투자 비추천이 아니다. 실제 기업 자료 부재나 Source producer 부재는 이제 차단 사유가 아니다.

현재 무네트워크 composition의 신규 CompanyResearch 호출·provider 구성은 0이다. 과거 획득의 GET attempt와 물리 HTTP/redirect 총수는 다르며, 물리 요청은 `unmeasured`, 과거 paid ledger는 `not_supplied_unverified`다. 누적 캠페인 비용·잔여 예산을 0 또는 새 예산으로 추정하지 않는다. KIPRIS/KRX/중기부/Tavily는 [기존 제외 범위](provider-scope.md)를 유지하며 여기서 호출하지 않았다.

## 검증 이력과 해석

- #222 최종 tree `ee1ae457ca0b9eb7d7e0d861e4979e2e778b4649`의 부모 gate는 **4940 passed, 8 skipped in 122.32s**였다. 당시 병합 커밋 `383c4c654a090fd2f0171a59e3a148d7e8c2ca68`과 tested tree 일치 및 producer/관련 테스트/Source-only 문서의 bytes를 대조한 과거 이력이다. 해당 producer 경로는 #225에서 변경되지 않았다. #224 worker 실행 결과도 actual 평가 성공도 아니다.
- 같은 #222 병합 기준의 별도 부모 `test_technology_actual_source_slice.py` 실행은 **1 passed in 1.09s**, exit 0이었다. 실제 π0.5 PDF·Chunk·span/quote closure와 빈 `TrustedReviewRegistry`의 `unreviewed` → `NOT_ESTABLISHED` 거절만 검증했다. 실제 semantic-positive 심사는 없다. 과거·현재 전체 gate에 합산하지 않는다.
- #223/PR #225의 최신 부모 로컬 gate는 **4986 passed, 8 skipped in 131.13s**, lint·format(356파일)·build·diff 검사 통과다. tested head `959d07cd57e2a9d284d9142d5f23595be6d84d7f`의 tree `c0e814e851385d6654b0de88f4f01ad41325a9e4`와 현재 main의 tree가 같다. #223은 CLOSED, PR #225는 MERGED다. CI checks는 빈 목록이며 CI PASS로 표시하지 않는다. 이 전체 회귀 gate도 실제 적격성·평가 성공 증거가 아니다.
- 부모의 Python 직접 합성 예제는 두 후보를 처리하고 PDF 5페이지·SUMMARY 비율 0.1484754956977179와 현재 draft/PDF hash, 9개 저장 산출물을 readback했다. 부모는 그 PDF 5페이지의 한글·표·URL·인용, TECHNOLOGY/MARKET 분리와 마지막 REFERENCE를 시각 확인했다. 같은 tested head의 clean clone·새 venv에서 캐시 기반 `uv sync --frozen --offline`과 직접 예제·readback도 성공했다. 기업·evaluator·Generator·Judge 입력은 합성이며 product 호출은 0이다. clean clone 결과는 합성 범위이고 별도 시각 검증은 미실행이다. 팀 전체의 clean actual replay·의미 심사·최종 발행 증거로 확대하지 않는다.
- 이 문서 작업은 scoped 경로·링크·사실 정합성 확인만 한다. 전체 suite/build, API/모델 실행, 신규 수집·embedding·설치는 수행하지 않으며 위 gate·예제·PDF 시각 확인은 부모의 기존 검증 기록이다.

## 승인과 구현의 남은 경계

[목차 승인 #35](https://github.com/rice-steamed-water/skala-rag/issues/35#issuecomment-6029117687), [#96 기록](https://github.com/rice-steamed-water/skala-rag/issues/96#issuecomment-6029118157), [#168 기록](https://github.com/rice-steamed-water/skala-rag/issues/168#issuecomment-6029118571)에 따라 제목은 정확히 `SUMMARY` → `COMPANY & TEAM` → `TECHNOLOGY` → `MARKET` → `INVESTMENT ASSESSMENT & RISKS` → `REFERENCE`다. [#223](https://github.com/rice-steamed-water/skala-rag/issues/223)/[PR #225](https://github.com/rice-steamed-water/skala-rag/pull/225)에서 기존 Generator/Validator/HTML/PDF 정합화를 구현·병합·검증했다. 목차 완료와 실제 평가·최종 발행은 별개이며 `ReportRunV3.final_allowed=False` guard는 유지한다.

구현 담당에게 남은 것은 fixture-only research/scoring/context 및 Finance guard의 정당한 actual admission, 실제 callable·review resolver 연결, 공유 AdapterRuntime reserve/settle와 누적 ledger, 같은 run의 Generator/Judge/PDF·발행·저장·replay다. flag 하나나 승인 문자열로 guard를 해제하지 않는다. 사용자/controller에게 필요한 것은 구체 원문 근거의 심사 범위와 신뢰된 reviewer 권위, D05/D06 적용·충돌 처리의 run-scoped 판단, 실제 실행 시 authoritative 누적 budget/사용량과 제출 시 재배포 권한이다. 이미 승인된 Core/Finance 숫자·운영 산술을 다시 미승인으로 취급하지 않으며, 누락된 사실은 권한 승인만으로 채울 수 없다. `unknown`→`eligible`, missing→0점, 기술실패→투자 비추천, Warning→final 자동 승격은 금지한다.

## 팀원 재현 전제

Python 3.11+·uv와 source checkout/개발 의존성을 사용한다. 저장소 루트에서 기존 합성 offline 예제는 다음과 같다. #224에서는 재실행하지 않았다.

```sh
uv sync --frozen --offline
uv run --offline --frozen python examples/v3_offline_demo.py
```

의존성 캐시가 있어야 offline 설치가 된다. 캐시가 없는 최초 준비는 `uv sync --frozen`으로 설치하며 네트워크가 필요할 수 있다. 이 예제는 실제 기업 archive/PDF/index나 모델을 소비하지 않고 ReportLab 합성 데모를 만든다. [기존 Python 직접 호출](python-execution.md)과 [데모 산출물 확인법](offline-delivery.md)을 따른다. 새 entrypoint/runner는 없다.

actual 보유본 테스트는 fresh clone만으로 재현되지 않는다. archive는 Git 제외 로컬 자산이며 아래 `SKALA_COMPANY_ARCHIVE_ROOT`는 원래 봉인 collection 전체가 있는 경로여야 한다. PDF source-proof의 `SKALA_APPROVED_SOURCE_ROOT`는 승인된 `data/local`과 `data/manifests`를 포함하는 root, `SKALA_APPROVED_INDEX`는 기존 승인 SQLite index 파일이다. 예시 경로는 개인 경로가 아닌 placeholder이며 실제 자산 경로로 바꾼다.

```sh
SKALA_COMPANY_ARCHIVE_ROOT=/path/to/2026-10-07-initial \
SKALA_COMPANY_ARCHIVE_PIN=84435b773164e95908165db2787827a983be0c14136a35fba19e64b84db6fe0b \
uv run --offline --no-sync pytest -q -s tests/integration/test_company_archive_source_only.py

SKALA_APPROVED_SOURCE_ROOT=/path/to/approved-source-root \
SKALA_APPROVED_INDEX=/path/to/approved/index.sqlite \
uv run --offline --no-sync pytest -q -rs -p no:cacheprovider tests/integration/test_technology_actual_source_slice.py
```

환경변수·자산이 없어서 skip되면 actual PASS가 아니다. archive 테스트는 환경변수 하나만 제공하거나 지정 경로/pin이 틀리면 실패한다. PDF source-proof도 승인 manifest/index가 없으면 skip한다. 이는 기존 자산의 무네트워크 검증 명령이며 새 수집·embedding·실제 평가 명령이 아니다. 별도 opt-in live 테스트와 #180 모델 실행은 기존 입력·API 설정·명시적 비용 승인 및 [live 정책](live-scoring-policy.md)을 요구한다.

원문·모델·index/cache의 팀원 제공 및 재배포 권한은 확인되지 않았다. `.env`/키·원문·생성 index·출력은 Git에 넣지 않으며, 공개 수집 허가를 재배포 또는 평가 의미 승인으로 간주하지 않는다. 사전 로컬 자산이 없거나 외부 API 응답이 달라지면 실제 사용 경로의 완전 재현은 보장되지 않는다. 합성 데모의 재현 가능성과 실제 기업 최종 보고서의 재현 완료를 구분한다.
