# 수동 기업 PDF 가져오기 가이드 (#237)

> 명령, 옵션, 생성 파일 이름은 구현된 `examples/import_company_pdfs.py`와 `src/skala_rag/rag/manual_pdf.py`를 읽고 맞췄다. 가져오기 명령은 합성 PDF로 재실행해 확인했고, 모델 API를 쓰는 보고서 실행은 확인하지 않았다(12절).

기업 PDF를 직접 받아 둔 사용자가 한 번의 가져오기로 보고서 검색에 쓸 수 있게 하는 문서다. 처음 보는 사람도 위에서부터 그대로 따라 하면 된다. 숨겨진 환경 변수나 미리 만들어 둔 저장소는 없다고 가정한다.

## 1. 전체 흐름

```text
raw/{기업명}/*.pdf
  -> MarkItDown 변환 (PDF -> Markdown, 플러그인 끔, LLM 호출 없음)
  -> 청크 분할 (1,200자, 150자 겹침, Markdown 문자 위치 기록)
  -> 로컬 BGE-M3 임베딩 + SQLite 인덱스 (새 snapshot)
  -> import state가 ready, 보고서용 설정 파일 생성
  -> run_company_report 가 검색한 청크만 모델에 전달 (최대 12개 고유 청크)
```

용어는 이렇게 쓴다.

| 용어 | 뜻 |
| --- | --- |
| 원본 PDF | `raw/{기업명}/`에 넣은 파일. 해시(`sha256:...`)로 고정된다 |
| Markdown | MarkItDown이 만든 변환본. 원본과 함께 보존되고 해시가 따로 기록된다 |
| 청크 | Markdown의 정확한 문자 구간. 페이지 번호는 만들지 않아 비어 있다 |
| snapshot | 한 번 가져온 결과로 만든 불변 인덱스 버전(`store_version`) |
| import state | 기업별 현재 상태. `importing`, `ready`, `empty`, `failed` 중 하나 |
| operator_folder | "사용자가 폴더에 넣은 자료"라는 표시. 사실 검토 완료나 법적 기업 식별이 아니다 |

## 2. 준비물

clone만으로는 생기지 않는 자산이 있다. 모델은 다운로드하지 않는다. 이미 준비된 파일을 가리키기만 한다.

1. **의존성:** 저장소 루트에서 `uv sync`. `markitdown[pdf]`가 함께 설치된다.
2. **로컬 임베딩 모델:** BAAI/bge-m3 revision `5617a9f61b028005a4858fdac845db406aefb181`. 이 팀의 기존 위치는 `data/local/models/bge-m3-5617a9f`다.
3. **모델 검증 영수증:** 위 모델을 검증한 receipt JSON. 기존 위치는 `outputs/issue145-local-bge-final/validation.json`이다. 영수증은 읽기만 하고 고치지 않는다. 청크 설정이 영수증과 달라도 snapshot이 따로 기록하므로 영수증을 고칠 필요가 없다.
4. **기본 설정(base config):** `configs/company-report.v2.json`은 상대 경로 템플릿이라 그대로는 실행되지 않는다. 복사본을 하나 만들고 경로를 **절대 경로**로 바꾼다(아래 예시는 그 복사본이다). 모델과 영수증 준비 절차는 [로컬 임베딩 안내](local-bge-validation.md)를 따른다.
5. **기존 기업 저장소는 필요 없다.** 가져오기가 새 저장소를 만든다. `store_dir`는 템플릿 형식만 맞추면 된다.

base config 예시(`data/local/manual-base-config.json`). `/abs/repo`는 자기 checkout의 절대 경로로 바꾼다.

```json
{
  "schema_version": "company-report-2",
  "runtime_path": "/abs/repo/configs/runtime.json",
  "store_dir": "/abs/repo/data/local/company-store",
  "model_path": "/abs/repo/data/local/models/bge-m3-5617a9f",
  "model_receipt_path": "/abs/repo/outputs/issue145-local-bge-final/validation.json",
  "research_enabled": false
}
```

## 3. PDF 넣기

- 위치는 저장소 루트의 `raw/`다. `docs/raws/`가 아니다. `docs/raws/`는 과제 원문 보관용이고 읽기 전용이다.
- 기업 하나에 폴더 하나: `raw/{기업명}/파일.pdf`. 폴더 이름이 곧 `--company` 값이다.
- 폴더 바로 아래의 `.pdf`만 읽는다. 하위 폴더는 무시하고, 다른 기업 폴더와 합치지 않는다. 파일은 이름순으로 처리한다.
- 기업명에 공백이 있어도 된다. 명령에서는 따옴표로 감싼다: `--company "Synthetic Robotics"`.
- 텍스트가 들어 있는 PDF만 된다. 스캔 이미지 PDF는 OCR을 하지 않으므로 빈 결과(`empty`)가 된다.
- `raw/`는 `.gitignore`에 들어 있다. 재배포가 안 되는 원문을 커밋하지 않는다.

```text
raw/
  Synthetic Robotics/
    company-overview.pdf
    funding-2026.pdf
```

## 4. 가져오기 실행

