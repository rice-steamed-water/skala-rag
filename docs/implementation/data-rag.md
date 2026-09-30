# 데이터 수집과 RAG 구현 가이드

[문서 홈](../README.md) · [공통 계약](contracts.md) · [작업 분담](delivery.md)

근거: [v3](../design/design-v3.html) B-1–B-3 및 이전 통합 원문 §1.3·§5–§7. Evidence Research 책임·문서 우선순위·BGE-M3 1차 선택과 비교 계획은 **v3 목표**, adapter·페이지 산정·실험 설정의 보완은 **구현 제안**이다. 실제 API 접근·모델 다운로드·라이선스 적합성·성능을 확인 완료로 주장하지 않는다.

**현재 구현 방향 — v3 전환 승인:** [사용자 전환 승인 #35 comment 5902877317](https://github.com/rice-steamed-water/skala-rag/issues/35#issuecomment-5902877317)(luk0715, 2026-09-30T02:29:07Z)에 따라 새 작업은 기존 baseline의 계속 구현이 아니라 v3에 정합화한다. baseline 코드·승인 기록은 호환성과 이력으로 보존하며 새 구현의 우선 방향이 아니다. 방향 승인은 상세 정책·DTO 전체 필드·provider·corpus·시간/비용 예산 승인이나 구현 완료가 아니다. 남은 세부 선택만 [결정 목록](decisions.md)의 OPEN gate를 따른다.

## 1. 첫 번째로 연결할 RAG 경로

```text
허용된 기술 백서 / 제품 문서
→ 페이지 보존 추출
→ 구조 기반 Chunk
→ 오픈소스 embedding
→ 검색 가능한 인덱스
→ Evidence Research가 실제 retrieve 호출
→ 검색 Chunk로부터 Evidence 추출
→ Technology Evaluation의 기술 요약 / 판단
→ 보고서 문장에 Evidence / Source / 페이지 인용
```

v3 B-2의 **Primary RAG Agent는 Evidence Research**다. 같은 Agent가 최초 수집과 Coverage의 부족 Evidence 재조사를 RAG/Web/API로 수행한다. 평가 branch와 Reporter는 검색하지 않고 고정 snapshot/context만 사용한다. 위 검색→Evidence→Technology 기술 요약/장단점 평가→인용 trace를 통해 이전 과제 가이드의 기술 요약 RAG 요구도 추적한다. vector DB 구축이나 검색 API 호출만으로 RAG 완료 처리하지 않는다.

최종 시연에서는 실제 자료 검색 결과가 실제 평가 입력과 보고서 근거에 사용되어야 한다. 해당 기업에 맞는 문서가 없으면 다른 기업 문서를 재활용하지 말고 missing으로 처리한다.

### 문서 선정 우선순위 — v3 B-2

| 순위 | 문서 | 선정 기준 |
| --- | --- | --- |
| 1 | 기업 공식 기술문서·IR/Pitch Deck·특허·공식 제품 자료 | 대상 기업과 직접 관련되고 평가 항목을 뒷받침하는 Primary Evidence |
| 2 | 논문·정부/공공기관 자료·산업/시장 보고서 | 발행 주체·시점 확인, 기술 검증·시장 규모/성장/경쟁 맥락 |
| 3 | 언론 기사·공식 인터뷰 | 다른 근거가 부족할 때 투자 이력·사업화·고객/파트너·최근 동향 보완 |

우선순위가 권한·품질 검증을 면제하지 않는다. 공식 IR의 자기주장을 독립 검증으로 바꾸지 않고 기업/산업 scope, 기준일, 최소 발췌와 출처를 보존한다.

## 2. 자료 수집 adapter

| 원문의 소스 | 목적 | 구현 순서 제안 | 미가용 시 처리 |
| --- | --- | --- | --- |
| 회사 공식자료 + 뉴스 검색 | 기업·제품·투자라운드 발견/확인 | 우선 연결; 검색 엔진은 팀 선택 | 수동 승인 자료 fixture와 live 결과를 분리 |
| Naver News Search API | 국내 후보/투자/고객 소식 | 국내 검색 adapter 우선 후보 | key/조회 실패를 unavailable로 기록 |
| KRX Open API | 국내 상장 확인 | Eligibility 보강 | 검색되지 않았다는 이유만으로 비상장 확정 금지 |
| OpenDART API | 공시·기업·재무 근거 | 재무 adapter | 해당 기업/공시 없음과 API 실패 구별 |
| 중기부 벤처기업 API | 기업 정보 보강 | 선택 보강 | 벤처 확인이 과제의 모든 적격조건을 대신하지 않음 |
| OpenAlex API | 연구자·논문 | 기술·창업자 보강 | 동명이인·소속·논문 저자 매칭 실패 표시 |
| KIPRISPlus API | 특허 | Moat 확장 단계 | 공개 문서 근거로 대체하거나 missing |
| KOSIS API | 시장·산업 통계 | 시장 보강 | 시장 정의/지역/연도 불일치면 사용하지 않음 |
| Crunchbase | 기업·투자 정보 보강 | 선택, MVP 필수 아님 | 유료 계정 없다고 전체 프로젝트를 차단하지 않음 |

원문에는 Tavily·Semantic Scholar 등의 참고 언급도 있다. 최종 provider 선택은 실제 계정·호출 가능성·비용·지원 국가를 확인한 뒤 기록한다. 이 가이드는 특정 API가 현재 무료인지, 특정 endpoint가 동작하는지 검증하지 않았다.

모든 adapter는 [ToolResult 계약](contracts.md)을 따르고, timeout·예산·오류·retrieval record를 공통으로 남긴다. **페이지/도구 응답에서 받은 텍스트를 실행 명령으로 취급하지 않는다.**

## 3. 200페이지 코퍼스 관리 — 과제 필수, 산정은 D13

전체 프로젝트의 승인된 RAG 문서 집합을 `corpus_manifest`로 관리한다. 기업별·Agent별로 각각 200페이지가 아니다. 페이지 구간 제외로 제한을 우회하지 않도록 원본과 실제 사용 구간을 모두 기록한다.

| Manifest 필드 | 의미 |
| --- | --- |
| corpus_version, document_id | 승인된 코퍼스와 문서의 식별자 |
| source_id, origin_url/local_path, content_hash | 원문 snapshot 및 무결성 |
| title, publisher, publication_date, language | 출처와 시점 |
| original_page_count, included_page_ranges, counted_pages | 전체와 사용 구간, 실제 예산 반영량 |
| permission_note | 팀이 해당 자료를 사용하는 근거/제한 |
| candidate_ids, scope | 회사 귀속 또는 산업 공통 |
| extraction_status, reviewer, approved | 추출 품질과 포함 승인 |

**제안 산정:** PDF는 문서 페이지, Pitch Deck은 슬라이드, HTML/Markdown은 고정 템플릿 PDF snapshot의 실제 페이지를 센다. 페이지 없는 원문을 임의로 1페이지로 계산하지 않는다. 일부 페이지만 쓰는 경우 해당 구간을 추출한 산출물과 provenance를 남기고 과제상 산정 허용 여부를 확인한다.

- 중복 파일/반복 embedding이 원문 페이지 수를 늘리지는 않는다. content hash와 원본 페이지 ID로 중복을 식별한다.
- 문서 추가 전에 전체 합이 200 이하인지 검사한다. 초과/미상/미승인 문서는 인덱싱을 거절한다.
- 재조사에서 찾은 새 문서를 RAG에 추가해도 같은 한도를 적용한다. 실행 중 코퍼스 자동 확장 대신 승인된 새 corpus_version을 만든다.
- 교체한 이전 문서는 active index에서 검색되지 않게 한다. 실행 manifest는 당시 사용 코퍼스를 고정한다.
- RAG에 넣지 않은 live Web/API Evidence는 수집 이력으로 분리한다. 이를 대량 문서 RAG 제한을 우회하는 숨은 코퍼스로 사용하지 않는다.
- 원문 IR/PDF를 Git에 올리는 것은 별도 문제다. 공개 저장소에는 허용된 fixture·manifest만 넣고 재배포 가능성을 확인한다.

## 4. 추출·chunk·검색

| 자료 | 기본 분할 | 보존할 정보 |
| --- | --- | --- |
| Pitch Deck / IR | 슬라이드(v3); 비슬라이드 IR은 section/page 보완 제안 | 기업, slide_no, topic, 페이지, 표 단위·주석 |
| 기술 백서·제품 문서 | heading/section | 기업·제품·section·source_date, 연결 구조 |
| 논문 | Abstract / Method / Experiment / Result / Conclusion section | 기업, 제목, section, year, 실험 조건·결과 |
| 특허 | 발명 설명/청구항 | 기업, patent_no, IPC, claim_no, 출원인·권리 상태 |
| 시장 보고서 | section/table 주변 | 시장 정의, 지역, 연도, 통화, 전망 기간 |
| 홈페이지 | heading | URL, 수집일, snapshot locator |
| 뉴스 | 기사 또는 원문 제안의 500~800 token | 발행일, 사건일, 원출처 |

Chunk 크기·overlap·top_k는 실험 설정으로 기록한다. 원문에 없는 모든 자료를 일률적으로 500~800 token으로 자르지 않는다. 표의 제목과 행/열 정의를 분리하지 않는다. OCR·이미지 추출 실패는 확인 후 표기하고, 읽지 못한 그래프의 수치를 모델이 채우게 하지 않는다.

**검색 순서 제안**

1. collector는 현재 `RetrievalRequest`의 query·candidate_id·corpus/index_version·as_of·top_k·allowed_source_ids를 만든다([공통 계약](contracts.md)). 승인된 manifest에서 대상 기업과 관련 industry 출처를 선택하고, 검색 adapter는 그 허용 목록과 실행 기준일을 함께 적용한다. 같은 질의라도 as_of가 다르면 별도 검색/cache 항목이다. v3의 doc_type/year metadata filter는 아직 DTO에 없다: Source의 versioned bibliographic metadata에서 유도한 document class/year와 request filter 확장 proposal을 schema로 승인한 뒤 cache key·returned result 검증에 함께 넣는다. class unknown은 우선순위 승격에 쓰지 않으며, 1→2→3은 hard filter가 아닌 명시적 fallback 정책이다.
2. 같은 embedding 모델·revision·차원으로 query와 document를 표현한다.
3. dense 검색 결과를 반환하고 source/chunk/page metadata와 RetrievalRecord를 보존한다. Web→RAG 재발견은 [EvidenceProvenance 병합 계약](contracts.md)으로 추적하며 근거의 내용과 수집 경로를 분리한다.
4. MVP는 dense 중심이다(v3 B-3). 특허번호·IPC·모델명 등 exact match가 필요한 경우 keyword/sparse를 **필요 시 확장**하며 특허를 포함한다는 이유만으로 hybrid·reranker를 필수화하지 않는다. 적용 여부·merge·reranking 설정과 실측을 기록하고 dense-only를 hybrid 완료로 표시하지 않는다.
5. 검색 결과의 숫자는 LLM 구조화 추출 + 코드 검증으로 Evidence에 옮긴다. similarity 값은 CAGR·수익률·신뢰도 점수가 아니다.

재인덱싱할 때 모델 revision, tokenizer, chunk 설정, 정규화 설정이 바뀌면 새 index_version을 만든다. 문서별 manifest/Chunk에는 document class·year와 자료 유형별 slide/patent metadata를 보존할 확장 shape를 별도 승인하며, 현재 `Source`/`Chunk` DTO에 그 필드가 이미 있다고 가정하지 않는다. 서로 다른 embedding 공간을 같은 collection에 섞지 않는다. vector store 제품과 배포 방식은 D07에서 정한다.

## 5. Embedding 후보와 선택 절차

### v3의 1차 선택과 최종 선택을 구별한다

**1차 선택은 `BAAI/bge-m3`**이며 D07의 최종 모델 확정은 동일 평가셋 비교 뒤다. 아래 검증 포인트는 제공된 v3 B-3의 실험 계획이지 라이선스/접근/성능 실측 결과가 아니다. 다운로드할 revision의 모델 카드·이용조건·실행환경 적합성은 도입 시 별도 확인한다.

| 후보 | v3가 요구하는 프로젝트 검증 포인트 | 현재 판단 상태 |
| --- | --- | --- |
| `BAAI/bge-m3` | 한국어→영문 기술문서, 긴 문서·기술/특허 용어, dense 중심 운영 | 1차 선택; 최종 성능·비용·운영 비교 미실행 |
| `intfloat/multilingual-e5-large` | 짧은 Chunk와 한/영 cross-lingual 검색 | 비교 후보; 접근·revision·라이선스·실행 미검증 |
| `nlpai-lab/KURE-v1` | 한국어 질의 정확도와 한국어→영문 문서 검색 | 비교 후보; 접근·revision·라이선스·실행 미검증 |

이전 원문 §6.3의 Jina/OpenAI는 **과거 참고 후보**이며 현재 비교 baseline을 대체하거나 추가 유료 실험을 요구하지 않는다. 과거 가이드의 pinned 모델 카드 참고 [EM1–EM3]도 실제 다운로드·라이선스 승인·측정 기록이 아니다. 후보가 권한·이용조건·장비 때문에 실행 불가하면 실패/미실행 이유를 기록하고 대체 실험 계획을 승인받는다. 결과를 임의로 채우지 않는다.

### 비교 실험 — RAG 담당 산출물

- 세 후보에 **동일한 문서 Chunk와 동일한 평가 Query**를 사용한다. corpus/chunk/query version·정답 Chunk ID를 고정하고 모델별 필수 입력 포맷만 기록한다. 모델마다 유리하게 재청킹하지 않는다.
- 한국어 질의→영문 문서, 한국어 기술용어, 시장 숫자·표, 동명이인/다른 기업 오검색, exact identifier를 포함.
- tuning용 질의와 최종 확인용 질의를 분리한다.
- **Hit Rate@1, Hit Rate@3, Hit Rate@5:** 각 상위 K개에 정답 Chunk가 하나라도 있는 질의 비율을 각각 보고한다.
- **MRR:** 첫 정답 rank 역수의 전체 질의 평균. 검색 깊이/cutoff를 D07 실험 설정에 명시하고 해당 범위에 정답이 없으면 0으로 계산하는 안이다. cutoff가 있으면 `MRR@depth`로 함께 표시하여 전체 순위 MRR로 오해하지 않게 한다.
- **Cross-lingual:** 한국어 질의→영문 논문/기술문서 subset을 별도 구성하고 같은 지표를 분리 보고한다. 전체 평균으로 교차언어 실패를 숨기지 않는다.
- retrieval latency, indexing time, peak memory, 실제 비용도 같은 하드웨어·설정에서 측정한다.
- **입력 길이/절단:** 모델별 최대 입력 길이와 실제 chunk token 분포·truncation 발생/방식을 측정해 기록한다.
- **통합·운영 복잡도:** 현재 pipeline과의 입력 포맷·의존성·index migration·배포/관측 부담을 정성 기준으로 비교한다. 이는 성능 수치가 아니며 실측 결과 없이 우열을 선언하지 않는다.
- 출처/페이지 복원 가능 여부와 기술 평가에 실제로 쓰인 근거를 확인한다.
- 임계값은 D07에서 사전에 정한다. 이 문서에는 측정 결과나 통과 수치를 기입하지 않는다.

실험 기록 양식:

```text
모델 / revision / 라이선스 확인:
코퍼스 / 질의셋 version:
실행환경 / library lock / embedding·chunk·retrieval 설정:
입력 길이 / chunk token 분포 / truncation 발생·방식: [실측 후 입력]
통합·운영 복잡도(입력 포맷·의존성·migration·관측): [확인 후 입력]
Hit Rate@1 / Hit Rate@3 / Hit Rate@5 / MRR(검색 depth·cutoff 포함): [실측 후 입력]
한국어→영문 subset 지표 / latency / indexing time / memory / cost: [실측 후 입력]
실패 사례 / 기술 평가에 사용된 evidence_ids:
최종 선택 / 제외 이유 / 승인자:
```

## 6. 데이터 품질과 보안 게이트

- `RetrievalRequest.as_of` 이후 발행·공개된 source snapshot은 평가 근거에서 제외한다. 날짜 미상 자료는 해당 시점 이전에 확보한 불변 snapshot 등 이용 가능성을 확인할 수 없으면 historical 검색에서 제외한다. 최신 편집본을 과거 발행일만 보고 통과시키지 않는다. 기준일 이전 공개된 전망은 미래 기간을 예측한다는 사실을 명시하고, 기준일 이후의 실제 사건·성과를 과거에 완료된 사실로 사용하지 않는다. live Web/API Evidence에도 같은 기준일 정책을 적용한다.
- 회사명·논문 저자·특허 권리자 식별이 모호하면 자동 귀속하지 않는다.
- 상충하는 최신/과거 정보는 둘 다 보존하고 사건일·문서 신뢰성에 따라 해소 이유를 남긴다.
- API key는 환경 또는 secret store에 둔다. `.env.example`에는 이름과 설명만, 테스트에는 가짜 값만 넣는다.
- 외부 fetch는 허용 프로토콜과 대상 검증, private/loopback/link-local 주소 차단, redirect 재검증, 크기·시간 제한을 구현한다. 로컬 파일은 승인된 corpus 경로 안에서만 읽는다.
- 비공개 IR을 외부 LLM에 보내는 것은 자료 제공 권한과 provider 정책을 별도 확인한 뒤 허용한다.

## 공식 기술 참고

```text
[EM1] BAAI — bge-m3 model card, pinned revision
https://huggingface.co/BAAI/bge-m3/blob/5617a9f61b028005a4858fdac845db406aefb181/README.md
[EM2] Jina AI — jina-embeddings-v4 model card, pinned revision
https://huggingface.co/jinaai/jina-embeddings-v4/blob/853c867b65b749f3c3c72a06868140d842e04f06/README.md
[EM3] Jina AI model repository — LICENSE, pinned revision
https://huggingface.co/jinaai/jina-embeddings-v4/blob/853c867b65b749f3c3c72a06868140d842e04f06/LICENSE
```
