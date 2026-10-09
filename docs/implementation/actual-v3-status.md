# 현재 actual-v3 구현과 검증 상태 (#224)

[문서 홈](../README.md) · [루트 README](../../README.md) · [Python 직접 실행](python-execution.md) · [Source-only 계약](source-only-v3.md)

기준: 2026-10-07, 현재 main 커밋 `a830c7d7271544eefe4a9447401291e788fdfb74`(#223/PR #225 병합). 이 문서는 해당 소스와 부모 검증 기록을 대조한 상태 기록이다. 본문 5절+REFERENCE 구현·병합·합성 실행은 확인됐지만 전체 actual-v3는 HOLD다. 목표는 실제 자료로 조사부터 평가·선정·의미 검증·PDF·재현까지 연결하는 전체 actual-v3다. #96은 PR #229 병합으로 CLOSED이고 #168은 아래 [무과금 연결 갱신](#168-무과금-연결-갱신-2026-10-08) 상태다. #219/#220의 [합성 offline 데모](offline-delivery.md)는 완료 이력으로 보존한다.

## #168 무과금 연결 갱신 (2026-10-08)

작업 브랜치 `feat/168-completion-connections`의 상태다. 아직 main에 병합되지 않았다.

- 사용자가 고른 순서는 연결 구현을 먼저 하고, 원문 기반 독립 agent review를 붙이는 것이다. 유료 full scored actual E2E는 계속 보류다. 새 actual scored run은 실행되거나 증명되지 않았다.
- 공개 연결 점검 [`examples/v3_connection_check.py`](../../examples/v3_connection_check.py)가 원래 outer graph, 5개 approved evaluator(Founder/Technology/Market/Moat/Business & Deal), EvidenceResearch, Generator/Judge, PDF를 공유 runtime·ledger 하나로 실행한다. 응답은 MockTransport 통제 응답이고 `actual_provider_calls=0`, `external_requests=0`이다. capture/replay/tamper 사용법은 [연결 점검](connection-check.md)에 있다.
- 제안된 Core artifact는 [`approval_registry.py`](../../src/skala_rag/scoring/approval_registry.py)의 code-owned pin으로 bytes 그대로 보존된다. pin 일치, 원문 capture 무결성(`verify_original_capture`), 의미 심사(`SourceBoundReviewResolver`)는 서로 다른 검사다. 연결 점검에는 positive semantic review가 하나도 없고 미상 기준 23개는 모두 Missing이다.
- 부모 검증 기록: evaluator 테스트 525개, audit 수정 테스트 353개, controller 테스트 954개 통과, 실제 provider 호출 0. 이 문서 작업에서는 재실행하지 않았다. 합성 통과이지 실제 평가 성공은 아니다.
- Dexory의 기존 actual source-review PDF는 [Source 검토 보고서](source-review-report.md)에 보관된 점수 없는 최소 최종 보고서다(PR #229 병합, #96 CLOSED). 새 scored run이 아니다.
- 저장소 정적 타입 검사는 깨끗하지 않다. 이전부터 쌓인 type debt가 남아 있고, 이번 연결 작업이 이를 해소했다고 주장하지 않는다.
- 남은 gate: 비용 승인 아래 실제 provider로 하는 유료 scored E2E, 그리고 원문 근거에 대한 신뢰된 reviewer의 positive semantic review. 둘 다 없으면 `final_allowed=False`와 점수 없음 상태를 유지한다.

### 실제 원문 독립 심사와 사전 검증

2026-10-08 보존 public-discovery packet의 후보 5개(avatarin, Autman Robotics, Kisui, Dexory, Unbox Robotics)를 독립 심사했다. claim 35개, 인용 59개, 참조 capture 8개 전체를 검토했고, 부모도 원본 body·추출문·파일 hash, codepoint/UTF-8 인용 범위, 입력 pin과 HTML 재추출을 직접 대조했다. 무결성 오류는 0건이다. 역사적 발행 진술로 제한해 지지되는 claim은 24개, 미상은 11개다. 제안 observation 14개는 operational 승인 기록이 아니며 실제 자격 승인·criterion rating·scored 후보는 모두 0개다.

로컬 심사 산출물은 `outputs/issue168-original-agent-review/report.json`과 `findings.md`다. JSON SHA-256은 `d3381ac6836cdcc97c16e55327a0ec66084b8a0f19a5890c56df282366290d7f`, Markdown SHA-256은 `066728b558c723e67782320fe026104b9b08fb4cff634d9b47ac8adb04736602`다. 출력과 원문은 Git에 포함하지 않는다.

현재 실행을 막는 구체 조건은 다음과 같다.

- 요청 기준일은 2026-10-07인데 참조 capture 8개 모두 10월 8일 획득이다. 과거 기사 발행일만으로 기준일 이전의 동일 edition을 증명하지 못한다. 사용자가 10월 9일 재개에서 “10월 7일 유지”를 명시했으므로 이 packet을 실제 평가 입력에 사용하지 않는다.
- 역사적 투자·제품 설명은 현재 비상장 여부, 최신 투자 단계·정확한 closing date, Exit 상태, 평가 충분성이나 등급 근거를 대신하지 않는다. Dexory의 USD165M headline은 본문의 USD100M Series C와 확대된 debt facility를 합친 funding 설명이며 전액 equity나 revenue가 아니다.
- packet의 `simple-html-text-v1`과 resolver의 HTML parser는 다르다. 원문 심사 결과를 사용할 때에도 승인된 추출문에 인용 범위를 다시 맞추고 정확한 candidate·기준일·snapshot·rubric·request/receipt에 결속해야 한다. 이 보고서는 그 기록을 발급하지 않았다.
- 기존 SQLite index는 실제 PI 기술 PDF 2개·Chunk 36개다. fresh-process read-only 재개 검증과 BGE-M3 revision `5617a9f61b028005a4858fdac845db406aefb181`의 파일 15개 hash 검증은 통과했다. 보존 query vector를 사용한 재개 확인이며 새 embedding 추론은 아니다. 이 corpus를 위 5개 기업의 근거로 이름만 바꿔 사용할 수 없다.
- 사용자가 10월 9일 “예산 1$ 승인”을 명시했다. 새 실제 실행의 누적 USD1 한도에 최대 40회 호출·60분·입력 200만/출력 12만 토큰·transport 재시도 없음 제한을 적용한다. 기존 소진된 Dexory 예산은 초기화하지 않는다. 이 승인은 누락된 기업 사실이나 자격 판정을 대신하지 않는다.

따라서 전체 actual-v3는 계속 HOLD다. 이번 심사·사전 검증의 새 외부 요청·provider 호출·다운로드는 0이며, 실제 scored 실행·그 실행의 replay·최종 scored PDF는 아직 검증되지 않았다.

### 10월 7일 보존본으로 재개 (2026-10-09)

기준일을 유지한 대안 입력은 기존 `2026-10-07-initial` archive의 PI·Skild 자료다. 부모가 봉인 파일 71개와 claim 22개의 원문·추출문 hash, Unicode 인용 범위 hash, 10월 7일 획득 시각을 확인했고 오류는 0건이다. 원래 Source-only 소비자의 기준일 포함·이전 기준일 제외 테스트도 `2 passed in 1.36s`, exit 0으로 통과했다. 이는 PI 7개·Skild 12개 Source의 미심사 입력 소비이며 실제 평가 성공은 아니다.

독립 원문 심사 산출물은 `outputs/issue168-cutoff-original-review/report.json`(SHA-256 `cd4a8f68f51198496da0c7d0ed8f23236190d1f609fbd8155ccb65cb63b73af5`)과 `findings.md`(SHA-256 `a0c7fb06b1d2a62205ee01ce5ee5df2330d76f350af05433194aa80c6927e925`)다. 부모가 22개 claim의 누락 없는 수록과 입력 pin을 확인했다. 역사적 발행 진술의 제한된 지지와 실제 자격·등급 승인은 다르며 이 심사는 operational resolver 기록을 만들지 않았다.

PI의 Series C 날짜는 Caplight의 2026-03-28과 CB Insights의 2026-06-01이 충돌하고, 인수 rumor는 Exit 완료나 미완료 어느 쪽도 증명하지 않는다. Skild의 Zebra 거래는 Skild가 매수자이며 Skild 자체의 Exit가 아니다. 회사가 발표한 USD100M ARR와 약 10개월 배포 이후 누적 인식 USD50M은 별개로 보존하고 회계 기간이 정렬된 매출로 변환하지 않는다. 원문 심사의 짧은 anchor만으로 CapitalG의 투자주체를 부정하거나 S1 날짜를 완전히 미상으로 확정하지도 않는다. CapitalG의 전체 문장과 Skild blog index의 2026-08-18 날짜는 추가 맥락으로 보존하되 자격·등급을 대신하지 않는다.

원래 `check_eligibility` 계약은 `exit_completed=false`에도 같은 후보의 유효한 company Evidence를 요구한다. 검색 결과가 없거나 인수 rumor가 미확인이라는 이유로 false를 만들 수 없다. 보존된 성공 Source 19개의 전체 추출문을 추가로 확인했지만, 두 후보의 Exit 미완료를 승인할 명시 근거는 확보하지 못했다. navigation의 다른 회사 투자 단계나 일반 broker 문구도 후보 사실로 승격하지 않았다.

승인 모델 `gpt-4.1-mini-2025-04-14`에 기존 키로 `GET https://api.openai.com/v1/models/gpt-4.1-mini-2025-04-14`를 1회 요청해 HTTP200과 정확한 model ID 접근을 확인했다. 키는 기록하지 않았고 재시도·모델 추론은 0회다. 이 metadata 확인은 실제 Responses 생성 성공이나 runtime reserve/settle 성공의 증거가 아니다. 사전 검증 기록은 `outputs/issue168-actual-preflight-20261009/receipt.json`이며 공식 RunManifest가 아니다.

실행 예산·모델 접근은 확인됐지만 자격 필드·현재 단계·D05/D06 충분성의 정확한 근거와 snapshot 결속은 여전히 부족하다. 따라서 PI·Skild도 아직 적격 후보로 승격하지 않았고 유료 추론·scored replay·최종 PDF는 미실행이다.

같은 브랜치에 Python callable `preflight_sources`, `actual`, `replay`(`run_actual`, `run_replay` wrapper)가 구현됐다. 누적 USD1·40회·60분·토큰 한도와 campaign marker가 코드에 들어 있고 controlled 테스트로 연결을 확인했다. 부모가 socket을 차단하고 새 준비 함수를 실제 자산으로 실행해 Source 19개·PI PDF 2개·Chunk 36개·모델 파일 15개를 검증했다(exit 0). 두 후보의 반환 eligibility는 `unknown`이고, `outputs/parent-retained-source-preflight-20261009/receipt.json`은 `EXTERNAL_AUTHORITY_REQUIRED`, provider 0, 발행 금지다. 이는 실제 scored 실행이 아니다. 호출 형태와 운영자가 공급해야 하는 packet·권위는 [actual-v3 실행과 replay](actual-execution-v3.md)에 정리했다.

## 요구사항별 현재 상태

### 최종 연결 실행과 전달 범위 (2026-10-09)

사용자가 완료 범위를 “전체 그래프 연결 및 구현 완료 후 최종 1회 실행”으로 정했다. 전체 Graph 연결은 원래 5개 approved evaluator와 6개 차원의 원자적 합류, selector, Generator/Judge, PDF 경로를 유지한 구현 기준이다. 실제 기업의 scored 완료와 구분한다.

최종 연결 실행은 `uv run --offline --no-sync python examples/v3_connection_check.py outputs/final-graph-connection-20261009` 한 번이며 exit 0이었다. 같은 snapshot을 쓴 5개 branch와 6개 차원 승격, 보고서 completed, PDF 3페이지·SUMMARY 비율 0.1164를 확인했다. 응답은 명시적 통제 응답으로 mock 8회·index retrieval 1회이며 실제 provider·외부 요청은 0회다. ledger의 비용 값 8은 합성 예약 단위이지 청구액이나 실제 USD 사용량이 아니다. PDF SHA-256은 `06c8ea26083185eeb8f7c4ee698d1fda5f73cf10d8cf08a9c9b8147c03a4faa0`으로 이 세션에서 모든 페이지를 확인한 PDF와 같다. 발행·final은 false다.

동일 소스의 부모 전체 회귀는 5,702 passed·10 skipped, exit 0이었다. skip은 미제공 실제 review, opt-in live API, 일부 Git 제외 원문 자산 때문이며 actual 성공으로 세지 않는다. Ruff·389개 파일 format·격리 offline build는 통과했다. 새 production 연결 코드의 LSP 오류는 0이고 기존 controller의 타입 오류 51개는 별도 부채로 남는다.

실제 실행 입력은 로컬 `outputs/actual-execution-inputs-20261009/inputs.json`에 두 후보·회사 Source 19개·index Source 2개와 원문 심사 보고서 pin을 연결했다. 관찰·rating 승인 기록은 0이므로 실제 기업 평가가 가능해졌다고 주장하지 않는다. 실제 유료 scored 실행·그 실행의 replay·scored PDF는 별도 미완료 상태로 Draft PR에 명시한다.

R01~R12는 [팀 가이드의 필수 요구](../README.md#필수-요구사항과-검증-위치) ID다. 아래 경로는 현재 소스·기존 테스트 위치이며, 테스트 파일의 존재를 이번 실행 PASS로 해석하지 않는다.

아래 표는 기준 main의 검증 이력이다. `fixture-only`, live 거절, Market callable 부재는 그 기준의 제한이며, #168 작업 브랜치에서는 위 연결 갱신처럼 명시적인 admission과 실제 callable 연결을 구현했다. 실제 provider·기업 사실·긍정적 의미 심사·최종 발행은 여전히 증명되지 않았다.

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

기준 main의 fixture-only research/scoring/context 및 Finance guard에 대한 명시적 admission, 실제 callable·review resolver, 공유 AdapterRuntime reserve/settle·누적 ledger와 같은 run의 Generator/Judge/PDF·저장·replay 연결은 #168 작업 브랜치에서 통제 응답으로 검증했다. 실제 기업 원문에 대한 긍정적 의미 심사와 실제 provider 실행·최종 발행은 남아 있다. 사용자/controller에게 필요한 것은 구체 원문 근거의 심사 범위와 신뢰된 reviewer 권위, D05/D06 적용·충돌 처리의 run-scoped 판단, 실제 실행 시 authoritative 누적 budget/사용량과 제출 시 재배포 권한이다. 이미 승인된 Core/Finance 숫자·운영 산술을 다시 미승인으로 취급하지 않으며, 누락된 사실은 권한 승인만으로 채울 수 없다. `unknown`→`eligible`, missing→0점, 기술실패→투자 비추천, Warning→final 자동 승격은 금지한다.

## 팀원 재현 전제

Python 3.11+·uv와 source checkout/개발 의존성을 사용한다. 저장소 루트에서 기존 합성 offline 예제는 다음과 같다. #224에서는 재실행하지 않았다.

```sh
uv sync --frozen --offline
uv run --offline --frozen python examples/v3_offline_demo.py
```

의존성 캐시가 있어야 offline 설치가 된다. 캐시가 없는 최초 준비는 `uv sync --frozen`으로 설치하며 네트워크가 필요할 수 있다. 이 예제는 실제 기업 archive/PDF/index나 모델을 소비하지 않고 ReportLab 합성 데모를 만든다. [기존 Python 직접 호출](python-execution.md)과 [데모 산출물 확인법](offline-delivery.md)을 따른다. #168 작업 브랜치의 추가 연결 실행·replay entrypoint는 [연결 점검](connection-check.md)을 따른다.

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