```bash
uv run python examples/import_company_pdfs.py \
  --company "Synthetic Robotics" \
  --base-config data/local/manual-base-config.json
```

기본값은 `--raw-root raw`, `--output-root data/local/manual`이다. 다른 위치를 쓸 때만 넘긴다.

```bash
uv run python examples/import_company_pdfs.py \
  --company "Synthetic Robotics" \
  --base-config /abs/repo/data/local/manual-base-config.json \
  --raw-root /abs/repo/raw \
  --output-root /abs/repo/data/local/manual
```

가져오기 단계는 모델 API를 부르지 않는다. 변환, 임베딩, 검색은 모두 로컬이다. 화면에는 파일 수, 실패 파일과 이유, 생성된 보고서 설정 경로가 나온다. 원문 전체나 비밀값은 출력하지 않는다.

**출력 예 (합성 PDF 2개, 1개는 손상 파일):**

```text
status=ready generation=<32자 hex> reused=False discovered=2 ok=1 empty=0 failed=1
file=02-corrupt.pdf status=failed code=PDF_INVALID
config_path=<output-root>/<24자 hex>/company-report.json
```

**생성 위치:** `--output-root` 아래 기업마다 해시 이름(24자 hex) 폴더가 하나 생긴다. 기업 이름 폴더가 아니다.

| 파일 | 뜻 |
| --- | --- |
| `<해시>/import-state.json` | import state. 상태, 파일별 결과, 입력 지문 |
| `<해시>/company-report.json` | 보고서용 설정. 보고서에는 **이 파일**을 쓴다. ready일 때만 생긴다 |
| `<해시>/generations/<세대 id>/store/` | 불변 snapshot 저장소. 원본 PDF와 Markdown 보존본도 여기 있다 |

`config_path=`는 ready일 때만 출력된다. 오류는 `IMPORT_REJECTED code=...` 또는 `IMPORT_CONFIG_ERROR code=...`로 나오며 PDF 본문은 출력하지 않는다.

## 5. 결과 상태와 종료 코드

| 상태 | 언제 | 종료 코드 | 보고서 실행 |
| --- | --- | --- | --- |
| ready (전부 성공) | 모든 PDF가 변환·색인됨 | 0 | 가능 |
| ready (부분 성공) | 일부 파일만 실패. 실패 파일과 `error_code`가 출력됨 | 0 | 성공한 파일만으로 가능 |
| empty | PDF가 없거나 모두 텍스트가 없음 | 1 | 막힘 |
| failed | 전부 실패하거나 도중에 오류가 남 | 1 | 막힘 |
| importing | 가져오기 도중 프로세스가 강제 종료됨 | (출력 없음) | 막힘. 다시 가져온다 |
| 입력·설정 오류 | 기업 이름 형식 오류, 폴더 없음, base config 오류, 인자 오류 | 2 | 해당 없음 |

부분 성공은 "일부 자료로 쓸 수 있다"는 뜻이지 실패가 아니다. 반대로 종료 코드 0만 보고 다 됐다고 판단하지 않는다. 실패 파일 목록을 확인한다.

## 6. 다시 가져오기와 캐시

- 같은 파일, 같은 설정으로 다시 실행하면 변환, 임베딩, 가져온 시각을 재사용한다.
- 부분 성공이었다면 다시 실행할 때 실패했던 파일만 재시도한다. 새로 성공한 파일이 생기면 새 snapshot이 된다.
- PDF를 추가·수정·삭제하거나 파서·청크·모델 설정이 바뀌면 새 snapshot을 만든다.
- **PDF를 바꾸고 다시 가져오지 않으면 보고서가 시작되지 않는다.** 보고서는 시작할 때 `raw/` 파일 목록과 해시를 다시 확인한다. 이전 설정 파일을 복사해 두었다가 쓰는 것도 막힌다. 이미 시작된 보고서는 시작 시점의 snapshot으로 끝까지 간다.

## 7. 보고서 실행

`.env`에 비밀값과 실행 변수를 둔다. 커밋하지 않는다.

```dotenv
OPENAI_API_KEY=sk-...
SKALA_COMPANY_NAME=Synthetic Robotics
SKALA_COMPANY_REPORT_CONFIG=/abs/repo/data/local/manual/<가져오기 출력의 설정 경로>
SKALA_COMPANY_REPORT_OUTPUT=/abs/repo/outputs/manual-synthetic-robotics-1
```

| 변수 | 뜻 |
| --- | --- |
| `OPENAI_API_KEY` | 작성·Judge 모델 호출용. runtime이 환경에서 읽고 JSON에는 넣지 않는다 |
| `SKALA_COMPANY_NAME` | 가져올 때 쓴 `--company`와 같은 이름 |
| `SKALA_COMPANY_REPORT_CONFIG` | 가져오기가 출력한 설정 파일 경로. base config가 아니다 |
| `SKALA_COMPANY_REPORT_OUTPUT` | 아직 없는 새 출력 폴더 |

```bash
uv run --env-file .env python examples/company_report_run.py
```

