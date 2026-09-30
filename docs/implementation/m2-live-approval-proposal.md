# M2 live 사전 승인 기록·잔여 요청안 — #43

[문서 홈](../README.md) · [결정 목록](decisions.md) · [데이터와 RAG](data-rag.md) · [공통 계약](contracts.md)

> 작성·공식 자료 조회일: 2026-09-30 (Asia/Seoul). **A·B APPROVED; C 3종 비교 실험은 진행하지 않음; embedding 모델은 BGE-M3로 사용자 선정.** [#43 최초 승인 기록](https://github.com/rice-steamed-water/skala-rag/issues/43#issuecomment-5903724740)의 당시 C 보류를 보존한다. 이후 사용자가 C 실험은 수행하지 않고 BGE-M3를 선택한다고 명확히 했다([오해 정정](https://github.com/rice-steamed-water/skala-rag/issues/43#issuecomment-5904136945)). 기록 담당: luk0715, 2026-09-30 (Asia/Seoul; 응답의 정확한 발생 시각을 주장하지 않음). 실행 설정/credential/readiness 또는 실측 증거가 아니다. 실제 provider API 호출, 모델 다운로드, index 생성, 비교 실험은 실행하지 않았다.

## 1. 승인 경계와 관측

- #43은 D07 모델·실험 경계, D08 미정 live 예산, D12 국가·언어를 기록한다. 사용자가 **BGE-M3를 직접 선정하고 3종 비교 실험은 수행하지 않기로 했다.** 과거의 실험 후 최종 선정 제안은 이 사용자 결정으로 대체한다. 모델 revision·라이선스·실제 동작·품질은 아직 검증되지 않았다.
- [#35 v3 전환 승인](https://github.com/rice-steamed-water/skala-rag/issues/35#issuecomment-5902877317)은 정합화 방향 승인이다. provider·모델·코퍼스·비용·시간·benchmark 설정 승인을 포함하지 않는다. 현재 worktree의 baseline 문서는 v3 전체 반영 완료가 아니므로 병합된 정책·계약과 실행 전에 다시 대조한다.
- #13(코퍼스 후보/readiness)과 #35(v3 문서 정합화)는 병합·종료되었다. #13 후보는 아직 승인된 실제 corpus가 아니다. 이 문서는 corpus_manifest 필드·등록규칙·승인권을 새로 정하지 않는다. #91의 D13 페이지 제한 적용 제외 기록에는 과제 담당자 확인 근거가 미기록이므로 과제 요건 충족 주장과 별개로 다룬다.
- #8 주입 경계와 #73 versioned 구조 DTO를 재정의하지 않는다. #73은 runtime/정책 실행 구현이 아니다. #82에 별도 명시 승인된 v3 운영 규칙도 live provider·비용·시간·corpus 승인을 포함하지 않는다. 아래 표는 후속 runtime의 **승인값과 미승인 제안**을 구분하며 새로운 DTO/API 계약이 아니다. transport 재시도는 #45, schema 구조 보정은 #22 wrapper 소유다.
- 공식 문서의 기능 설명은 관측했으나 계정 접근 성공은 관측하지 않았다. 실제 `.env`·credential 파일·secret store를 읽지 않았으며 **credential 존재 boolean도 미확인**이다. key/model/index readiness, 계정별 quota, 실제 사용량·과금·성능은 모두 미확인이다. mock/fixture 성공을 live 성공으로 표시하지 않는다.
- Hermes의 OpenAI Codex 로그인과 앱의 OpenAI API 인증·billing은 별개다. 이 세션의 모델/provider를 앱 기본값으로 전용하지 않는다. 비밀값은 문서·이슈·로그에 기록하지 않는다.

## 2. 경로·지원 범위 (A 승인, 모델 BGE-M3 선정)

**승인된 A:** 첫 M2 연결은 한국(KR)·미국(US), 한국어(ko)·영어(en) 자료/질의로 제한한 *pilot*이다. 국내외 최종 과제 범위를 국내만으로 축소하는 승인이 아니다. 다른 국가·언어는 `unsupported` 사유를 남기고 자동 번역/자동 확대하지 않는다. 검색 엔진의 국가 옵션은 법인 소재국 판정이 아니다. 후보의 법인 식별·Seed~C·비상장·Exit 여부는 별도 직접 근거를 요구하며 0건으로 확정하지 않는다.

| 경로 | 우선 승인 요청안 / 대안 | pilot의 required/optional | 접근 조건·환경변수 이름 제안 | 미준비·자료 부재 처리 |
| --- | --- | --- | --- | --- |
| Web discovery + 회사 공식 공개자료 | Tavily Search `general`, `basic`; 공식 회사 원문 확인. 대안은 Naver Web 별도 검토 또는 승인 수동 자료 smoke이며 live discovery 대체 완료 아님 | live discovery에서 Tavily required; 원문 provenance 확인 required | `TAVILY_API_KEY`; 계약·계정별 사용량 확인 필요 [S4] | key/접근 미준비면 live discovery 시작 거절. 승인 수동 자료는 manual/fixture 경로로 분리 |
| 국내 뉴스 | Naver News Search; 해외 뉴스는 Tavily `news` 후보 | Naver optional; 별도 뉴스 smoke를 선택하면 해당 provider required | `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` [S5] | optional unavailable 기록 후 계속; 국내뉴스/해외뉴스 미지원 표시. 기사 URL/일자와 원문 확인, 검색 snippet만으로 재무 사실 확정 금지 |
| KR 적격성·기업정보 | 공식 회사 자료 + KRX 상장 확인, OpenDART 기업개황/공시 | 공식 출처 확인 경로 required; KRX/OpenDART adapter optional | `KRX_API_KEY`(앱 이름 제안), `OPENDART_API_KEY`(앱 이름 제안); 인증키/서비스 이용 승인 필요 [S6,S7] | 조회 실패와 정상 0건 분리. 미조회/동명/식별 실패는 unknown; 비상장·Exit 미완료를 자동 확정하지 않음 |
| KR 재무 | OpenDART 공시와 승인된 공개 IR 원문 | optional, 직접 근거가 없는 criterion은 missing | 공시대상 고유번호 매칭 required; 비공개 IR 외부 전송 제외 [S6] | 공시 없음을 매출 0으로 변환하지 않음. 단위·기간·회사·기준일 확인 |
| US 적격성·재무 보강 | 회사 공식 공개자료 + SEC EDGAR submissions/companyfacts | SEC optional; 미국 스타트업 전체 포괄 경로로 주장하지 않음 | `SEC_USER_AGENT`(연락처 포함 정책용 앱 이름 제안); SEC 접근 정책 준수 [S8] | SEC 미등록/0건은 비상장·Exit 없음·재무 0의 증거 아님. private-company 자료 부족은 unknown/missing |
| structured-output LLM | OpenAI API `gpt-4.1-mini-2025-04-14` 고정 snapshot, JSON Schema structured output | 추출/평가 LLM smoke에서 required | `OPENAI_API_KEY`, `SKALA_LLM_MODEL`(앱 이름 제안). 실제 project billing/quota 미확인 [S9,S10] | key/model/schema 지원 미준비면 smoke 시작 거절; refusal/전송/구조 실패를 missing·빈 성공·0점으로 바꾸지 않음 |
| LLM 대안 | Anthropic Claude API structured outputs; **model ID는 OPEN, 대안 선택 시 추가 고정 필요** | 현재 안에서는 미연결 optional | `ANTHROPIC_API_KEY`(대안 이름 제안) [S11] | 자동 fallback 금지. 별도 모델·요금·지역/데이터 정책·schema 호환 승인 후만 사용 |
| 기술 RAG | 승인 corpus + 사용자가 선정한 BGE-M3 dense index/검색 | 기술 RAG smoke에서 corpus/model/index required | `SKALA_EMBEDDING_MODEL`, `SKALA_EMBEDDING_REVISION`, `SKALA_INDEX_PATH`(모두 이름 제안) | 파일/모델/index 부재나 corpus 승인·버전/차원 불일치는 시작 거절. 다른 기업 문서 재사용 금지 |

환경변수는 **이 문서의 이름 제안**일 뿐 기존 `.env.example`·코드에 존재하거나 설정되어 있다는 뜻이 아니다. 설정 파일은 이 작업에서 수정하지 않는다. 모든 API 동시 연결을 요구하지 않는다. Tavily/Naver/LLM/공공 API 각각 선택된 smoke에 필요한 도구만 required로 판정한다.

공식 자료는 endpoint/인증 형식과 기능 지원을 확인하는 근거다. 한국·미국 검색 품질, 한국어 JSON 추출 품질, 모든 스타트업 재무 확보, 계정의 사용 가능성은 아직 검증되지 않았다. Tavily의 국가 옵션을 법인 소재국이나 뉴스 국가 판정의 근거로 쓰지 않는 안이다. 정확한 옵션 적용범위는 실행 전 [S4]에서 확인한다.

## 3. 호출·시간·비용 승인 요청안

### #62 후속 실행 승인 (2026-09-30)

사용자에게 “LLM timeout 30초·transport 재시도 0회”로 실행할 것을 제안했고,
사용자가 “승인합니다.”라고 응답했다. #62 M2 smoke의 LLM 시도 timeout은
30초, 추가 transport 재시도는 0회로 승인되었다. 아래 최초 제안의
LLM timeout OPEN 및 retry pending은 이 범위에서만 해소된다.
다른 실행의 retry/backoff 운용이나 M3 실행으로 확대하지 않는다.
기존 model/token/공유 호출·시간·비용 한도와 schema 보정 회계는 유지한다.
실제 조사 State, 계정 요금·잔여 credit 및 required readiness의 확인은 별도다.

#### #62 API 키 후속 제공·개발 지속 지시

사용자는 “나중에 API 키를 넣어줄테니, 그냥 나만 믿고 진행해.”라고 지시했다.
계정 잔액 정보를 추가 개발 착수 조건으로 재요청하지 않고, API 호출 없는 구현·
가상 응답 통합 검증·로컬 사전 점검을 계속한다. 현재 실행에서 실제 LLM 호출은 하지 않는다.
후속 component CLI는 키 제공 후 사용자가 명시적으로 `--live`를 선택해 실행한다.
계정 잔액 증빙 파일을 별도 입력으로 요구하지 않으며, 잔액 검증 여부는 false로 기록한다.
공개 요금으로 계산한 요청 상한과 기존 8회/8,000·2,000 token/10분/USD 1/run·
USD 3/campaign 범위를 유지한다. 실제 비용은 확인 전까지 None이다.
이 후속 지시로 키 미제공 상태의 호출, 신규 구독·credit 구매, M3 전체 실행을 허용하지 않는다.

아래 **approved 열에 pending인 값은 기본값으로 적용할 수 없다.** D08 기존 제한은 현재 worktree [승인 기록](decisions.md)의 값이며, #35 정합화 후 변경 여부를 재확인한다. M2 adapter smoke/검색 실험 예산이며 M3 전체 runner·보고서/Judge 허용을 뜻하지 않는다.

| 항목 | 승인 요청안(proposed) | 승인된 값(approved) / 범위 |
| --- | --- | --- |
| 기존 후보·추가조사·보고서 수정 | 이 문서에서 변경하지 않음 | baseline D08의 후보 5 기록은 보존. v3 추가조사/보고서 수정은 #82의 명시 승인 규칙(최초 제외 각각 2회, 소모·종료 의미 포함)을 소비하며 live 실행 허가는 별도 |
| 도구 batch / transport | 기존 승인 범위 이하 | baseline batch당 8회(재시도 포함), 추가 재시도 최대 2회, 시도별 timeout 30초(D08) |
| M2 외부 smoke 전체 | 실행 한 번 10분, 외부 요청 총 20회, 외부 동시성 1 | APPROVED (B), readiness 충족 후 M2 smoke만 |
| smoke provider별 최대 실제 요청 | Tavily 6, Naver 2, 공공 경로 합계 4, LLM 8; 모두 전체 20회 안에서 공유. optional 생략분 자동 증액 금지 | APPROVED (B) |
| LLM 시도 timeout / tokens | timeout 30초는 제안; 요청당 입력 최대 8,000 token, 출력 최대 2,000 token; 전체 입력 64,000/출력 16,000 token 이내 | token 한도 APPROVED (B). LLM 전용 timeout 값은 질문에 명시하지 않아 OPEN; 기존 도구 timeout 승인을 확대하지 않음 |
| transport 재시도 구체 운용 | 401/403 재시도 0; timeout/429/일시적 5xx만 추가 최대 2, backoff 1초/2초; Retry-After가 남은 시간보다 길면 중단 | pending; 기존 최대 2 범위 내 운용안 |
| LLM schema 보정 | #22 wrapper의 보정 범위 유지; transport와 schema 보정 모두 실제 요청 8회·전체 20회·시간·token·비용 공유 | 공유 한도 소비 APPROVED (B); adapter 별도 retry loop 금지 |
| 외부 비용 상한 | smoke 한 번 USD 1.00, 승인 campaign 누적 USD 3.00; 신규 유료 구독/credit 구매 금지 | APPROVED (B); 한도는 요금이나 예상 실측 비용이 아님 |
| 비용 미확인 호출 | 계정 요금/잔여 credit와 다음 요청 보수적 최대 비용을 확인하지 못하면 새 과금 가능 호출 거절. 사용량과 비용은 별도 기록 | 호출 거절 APPROVED (B); **actual_cost=None**, 사용량·과금 미확인. 0으로 채우지 않음 |
| embedding 실험 시간 | 3종 campaign 총 120분, 모델당 최대 35분, 준비·정리 포함 전체 한도 우선; 1회 campaign만 | pending; 다운로드 시간도 전체 시간에 포함 |
| 로컬 자원 | embedding 프로세스 1개, batch 1, CPU float32 기준안, 프로세스 peak memory 6 GiB 중단선, 로컬 모델/index 공간 총 10 GiB 중단선 | pending; 실제 여유 메모리/디스크는 미측정. 다른 병렬 작업 보호를 위해 추가 가용성 확인 필요 |

provider가 제공하는 default retry/auto 옵션은 명시적으로 끄거나 #45 공통 제어로 제한한다. 정상 응답 0건은 empty, optional 미준비는 unavailable, 인증/전송/구조 실패는 failed로 기존 계약에 맞게 기록한다. required 미준비·총예산 소진은 새 호출을 거절하며 workflow 실패와 정보부족을 혼동하지 않는다. 시도 전 잔여 횟수·시간·token·비용을 확인하고 재시도도 차감한다. 이 문서는 새로운 runtime enum이나 예산 모듈을 만들지 않는다.

현재 provider별 요금표/계정 tier·부가세·환율·free credit 잔액을 확정하지 않았다. 따라서 견적 합계도 **None**이다. [S9]에 가격 정보가 있더라도 계정 과금 관측을 대신하지 않는다. 승인 후 실행 직전에 dated 공식 요금/계정 조건과 최대 청구량 계산을 확보하고 그 상한을 enforcement할 수 있을 때만 과금 요청을 한다. 무료로 보이는 공공 API도 무조건 0 비용이라고 기록하지 않는다.

## 4. D07 모델 직접 선정과 미실행 실험안 (C 미수행)

사용자는 **3종 비교 실험을 수행하지 않고 `BAAI/bge-m3`를 embedding 모델로 선정**했다. 이 직접 선정은 측정에 따른 성능 우위나 특정 revision/운영용 store 승인으로 해석하지 않는다. 아래 비교 계획은 실행 승인안이 아닌 과거 제안·후속 참고로만 보존한다. 실제 weights/tokenizer 다운로드, embedding/index/benchmark 실행은 이 결정의 완료 증거가 아니다. BGE-M3를 실제 연결하려면 별도 담당 이슈에서 revision/LICENSE/library lock·corpus·자원·품질 검증을 확인한다.

### 4.1 모델 source cards — 공식 제공자 설명, 실측 아님

| 후보 / 1차 역할 | 2026-09-30 확인한 카드·라이선스·라이브러리 | Chunk/Query 규칙과 미확인 사항 |
| --- | --- | --- |
| `BAAI/bge-m3` / 1차 dense baseline | MIT 표기, 1024차원, 최대 8192 token, 다언어; `FlagEmbedding.BGEM3FlagModel`과 Sentence Transformers dense 사용 안내 [S1] | query instruction 불필요 안내. 동일 revision으로 Chunk/Query encode, L2 정규화/코사인 비교안. sparse/ColBERT/reranker는 이번 비교 제외. 기존 문서 snapshot `5617a9f61b028005a4858fdac845db406aefb181`은 다운로드·최신 revision 증거 아님 |
| `intfloat/multilingual-e5-large` / 비교 | MIT 표기, 다언어; Transformers 평균 pooling/L2 정규화와 Sentence Transformers 예제 [S2] | 비영어 포함 Query `query: `, Chunk `passage: ` prefix 필수; 예제 max_length 512. 다른 instruct 모델로 바꾸지 않음. 실험 revision pending |
| `nlpai-lab/KURE-v1` / 비교 | MIT 표기, Korean/English, BGE-M3 기반, 1024차원/8192 token, Sentence Transformers 예제 [S3] | 카드 예제의 plain-text encode 기반안; E5 prefix를 임의 공유하지 않음. 한국어 학습 설명은 프로젝트 cross-lingual 우위 증거가 아님. 실험 revision pending |

위는 공개 model card의 MIT 표기 확인이지 특정 revision의 모든 파일/LICENSE·의존성·재배포 의무 검토 완료가 아니다. **실행 전 모델별 정확한 commit SHA, 해당 revision의 LICENSE/카드/tokenizer/config, uv library lock을 고정**해야 한다. 이 작업은 weights·tokenizer·패키지를 다운로드하지 않았다. library 문서 예제 확인은 현재 프로젝트 import/Apple Silicon/MPS 동작 검증이 아니다. CPU float32가 비교 기준안이며 MPS/양자화/다른 dtype으로 바꾸려면 별도 환경 비교 승인이 필요하다.

### 4.2 저장소·고정 환경 승인 요청안

- **과거 제안: SQLite metadata + 명시 float32 dense vectors, exhaustive cosine 검색.** SQLite 내장 vector extension 지원을 주장하지 않는다. 애플리케이션이 저장된 벡터를 읽어 L2-normalized dot product를 계산하고 동점은 chunk_id 정렬로 처리하는 실험용 store 안이었다. 현재 비교 실험을 진행하지 않으며 제품 store도 미선정이다. D13의 페이지 제한 적용 제외는 과제 담당자 확인 근거가 미기록이다.
- 대안: FAISS flat dense index + 별도 metadata. 추가 native 의존성·serialization/platform 검증 부담을 이유로 이번 우선안에서는 보류한다. Chroma/운영용 vector DB·hybrid는 범위 밖이다. **SQLite는 실험용 선택 요청이며 product 최종 선택이 아니다.**
- 과거 비교 제안: 사용자 제공 M5 Pro / 16GB / 여러 병렬 작업을 전제로 **BGE-M3 → E5-large → KURE-v1 순차 로딩·실행·종료**한다는 안이었다. 현재 이 3종 비교는 수행하지 않는다. 향후 실제 BGE-M3 연결은 별도 이슈의 자원 점검을 따른다.
- 실행 전 기록: 실제 OS/architecture/칩/총·가용 메모리, Python/uv/torch/transformers/sentence-transformers/필요 시 FlagEmbedding 버전·lock, CPU device/dtype/thread 수, batch/seed·정규화·tokenizer revision·길이/초과 처리, corpus/query/Chunk hash, store 설정, 코드 revision. 지금 실제 하드웨어·설치 버전을 측정했다고 주장하지 않는다.
- Chunk는 #49 승인 설정을 고정 소비한다. 비교에서는 동일 Chunk 텍스트·페이지·기업 귀속·allowed source/as_of 필터를 유지한다. 공통안은 **각 후보 tokenizer로 prefix+special tokens 포함 512 token 이하임을 사전 확인**하는 비교 입력이다. 초과 Chunk는 한 후보에서만 자르지 않고 공통 재분할 후 새 Chunk/query version으로 전 후보 재실행한다. 본문 구조·표/단위 보존 조건과 충돌하면 실험을 보류한다. #49 설정 변경은 별도 소유자와 승인한다.
- 모델별 독립 index, model/revision/tokenizer/전처리/정규화/dtype/corpus hash/Chunk 설정/store version을 index metadata에 기록한다. 변경 시 새 index_version; embedding 공간을 섞지 않는다. 원문과 모델/index는 Git 제외 경로에만 둔다.

### 4.3 benchmark 사전 승인 요청안 (측정값은 모두 None)

- 동일 #13 승인 corpus와 사람이 지정한 정답 Chunk 집합. **질의 40개: tuning 10 / 최종 확인 30** 고정안. 최종 30개는 한→영 cross-lingual 10, 한국어 기술용어 5, 숫자/표 5, 동명/타기업 5, exact identifier 5로 주 범주를 구분한다. 실제 정답 작성·검수와 version/hash는 pending이다.
- 타기업 범주는 올바른 기업의 정답 Chunk가 있는 질의로 구성하고, 별도 no-answer negative fixture는 오검색 안전성 검증에 사용한다. no-answer 질의를 HitRate/MRR의 정답 질의 분모에 섞지 않는다.
- top_k=5 dense-only, 동일 기업/산업 허용 목록·as_of·Chunk 집합, 모델별 필수 prefix만 다르게 적용. reranking/hybrid/fine-tuning과 최종 질의셋에 대한 tuning 금지.
- HitRate@1/3/5: 상위 K에 정답 Chunk 하나 이상인 최종 질의 비율. MRR@5: 처음 맞은 rank 역수 평균, 5 안에 없으면 0. cross-lingual subset도 같은 산식과 실제 분모를 따로 보고한다. 작은 수작업 fixture로 산식을 먼저 확인한다.
- **과거 비교 품질 제안(미승인·미측정):** 최종 전체 HitRate@1 ≥0.60, @3 ≥0.80, @5 ≥0.90, MRR@5 ≥0.70; cross-lingual HitRate@5 ≥0.80; 모든 반환 Chunk의 Source/page 복원 가능, 허용 범위 밖 기업·cutoff 이후 source 반환 0건. 이 수치를 BGE-M3의 실측 성능이나 현재 최종 선정의 통과 근거로 쓰지 않는다.
- 모델별 indexing wall time·peak process memory·disk, retrieval query embedding 포함 latency(모델 로딩 별도), retrieval-only latency, warm-up 1회/측정 반복 3회 p50/p95, actual usage/cost를 기록하는 안. 본문/표 추출·정답 오류와 모델 검색 실패를 분리한다. cost 미상은 None; CPU 로컬 실행도 전력비를 0으로 꾸미지 않는다.
- 과거 비교 제안에서는 실행 실패·라이선스/환경 부적합·자원 한도 초과의 reason/log·미측정 셀을 남기도록 했다. 현재 #56의 3종 비교 완료를 주장하지 않으며, 담당자가 사용자 결정에 따라 범위를 조정해야 한다.
- 과거의 **#56 실측 후 선정안**은 직접 BGE-M3 선정으로 대체된다. #56의 실험 범위 변경은 담당 이슈에서 조정하며, 카드의 benchmark 순위를 본 프로젝트 실측으로 쓰지 않는다.

## 5. 최초 승인 질문(이력)과 최신 응답

아래는 최초 제시한 질문의 이력이다. 현재 결정은 이어지는 표와 §4의 사용자 후속 응답이 우선한다. 무응답·부분 승인은 남은 OPEN 승인으로 간주하지 않는다.

1. **경로·범위 A:** KR/US + ko/en pilot, Tavily 기본 Web/해외 뉴스, Naver 국내 뉴스 optional, KRX/OpenDART/SEC optional, 공식자료 기반 적격성·missing 처리, OpenAI API `gpt-4.1-mini-2025-04-14` structured-output 우선안 및 §2의 required/optional·비공개자료 외부 전송 제외를 승인합니까? 대안을 고르면 provider와 정확한 model ID·지원범위를 지정해야 합니다.
2. **외부 실행 B:** §3의 10분·20요청·LLM 8요청/token 상한·단일 동시성·retry/미준비 처리·USD 1/run 및 USD 3/campaign 한도·신규 구독/credit 구매 금지·요금 미확인 호출 거절을 승인합니까? 이는 prerequisite/readiness/요금 확인 이후 M2 smoke만 허용하며 M3 전체 실행은 제외합니다.
3. **로컬 실험 C (과거 승인 질문, 현재 미채택):** §4의 BGE-M3 1차 및 E5/KURE 3종 순차 비교, SQLite 실험 store, CPU float32/batch 1·자원/120분 상한, 40질의 split·dense top5·사전 품질 기준을 승인합니까? 현재 사용자는 이 비교를 수행하지 않고 BGE-M3를 직접 선정했다. 이 질문은 실행 허가가 아니다.

사용자 응답 기록 (승인·보류는 실행/readiness와 별개):

| 묶음 / 결정 | 상태 | 승인자·시각·근거 | 거절/보류 대안 |
| --- | --- | --- | --- |
| A / D12·provider·LLM snapshot | APPROVED | 사용자 UI「제안한 pilot 경로·범위 승인」; 기록 시각/근거는 서두 | 대안 모델·자동 유료 fallback·모든 API 필수화는 승인하지 않음 |
| B / D08 미정 live 예산 | APPROVED (질문에 명시된 범위) | 사용자 UI「제안한 smoke 예산 승인」; 기록 시각/근거는 서두 | 신규 backoff/LLM timeout 세부안 미확정; 무제한·요금 미상 강행·M3 전체 실행 금지 |
| C / D07 3종 실험·SQLite 실험 store | NOT PLANNED / 실험 미수행 | 최초 UI「다운로드·실험은 보류」 이후 사용자 명시「C실험 수행하지 않음」; 근거는 서두와 #43 후속 기록 | 3종 비교·실험 store·benchmark를 완료로 주장하지 않음 |
| D07 embedding 모델 | SELECTED: `BAAI/bge-m3` | 사용자 명시「embedding모델은 이미 결정 됨」, 후속 확인「BGE-M3」; #43 후속 기록 | 정확한 revision, 실제 license/접근·품질 검증, product store는 미확정 |

승인 후 담당자가 응답의 정확한 범위를 이슈 근거와 timezone-aware 시각으로 남기고 관련 결정 문서·policy fixture를 별도 소유 범위에서 반영한다. 이 문서가 기존 승인 문서나 configs를 자동 변경하지 않는다. 과제 조건 완화가 생기면 과제 담당자 확인도 필요하다.

## 6. 후속 시작 조건과 PR 상태

| 후속 | 시작에 필요한 확인 | 완료라고 주장하지 않을 항목 |
| --- | --- | --- |
| #45 runtime | #8·#35·#43 관련 계약/정책 승인, #73 구조 DTO 및 #82 운영 정책과의 호환 확인; live는 B 승인과 required readiness 뒤 | offline fake transport/clock은 실제 접근 증거 아님 |
| #47 LLM adapter | #8·#22·#35·#43·#45 준비, A의 정확한 snapshot/provider와 B 예산, API account/schema/readiness 확인 | wrapper fixture 성공은 real structured-output smoke 아님 |
| #52 실제 index | BGE-M3 선정; #49 준비, 정확한 revision/라이선스/환경·corpus 승인 확인 | index fake embedding은 real model indexing 아님; 선택이 실제 작동 증거는 아님 |
| #56 3종 benchmark | 사용자 결정에 따라 3종 실험은 진행하지 않음. 해당 이슈의 범위·완료 조건은 담당자와 별도로 조정 | 비교·최종 선정 실측을 수행했다고 주장하지 않음 |

#43의 결정 기록과 M2 실제 실행 완료는 다르다. #13/#35 문서 선행은 병합되었고 A/B 승인·BGE-M3 직접 선정·C 실험 미수행을 기록했으므로 이 승인 계획 이슈는 완료 처리할 수 있다. 실제 corpus·model revision·license·library lock·자원·credential·요금 readiness는 후속 실행 시 검증한다. #43 완료가 #52/#56 또는 M2 실험 완료를 뜻하지 않는다. readiness 불충족이면 live는 계속 막힌다.

## 7. 공식 근거 목록과 retrieval gaps

모두 **2026-09-30 조회 대상인 현재 문서**이며 과거 시점 snapshot 증거/모델 다운로드/계정 접근 테스트가 아니다. `web_extract`는 search-only backend 오류로 실패하여 설정 변경 없이 `curl -fsSL`로 공개 README/HTML 문서만 전용 scratch에 수집했다. S1–S7·S9–S11의 공개 문서 fetch는 성공했고 S8은 403으로 미확인이다. 아래 URL은 공식 제공자 자료다. mutable model card는 실행 revision pin의 대체물이 아니다.

- [S1: BAAI BGE-M3 model card](https://huggingface.co/BAAI/bge-m3) — MIT 표기, dense 라이브러리 예제, query instruction/차원/길이.
- [S2: intfloat multilingual-e5-large model card](https://huggingface.co/intfloat/multilingual-e5-large) — MIT 표기, prefix·pooling·정규화·512 token 예제.
- [S3: NLP&AI Lab KURE-v1 model card](https://huggingface.co/nlpai-lab/KURE-v1) — MIT 표기, 한/영, BGE 기반, Sentence Transformers 예제.
- [S4: Tavily Search reference](https://docs.tavily.com/documentation/api-reference/endpoint/search) — 인증·topic/search_depth·국가 옵션. 이용요금/계정 권한은 이번 확정 범위 밖.
- [S5: Naver News Search](https://developers.naver.com/docs/serviceapi/search/news/news.md) — 뉴스 검색·client ID/secret headers. 실제 검색 품질 미측정.
- [S6: OpenDART 기업개황 개발가이드](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019002) — 공개 문서 curl 조회 성공, company 요청 및 crtfc_key/corp_code 확인. 모든 재무 endpoint 지원을 검증한 것은 아님.
- [S7: KRX 서비스 이용방법](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp) — 공개 문서 curl 조회 성공, 로그인/인증키 신청(관리자 승인) 확인. 선정할 정확한 상장 조회 endpoint/응답/계정 권한은 OPEN.
- [S8: SEC EDGAR API 후보 문서](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) — **curl 403으로 본문 미확인**. submissions/companyfacts는 경로 shortlist 제안이며 이 조회의 검증 사실이 아니다. 정확한 접근 정책·endpoint·coverage를 공식 문서로 확인하기 전 SEC adapter live는 보류.
- [S9: OpenAI GPT-4.1 mini model reference](https://developers.openai.com/api/docs/models/gpt-4.1-mini) — structured outputs 지원과 `gpt-4.1-mini-2025-04-14` snapshot 표기 확인. 최신 모델이라고 주장하지 않음; 계정별 가용성·견적은 미확인.
- [S10: OpenAI structured outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs) — 기능 문서 확인. 프로젝트 schema subset/거절·실패 처리는 #22/#47 검증 필요.
- [S11: Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) — 기능 문서 공개 curl 조회 성공; 대안의 정확한 model snapshot/요금은 미선정.

문서 조회 실패를 provider 장애·credential 부재로 확대하지 않는다. 공식 페이지/검색에서 확인된 것과 실제 API 관측은 분리했다. pricing/account·exact model revision·library/platform compatibility·credential readiness·실험/최종 승인은 named gaps로 남긴다.
