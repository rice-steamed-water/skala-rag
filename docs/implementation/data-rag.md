# 데이터 수집과 RAG 구현 가이드

[문서 홈](../README.md) · [공통 계약](contracts.md) · [작업 분담](delivery.md)

근거: 원문 §1.3, §5, §6, §7. 도구 목록은 원문 설계이고, 우선순위·adapter·검증 방식은 **구현 제안**이다. 이 문서는 API 계정 발급이나 접근 성공을 보장하지 않는다.

## 1. 첫 번째로 연결할 RAG 경로

```text
허용된 기술 백서 / 제품 문서
→ 페이지 보존 추출
→ 구조 기반 Chunk
→ 오픈소스 embedding
→ 검색 가능한 인덱스
→ Evidence Collector가 실제 retrieve 호출
→ 검색 Chunk로부터 Evidence 추출
→ Technology Evaluation의 기술 요약 / 판단
→ 보고서 문장에 Evidence / Source / 페이지 인용
```

이 경로를 과제 가이드의 **기술 요약 에이전트 RAG 적용**에 대응시킨다. 별도 Technology 이름을 쓰더라도 “검색된 기술 문서를 요약하고 장단점을 평가”하는 책임과 trace를 보여줘야 한다. vector DB를 만들거나 검색 API만 호출한 것으로 RAG 완료 처리하지 않는다.

최종 시연에서는 실제 자료 검색 결과가 실제 평가 입력과 보고서 근거에 사용되어야 한다. 해당 기업에 맞는 문서가 없으면 다른 기업 문서를 재활용하지 말고 missing으로 처리한다.

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

## 3. 코퍼스 manifest 관리