- 기준일(`as_of`)은 따로 지정하지 않으며 실행한 날, 즉 오늘이다.
- 종료 코드: 0 = 검증 통과한 completed/partial, 1 = 실패·검증 실패(`REPORT_NOT_COMPLETED outcome=... reasons=... result=<경로>`), 2 = 환경 변수·설정 오류.
- 결과는 출력 폴더의 `run-result.json`에서 outcome, completeness, validation을 확인한다.
- 이 단계는 유료다. 가져오기와 달리 실제 API 비용이 생긴다. 실행 기록의 호출 수와 추정 비용은 코드의 회계값이지 실제 청구액이 아니다. 실제 청구는 API 콘솔에서 확인한다.

## 8. 개인정보와 외부 전송

- 가져오기는 로컬에서 끝난다. PDF는 외부로 나가지 않는다.
- 보고서 단계에서는 **검색으로 선택된 청크 본문이 설정된 모델 API로 전송된다.** 최대 12개 고유 청크이며, 기존 요청 크기·비용·시간 한도가 그대로 적용된다. 민감한 자료는 넣기 전에 판단한다.
- PDF 안의 지시문처럼 보이는 문장도 근거 데이터로만 다룬다. 출처 URL, 파일 경로, 검색 메타데이터 같은 구조화된 출처 정보는 모델에 보내지 않고 인용으로만 쓴다. 단, 선택된 근거의 원문(`claim`, `excerpt`)은 PDF에 적힌 그대로 전송되므로, 본문에 URL이나 경로가 적혀 있으면 그 글자도 함께 모델 API로 나간다.

## 9. 보고서에 나타나는 방식과 한계

- 수동 자료의 근거는 `reported`, 신뢰도 `unknown`이다. 출판일, 페이지, 수치·날짜 값은 만들어 내지 않는다.
- 인용은 원본 PDF와 Markdown의 정확한 문자 구간을 가리킨다.
- 폴더에 넣었다는 사실은 기업 법적 식별이나 사실 검토가 아니다. 자료가 부족하면 `partial`이고 한계와 확인 과제가 남는다.

## 10. 코드 읽는 순서

1. `examples/import_company_pdfs.py`: 인자 해석과 출력만 하는 얇은 CLI.
2. `src/skala_rag/rag/manual_pdf.py`: 폴더 탐색, MarkItDown 변환, 청크, import state 발행.
3. `src/skala_rag/rag/company_store.py`: snapshot 저장소 생성과 `open(version=...)`.
4. `src/skala_rag/company_report.py`: `run_company_report`가 설정과 현재 입력을 검사.
5. `src/skala_rag/graph/company_report.py`: 검색 청크 제한과 보고서 수명주기.

계약 타입(`OperatorFolderConfig`, `ImportState`, `ImportFile`, `OperatorFolderBinding`)은 `src/skala_rag/contracts/company_report.py`에 있다.

## 11. 문제 해결

| 증상 | 원인 | 조치 |
| --- | --- | --- |
| 빈 폴더라며 종료 코드가 1 | `raw/{기업명}/`에 PDF가 없거나 이름이 다름 | 폴더 이름과 `--company` 철자·공백 확인 |
| 모든 파일이 `empty` | 스캔 이미지 PDF | 텍스트 PDF로 교체. OCR은 지원하지 않음 |
| 일부 파일 `failed` | 손상·암호화 PDF | 파일 교체 후 다시 가져오기 |
| 모델·영수증 오류 | base config가 상대 경로이거나 자산 없음 | 절대 경로 확인, [로컬 임베딩 안내](local-bge-validation.md) |
| 보고서가 입력 변경으로 거부 | PDF를 바꾸고 다시 가져오지 않음 | 가져오기 재실행 후 새 설정 경로 사용 |
| 보고서가 상태 때문에 거부 | state가 ready가 아님(중단·실패·빈 입력) | 가져오기 재실행 |
| `ENV_REQUIRED name=SKALA_...` | `.env` 변수 누락 또는 `--env-file` 생략 | 7절의 네 변수와 명령 확인 |
| `IMPORT_REJECTED code=...` (종료 코드 2) | 기업 이름 형식, 폴더 없음 등 | 코드 이름과 `--company`, `--raw-root` 확인 |
| `IMPORT_CONFIG_ERROR code=...` (종료 코드 2) | base config 경로·내용 오류 | 2절의 절대 경로 확인 |

## 12. 검증 범위

| 경로 | 상태 |
| --- | --- |
| 4절 가져오기 명령 (절대 경로형) | 합성 PDF 2개(정상 1, 손상 1)와 실제 로컬 모델·영수증으로 재실행했다. 부분 성공 종료 코드 0, 실패 파일 사유 출력, 같은 입력 재실행 시 `reused=True`, 생성 경로가 위 표와 일치함을 확인했다 |
| 7절 `.env` 변수 | 보고서 예제가 읽는 변수 이름과 정적으로 대조했다 |
| 7절 보고서 실행 | **실행하지 않았다.** 유료 모델 API 호출이 필요하다. 명령과 변수 이름만 코드와 맞췄고 결과는 보증하지 않는다 |
| 실제 기업 자료 | 사용하지 않았다 |

합성 PDF로 한 검증은 배선 확인이지 실제 기업 성과나 모델 품질 증거가 아니다.
