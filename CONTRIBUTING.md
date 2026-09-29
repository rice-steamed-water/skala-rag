# 협업 규칙

팀원이 GitHub에서 서로 겹치지 않게 병렬로 작업하기 위한 최소 규칙이다. 코딩 에이전트용 요약은 [AGENTS.md](AGENTS.md), 구현 내용은 [docs/README.md](docs/README.md)에 있다.

**이슈 1개 = 담당자 1명 = 브랜치 1개 = PR 1개.** `main`은 PR 병합으로만 바뀐다.

## main 보호

- `main`에 직접 commit·push하지 않는다. 모든 변경은 브랜치 → PR → 병합으로 들어간다.
- 이 저장소는 GitHub Free의 private 저장소라 서버에서 브랜치 보호를 걸 수 없다. 규칙은 각자 지킨다.
- 실수로 `main`에 push했다면 force push로 되돌리지 않는다. 팀에 알리고, 되돌려야 하면 `git revert` 커밋을 브랜치에서 만들어 PR로 올린다.

## 브랜치

GitHub Flow를 쓴다. `main` 하나와 짧게 사는 작업 브랜치만 둔다.

- 이름: `<type>/<이슈번호>-<slug>` — 예: `feat/12-rag-retriever`
- `type`: `feat` 기능 · `fix` 버그 · `docs` 문서 · `test` 테스트 · `refactor` 동작 변경 없는 구조 개선 · `chore` 설정·의존성·기타
- `slug`: 영어 소문자 kebab-case, 짧게
- 항상 최신 `origin/main`에서 만들고, 이슈 하나가 끝나면 병합한다.
- `main` 변경 반영은 자기 브랜치에서 `git merge origin/main` 또는 `git rebase origin/main` 후 `git push --force-with-lease`. force push는 자기 브랜치에만 한다.

## 이슈로 일하기

### 1. 만들기 전에 검색

```bash
gh issue list --state all --search "retriever"
```

같은 작업이 이미 있으면 새로 만들지 않고 그 이슈에 코멘트한다.

### 2. 이슈 작성

