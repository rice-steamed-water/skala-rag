# 코퍼스 페이지 산정 제안과 embedding 접근 점검 — #13

작성: xxhigh, 2026-09-30 (Asia/Seoul). **D13: OPEN / 사용자 승인 대기.**
이 문서는 #13의 검토 산출물이며 승인된 정책·live readiness·모델 선정 기록이 아니다.
[후보 표](../../data/manifests/README.md)와
[JSON 초안](../../data/manifests/corpus-candidates.draft.json)을 함께 읽는다.

## D13 구체 제안

전체 프로젝트의 활성 코퍼스에 200페이지를 적용한다. 다음 안은 승인 전 제안이다.

| 형태 | 제안 산정법 | 남길 관측 |
| --- | --- | --- |
| PDF | 표지·본문·참고문헌·부록·빈 페이지 포함 전체 파일 페이지 수 | 원문 SHA-256, 전체 수, 사용 구간 |
| PPT/PPTX | 숨김 슬라이드 포함 전체 슬라이드 수, 슬라이드별 1장 PDF snapshot | 원본 슬라이드 수, PDF 수, 변환 설정·버전 |
| HTML/Markdown | 고정 PDF snapshot의 실제 전체 페이지 수 | 원문 bytes 및 PDF hash, URL·수집시각, 렌더러/폰트/템플릿 버전 |
| 부분 PDF/슬라이드 | 포함 구간을 기록해도 기본 산정은 전체 원본 수 | 원본→발췌 페이지 매핑과 두 파일 hash |

HTML snapshot은 A4 세로, 사방 20mm, 본문 Noto Sans KR 11pt·행간 1.4,
이미지/표/제목/참고문헌 포함, 메뉴·광고 제외를 승인안으로 제안한다.
renderer와 font 파일 revision은 #43/#49에서 고정한다. 영상은 transcript 등
별도 승인된 문서화 없이는 이 코퍼스에 포함하지 않는다. 이 설정은 보고서 PDF의
D09를 확정하지 않으며 아직 구현·렌더링하지 않았다.

부분 발췌 페이지 수만 반영하는 예외는 과제 확인 담당의 허용 근거와 사용자 승인을
별도로 기록한 뒤 채택한다. 현재 기본안은 발췌로 200페이지 한도를 줄이지 않는다.
원본 페이지/슬라이드와 snapshot 수가 불일치하면 자동 통과시키지 않고 확인한다.
추출 실패나 읽지 못한 이미지가 있다는 이유로 예산에서 페이지를 빼지 않는다.

동일 원문 bytes의 중복은 한 번 세되 사용 구간·회사 귀속 이력을 보존한다.
서로 다른 revision/HTML·PDF 표현은 단순 제목 일치로 중복을 제거하지 않는다.
동일성 근거가 없으면 별개 문서로 센다. 교체 문서는 active index에서 제거하고
이전 run의 corpus_version·hash를 보존한다. 새 조사 문서를 자동으로 추가하지 않는다.
미상·미승인·권한 미확인·합계 초과 문서는 #44의 gate에서 거절하는 안이다.

대안: 사용 구간만 세면 적은 페이지로 더 많은 문서를 쓸 수 있으나 과제 제한 우회
여지가 있다. HTML을 무조건 1페이지로 세는 안은 실제 분량을 보존하지 않아 제안하지 않는다.

승인 기록에 필요한 항목: 위 산정법의 채택/수정, HTML 설정, 부분 발췌 예외의 과제
확인 근거, 승인자·승인일·코멘트 URL. 현재 모두 승인 미기록이다.
`decisions.md`의 OPEN 상태를 이 문서 작성만으로 APPROVED로 바꾸지 않는다.

## 실제 자료 점검

| 자료 | 고정 원문 | 확인 결과 |
| --- | --- | --- |
| π0 | https://arxiv.org/pdf/2410.24164v4 | pypdf 실제 17페이지, 7,893,845 bytes; 표지 회사 귀속 확인 |
| π0.5 | https://arxiv.org/pdf/2504.16054v1 | pypdf 실제 19페이지, 16,213,397 bytes; 표지 회사 귀속 확인 |
| Skild 기술 소개 | https://www.skild.ai/blogs/building-the-general-purpose-robotic-brain | HTML 34,345 bytes; PDF 수 미확인 |

논문 최초 공개일과 revision 공개일은 다르다. π0 v4의 평가 기준일 필터에는
2026-01-08 revision 날짜를 보존하며 최초 공개일 2024-10-31로 소급하지 않는다.
π0.5 v1은 2025-04-22, Skild 글 표기일은 2025-07-29다.
HTML 최신 내용의 과거 시점 가용성을 발행일만으로 확정하지 않는다.
회사 공식 PDF URL은 웹 조회에서 403이어서 저자 논문의 버전 고정 arXiv 경로를 사용했다.
원문 전체 추출·그림/OCR·표 품질·historical snapshot·권한 검증은 미실행이다.

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

## 남은 승인과 인계

#13의 manifest 초안·제안 소계·모델 점검 기록은 PR로 검토할 수 있다.
D13 승인 전까지 이슈는 진행 중이다. #43은 live 계획과 실험 승인을,
#44는 이 초안의 승인된 runtime manifest 변환·200페이지 gate를 담당한다.
#49의 추출, #52의 index, #56의 모델 비교는 이번 작업에서 완료하지 않았다.

## 이번 PR의 실제 검증

- `uv sync --frozen`: 기존 lockfile 47개 패키지 설치 성공, 의존성 파일 변경 없음.
- `uv run ruff check .`: All checks passed.
- `uv run ruff format --check .`: 113 files already formatted.
- `uv run pytest`: 1289 passed in 2.13s (기존 offline/fixture 회귀, live 증거 아님).
- `git diff --check`: 통과.
- 임시 검증 스크립트로 JSON parse·ID 중복·draft/승인/권한 상태·3개 hash/bytes·
  PDF 수·36페이지 소계·미상 전체·문서 상대 링크를 대조해 통과했다.
  이 스크립트는 검토용 임시 도구이며 제품의 #44 gate 구현이 아니다.

페이지 관측 재현 예시(원문은 임시 경로에만 저장):

```bash
curl --fail --location --max-time 45 https://arxiv.org/pdf/2410.24164v4 --output /tmp/issue13-pi0.pdf
curl --fail --location --max-time 45 https://arxiv.org/pdf/2504.16054v1 --output /tmp/issue13-pi05.pdf
uv run python - <<'PY'
from hashlib import sha256
from pathlib import Path
from pypdf import PdfReader
for name in ('pi0', 'pi05'):
    path = Path('/tmp/issue13-' + name + '.pdf')
    data = path.read_bytes()
    print(name, len(PdfReader(path).pages), len(data), sha256(data).hexdigest())
PY
```

외부 요청은 환경에 따라 실패하거나 bytes가 바뀔 수 있다. 현재 manifest hash와
다르면 관측 기록을 재검토하며 이전 run의 hash를 덮어쓰지 않는다.
