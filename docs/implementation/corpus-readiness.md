# 코퍼스 후보와 embedding 접근 점검 — #13

작성: xxhigh, 2026-09-30 (Asia/Seoul).
[PR #92](https://github.com/rice-steamed-water/skala-rag/pull/92)의 병합에 따라
R05 200페이지 한도·D13 산정 규칙은 적용 제외다. 기존 #13의 산정 제안과 승인 대기는 철회한다.
과제 담당자 확인 근거의 미기록 상태는 [결정 목록](decisions.md)의 #91 기록을 따른다.
이 문서는 후보 metadata·접근 점검 기록이며 승인된 live corpus가 아니다.
[후보 표](../../data/manifests/README.md)와
[JSON 초안](../../data/manifests/corpus-candidates.draft.json)을 함께 읽는다.

## 코퍼스 준비 상태

페이지 예산 필드·전체 합계·HTML PDF 변환을 통한 분량 산정은 요구하지 않는다.
문서 포함 승인·권한·추출 품질·회사 귀속·기준일·원문 hash·corpus/index version은 유지한다.
실제 자료의 원본 페이지/슬라이드 locator는 Chunk→Evidence→평가→보고서 인용을 위해
보존한다. 문서 전체 페이지 수나 전체 PDF snapshot이 없는 이유만으로 거절하지 않는다.
HTML 원문은 URL·snapshot hash·section locator를 남기고, 추출 방식은 #49에서 다룬다.

| 자료 | 고정 원문 | 이번 관측 / 후속 확인 |
| --- | --- | --- |
| π0 | https://arxiv.org/pdf/2410.24164v4 | 7,893,845 bytes·SHA-256·표지 회사 귀속 확인; 추출 품질/권한 대기 |
| π0.5 | https://arxiv.org/pdf/2504.16054v1 | 16,213,397 bytes·SHA-256·표지 회사 귀속 확인; 추출 품질/권한 대기 |
| Skild 기술 소개 | https://www.skild.ai/blogs/building-the-general-purpose-robotic-brain | HTML 34,345 bytes·SHA-256; 추출/권한 대기 |

논문 최초 공개일과 revision 공개일은 다르다. π0 v4의 평가 기준일 필터에는
2026-01-08 revision 날짜를 보존하며 최초 공개일 2024-10-31로 소급하지 않는다.
π0.5 v1은 2025-04-22, Skild 글 표기일은 2025-07-29다.
HTML 최신 내용의 과거 시점 가용성을 발행일만으로 확정하지 않는다.
회사 공식 PDF URL은 웹 조회에서 403이어서 저자 논문의 버전 고정 arXiv 경로를 사용했다.
원문 전체 추출·그림/OCR·표 품질·historical snapshot·권한 검증은 미실행이다.
이전 커밋의 페이지 실측은 관측 이력으로 남지만 현재 corpus gate/완료 조건으로 사용하지 않는다.

## Embedding 문서 접근 점검 — 실측 성능 아님


공식 자료를 2026-09-30 재조회했다. model card 열람과 weights 다운로드 성공은 구별한다.

| 대상 | 문서 관측 | 이번 작업 결과 |
| --- | --- | --- |
| BAAI/bge-m3 | 고정 README에 MIT·1024차원·최대 8192 token 표기. 고정 파일 목록에 pytorch_model.bin 2.27 GB 표기 | 카드·파일 목록 접근 확인. weights 다운로드·inference·메모리 실측 미실행 |
| jinaai/jina-embeddings-v4 | 고정 LICENSE는 Qwen Research License, §2 비상업 목적 제한·§3 재배포 조건 | 조건 확인 기록만 제공. 오픈소스 필수 충족/과제 이용 최종 승인·실행 미확정 |

BGE의 2.27 GB는 제공자 파일 목록 표기이지 다운로드 실측량·peak RAM·VRAM이 아니다.
CPU/GPU/Apple MPS 실행 가능 여부도 모델 카드만으로 보장하지 않는다.
현재 프로젝트 lockfile에 torch/FlagEmbedding/sentence-transformers가 없고,
이번 점검에서 `uname -m`은 arm64였다. `sysctl -n hw.memsize`는 샌드박스에서
Operation not permitted로 실패해 실제 RAM 값은 미확인이다.

모델 다운로드와 메모리 측정을 하지 않은 이유: #43의 실행환경·실험 범위·시간/저장공간
예산 승인이 아직 없으며, #13은 미실행 사유 기록을 허용한다. 카드 접근 성공을
로컬 모델 실행 성공으로 표시하지 않는다. 모델 library를 추가하거나 weights를 받지 않았다.

승인 후 점검안: revision·hardware·OS·library lock·dtype·device·batch·입력 길이를
고정하고, 다운로드 bytes/시간·실제 저장공간·load/encode 시간·peak host RAM 및
해당 device memory를 측정한다. 성공/실패·OOM을 함께 기록한다. device가 지원되지
않으면 임의 다른 장치의 수치를 대체하지 않는다. 실제 retrieval 품질 비교는 #56이다.

Jina는 기존 원문의 이용조건 검토 대상으로 남긴다. #43의 v3 비교 제안인
bge-m3/multilingual-e5-large/KURE-v1의 최종 승인·실험을 이 점검으로 대신하지 않는다.
#13 초안·미실행 기록을 먼저 검토하고 #43 실험 승인을 별도로 진행할 수 있으므로
#13이 승인 전 모델 실행을 기다리는 순환 의존성을 만들지 않는다.

공식 확인 출처:

- [BGE 고정 카드](https://huggingface.co/BAAI/bge-m3/blob/5617a9f61b028005a4858fdac845db406aefb181/README.md)
- [BGE 고정 파일 목록](https://huggingface.co/BAAI/bge-m3/tree/5617a9f61b028005a4858fdac845db406aefb181)
- [Jina 고정 LICENSE](https://huggingface.co/jinaai/jina-embeddings-v4/blob/853c867b65b749f3c3c72a06868140d842e04f06/LICENSE)
- [π0 버전 이력](https://arxiv.org/abs/2410.24164v4)
- [π0.5 버전 이력](https://arxiv.org/abs/2504.16054v1)

## 인계와 검증

#13의 manifest 초안과 접근 점검은 PR #90의 문서 산출물이다. D13 승인 대기는 해제한다.
실제 corpus 승인·후보 ID 매핑·권한·추출 완료는 #43/#44/#49에서 확인하고,
#52 index·#56 모델 비교를 이번 작업에서 완료했다고 표시하지 않는다.
#44의 runtime schema와 다른 검토 초안이며 runtime_ready=false를 유지한다.

이전 커밋에서 uv sync --frozen, lint/format, 기존 offline pytest 1289개와
원문 hash/bytes·상대 링크 대조를 실행했다. PR #92 반영 후 lint 통과·format 119 files already formatted·
pytest 1334 passed in 1.34s 및 JSON/hash/bytes/링크·페이지 예산 필드 제거 검증이 통과했다. 모델 다운로드·inference·메모리·retrieval 품질은 미실행이다.