전체 프로젝트의 승인된 RAG 문서 집합을 `corpus_manifest`로 관리한다. 이 프로젝트는 원문 §1.3의 코퍼스 200페이지 한도(R05)를 적용하지 않는다([D13 기록](decisions.md#d13--200페이지-산정-규칙-적용-제외-91)). 페이지 수 산정·합계 검사는 하지 않는다.

| Manifest 필드 | 의미 |
| --- | --- |
| corpus_version, document_id | 승인된 코퍼스와 문서의 식별자 |
| source_id, origin_url/local_path, content_hash | 원문 snapshot 및 무결성 |
| title, publisher, publication_date, language | 출처와 시점 |
| permission_note | 팀이 해당 자료를 사용하는 근거/제한 |
| candidate_ids, scope | 회사 귀속 또는 산업 공통 |
| extraction_status, reviewer, approved | 추출 품질과 포함 승인 |

- 미승인·추출 미완료 문서는 인덱싱을 거절한다.
- 재조사에서 찾은 새 문서를 RAG에 추가할 때는 실행 중 코퍼스 자동 확장 대신 승인된 새 corpus_version을 만든다.
- 교체한 이전 문서는 active index에서 검색되지 않게 한다. 실행 manifest는 당시 사용 코퍼스를 고정한다.
- RAG에 넣지 않은 live Web/API Evidence는 수집 이력으로 분리한다.
- 원문 IR/PDF를 Git에 올리는 것은 별도 문제다. 공개 저장소에는 허용된 fixture·manifest만 넣고 재배포 가능성을 확인한다.

## 4. 추출·chunk·검색

| 자료 | 기본 분할 | 보존할 정보 |
| --- | --- | --- |
| Pitch Deck | 슬라이드 | 슬라이드 번호, 제목, 표/그림 설명 |
| IR/PDF | section/page | 페이지, 기업명, 표 제목·단위·주석 |
| 기술 백서/논문 | heading/section | 방법·실험 조건·결과 구분 |
| 특허 | 청구항/발명 설명 | 공개번호, 출원인, 권리 상태, 정확한 용어 |
| 시장 보고서 | section/table 주변 | 시장 정의, 지역, 연도, 통화, 전망 기간 |
| 홈페이지 | heading | URL, 수집일, snapshot locator |
| 뉴스 | 기사 또는 원문 제안의 500~800 token | 발행일, 사건일, 원출처 |

Chunk 크기·overlap·top_k는 실험 설정으로 기록한다. 원문에 없는 모든 자료를 일률적으로 500~800 token으로 자르지 않는다. 표의 제목과 행/열 정의를 분리하지 않는다. OCR·이미지 추출 실패는 확인 후 표기하고, 읽지 못한 그래프의 수치를 모델이 채우게 하지 않는다.

**검색 순서 제안**

1. collector는 query·candidate_id·corpus/index_version·as_of·top_k·allowed_source_ids를 담은 `RetrievalRequest`를 만든다([공통 계약](contracts.md)). 승인된 manifest에서 대상 기업과 관련 industry 출처를 선택하고, 검색 adapter는 그 허용 목록과 실행 기준일을 함께 적용한다. 같은 질의라도 as_of가 다르면 별도 검색/cache 항목이다.
2. 같은 embedding 모델·revision·차원으로 query와 document를 표현한다.
3. dense 검색 결과를 반환하고 source/chunk/page metadata와 RetrievalRecord를 보존한다. Web→RAG 재발견은 [EvidenceProvenance 병합 계약](contracts.md)으로 추적하며 근거의 내용과 수집 경로를 분리한다.
4. 특허를 실제 범위에 넣는 단계에서는 원문 요구대로 keyword/sparse 검색과 merge·reranking을 추가한다. dense-only 상태를 특허 hybrid 구현 완료로 표현하지 않는다.
5. 검색 결과의 숫자는 LLM 구조화 추출 + 코드 검증으로 Evidence에 옮긴다. similarity 값은 CAGR·수익률·신뢰도 점수가 아니다.

재인덱싱할 때 모델 revision, tokenizer, chunk 설정, 정규화 설정이 바뀌면 새 index_version을 만든다. 서로 다른 embedding 공간을 같은 collection에 섞지 않는다. vector store 제품과 배포 방식은 D07에서 정한다.

## 5. Embedding 후보와 선택 절차

### 확인한 사실과 선택 제안

2026-09-29의 제공자 원문 확인 기준이다. 아래는 **벤치마크 결과가 아니다.**

| 후보 | 확인 내용 / 근거 | 이번 프로젝트에서의 처리 |
| --- | --- | --- |
| `BAAI/bge-m3` | 제공자 모델 카드에 MIT, multilingual, dense/sparse/ColBERT 계열 표현 지원 표기 [EM1] | 한/영 기술 문서 baseline 실험 우선 후보. 성능·메모리·지연 측정 후 최종 승인 |
| `jinaai/jina-embeddings-v4` | 모델 카드는 multimodal/multilingual을 설명. 확인한 revision의 LICENSE에는 Qwen Research License와 비상업적 사용 제한이 기재됨 [EM2, EM3] | 원문의 시각 문서 후보로 보존하되, 공개 weights만으로 오픈소스 필수 조건 충족을 선언하지 않음. 과제 적합성과 이용조건 확인 전 채택 보류 |
| 원문 OpenAI embedding 후보 | 원문 §6.3에서 비교/참고 모델로만 남김 | 과제의 오픈소스 최종 선택을 대체하지 않음; 유료 비교 실험 필수 아님 |

확인한 모델 revision:

```text
BAAI/bge-m3: 5617a9f61b028005a4858fdac845db406aefb181
jinaai/jina-embeddings-v4: 853c867b65b749f3c3c72a06868140d842e04f06
```

위 revision은 문서 확인 snapshot이지 프로젝트 lockfile이나 모델 다운로드 완료 증거가 아니다. 비교 후보가 이용조건 때문에 제외되면 그 이유를 기록한다. 실행 가능한 대안이 필요하면 별도 오픈소스 모델을 추가 검토하되 출처·라이선스·비교 조건을 같은 표에 남긴다.

### 비교 실험 — RAG 담당 산출물

- 같은 corpus와 사람이 정답 Chunk를 지정한 질의 집합 사용.
- 한국어 질의→영문 문서, 한국어 기술용어, 시장 숫자·표, 동명이인/다른 기업 오검색, exact identifier를 포함.
- tuning용 질의와 최종 확인용 질의를 분리한다.
- **Hit Rate@K:** 상위 K개에 정답 Chunk가 하나라도 있는 질의 비율.
- **MRR@K:** 첫 정답 rank의 역수 평균; K 안에 정답이 없으면 0.
- retrieval latency, indexing time, peak memory, 실제 비용도 같은 하드웨어·설정에서 측정한다.
- 출처/페이지 복원 가능 여부와 기술 평가에 실제로 쓰인 근거를 확인한다.
- 임계값은 D07에서 사전에 정한다. 이 문서에는 측정 결과나 통과 수치를 기입하지 않는다.

실험 기록 양식:

```text
모델 / revision / 라이선스 확인:
코퍼스 / 질의셋 version:
실행환경 / library lock / embedding·chunk·retrieval 설정:
Hit Rate@K / MRR@K / latency / memory / cost: [실측 후 입력]
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
