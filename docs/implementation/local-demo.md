# 단일 기업 로컬 라이브 데모 (#180)

대상은 **Physical Intelligence** 하나다. 웹 입력마다 로컬 BGE-M3 검색 →
LangGraph의 다섯 역할별 실제 모델 분석 → 보고서 Generator → 별도 Semantic Judge →
기존 한글 HTML/Chromium PDF 검증을 새로 수행한다. 이전 보고서를 생성 결과로 재사용하지 않는다.

## 범위와 한계

- **점수 없는 자료 기반 투자 검토 초안**이다. 기술 논문만으로 적격성·투자단계·시장규모·매출·투자조건을 추정하지 않는다.
- 사용자 지정 조사 대상과 투자 추천 후보 선정은 다르다. 기존 fixture 전용 scoring/selector를 live로 바꾸지 않는다.
- 본문은 원문에서 확인된 저자 보고, 분석·해석, 판단 불가를 구분한다. 원문도 독립 검증된 사실은 아니다.
- `status=completed`는 이 실행의 보고서·PDF 검증 통과다. `publication_allowed=false`, `whole_m3_verified=false`는 그대로다.
- 로컬은 웹 앱과 자료·검색을 뜻한다. 실제 분석·Generator/Judge는 OpenAI API를 사용하므로 모델 호출 시 인터넷이 필요하다.
- 새 수집 provider, 외부 배포, 임의 기업, 후속 Q&A는 포함하지 않는다.

## 설치와 사전 자료 준비

프로젝트 루트에서 실행한다. 신규 CLI/옵션은 만들지 않고 Python callable을 사용한다.

```bash
uv sync
uv run playwright install chromium
uv run python -c 'from pathlib import Path; from skala_rag.demo_prepare import prepare_demo; print(prepare_demo(root=Path.cwd()))'
```

준비 함수는 기존 `issue49-source-snapshots.json`과 `issue52-reviewed-text-v1.json`을 사용한다.
원문 해시, 승인된 페이지별 텍스트 해시를 검증한 뒤 기존 인덱스 생성·재개방 코드를 호출한다.
모델은 `BAAI/bge-m3`, 고정 revision `5617a9f61b028005a4858fdac845db406aefb181`이다.
모델 최초 다운로드는 크며 준비 시간은 보고서 생성 시간과 별도로 기록한다.
이미 유효한 인덱스가 있으면 검증 후 재사용한다. 손상된/부분 생성 인덱스를 자동 덮어쓰지 않는다.

원문은 다음 두 논문의 특정 버전이다. 최신 기업 실사 자료라는 의미는 아니다.

- π0: A Vision-Language-Action Flow Model for General Robot Control — `https://arxiv.org/pdf/2410.24164v4`
- π0.5: a Vision-Language-Action Model with Open-World Generalization — `https://arxiv.org/pdf/2504.16054v1`

원문과 모델은 `data/local/`, 인덱스·검증 영수증은 `outputs/issue180-local-bge/`에 있으며 커밋하지 않는다.
텍스트 전용 검색이므로 그림·표의 미추출 수치를 보고서 근거로 사용하지 않는다.

## 키와 승인

프로젝트 `.env`의 `OPENAI_API_KEY` 또는 해당 환경변수를 사용한다. 키 값은 UI·영수증에 넣지 않는다.
추가 수집 API 키가 있어도 이 경로에서는 사용하지 않는다.

이번 실행에는 사용자가 승인한 **누적 US$3, LLM 30회, 첫 실행부터 20분, 자동 재시도 없음**을 적용한다.
승인 참조를 `data/local/demo180-approval.json`에 기록해야 하며, 이 파일이 없으면 호출을 시작하지 않는다.
이 세션의 승인 파일은 실행 준비 과정에서 작성한다. 이후 재승인 없이 campaign 파일을 지워 제한을 초기화하지 않는다.

새 컴퓨터에서의 최초 실행은 **실행자가 위 한도의 유료 호출을 승인한 뒤에만** 아래 형태의 JSON을
`data/local/demo180-approval.json`에 작성한다. `approval_reference`는 실제 승인 기록을 식별하는
고유한 문자열로 바꾼다. 다른 사람의 과거 승인을 복사해 사용하지 않는다.

```json
{
  "approval_reference": "실제-실행자-승인기록의-고유-ID",
  "company": "Physical Intelligence",
  "limits": {"llm_calls": 30, "cost_usd": "3", "seconds": 1200},
  "automatic_retries": false,
  "scope": "단일 기업 로컬 자료 기반 분석 및 보고서/PDF 검증"
}
```

이 JSON은 키를 포함하지 않으며 프로그램은 한도를 고정한다. 값을 크게 바꾸어도
추가 호출을 승인하지 않는다. 기존 `outputs/demo180-campaign.json`이 있는 환경에서는
최초 승인 예시로 덮어쓰지 말고 아래 재승인 절차를 따른다.

