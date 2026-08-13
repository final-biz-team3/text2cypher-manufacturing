# 0006. 프론트엔드 코드 품질 도구(ESLint+Prettier) 및 pre-commit/CI 도입

## 상태
확정 (2026-08-13)

## 한 줄 요약

> 프론트엔드에 Prettier를 도입해 ESLint(품질)와 포매팅 책임을 분리하고, 백엔드(Ruff+Black+mypy)와 대칭되도록 pre-commit 훅과 CI를 추가했다. 백엔드 담당은 별도로 있어 이 문서는 프론트엔드 범위만 다룬다.

---

## 배경 — 왜 이 결정이 필요했나

백엔드는 이미 Ruff(품질)+Black(포매팅)+mypy(타입)+pre-commit이 갖춰져 있었는데, 프론트엔드는 ESLint만 있고 포매팅 도구가 전혀 없었다. 그 결과:

- 코드 스타일 일관성이 도구가 아니라 "같은 사람이 계속 짜서 우연히 비슷했던 것"에 의존하고 있었다 — 컨트리뷰터가 늘어나면 스타일 논쟁이나 불일치가 바로 드러날 상황이었다.
- `.pre-commit-config.yaml`이 `backend/**`만 대상으로 해서, 프론트엔드는 커밋 시점에 아무 검사도 걸리지 않았다.
- 백엔드는 PEP8(스타일)과 타입 힌트(mypy) 검사가 분리된 도구 조합으로 강제되는데, 프론트엔드만 이 짝이 없는 비대칭 상태였다.

## 결정 — 무엇을 어떻게 하기로 했나

### 1. ESLint(품질)와 Prettier(포매팅) 책임 분리

- ESLint: 코드 품질(미사용 변수, React hooks 규칙, `typescript-eslint`를 통한 타입 인지 린팅 — PEP8 생태계에서 mypy가 맡는 포지션과 동일)
- Prettier: 포매팅(들여쓰기, 줄바꿈, 따옴표, 세미콜론)만 전담
- `eslint-config-prettier`를 ESLint 설정 마지막에 추가해 두 도구의 규칙이 충돌하지 않게 함(Black이 스타일을 전담하고 Ruff가 관여하지 않는 것과 같은 구조)
- `.prettierrc.json`: 기존 코드 스타일(세미콜론 없음, 싱글쿼트)에 맞춤. `endOfLine: "auto"`도 추가했는데, Windows에서 git이 체크아웃 시 CRLF로 변환하는 게 Prettier 기본값(LF)과 달라서 로컬 `format:check`가 전부 실패로 잡히는 문제를 막기 위함이다 — 이게 없으면 Windows 로컬과 Ubuntu CI 결과가 서로 달라진다.

### 2. pre-commit 로컬 훅 추가

`.pre-commit-config.yaml`에 `frontend/**` 대상 로컬 훅 2개 추가(`eslint --fix`, `prettier --write`) — 백엔드의 `ruff --fix` / `black` 패턴과 동일한 구조.

- `node frontend/node_modules/eslint/bin/eslint.js` / `node frontend/node_modules/prettier/bin/prettier.cjs`로 JS 엔트리포인트를 직접 호출한다. 처음엔 `frontend/node_modules/.bin/eslint`(셔뱅 스크립트)를 `language: system`으로 바로 실행했는데, Windows에서 `/bin/sh`를 못 찾아 실패했다.
- `pass_filenames`를 끄지 않아 스테이징된 파일만 정확히 처리한다. 처음엔 전체 디렉토리를 무조건 다시 쓰게 했다가, 다른 미스테이징 변경이 있는 상태에서 커밋하면 pre-commit의 stash 격리와 충돌해서 커밋 자체가 실패하는 걸 발견해 고쳤다.

### 3. CI(`frontend-code-quality.yml`) 추가

