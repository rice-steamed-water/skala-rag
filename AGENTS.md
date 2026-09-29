# AGENTS.md

Physical AI / Robotics 스타트업 투자 조사·평가용 LangGraph Multi-Agent RAG. 수업 과제다. 구현 전에 [docs/README.md](docs/README.md)와 [공통 데이터 계약](docs/implementation/contracts.md)을 읽는다. 사람용 협업 규칙 전문은 [CONTRIBUTING.md](CONTRIBUTING.md)에 있다.

## 작업 흐름

1. **이슈 1개가 작업 1개다.** 요청받은 GitHub 이슈 번호를 확인한다. 이슈가 없거나, 사용자가 아닌 다른 사람이 assignee라면 착수하지 않고 사용자에게 알린다.
2. **브랜치:** 최신 `origin/main`에서 `<type>/<이슈번호>-<slug>`를 만든다. `type`은 `feat|fix|docs|test|refactor|chore`, `slug`는 영어 소문자 kebab-case.
3. **commit·push는 그 브랜치에만 한다.** `main`은 PR 병합으로만 바뀐다. `main`에 직접 commit·push하지 않는다. force push는 자기 작업 브랜치에 `--force-with-lease`로만 한다.
4. **PR:** [PR 템플릿](.github/pull_request_template.md) 양식으로 본문을 쓰고 `Closes #<이슈번호>`를 넣는다. 진행 중이면 Draft로 연다. 병합은 사용자가 요청할 때만 한다.
5. **막히면** 이슈에 `blocked` 라벨을 달고 이유와 필요한 것을 코멘트한 뒤 사용자에게 알린다.

## 범위

- 이슈의 WP가 담당하는 경로만 수정한다([폴더 구조와 담당 WP](CONTRIBUTING.md#폴더-구조와-담당-wp)). 다른 WP 경로나 `contracts/`·`graph/` 변경이 필요하면 멈추고 사용자에게 제안한다.
- `docs/raws/`는 읽기 전용이다.
- [결정 목록](docs/implementation/decisions.md)의 `OPEN` 항목은 사용자에게 제안하고, 승인 전까지 fixture·인터페이스까지만 구현한다. 코드 기본값으로 결정을 대신하지 않는다.

## 명령

`pyproject.toml`이 생긴 뒤부터 동작한다. 패키지는 uv로만 설치한다.

```bash
uv sync
uv run ruff check .
uv run ruff format .
uv run pytest
uv add <패키지>          # 개발 의존성은 --dev; pyproject.toml과 uv.lock을 함께 커밋
```

## 완료 보고

- PR 검증 칸에 실제 실행한 명령과 결과를 적는다. 실행하지 못한 테스트·API는 이유를 적는다. fixture 결과를 실측으로 표시하지 않는다([delivery §6](docs/implementation/delivery.md)).
- `.env`·API key, `data/local/`, `outputs/`, 생성된 index, 재배포 불가 원문은 커밋에서 제외한다.