예산은 `outputs/demo180-campaign.json`에 **요청 전에** 보수적으로 예약·영속화한다.
프로세스 재시작이나 새 run ID로 예산을 리셋하지 않는다. 작업 중 중단된 요청도 예약을 환불하지 않는다.
실제 청구액은 미확인이며 `cost_usd_accounted`는 공식 단가 기반 예약 상한이다.
Graph 역할별 호출은 순차이며 HTTP 재시도와 보고서 자동 수정 재호출은 비활성화한다.
기간/예산 소진 또는 생성·Judge 실패 후 추가 실행은 사유 확인과 사용자 승인을 거친다.
실패·중단된 실행은 캠페인에 `reapproval_required`를 영속화한다. 재승인 시
새 `approval_reference`와 `reapproval.previous_approval_reference`,
`reapproval.failed_run_id`, 비어 있지 않은 `reapproval.review_reference`가 필요하다.
승인 기록은 실제 사용자 승인 이후에만 갱신한다. 재승인은 실행 시간을 새로 부여하지만
누적 호출·비용 예약은 유지한다. 기존 캠페인 파일을 삭제해 갱신하지 않는다.
실행은 POSIX 작업 프로세스에서 수행하며 감독 프로세스가 캠페인 마감 시 작업 그룹을 종료한다.
모순된 Judge 통과 응답은 거부하고, 수정 요구는 자동 재생성 없이 실패로 남긴다.

## 실행과 시연

```bash
uv run python -c 'from skala_rag.demo_web import serve; serve()'
```

1. 브라우저에서 `http://127.0.0.1:8765`를 연다. `localhost` 대신 이 주소를 사용한다.
2. `Physical Intelligence` 또는 `피지컬 인텔리전스`를 입력하고 실행한다.
3. 로컬 검색과 다섯 역할 분석, 생성·검증 진행 상태를 확인한다.
4. 완료 화면에서 보고서와 원문 발췌·페이지/출처를 확인한다.
5. 해당 실행의 PDF를 다운로드하고 PDF 뷰어로 열어 한글·내용·출처·페이지 배치를 확인한다.
6. 실패 또는 미검증 결과는 완료로 표시하지 않으며 검증된 PDF 다운로드를 제공하지 않는다.

서버는 loopback만 허용한다. Host/Origin/CSRF 검증, 단일 실행 잠금,
산출물 allowlist 및 해시 검증을 수행한다. `.env`, 임의 파일, 디렉터리 목록은 제공하지 않는다.
서버 재시작 시 과거 job UI 목록을 복원하지 않지만 실행 산출물은 디스크에 남는다.

### 근거와 원문 읽는 법

Evidence는 원문의 특정 페이지/발췌에 연결된 **근거 단위**, Source는 **원문 문서 단위**다.
따라서 하나의 논문에서 근거 여러 개를 인용해도 원문 목록은 한 개일 수 있다.
웹에는 검색된 근거가 표시되고 보고서/PDF 근거 목록에는 본문과 추가 역할 출력에서 인용한 근거가 표시된다.
검색 근거 수·본문 인용 근거 수·원문 수를 혼동하지 않는다. 원시 JSON은 추적/디버깅 보조 자료다.

### 연구 보고서의 스코어보드와 추가 내용

`unscored-research-only-1` 경로만 별도 편집 디자인을 사용한다. SUMMARY 바로 뒤의
스코어보드는 투자 점수가 아니라 `live_reviews`에 저장된 **다섯 역할의 기록 수**다.
사업·투자조건(`business_deal`)은 한 역할로 표시하며 여섯 개 평가 점수를 만들지 않는다.

- 관측·해석: 원본 항목 수(중복 포함).
- 근거: 해당 역할이 참조하며 context에서 해소되는 고유 Evidence ID 수.
- 출처: 해당 근거에서 연결되고 context에 존재하는 고유 Source ID 수.
- 결측: 역할 내 동일 문자열 중복 제거 수. 결측 비율이나 조사 충분성은 계산하지 않는다.
- 근거 신뢰도: 고유 참조 Evidence에 저장된 `high/medium/low/unknown` 등의 범주별 건수.
  **수치 신뢰도는 미산정**이다. medium을 50/70점 등으로 변환하지 않는다.
- 출력이 없는 역할은 `출력 없음`, 해소되지 않은 근거 ID는 `미해소`로 표시한다.
  역할 간 합산은 동일 근거·문서의 반복을 포함하며 고유 출처 수도 독립 검증 횟수가 아니다.

기업·팀 / 기술·시장 / 투자 평가·위험에는 해당 역할의 원본 관측·해석·결측 내용을
추가한다. 동일 역할·분류 안에서 **문장과 참조 ID 집합이 모두 같은 항목만** 중복 제거한다.
유사한 문장을 새 주장으로 합성하거나 임의로 잘라내지 않는다. 역할 이름은 해당 영역의
실사 완료를 뜻하지 않으며, 창업자·시장 역할에도 기술 설명만 있을 수 있다.
추가 역할 출력은 저장된 내용을 표시한 것으로, 기존 Generator 초안의 Semantic Judge 통과를
이 추가 내용의 별도 의미 검증으로 확대하지 않는다.

