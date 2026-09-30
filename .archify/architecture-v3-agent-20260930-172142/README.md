# v3 실행 그래프 — #111

[HTML 열기](v3-agent.html) · [Archify candidate](candidate.json)

- 타입: `architecture`, static / showcase. 실행 경로와 데이터 경계를 함께 보여준다.
- 소스 고정: `12d37646bf1d34f0663522567efaf61f122410be`.
- 전체 흐름은 단일 LangGraph가 아니다. Python 후보 controller, 다섯 병렬 branch의 LangGraph, 순차 보고서 pipeline을 구별한다.
- 현재 CLI는 synthetic fixture 후보·평가 및 stub Generator/Judge와 **실제 PDF renderer**를 연결한다. live CLI는 차단된다. 이 문서 작업에서 애플리케이션 runtime/live API를 실행하지 않았다.
- 무작위 사전 대상 선정과 Coverage 재조사 점선은 승인 목표다. 현재 `run_candidates_v3`는 normalize/collect/freeze 주입 경계를 제공하지만 이 두 controller를 연결하지 않는다. 별도 Normalize API가 CLI에서 실행된다는 뜻도 아니다.
- `State → Evidence → Snapshot → branch result / dimension evaluation → ReportContext` 경계와 출처는 노드의 SRC 및 상세 카드에서 확인한다. README의 과거 pinned 구현 현황보다 이 그래프의 고정 revision 소스를 우선한다.
- 기본 READ는 그래프 중심이다. 상세 설명·구현/목표 tag는 LENS/노드 상세에서 확인한다. 한국어 설명, 고정 Viewer UI 및 HTML lang은 English fallback이다. 아래쪽은 정상 페이지 스크롤로 읽는다.

## 실제 검증 결과

Archify 3.0.1의 최종 `finalize` exit 0:

- validate **9/9 showcase**, composition **0 errors / 0 warnings**.
- deliver, strict provenance check, real-browser browser-check 모두 pass.
- 브라우저: light 1440×900 / 1600×1000 / 1920×1080 / 2048×1320 및 dark endpoint 검사. 가로 overflow 없음; 읽을 수 있는 세로 페이지 스크롤 허용.
- 별도 visual-check exit 0, light/dark endpoint PNG 네 장과 contact sheet 생성.
- 직접 확인한 캡처: 2048×1320 light와 1440×900 dark의 위쪽 viewport. 노드·주 경로를 확인했지만 오류/return과 fan-out/Join 주변은 조밀하다. **전체 화면·카드·상호작용에 대한 perceptual 승인으로 주장하지 않는다.**
- crossover 권고에 따라 위치/크기만 한 번 수정했다. 최종 receipt는 resolved crossovers 32, 권장보다 꺾임 많은 경로 12를 기록한다. 이 권고는 실패가 아니지만 시각적 단순성의 증거도 아니다.
- `update.noticeRequired=false` (installed/available 3.0.1).

최종 SHA-256:

```text
candidate 77bb6ac2838dea1186a511203ae60be765988f0eec84ffd95b50d347b246c49f
HTML      3e8316a6f0587c0608a161aa8280e313036e26e6e22df59f15a3d84563e1e819
```

## 재생성

저장소 루트에서 설치된 Archify CLI 경로를 지정한다. 패키지 설치나 runtime 코드 변경은 필요 없다.

```sh
ARCHIFY=/path/to/archify/bin/archify.mjs
DIR=.archify/architecture-v3-agent-20260930-172142
node "$ARCHIFY" finalize architecture "$DIR/candidate.json" "$DIR/v3-agent.html" \
  --repo-root "$PWD" --quality showcase --out-dir "$DIR/local-check" --json
```

`meta.output`은 위 HTML 상대 경로와 일치한다. source origin이 SSH라 `local-only` source links를 사용한다.

## 로컬 증거 위치 / 커밋 경계

다음 자동 receipt에는 로컬 절대경로가 포함된다. 원본을 변조하지 않고 이 폴더의 `.gitignore`로 Git 대상에서 제외했다. candidate/HTML에는 `/Users/` 로컬 절대경로가 없다.

- **현재 HTML에 대응하는 최종 receipt:** `review-2/v3-agent.finalize-summary.json`, `review-2/v3-agent.finalize.json`.
- **현재 browser receipt:** `review-2/v3-agent.browser-check.json`.
- provenance: `v3-agent.delivery.json`.
- captures: `visual-check/v3-agent.visual-check.json`, `visual-check/v3-agent.visual-check.html`, 같은 폴더의 PNG 네 장.
- 최상위 `v3-agent.finalize*.json` 및 browser receipt는 위치 수정 전 artifact 이력이며 현재 증거로 사용하지 않는다.

초기 workflow 후보는 교차/공유 통로 gate에 실패하여 납품하지 않았다. 동일 노드·방향·라벨·출처를 유지한 architecture 표현으로 전환했다. 실패 파일은 저장소 밖 scratch에 보존했다. 최종 HTML 통과와 초기 실패를 혼동하지 않는다.
