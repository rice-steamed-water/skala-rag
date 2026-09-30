# #145 — 실제 로컬 BGE-M3 검증

2026-09-30 사용자가 이 작업 대화에서 “차라리 모델을 로컬로 다운로드하고
진행합시다”라고 지시하여 기존 모델/tokenizer 다운로드 보류와 HF API 전용 범위를
이번 로컬 검증에 한해 변경했다. 전용 유료 Endpoint는 생성하지 않았다.
SQLite는 이 실행의 로컬 검증 artifact 저장에 사용하며 운영 DB 배포/선정이 아니다.

## 실제 결과

- 모델/tokenizer: `BAAI/bge-m3`, revision `5617a9f61b028005a4858fdac845db406aefb181`.
  [고정 모델 카드](https://huggingface.co/BAAI/bge-m3/blob/5617a9f61b028005a4858fdac845db406aefb181/README.md)와
  [Hub metadata](https://huggingface.co/api/models/BAAI/bge-m3)는 MIT를 표시한다.
  고정 revision의 modules/Pooling 설정은 Transformer → CLS pooling → Normalize다.
- 승인 corpus `issue52-reviewed-text-v1`의 두 실제 PDF를 다시 추출해 Source bytes hash,
  모든 페이지 text hash, extraction settings 및 omissions를 확인했다. 17+19=36개 Chunk다.
- 전체 문서는 각각 `partial`이며 본문/caption/텍스트 표만 사용한다.
  이미지/그래프 미추출·layout 한계를 Source snapshot의 text_index_review에 보존했다.
- 각 입력을 실제 로컬 tokenizer로 사전 검사했다. 최대 1874 token으로 8192 한도 이내다.
  초과 입력은 encode 전에 거절하며 prefix 없음·batch_size=1·1024차원·L2를 검증했다.
- Apple MPS / 메모리 16GB에서 마지막 실행 15.39초(모델 로드·추출·embedding·index·
  재오픈·파일 hash 포함, 다운로드/설치 제외). 성능 benchmark나 품질 우위가 아니다.
- 새 Python interpreter에서 같은 metadata 및 **모든** Chunk/Source payload를 복원·대조한 뒤
  실제 query vector로 cosine Top-5를 검색했다. π0.5의 2, 1, 7, 10, 4페이지가 반환됐다.
  Similarity는 confidence 또는 독립 사실 검증 점수가 아니다.
- inference API 호출 0회·API 비용 USD 0. 로컬 전력/장비 비용은 측정하지 않았다.
  다운로드는 공개 Hub 전송이며 유료 inference 요청이 아니다.

실행 기록은 `outputs/issue145-local-bge-final/validation.json`, index는 같은 폴더의
`index.sqlite`에 있다. 원문/모델/벡터/index/추출 텍스트는 Git에 넣지 않는다.
기록에는 model 파일 hash, tokenizer/model revision, lockfile hash, 라이브러리 버전,
입력 token 수, metadata, 검색 ID/출처/page, 시간 및 text-only 한계가 있다.

- corpus hash: `sha256:d73dbc555d09d936d3c29d9d57f73d9505e4cecdbd8234013e926a2171039575`
- index version: `sha256:bc345db2d4ed9c6c4edb46e02ccc3021408ae8aabe1431b376af1fa579ba53c7`
- 실행환경: Python 3.11.15, torch 2.14.0, transformers 5.17.0,
  sentence-transformers 5.7.0. 의존성은 uv.lock으로 고정한다.

## 재현

`uv sync --frozen` 후 명시적인 고정 revision 다운로드를 한 번 수행한다.
HF token은 필요하지 않으며 설정돼 있어도 이 다운로드에는 사용하지 않는다.

```python
from huggingface_hub import snapshot_download
snapshot_download(
    "BAAI/bge-m3", revision="5617a9f61b028005a4858fdac845db406aefb181",
    local_dir="data/local/models/bge-m3-5617a9f", token=False,
    allow_patterns=["*.json", "sentencepiece.bpe.model", "pytorch_model.bin",
                    "1_Pooling/config.json", "README.md"],
)
```

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
uv run python -m skala_rag.rag.local_bge_validation \
  --root . --model-path data/local/models/bge-m3-5617a9f \
  --output-dir outputs/issue145-local-rerun \
  --query 'How does pi0 use flow matching for general robot control and how does pi0.5 generalize to new environments?' \
  --device mps
```

GPU가 없는 환경은 명시적으로 `--device cpu`를 선택한다. 기존 출력은 덮어쓰지 않는다.
모델은 local_files_only/trust_remote_code=false로 읽으며 download receipt의 고정 revision을
검사한다. Receipt 검사는 독립된 서명 검증이 아니며, 로컬 파일 hash도 실행 기록에 보존한다.
실패한 실행은 성공 validation.json을 만들지 않으며 이미 생성된 index는 진단용으로 남을 수 있다.
기본 pytest에서는 모델을 로드하거나 다운로드하지 않는다.

## 인계

#54는 위 index version/metadata와 저장된 Source/Chunk를 후보·출처·as_of 필터에
연결하고 동일 local encoder로 query embedding을 생성해야 한다.
#62는 retrieval→실제 LLM Evidence→snapshot→Technology 평가 trace를 별도로 검증한다.
이번 성공은 실제 embedding/index/search smoke이며 평가·보고서·전체 M2 완료가 아니다.
HF router/전용 Endpoint의 BGE-M3 serving revision/payload/비용 검증은 실행하지 않았고,
사용자 지시에 따라 로컬 경로로 대체했다. 3종 모델 비교는 기존 #56 Not planned를 유지한다.