본문의 작은 위첨자 `[n]`은 PDF 내부의 근거 항목으로 이동한다. 근거 목록에는 위치,
원문 발췌(공백 정리 후 최대 480자, 초과 시 생략 명시), 저장 신뢰도·유형,
retrieval/chunk 추적 ID와 validator용 원래 인용 토큰을 보존한다. 실제 추가 인용된
근거·출처만 부록에 포함하며 draft/context 자체와 기존 인용 ID 목록은 수정하지 않는다.

A4·18mm 여백과 본문 10.5pt/15pt를 유지한다. 별도 표지 없이 회사명·요약·스코어보드부터
읽고, 참고문헌은 새 페이지에서 시작한다. PDF≤5페이지와 SUMMARY≤0.5 검사는 그대로이며,
현재 validator는 요약 제목부터 기업·팀 제목까지(스코어보드 포함)를 보수적으로 측정한다.
초과 시 내용 숨김·본문 축소 없이 기존 layout 실패로 남긴다. 다른 fixture 모드의 디자인은 바꾸지 않는다.

### 저장 결과 재렌더와 새 분석의 구분

저장된 `report-context.json`과 `report-pipeline.json`의 draft를 로드해
`render_report_html` → Chromium으로 표시만 재생성할 수 있다. 이 작업은 새 검색·모델 분석·
Semantic Judge 호출이 아니다. 원본 실행 디렉터리는 변경하지 않고 별도 검증 디렉터리에
HTML/PDF와 입력 해시·실제 layout 결과를 보존하며 **재렌더 / 신규 의미 검증 미실시**로 표기한다.
과거 승인이 만료된 경우 이 표시 작업을 새 유료 호출이나 final 발행 승인으로 사용하지 않는다.

### 문제 해결

| 증상/영수증 코드 | 확인할 사항 |
| --- | --- |
| `uv: command not found` | uv 설치/PATH를 확인한다. 이 컴퓨터에서 사용한 절대 경로를 다른 컴퓨터에 그대로 복사하지 않는다. |
| `OPENAI_API_KEY_MISSING` | 프로젝트 `.env` 또는 서버 프로세스 환경변수에 키를 설정하고 서버를 다시 시작한다. |
| `LOCAL_INDEX_MISSING`, 원문/검토 해시 오류 | 위 자료 준비 명령을 실행하고 manifest와 원문을 확인한다. 검증을 우회하거나 가상 자료로 대체하지 않는다. |
| `APPROVAL_REQUIRED`, `REAPPROVAL_REQUIRED` | 실제 승인 기록과 승인/캠페인 JSON을 확인한다. 실패 원인 검토 후 새 사용자 승인이 필요하다. |
| `CAMPAIGN_EXPIRED`, `BUDGET_EXHAUSTED` | 자동 재시도하지 않는다. 시간·누적 비용·호출 기록을 보존하고 운영자에게 재승인을 요청한다. 파일 삭제로 리셋하지 않는다. |
| `REPORT_REVISION_REQUIRED`, Judge/PDF 실패 | 해당 실행의 `report-pipeline.json`, `run-result.json`을 확인한다. 기존 PDF나 과거 성공 결과로 바꿔 표시하지 않는다. |
| 포트 8765 사용 중 | 이전 데모 서버가 실행 중인지 확인한다. 다른 앱을 무조건 종료하지 않는다. |

캠페인 만료만 있고 실패 실행이 기록되지 않은 경우에는 자동 갱신 경로가 없다.
운영자가 실제 승인 및 만료 사유를 확인해 캠페인 실패/재승인 기록을 정리해야 한다.
이 제한을 피하려고 기존 캠페인이나 사용량을 삭제하지 않는다.

## 실행 산출물

`outputs/demo180-<run-id>/`:

- `run-result.json`: 상태, 실제 소요 시간, 모델 사용량, 예산, 산출물 해시, 한계
- `trace.json`: LangGraph 단계별 경과 시간
- `retrieval.json`: 실제 반환 Chunk·Source·RetrievalRecord와 Evidence 연결
- `evidence.json`, `sources.json`: 원문 URL·로컬 위치·해시·페이지와 발췌
- `reviews.json`, `*-response.json`: 이 실행의 실제 모델 분석·생성·Judge 응답
- `report-context.json`, `report-pipeline.json`: 고정 맥락과 동일 draft에 대한 검증
- `draft.md`: 반환된 초안(검증 통과와 별개)
- `report.md`, `report.html`, `report.pdf`: 보고서와 실제 PDF 검증 통과 시에만 생성
- `rendered/`: 기존 renderer의 원본 산출물·레이아웃 관측

## 검증 구분

```bash
uv run pytest tests/unit/test_local_demo.py tests/unit/test_demo_budget.py tests/unit/test_demo_prepare.py tests/unit/test_demo_web.py tests/integration/test_demo_pipeline.py -q
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

단위/통합 테스트의 합성 자료·mock 모델 응답은 실제 기업 분석이나 실제 API 성공 증거가 아니다.
`test_demo_pipeline.py`는 mock 모델과 **실제 Chromium PDF**의 결합 검증이다.
별도의 실제 웹/API 시연 기록은 실행 디렉터리와 검증 보고서로 확인한다.