- `작업` 또는 `버그` 템플릿을 쓴다. 한 사람이 PR 하나로 끝낼 크기로 쪼갠다.
- 라벨 `wp:N`·`type:*`와 마일스톤을 지정한다([라벨과 마일스톤](#라벨과-마일스톤)).
- 먼저 끝나야 하는 이슈가 있으면 `선행 이슈`에 적는다.

### 3. 선점 — assignee

assignee가 곧 "내가 하고 있다"는 표시다.

```bash
gh issue edit 12 --add-assignee @me
gh issue view 12            # assignee가 본인 1명인지 확인
```

- assignee가 **비어 있는** 이슈만 가져간다.
- 두 명이 거의 동시에 지정됐으면 이슈 타임라인에서 먼저 지정한 사람이 맡고, 나머지는 스스로 해제한다.
- 담당자가 있는 이슈를 하고 싶으면 코멘트로 요청하고, 담당자가 해제한 뒤 가져간다.
- assignee가 없는 이슈로 브랜치를 만들지 않는다.

### 4. 착수 — 브랜치와 Draft PR

선점하면 바로 브랜치를 만들고 Draft PR을 연다. 다른 팀원이 진행 상황과 바뀌는 파일을 볼 수 있다.

```bash
git switch main && git pull --ff-only
git switch -c feat/12-rag-retriever
git commit --allow-empty -m "chore: start #12"   # 아직 바꾼 파일이 없을 때
git push -u origin HEAD
gh pr create --draft                               # 템플릿이 열린다
```

GitHub는 변경이 없는 브랜치로 PR을 열 수 없어서 첫 커밋이 필요하다. PR 본문에 `Closes #12`를 넣으면 병합할 때 이슈가 자동으로 닫힌다.

### 5. 진행 중

- 막히면 이슈에 `blocked` 라벨을 달고, 무엇을·누구를 기다리는지 코멘트한다. 풀리면 라벨을 뗀다.
- 선행 이슈가 끝나지 않았으면 합의된 fixture로 먼저 진행하거나 `blocked`로 둔다.
- 그만둘 때는 이유를 코멘트하고 assignee를 해제한 뒤 Draft PR을 닫는다. 브랜치는 남겨 다음 담당자가 이어받을 수 있게 한다.

### 6. 완료

```bash
gh pr ready 34              # Draft → Ready
gh pr merge 34              # 병합 방식은 아래 참고
```

## 라벨과 마일스톤

| 라벨 | 의미 |
| --- | --- |
| `wp:1` ~ `wp:6` | 작업 패키지. [delivery §1](docs/implementation/delivery.md) |
| `type:feat` `type:fix` `type:docs` `type:test` `type:refactor` `type:chore` | 브랜치 `type`과 같다 |
| `blocked` | 외부 요인으로 진행할 수 없음. 이유는 코멘트에 |

| 마일스톤 | 내용 ([delivery §3](docs/implementation/delivery.md)) |
| --- | --- |
| `M0` | 공통 계약과 정책 합의 |
| `M1` | fixture 기반 전체 세로 흐름 |
| `M2` | 실제 수집과 RAG 연결 |
| `M3` | 전체 live 평가와 검증 |
| `M4` | 재현·제출 |

## PR과 병합

- PR 템플릿을 채운다. 검증 칸에는 실제로 실행한 명령과 결과를 적고, 실행하지 못한 것은 이유를 적는다.
- 병합 방식은 squash, merge commit, rebase 모두 허용한다.
- 리뷰는 선택이다. 작성자가 직접 병합할 수 있다. 리뷰를 받고 싶으면 `gh pr edit 34 --add-reviewer <github-id>`로 요청하고, 요청했다면 중요한 지적을 해결한 뒤 병합한다.
- CI는 아직 없다. 코드 PR은 병합 전에 [개발 환경](#개발-환경)의 ruff·pytest를 로컬에서 통과시킨다.
- 충돌은 PR 작성자가 자기 브랜치에서 해결한다.
- 완료 정의는 [delivery §6](docs/implementation/delivery.md)을 따른다.

## 폴더 구조와 담당 WP

```text
skala-rag/
├── README.md                    # 최종 실행 안내·발표 기준
├── AGENTS.md                    # 코딩 에이전트 규칙
├── CLAUDE.md                    # AGENTS.md import
├── CONTRIBUTING.md              # 이 문서
├── pyproject.toml               # Python·의존성·ruff·pytest 설정
├── uv.lock
├── .env.example                 # 비밀 없는 설정명
├── .github/                     # 이슈·PR 템플릿
├── configs/                     # 승인 정책·모델·예산·보고서 설정
├── docs/
│   ├── README.md                # 구현 가이드 진입점
│   ├── implementation/          # 팀 설계 문서
│   └── raws/                    # 수정하지 않는 원문
├── src/skala_rag/
│   ├── contracts/               # 공통 DTO·State·catalog 타입
│   ├── graph/                   # wiring·router·reducer·controller
│   ├── agents/                  # 조사 Agent와 영역 평가 노드
│   ├── tools/                   # Web/API adapter·budget wrapper
│   ├── rag/                     # load·chunk·index·retrieve
│   ├── scoring/                 # 순수 산술·정책 판단
│   ├── reporting/               # draft·validator·judge·PDF
│   ├── prompts/                 # 버전 관리되는 prompt
│   └── cli.py                   # runner 진입점
├── data/
│   ├── manifests/               # 공개 가능한 corpus metadata
│   └── local/                   # 원문·index (git 제외)
├── tests/
│   ├── fixtures/                # 가상/재배포 허용 자료; 출처 표기
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   └── evals/                   # retrieval·report 평가
└── outputs/                     # run별 산출물 (git 제외)
```

| 경로 | 담당 | 비고 |
| --- | --- | --- |
| `pyproject.toml` `uv.lock` `.env.example` `cli.py` | WP1 | 의존성 추가는 각 WP가 `uv add`로 하고 PR에 적는다 |
| `contracts/` `graph/` | WP1 | 공통 타입 변경은 소비하는 WP와 먼저 합의하고 fixture를 함께 바꾼다([delivery §1](docs/implementation/delivery.md)) |
| `agents/` `prompts/` | 해당 노드를 맡은 WP | WP2 조사 · WP3 근거 · WP4 Founder/Market/Technology/Moat · WP5 Traction/Deal Terms |
| `tools/` | adapter를 쓰는 WP | budget wrapper는 WP1 |
| `rag/` `data/manifests/` | WP3 | |
| `scoring/` | WP5 | |
| `reporting/` `README.md` | WP6 | |
| `configs/` | 설정 종류별 | 점수 정책 WP5 · 예산 WP1 · 임베딩·코퍼스 WP3 · 보고서 WP6 |
| `tests/fixtures/` | WP1 공통 fixture | 영역별 rubric·점수 fixture는 해당 WP |
| `tests/unit/` `contract/` `integration/` | 테스트 대상 모듈의 WP | |
| `tests/evals/` | WP3 retrieval · WP6 report | |
| `docs/implementation/` `.github/` `AGENTS.md` `CONTRIBUTING.md` | 전원 | 규칙 변경은 이슈에서 합의한 뒤 PR |
| `docs/raws/` | — | 읽기 전용 |

다른 WP 경로를 바꿔야 하면 그 WP 담당자에게 이슈나 PR 코멘트로 먼저 알린다.

## 개발 환경

패키지·실행은 [uv](https://docs.astral.sh/uv/getting-started/installation/), lint·포맷은 ruff, 테스트는 pytest를 쓴다.

```bash
uv sync                          # .venv 생성과 의존성 설치
uv run ruff check .              # lint
uv run ruff format .             # 포맷 (PR 전에는 --check)
uv run pytest                    # 테스트
uv add <패키지>                   # 런타임 의존성
uv add --dev <패키지>             # 개발 의존성
```

- `pyproject.toml`, Python 버전(`requires-python`), ruff·pytest 설정은 WP1이 첫 코드 PR에서 만든다. 그 전에는 위 명령이 동작하지 않는다.
- 패키지는 uv로만 설치한다. 의존성을 바꾼 PR은 `pyproject.toml`과 `uv.lock`을 함께 커밋한다. `uv.lock`이 충돌하면 `main`을 반영한 뒤 `uv lock`으로 다시 만든다.
- unit/contract 테스트는 네트워크 없이 돈다. live 테스트 규칙은 [delivery §4](docs/implementation/delivery.md)를 따른다.
- GitHub Actions CI는 코드가 생긴 뒤 추가한다.

## 커밋하지 않는 것

`.env`와 API key, `data/local/`, `outputs/`, 생성된 index, 재배포할 수 없는 원문. 대부분은 [.gitignore](.gitignore)가 막지만 `git add` 전에 `git status`로 확인한다.
