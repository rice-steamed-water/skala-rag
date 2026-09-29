# skala-rag

Physical AI / Robotics 스타트업의 투자 조사·평가를 위한 수업용
LangGraph Multi-Agent RAG 프로젝트다. 실제 투자 실행 시스템은 아니다.

현재는 설치 가능한 `src/skala_rag` 패키지 골격과 네트워크 호출 없는
import smoke test만 제공한다. 애플리케이션, CLI, live RAG, 평가 및
보고서 출력은 구현되지 않았다. 설계 결정은 모두 OPEN 상태이며
정책·모델·provider 기본값을 정하지 않는다.

## 설치

Python 3.11 이상과 [uv](https://docs.astral.sh/uv/getting-started/installation/)가
필요하다. 저장소 루트에서 실행한다.

```bash
uv sync
```

런타임 직접 의존성은 `langgraph`, `pydantic`이며 개발 도구는 `ruff`,
`pytest`다. `uv.lock`에 의존성 해석 결과를 기록한다.
`.env.example`은 향후 설정 이름 안내용이며 현재 코드에서 읽지 않는다.
검증에는 API key나 `.env`가 필요하지 않다.

## Usage

설치 후 아래 명령으로 패키지 골격을 검증한다. 설치 및 아래 명령은
로컬에서 실제 실행해 확인했으며, 애플리케이션 실행 명령은 아직 없다.

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## 구조와 문서

- `src/skala_rag/`: contracts, graph, agents, tools, rag, scoring,
  reporting, prompts의 빈 하위 패키지
- `tests/unit/`: 패키지 및 의존성 import smoke test
- `tests/contract/`, `tests/integration/`, `tests/fixtures/`: 빈 골격
- [구현 가이드](docs/README.md): 목표와 승인 전 설계
- [협업 규칙](CONTRIBUTING.md): 이슈·브랜치·개발 환경 규칙
