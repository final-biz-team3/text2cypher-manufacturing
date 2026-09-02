# 로그인 · 권한(admin/user) 설계

## 상태

재설계 확정 (2026-08-25) — httpOnly 쿠키 방식을 폐기하고 Bearer 토큰 방식으로 전환(사용자 지시). 이전 구현(15개 커밋)은 전부 되돌리고 이 문서 기준으로 재구현한다. 문서 커밋은 하지 않음 (사용자 지시)

## 배경

현재 `/chat`([backend/api/chat.py](../../../backend/api/chat.py))은 인증 없이 누구나 호출 가능하고, 사용자·세션 개념이 전혀 없다. 다음 단계로 대화기록을 DB에 영속 저장하는 기능(history)을 붙일 예정인데, history 테이블이 "누가 물었는지"를 참조(FK)하려면 사용자 개념이 먼저 있어야 한다. 이 문서는 그 선행 작업인 로그인·권한 기능만 다룬다. history 설계는 별도 스펙·브랜치로 진행한다.

프론트엔드는 로그인 화면 목업(스크린샷)과 참고 구현 코드(zip)를 전달받았다. 참고 코드는 ADR [0003](../../adr/0003-tech-stack.md)이 원래 계획했으나 아직 설치되지 않은 axios/React Query/zod를 사용하고 있어 이번에 함께 도입한다. 토큰 저장 방식은 처음엔 httpOnly 쿠키로 갔었으나(보안상 더 안전) 사용자가 재검토 후 참고 코드와 동일한 Bearer 토큰 방식(sessionStorage + Authorization 헤더)으로 최종 결정했다. 5회 실패 시 10분 잠금 안내는 이번에도 채택하지 않는다(아래 비목표 참고).

## 목표

- 고정 시드 계정 2개(admin, user)로 로그인/로그아웃
- 로그인 상태를 Bearer 토큰(JWT)으로 유지 — 로그인 응답 본문에 `access_token`을 담아 반환, 프론트가 `sessionStorage`에 저장하고 이후 요청마다 `Authorization: Bearer <token>` 헤더로 전송
- admin과 user를 구분하는 서버 측 권한 체크(`require_admin`) — 다음 단계 history 전체조회 API가 바로 재사용
- `/chat`을 로그인 사용자만 호출 가능하도록 보호
- 로그인 화면 UI를 전달받은 목업대로 구현

## 비목표 (이번 스코프 제외)

- 회원가입 화면 — 계정은 서버 기동 시 시드로만 생성
- 로그인 실패 횟수 카운팅 및 계정 잠금(5회/10분) — 사용자 지시로 제외. UI에도 관련 문구를 넣지 않는다
- 비밀번호 재설정, "비밀번호를 잊으셨나요" 실제 동작 — 목업엔 링크만 존재, 클릭 동작 없음
- 리프레시 토큰 — 만료 시 재로그인만 요구
- `/graph/stats` 같은 새 엔드포인트 — 로그인 화면 우측 패널 수치는 정적 값으로 표시(기존 Dashboard.tsx의 `CONNECTED`/`CONNECTION_ENDPOINT` 패턴과 동일)
- 대화기록(history) 저장 자체 — 다음 스펙에서 다룸

## 1. 백엔드

### 1-1. 데이터베이스

`app` 스키마를 신설해 `production.*`(AdventureWorks 비즈니스 데이터)와 분리한다.

```sql
CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE IF NOT EXISTS app.users (
    id SERIAL PRIMARY KEY,
    username VARCHAR NOT NULL UNIQUE,
    password_hash VARCHAR NOT NULL,
    role VARCHAR NOT NULL CHECK (role IN ('admin', 'user')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`backend/main.py`의 `lifespan`에서 `pg_trgm` 확장 생성과 같은 자리에 위 DDL과 시드 계정 삽입을 idempotent하게 실행한다(`CREATE TABLE IF NOT EXISTS`, 계정은 `ON CONFLICT (username) DO NOTHING`). 계정 자격증명은 환경변수로 관리한다: `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `USER_USERNAME`, `USER_PASSWORD`.

### 1-2. 비밀번호 · 토큰

- 해싱: `bcrypt`
- 토큰: `PyJWT`, HS256, payload `{sub: username, role, exp}`, 만료 12시간
- 서명 키: 환경변수 `JWT_SECRET_KEY`
- 신규 의존성(`backend/requirements.txt`에 추가): `bcrypt`, `PyJWT`

### 1-3. API

신규 파일 `backend/api/auth.py`:

| 메서드/경로 | 요청 | 응답 | 비고 |
|---|---|---|---|
| `POST /auth/login` | `{username, password}` | `{access_token, token_type: "bearer", username, role}` | 쿠키 없음 — 토큰은 응답 본문으로만 전달 |
| `POST /auth/logout` | - | `204` | Bearer 토큰은 서버가 상태를 들고 있지 않아(stateless) 무효화할 게 없다. 프론트가 `sessionStorage`를 지우는 것으로 로그아웃이 끝난다. 이 엔드포인트는 향후 토큰 블랙리스트 등을 붙일 자리로만 남겨둔다 |
| `GET /auth/me` | - | `{username, role}` | `Authorization` 헤더 없음/무효 → `401` |

로그인 실패(아이디 없음 또는 비밀번호 불일치) 시 `401`, 응답 본문은 `{"detail": "아이디 또는 비밀번호가 올바르지 않습니다"}`만 — 어느 쪽이 틀렸는지 구분하지 않는다. 시도 횟수·잠금 필드는 응답에 포함하지 않는다(비목표).

### 1-4. 인증 의존성

신규 파일 `backend/core/auth.py`:

