# skala-rag

Physical AI / Robotics 스타트업의 투자 조사·평가를 위한 수업용
LangGraph Multi-Agent RAG 프로젝트다. 실제 투자 실행 시스템은 아니다.

[#35 전환 승인](https://github.com/rice-steamed-water/skala-rag/issues/35#issuecomment-5902877317)에 따라 새 구현은 **v3**를 따른다. baseline 승인 기록·코드는 호환성/이력으로 보존한다. 후속 [#82 운영 승인](https://github.com/rice-steamed-water/skala-rag/issues/82)은 Missing/N/A·0분모·최종 selector·exact 점수 경계·재조사 회계·completed Warning/CLI2를 확정했다. [사전 선정 승인](https://github.com/rice-steamed-water/skala-rag/issues/35#issuecomment-5903574761)은 **평가 전 조사·평가 대상 집합을 무작위로 선정**하는 것이며 단순 순서 shuffle도 최종 selector의 무작위 변경도 아니다. dedup·Eligibility는 유지한다. 새 후보 수·난수/seed·보충 선정, rubric rule/품질·Evidence gate·PDF 상세·provider/corpus/live 예산은 OPEN으로 추적한다. 승인과 구현 가용성은 [결정 목록](docs/implementation/decisions.md)에서 구별한다.

## 설치

pinned 통합 기준 `906312a`에는 baseline DTO/State/reducer/catalog/점수·재무 helper·DTO adapter뿐 아니라 #17 발견/Normalize, #18 fixture 조사·Eligibility, #19 GuardedRetriever/EvidenceCollector, #22 dimension 평가 wrapper, #23 fixture 후보 Graph, #26 ReportContext, #27 baseline Structural Validator 및 #73/PR #74의 독립 `contracts.v3` 구조 DTO가 있다. #23은 baseline 첫 추천 인계이며 v3 전 후보 selector가 아니다. v3 구조 DTO는 계산·selector·0분모/Warning controller·State/Graph 연결을 실행하지 않는다. CLI·보고서 생성/Judge·실 PDF·live RAG는 여전히 목표다.

Python 3.11 이상과 [uv](https://docs.astral.sh/uv/getting-started/installation/)가
필요하다. 저장소 루트에서 실행한다.

```bash
uv sync
```

런타임 직접 의존성은 아래 7개뿐이며 개발 도구는 `ruff`, `pytest`뿐이다.
Python `>=3.11`을 유지하며 uv가 해석한 호환 버전을 `uv.lock`에 기록한다.
tutorial의 과거 하한 버전이나 전체 의존성 목록은 복사하지 않았다.
`.env.example`은 향후 설정 이름 안내용이며 현재 코드에서 읽지 않는다.
검증에는 API key나 `.env`가 필요하지 않다.

## 의존성 선택과 경계

| 직접 의존성 | 용도와 제한 |
| --- | --- |
| `langgraph` | State reducer 및 fixture 후보 Graph; v3 workflow 연결은 미구현 |
| `pydantic` | baseline 및 독립 v3 구조 DTO 검증; v3 정책 실행은 아님 |
| `langchain-core` | 중립적인 Document·message·prompt 인터페이스; 가상 Document·HumanMessage와 ChatPromptTemplate의 변수 포맷팅·invoke 결과를 검증, 모델 client 없음 |
| `langchain-text-splitters` | 문서 분할 유틸리티; 명시적 테스트 전용 크기·overlap은 운영 chunk 정책 선택이 아님 |
| `httpx` | HTTP client; MockTransport만 검증하며 특정 API·provider를 선택하지 않음 |
| `python-dotenv` | `dotenv_values(stream=StringIO(...))`로 가짜 값만 명시적 파싱; 환경 변경·자동 `load_dotenv()`·실제 `.env` 읽기 없음 |
| `pypdf` | PDF 읽기와 페이지 metadata 확인; BytesIO 빈 페이지 write/read만 검증, renderer·OCR·투자 보고서 생성이 아님 |

`langchain-core`와 `httpx`는 이미 `langgraph`의 전이 의존성이었다.
호환성 테스트에서 명시적으로 사용하고 향후 공통 경계에서도 사용할 유틸리티이므로
직접 의존성으로 선언했다. 애플리케이션 기능을 추가한 것은 아니다.

tutorial의 omnibus/community/experimental, deepagents·supervisor·swarm·MCP는
현재 골격에 필요하지 않아 보류한다. provider 통합·Tavily는 서비스와 접근 조건 검토 전,
FAISS·embedding·PyTorch는 모델·저장소 선택 전, Postgres·Redis는 checkpoint 설계 전이므로
추가하지 않는다. notebook/Jupyter, 금융 데이터, plotting, RAG 평가 패키지와
추가 Office/PDF 도구도 아직 구현·검증할 사용 경로가 없어 보류한다.
[공통 계약](docs/implementation/contracts.md)과
[데이터·RAG 설계](docs/implementation/data-rag.md)의 경계를 따르며,
[결정 목록](docs/implementation/decisions.md)의 OPEN 상태는 바꾸지 않는다.
특히 D07(embedding·vector store), D09(renderer), D13(페이지 산정)은 승인하지 않았다.
`pypdf` 페이지 metadata 검증은 D13 산정 정책이나 PDF 렌더링 품질 검증이 아니다.

## Usage

설치 후 아래 명령으로 패키지·구조 DTO·fixture를 검증한다. 초기 패키지 작업에서 확인한 명령이며,
각 PR은 해당 head에서 실행한 명령·환경·결과를 별도로 기록한다. 애플리케이션 실행 명령은 아직 없다.

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv lock --check
.venv/bin/python -I -m pytest
```

초기 패키지 작업(#4)의 검증 환경은 Python 3.11.9이며 당시 `uv sync --python 3.11.9`도 확인했다.
호환성 테스트는 가상 문자열, `MockTransport`의 `fixture.invalid` 응답,
가짜 dotenv `StringIO`, 빈 PDF `BytesIO`만 사용한다. 네트워크 연결·모델 다운로드·
실제 기업 자료·비밀 설정 없이 설치된 공개 API를 검증하며 운영 RAG 성능 검증은 아니다.

## 구조와 문서

- `src/skala_rag/contracts/`: #5·#6 baseline DTO·결정적 ID·State와 별도 `v3.py` 구조 DTO
- `src/skala_rag/scoring/catalog.py`, `configs/scoring.draft.json`: #9 fixture 전용 draft catalog 로더·설정; v3 집계·판정 구현 아님
- `src/skala_rag/graph/reducers.py`: ID 병합·충돌 검증 및 State 연결; `graph/candidates.py`에 baseline fixture 후보 Graph
- `src/skala_rag/scoring/aggregate.py`, `decide.py`: baseline 집계·판정 순수 함수; v3 계약과 다름
- `src/skala_rag/scoring/summary.py`: baseline 계산값을 #6 ScoreSummary·InvestmentDecision으로 연결하는 adapter
- `src/skala_rag/scoring/finance.py`: 재무 파생값·검증 helper; rating/rubric 승인과 별개
- `agents`, `tools`, `rag`, `reporting`: fixture 조사/수집·평가 wrapper·보고서 context/구조 검증; live·생성/Judge·PDF는 미구현
- `tests/contract/`, `tests/fixtures/contracts.json`: 구조 DTO/state 검증 fixture·tests; 테스트 총수는 실행 결과로만 보고
- `tests/unit/`, `tests/integration/`: 각 작업의 별도 검증 범위
- [구현 가이드](docs/README.md): 승인된 v3 방향과 미결정 세부 설계
- [협업 규칙](CONTRIBUTING.md): 이슈·브랜치·개발 환경 규칙