백엔드 `code-quality.yml`과 대칭되는 워크플로우를 추가해 `frontend/**` 변경 시 `npm run lint` + `npm run format:check`을 PR에서 강제한다. pre-commit은 `--no-verify`로 우회 가능하고 로컬에 설치 안 했으면 애초에 안 걸리므로, PR 화면에서 실제로 보이는 관문이 필요했다.

`npm run build`는 이번 범위에서 제외했다 — `dev` 브랜치에 남아있는 원본 Vite 스캐폴드가 존재하지 않는 `./assets/hero.png`를 import하는 기존 버그가 있어서, 지금 CI에 넣으면 이 PR과 무관하게 무조건 빨간불이 뜬다. 이 버그를 고치는 다른 브랜치(`feat/kg-dashboard-ui-shell`)가 `dev`에 머지되면 build 스텝을 추가한다.

### 4. 기존 파일 최초 재포매팅은 별도 커밋으로 분리

Prettier를 처음 켜면 기존 파일 전체가 한 번에 재포매팅되는데, 이 diff를 설정 변경 커밋과 섞지 않고 "Style: Prettier 최초 적용" 단독 커밋으로 분리했다 — 리뷰어가 "이 커밋은 로직 변경 없음"을 한눈에 확인할 수 있게 하기 위함이다.

## 검토했으나 채택하지 않은 대안

**Prettier 없이 그대로 두기.** ESLint의 최신 `recommended` 프리셋은 스타일 규칙을 다루지 않는다(포매팅은 Prettier가 맡는 게 현재 생태계 표준). 이대로 두면 스타일 일관성이 계속 우연에 의존하게 되고, 팀 프로젝트라 다른 사람이 프론트엔드에 붙을 가능성을 고려하면 리스크가 더 커진다.

**pre-commit만 하고 CI는 생략.** pre-commit은 로컬 도구를 설치해야 걸리고 `--no-verify`로 우회 가능해서, PR 리뷰어 입장에서는 실제로 지켜졌는지 확인할 방법이 없다. 백엔드도 pre-commit+CI 둘 다 갖추고 있어서, 프론트만 CI를 생략하면 비대칭이 남는다.

**pre-commit 훅에서 `pass_filenames: false`로 디렉토리 전체를 무조건 재포맷.** mypy 훅처럼 프로젝트 전역 검사가 필요한 경우엔 맞는 방식이지만, ESLint/Prettier는 파일 단위 검사라 이 방식을 쓸 이유가 없었고, 실제로 다른 미스테이징 변경과 충돌하는 버그가 나서 기각했다.

**CI에 `npm run build`도 즉시 포함.** 원칙적으로는 포함하는 게 맞지만, 이 PR과 무관한 기존 버그 때문에 지금 넣으면 항상 빨간불이 떠서 CI 자체의 신뢰도를 떨어뜨린다. 버그가 해소된 뒤 추가하는 게 순서가 맞다고 판단했다.

## 결과 및 트레이드오프

- 프론트엔드도 이제 pre-commit(로컬)+CI(원격) 이중 강제 체계를 갖춰 백엔드와 대칭이 맞다.
- 최초 Prettier 적용으로 기존 파일 전체가 재포매팅되어 git blame이 한 커밋에서 끊긴다(내용 변경은 없음, 별도 커밋으로 분리해 영향 최소화).
- CI에 아직 `npm run build`(타입 에러·빌드 실패 검출)가 없어서, lint/format 문제는 걸러지지만 타입 에러나 번들링 실패는 PR 단계에서 아직 안 걸린다.
- TODO: `feat/kg-dashboard-ui-shell` 머지로 `hero.png` 버그가 없어지면 CI에 `npm run build` 스텝 추가.

## 확실하지 않은 부분

- pre-commit 훅(`node frontend/node_modules/...` 직접 호출 방식)이 macOS/Linux 환경에서도 문제없이 동작하는지는 아직 검증 전이다 — 지금까지는 Windows 환경에서만 확인했다.
- CI의 Node 버전(24)이 팀원 로컬 환경과 항상 일치하는지는 `.nvmrc` 같은 버전 고정 파일이 없어 아직 느슨하게 맞춰져 있다.
