# skala-rag

Physical AI / Robotics 스타트업의 투자 조사·평가를 위한 수업용
LangGraph Multi-Agent RAG 프로젝트다. 실제 투자 실행 시스템은 아니다.

현재는 설치 가능한 `src/skala_rag` 패키지와 네트워크 호출 없는 검증이 있으며, #5의 구조 DTO
(`RunInput`·후보·Source/Chunk/Evidence·retrieval/coverage bundle)와 #7의 `InvestmentState`·
`create_initial_state`가 포함된다. 이는 정책 계산이 없는 저장/검증 및 초기 state 범위다. Graph/reducer wiring, CLI, live RAG, 평가·점수 계산 및 보고서 출력은 구현되지 않았다. 새 구현 목표는 사용자 제공
[설계 v3 보존본](docs/design/design-v3.html)이며, 이전 설계와의 차이 및
main·미병합 PR 기준은 [v3 정합화 기록](docs/implementation/design-v3-alignment.md)에 있다.
v3의 명시 목표와 팀의 구현 정책 승인은 별개다. #3에는 D01–D06·D08의 **기존 baseline** 승인 기록이 있으나 v3의 selector·N/A·Warning 대체 세부는 별도 `v3-OPEN`이다. [결정 목록](docs/implementation/decisions.md)은 이 경계를 추적하며, 미정 정책·최종 모델·provider를 실행 기본값으로 정하지 않는다.

## 설치

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
| `langgraph` | 기존 Graph 패키지 import 검증; 실제 workflow는 미구현 |
| `pydantic` | 기존 검증 도구 import; 프로젝트 DTO·정책은 미구현 |
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

설치 후 아래 명령으로 패키지 골격을 검증한다. 설치 및 아래 명령은
로컬에서 실제 실행해 확인했으며, 애플리케이션 실행 명령은 아직 없다.

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv lock --check
.venv/bin/python -I -m pytest
```

로컬 검증 환경은 Python 3.11.9이며 `uv sync --python 3.11.9`도 확인했다.
호환성 테스트는 가상 문자열, `MockTransport`의 `fixture.invalid` 응답,
가짜 dotenv `StringIO`, 빈 PDF `BytesIO`만 사용한다. 네트워크 연결·모델 다운로드·
실제 기업 자료·비밀 설정 없이 설치된 공개 API를 검증하며 운영 RAG 성능 검증은 아니다.

## 구조와 문서

- `src/skala_rag/contracts/`: #5 구조 DTO와 #7 state factory; 다른 `graph`, `agents`, `tools`, `rag`, `scoring`, `reporting`, `prompts`는 업무 흐름 미구현
- `tests/contract/`, `tests/fixtures/contracts.json`: 구조 DTO/state 검증 fixture·tests; 테스트 총수는 실행 결과로만 보고
- `tests/unit/`, `tests/integration/`: 각 작업의 별도 검증 범위
- [구현 가이드](docs/README.md): 목표와 승인 전 설계
- [협업 규칙](CONTRIBUTING.md): 이슈·브랜치·개발 환경 규칙