- `get_current_user(authorization: str | None = Header(default=None)) -> CurrentUser`: `Authorization: Bearer <jwt>` 헤더를 파싱해 검증, 헤더 없음/형식 불일치/토큰 무효 시 `401` 발생
- `require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser`: `role != "admin"`이면 `403`

`backend/api/chat.py`의 `/chat` 라우트에 `Depends(get_current_user)`를 추가해 비로그인 접근을 차단한다.

## 2. 프론트엔드

### 2-1. 신규 의존성

`axios`, `@tanstack/react-query`, `zod` — ADR 0003에 계획돼 있었으나 미설치 상태였다. `react-router-dom`도 신규(현재 라우팅 없음, `App.tsx`가 `Dashboard`만 직접 렌더링).

### 2-2. 파일 구성

```
frontend/src/
├─ lib/
│  ├─ api.ts              # axios 인스턴스 + 요청 인터셉터(sessionStorage 토큰을 Authorization 헤더로 부착), login()/logout()/fetchMe()
│  └─ schemas.ts           # zod: LoginRequest/LoginResponse/LoginError/CurrentUser
├─ store/
│  ├─ useAuthStore.ts       # 실제 인증 상태: user, status, login(), logout(), checkAuth()
│  └─ useLoginPrefsStore.ts # 아이디 기억 체크박스만(localStorage persist), theme과 무관
├─ pages/
│  └─ LoginPage.tsx
├─ components/layout/
│  └─ LoginAsidePanel.tsx  # 우측 컨텍스트 패널(할 수 있는 것 / 스키마 노드 / 정적 수치)
└─ App.tsx                 # react-router-dom 라우팅 + ProtectedRoute 추가
```

기존 [TopBar.tsx](../../../frontend/src/components/layout/TopBar.tsx)를 로그인 화면에도 그대로 재사용한다(새 TopBar를 만들지 않음). `onNavigateHome`은 로그인 화면에서 no-op으로 전달.

`LoginAsidePanel`의 "연결된 그래프 스키마" 목록은 기존 [nodeColors.ts](../../../frontend/src/lib/nodeColors.ts)와 [SchemaSidebar.tsx](../../../frontend/src/components/layout/SchemaSidebar.tsx)가 쓰는 노드 정의를 재사용한다(별도 정의 추가하지 않음).

### 2-3. 인증 상태 흐름

- 앱 로드 시 `useAuthStore.checkAuth()`가 `sessionStorage`에 토큰이 있으면 `GET /auth/me` 호출(인터셉터가 Authorization 헤더 자동 부착) → 성공하면 `status: 'authenticated'`, 토큰이 없거나 실패(401)하면 `status: 'unauthenticated'`
- `ProtectedRoute`: `status === 'unauthenticated'`면 `/login`으로 리다이렉트, `'loading'`이면 로딩 표시
- 로그인 성공: `useAuthStore.login()`이 `POST /auth/login` 호출 → 응답의 `access_token`을 `sessionStorage`에 저장하고 `user` 세팅 후 원래 가려던 화면(또는 기본 화면)으로 이동
- 로그아웃: `sessionStorage`에서 토큰을 지우고 `user`를 `null`로, `/login`으로 이동 (백엔드 `/auth/logout` 호출은 선택 사항 — 상태를 들고 있지 않아 실질적 효과는 없지만 감사 로그 등 향후 확장을 위해 호출은 해둔다)

### 2-4. 로그인 화면 UI

목업/참고코드를 기반으로 하되 다음을 반영한다:

- 에러 배너는 "인증 실패 · 아이디 또는 비밀번호가 일치하지 않습니다"만 표시. 잠금 안내 문구·남은 시도 횟수 표시는 넣지 않는다
- 로그인 성공 시 `login()`이 반환한 `access_token`을 `sessionStorage.setItem('kg-access-token', ...)`으로 저장(참고코드와 동일한 키 이름 사용)
- 아이디 기억 체크박스: 체크 시 `useLoginPrefsStore`에 아이디만 localStorage 저장(비밀번호는 저장하지 않음) — 토큰과는 별개의 저장소(sessionStorage vs localStorage)

## 3. 에러 처리

| 상황 | 서버 응답 | 프론트 동작 |
|---|---|---|
| 아이디/비밀번호 불일치 | `401 {"detail": "..."}` | 로그인 폼에 에러 배너 표시 |
| 토큰 없음/만료된 상태로 보호된 API 호출 | `401` | 로그인 페이지로 리다이렉트, `sessionStorage` 토큰 제거 |
| admin 전용 API를 user가 호출 | `403` | (다음 단계 history 스펙에서 다룸) |

## 6. 보안 트레이드오프 (기록용)

httpOnly 쿠키 대비 Bearer+sessionStorage 방식은 토큰이 JS에서 접근 가능해 XSS 시 탈취 위험이 있다. 사용자가 이 트레이드오프를 인지한 상태에서 참고 코드와의 일관성 및 구현 단순성을 이유로 Bearer 방식을 최종 선택했다. CSRF 방어는 애초에 불필요해진다(쿠키를 안 쓰므로 브라우저가 자동으로 실어 보내지 않음).

## 4. 테스트

`backend/tests/api/test_auth.py`(신규): 로그인 성공/실패, `/auth/me` 인증됨/안됨, `require_admin`이 user 역할을 막는지. 작성만 하고 실행은 사용자가 `backend/venv`로 직접 진행.

프론트 테스트는 참고코드의 `LoginPage.test.tsx` 패턴을 따르되 위 2-4의 변경사항(잠금 UI 제거, 토큰 저장 없음)에 맞춰 조정한다.

## 5. 코드 스타일

주석은 코드가 무엇을 하는지만 짧게 남기고, 왜 그렇게 했는지는 적지 않는다.
