# 로그인 · 권한(admin/user) Implementation Plan (Bearer 토큰 재설계)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 고정 시드 계정(admin/user) 로그인, Bearer 토큰(JWT, sessionStorage 저장) 기반 세션 유지, 서버 측 role 체크, `/chat` 보호, 로그인 화면 UI 구현.

**Architecture:** 백엔드는 FastAPI + raw psycopg(ORM 없음, 기존 패턴 유지)로 `app.users` 테이블에 시드 계정을 두고 bcrypt+PyJWT로 인증한다. 로그인 성공 시 JWT를 응답 본문(`access_token`)으로 반환하고, 프론트는 이를 `sessionStorage`에 저장한 뒤 매 요청마다 axios 인터셉터가 `Authorization: Bearer <token>` 헤더로 자동 부착한다. 서버는 상태를 들고 있지 않다(stateless) — 로그아웃은 프론트가 토큰을 지우는 것으로 끝난다.

**Tech Stack:** FastAPI, psycopg3, bcrypt, PyJWT, pytest / React 19, Vite, TypeScript, axios, zod, react-router-dom, zustand

## 이전 설계에서 바뀐 점 (기록용)

이 기능은 처음엔 httpOnly 쿠키 방식으로 10개 태스크 전부 구현·리뷰·최종수정까지 마쳤으나, 사용자가 재검토 후 Bearer 토큰 방식(참고코드와 동일)으로 전환을 지시해 브랜치를 dev로 되돌리고 이 문서로 재작업한다. 바뀐 부분만 요약:
- 토큰 전달: `Set-Cookie` httpOnly → 응답 본문 `access_token` 필드
- 토큰 검증: `Cookie` 파싱 → `Authorization: Bearer <token>` 헤더 파싱
- 프론트 저장: 없음(쿠키가 브라우저가 자동 관리) → `sessionStorage`
- `/auth/logout`: 쿠키 삭제 → 사실상 no-op(토큰 무효화 수단이 없음, 프론트가 sessionStorage만 지움)
- axios: `withCredentials: true` 불필요 → 요청 인터셉터로 Authorization 헤더 부착

이전 구현에서 걸렸던 실제 버그(타이밍 사이드채널 anti-enumeration, pytest 파일명 충돌, mypy unused-ignore, JWT_SECRET_KEY 미검증)는 이번에도 똑같이 해당하므로 아래 태스크에 처음부터 반영했다.

## Global Constraints

- 로그인 실패 응답은 아이디/비밀번호 구분 없이 동일한 메시지 하나만 반환한다. `verify_password`는 아이디 존재 여부와 무관하게 항상 실행되어야 한다(타이밍 사이드채널로 계정 존재 여부가 새면 안 됨)
- 로그인 실패 횟수 카운팅·계정 잠금 기능 및 관련 UI 문구는 구현하지 않는다
- JWT는 HS256, payload `{sub, role, exp}`, 만료 12시간
- `JWT_SECRET_KEY`는 앱 시작 시 32자 이상인지 검증하고, 아니면 시작을 실패시킨다(fail-fast)
- 역할 값은 `admin`/`user` 두 가지만 사용한다
- 신규 코드의 주석은 무엇을 하는지만 짧게 적고 왜 그런지는 적지 않는다
- 계획 문서·스펙 문서 모두 git 커밋하지 않는다 (사용자 지시) — 코드 변경 커밋은 각 태스크의 Step "Commit"만 수행
- pytest 설정(`pyproject.toml`)에 `--import-mode=importlib`를 적용해 `backend/tests/api/test_auth.py`와 `backend/tests/core/test_auth.py`의 파일명 충돌을 처음부터 방지한다 (이전 구현에서 겪은 실제 버그)
- `bcrypt`/`PyJWT` import에 `# type: ignore`를 달지 않는다 — 대신 `.pre-commit-config.yaml`의 mypy 훅 `additional_dependencies`에 두 패키지를 추가해 로컬·CI mypy 환경을 일치시킨다 (이전 구현에서 겪은 실제 CI 실패)

---

## 파일 구성

**백엔드 신규**
- `backend/core/auth.py` — 비밀번호 해싱, JWT 발급/검증, `CurrentUser`, `get_current_user`/`require_admin` 의존성(Authorization 헤더 기반), `bootstrap_users`(테이블 생성+시드)
- `backend/api/auth.py` — `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`
- `backend/tests/api/test_auth.py`, `backend/tests/core/test_auth.py`

**백엔드 수정**
- `backend/main.py` — auth 라우터 등록, lifespan에서 `JWT_SECRET_KEY` 검증 + `bootstrap_users` 호출
- `backend/api/chat.py` — `/chat`에 `get_current_user` 의존성 추가
- `backend/requirements.txt` — `bcrypt`, `PyJWT` 추가
- `.pre-commit-config.yaml` — mypy 훅에 `bcrypt`, `PyJWT` 추가
- `pyproject.toml` — pytest `addopts`에 `--import-mode=importlib` 추가
- `.env.example`, `docker-compose.yml` — `JWT_SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `USER_USERNAME`, `USER_PASSWORD`
- `backend/tests/api/test_chat.py` — 인증 보호를 확인하는 HTTP 레벨 테스트 추가

**프론트엔드 신규**
- `frontend/src/lib/schemas.ts`, `frontend/src/lib/api.ts`
- `frontend/src/lib/schemaNodes.ts` — `Dashboard.tsx`에 있던 `SCHEMA_NODES`/`RELATIONSHIPS`를 공용 모듈로 추출
- `frontend/src/store/useAuthStore.ts`, `frontend/src/store/useLoginPrefsStore.ts`
- `frontend/src/pages/LoginPage.tsx`
- `frontend/src/components/layout/LoginAsidePanel.tsx`
- `frontend/src/components/ProtectedRoute.tsx`

**프론트엔드 수정**
- `frontend/src/App.tsx` — react-router-dom 라우팅
- `frontend/src/screens/Dashboard.tsx` — `SCHEMA_NODES`/`RELATIONSHIPS`를 `lib/schemaNodes.ts`에서 import + 로그아웃 연결
- `frontend/src/components/layout/TopBar.tsx` — 로그인 사용자/로그아웃 버튼(선택적 props)
- `frontend/package.json` — `axios`, `zod`, `react-router-dom` 추가

---

## Task 1: 비밀번호 해싱 · JWT 발급/검증 (`backend/core/auth.py` 1부)

**Files:**
- Create: `backend/core/auth.py`
- Test: `backend/tests/core/test_auth.py`

**Interfaces:**
- Produces: `hash_password(password: str) -> str`, `verify_password(password: str, password_hash: str) -> bool`, `CurrentUser(username: str, role: str)`(pydantic `BaseModel`), `create_access_token(username: str, role: str) -> str`, `decode_access_token(token: str) -> CurrentUser`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/core/test_auth.py`:

```python
"""비밀번호 해싱과 JWT 발급/검증 동작을 테스트한다."""

import pytest

from core.auth import (
    CurrentUser,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_password_and_verify_correct_password() -> None:
    password_hash = hash_password("s3cret!")
    assert verify_password("s3cret!", password_hash) is True


def test_verify_password_rejects_wrong_password() -> None:
    password_hash = hash_password("s3cret!")
    assert verify_password("wrong", password_hash) is False


def test_create_and_decode_access_token_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-at-least-32-characters-long")
    token = create_access_token("kim.quality", "admin")
    user = decode_access_token(token)
    assert user == CurrentUser(username="kim.quality", role="admin")


def test_decode_access_token_rejects_garbage_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-at-least-32-characters-long")
    with pytest.raises(Exception):
        decode_access_token("not-a-real-token")
```

(테스트 시크릿을 32자 이상으로 잡아 `InsecureKeyLengthWarning`을 처음부터 피한다 — 이전 구현에서 반복적으로 지적된 minor.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/core/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.auth'`

- [ ] **Step 3: 최소 구현 작성**

`backend/core/auth.py`:

```python
"""비밀번호 해싱, JWT 발급/검증, 로그인 사용자 정보를 다룬다."""

import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from pydantic import BaseModel

_ALGORITHM = "HS256"
_EXPIRE_HOURS = 12


class CurrentUser(BaseModel):
    username: str
    role: str


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def _secret_key() -> str:
    return os.environ["JWT_SECRET_KEY"]


def create_access_token(username: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=_EXPIRE_HOURS)
    payload = {"sub": username, "role": role, "exp": expire}
    return jwt.encode(payload, _secret_key(), algorithm=_ALGORITHM)


def decode_access_token(token: str) -> CurrentUser:
    payload = jwt.decode(token, _secret_key(), algorithms=[_ALGORITHM])
    return CurrentUser(username=payload["sub"], role=payload["role"])
```

`bcrypt`/`jwt` import에 `# type: ignore`를 달지 않는다 — 두 패키지 모두 `py.typed`를 배포하므로 필요 없다(Global Constraints 참고).

- [ ] **Step 4: 테스트 통과 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/core/test_auth.py -v`
Expected: PASS (4개)

- [ ] **Step 5: Commit**

```bash
git add backend/core/auth.py backend/tests/core/test_auth.py
git commit -m "feat: 비밀번호 해싱과 JWT 발급/검증 추가"
```

---

## Task 2: 인증 의존성 `get_current_user`/`require_admin` (Authorization 헤더 기반)

**Files:**
- Modify: `backend/core/auth.py`
- Test: `backend/tests/core/test_auth.py`

**Interfaces:**
- Consumes: Task 1의 `CurrentUser`, `decode_access_token`
- Produces: `get_current_user(authorization: str | None = Header(default=None)) -> CurrentUser`, `require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser`

- [ ] **Step 1: 실패하는 테스트 추가**

`backend/tests/core/test_auth.py`에 추가:

```python
from fastapi import HTTPException

from core.auth import get_current_user, require_admin


def test_get_current_user_rejects_missing_header() -> None:
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(authorization=None)
    assert exc_info.value.status_code == 401


def test_get_current_user_rejects_malformed_header() -> None:
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(authorization="not-bearer-format")
    assert exc_info.value.status_code == 401


def test_get_current_user_rejects_invalid_token() -> None:
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(authorization="Bearer not-a-real-token")
    assert exc_info.value.status_code == 401


def test_get_current_user_accepts_valid_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-at-least-32-characters-long")
    token = create_access_token("kim.quality", "user")
    user = get_current_user(authorization=f"Bearer {token}")
    assert user == CurrentUser(username="kim.quality", role="user")


def test_require_admin_rejects_non_admin_role() -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_admin(user=CurrentUser(username="kim.quality", role="user"))
    assert exc_info.value.status_code == 403


def test_require_admin_accepts_admin_role() -> None:
    admin = CurrentUser(username="park.admin", role="admin")
    assert require_admin(user=admin) == admin
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/core/test_auth.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_current_user'`

- [ ] **Step 3: 구현 추가**

`backend/core/auth.py`에 추가 (import에 `from fastapi import Depends, Header, HTTPException` 추가):

```python
def decode_access_token(token: str) -> CurrentUser:
    try:
        payload = jwt.decode(token, _secret_key(), algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=401, detail="인증이 필요합니다"
        ) from exc
    return CurrentUser(username=payload["sub"], role=payload["role"])


def get_current_user(
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="인증이 필요합니다")
    token = authorization.removeprefix("Bearer ")
    return decode_access_token(token)


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다")
    return user
```

기존 `decode_access_token`(Step 3에서 만든, try/except 없는 버전)을 위 코드로 교체한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/core/test_auth.py -v`
Expected: PASS (10개)

- [ ] **Step 5: Commit**

```bash
git add backend/core/auth.py backend/tests/core/test_auth.py
git commit -m "feat: get_current_user/require_admin 인증 의존성 추가 (Authorization 헤더)"
```

---

## Task 3: `app.users` 테이블 부트스트랩

**Files:**
- Modify: `backend/core/auth.py`
- Test: `backend/tests/core/test_auth.py`

**Interfaces:**
- Consumes: Task 1의 `hash_password`
- Produces: `bootstrap_users(connection: Any) -> None`

- [ ] **Step 1: 실패하는 테스트 추가**

`backend/tests/core/test_auth.py`에 추가:

```python
import logging
from typing import Any

from core.auth import bootstrap_users


class _FakeConnection:
    """bootstrap_users가 실행한 SQL 문을 순서대로 기록하는 가짜 연결."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.committed = False

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        self.statements.append((query, params))
        class _Cursor:
            rowcount = 1
        return _Cursor()

    def commit(self) -> None:
        self.committed = True


def test_bootstrap_users_creates_schema_table_and_seeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMIN_USERNAME", "kim.quality")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
    monkeypatch.setenv("USER_USERNAME", "lee.viewer")
    monkeypatch.setenv("USER_PASSWORD", "user-pass")
    connection = _FakeConnection()

    bootstrap_users(connection)

    joined = "\n".join(query for query, _ in connection.statements)
    assert "CREATE SCHEMA IF NOT EXISTS app" in joined
    assert "CREATE TABLE IF NOT EXISTS app.users" in joined
    insert_statements = [
        params for query, params in connection.statements if "INSERT INTO app.users" in query
    ]
    assert len(insert_statements) == 2
    assert insert_statements[0][0] == "kim.quality"
    assert insert_statements[0][2] == "admin"
    assert insert_statements[1][0] == "lee.viewer"
    assert insert_statements[1][2] == "user"
    assert connection.committed is True


def test_bootstrap_users_skips_seed_when_env_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("USER_USERNAME", raising=False)
    monkeypatch.delenv("USER_PASSWORD", raising=False)
    connection = _FakeConnection()

    with caplog.at_level(logging.WARNING):
        bootstrap_users(connection)

    insert_statements = [
        params for query, params in connection.statements if "INSERT INTO app.users" in query
    ]
    assert insert_statements == []
    assert "환경변수" in caplog.text or "admin" in caplog.text.lower()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/core/test_auth.py -v`
Expected: FAIL — `ImportError: cannot import name 'bootstrap_users'`

- [ ] **Step 3: 구현 추가**

`backend/core/auth.py` 상단에 `import logging` 추가, 모듈 레벨에 `logger = logging.getLogger(__name__)` 추가. 파일 끝에 추가:

```python
def bootstrap_users(connection: Any) -> None:
    """app.users 스키마·테이블을 만들고 환경변수의 시드 계정을 채운다."""
    connection.execute("CREATE SCHEMA IF NOT EXISTS app")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS app.users (
            id SERIAL PRIMARY KEY,
            username VARCHAR NOT NULL UNIQUE,
            password_hash VARCHAR NOT NULL,
            role VARCHAR NOT NULL CHECK (role IN ('admin', 'user')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    for username_env, password_env, role in (
        ("ADMIN_USERNAME", "ADMIN_PASSWORD", "admin"),
        ("USER_USERNAME", "USER_PASSWORD", "user"),
    ):
        username = os.getenv(username_env)
        password = os.getenv(password_env)
        if not username or not password:
            logger.warning("%s/%s 환경변수가 없어 %s 계정 시드를 건너뜁니다", username_env, password_env, role)
            continue
        cursor = connection.execute(
            "INSERT INTO app.users (username, password_hash, role) "
            "VALUES (%s, %s, %s) ON CONFLICT (username) DO NOTHING",
            (username, hash_password(password), role),
        )
        if cursor.rowcount == 1:
            logger.info("%s 계정을 새로 생성했습니다", username)
        else:
            logger.info("%s 계정이 이미 존재해 비밀번호를 변경하지 않았습니다", username)
    connection.commit()
```

(`import os`는 이미 Task 1에서 추가돼 있음. `Any`는 `from typing import Any`를 파일 상단에 추가해야 함.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/core/test_auth.py -v`
Expected: PASS (12개)

- [ ] **Step 5: Commit**

```bash
git add backend/core/auth.py backend/tests/core/test_auth.py
git commit -m "feat: app.users 테이블 부트스트랩 추가"
```

---

## Task 4: `/auth/login`, `/auth/logout`, `/auth/me` API (Bearer 토큰 응답)

**Files:**
- Create: `backend/api/auth.py`
- Test: `backend/tests/api/test_auth.py`

**Interfaces:**
- Consumes: Task 1-2의 `CurrentUser`, `create_access_token`, `verify_password`, `hash_password`, `get_current_user`
- Produces: `router`(APIRouter, prefix `/auth`), `LoginRequest`(pydantic)

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/api/test_auth.py`:

```python
"""POST /auth/login, /auth/logout, GET /auth/me 핸들러를 테스트한다."""

from typing import Any

import pytest
from fastapi import HTTPException

import api.auth as auth_module
from api.auth import LoginRequest, login, logout, me
from core.auth import CurrentUser, hash_password


class _FakeConnection:
    def __init__(self, rows_by_username: dict[str, tuple[Any, ...]]) -> None:
        self._rows_by_username = rows_by_username

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> "_FakeCursor":
        username = params[0]
        return _FakeCursor(self._rows_by_username.get(username))


class _FakeCursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


def test_login_returns_access_token_and_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-at-least-32-characters-long")
    monkeypatch.setattr(
        auth_module,
        "get_connection",
        lambda: _FakeConnection(
            {"kim.quality": ("kim.quality", hash_password("s3cret!"), "admin")}
        ),
    )

    result = login(LoginRequest(username="kim.quality", password="s3cret!"))

    assert result["token_type"] == "bearer"
    assert result["username"] == "kim.quality"
    assert result["role"] == "admin"
    assert isinstance(result["access_token"], str) and len(result["access_token"]) > 0


def test_login_rejects_wrong_password_with_generic_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        auth_module,
        "get_connection",
        lambda: _FakeConnection(
            {"kim.quality": ("kim.quality", hash_password("s3cret!"), "admin")}
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        login(LoginRequest(username="kim.quality", password="wrong"))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "아이디 또는 비밀번호가 올바르지 않습니다"


def test_login_rejects_unknown_username_with_same_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_module, "get_connection", lambda: _FakeConnection({}))

    with pytest.raises(HTTPException) as exc_info:
        login(LoginRequest(username="ghost", password="whatever"))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "아이디 또는 비밀번호가 올바르지 않습니다"


def test_login_calls_verify_password_even_for_unknown_username(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """존재하지 않는 아이디라도 verify_password가 실행돼 응답 시간이 일정해야 한다."""
    monkeypatch.setattr(auth_module, "get_connection", lambda: _FakeConnection({}))
    calls: list[str] = []
    original = auth_module.verify_password

    def spy(password: str, password_hash: str) -> bool:
        calls.append(password)
        return original(password, password_hash)

    monkeypatch.setattr(auth_module, "verify_password", spy)

    with pytest.raises(HTTPException):
        login(LoginRequest(username="ghost", password="whatever"))

    assert calls == ["whatever"]


def test_logout_returns_no_content() -> None:
    assert logout() is None


def test_me_returns_current_user() -> None:
    result = me(user=CurrentUser(username="kim.quality", role="admin"))
    assert result == {"username": "kim.quality", "role": "admin"}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/api/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.auth'`

- [ ] **Step 3: 구현 작성**

`backend/api/auth.py`:

```python
"""로그인/로그아웃/현재 사용자 조회 엔드포인트."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from core.auth import (
    CurrentUser,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from core.postgres import get_connection

router = APIRouter(prefix="/auth")

_DUMMY_HASH = hash_password("dummy-password-for-timing")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str


def _find_user_row(connection: Any, username: str) -> tuple[str, str, str] | None:
    cursor = connection.execute(
        "SELECT username, password_hash, role FROM app.users WHERE username = %s",
        (username,),
    )
    return cursor.fetchone()


@router.post("/login")
def login(request: LoginRequest) -> dict[str, str]:
    connection = get_connection()
    row = _find_user_row(connection, request.username)
    password_hash = row[1] if row is not None else _DUMMY_HASH
    password_ok = verify_password(request.password, password_hash)
    if row is None or not password_ok:
        raise HTTPException(
            status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다"
        )
    username, _, role = row
    token = create_access_token(username, role)
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": username,
        "role": role,
    }


@router.post("/logout", status_code=204)
def logout() -> None:
    return None


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    return {"username": user.username, "role": user.role}
```

(타이밍 사이드채널 방지를 위해 `_DUMMY_HASH`를 처음부터 넣는다 — 이전 구현에서 발견된 실제 버그를 재발 방지.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests/api/test_auth.py -v`
Expected: PASS (6개)

- [ ] **Step 5: Commit**

```bash
git add backend/api/auth.py backend/tests/api/test_auth.py
git commit -m "feat: /auth/login, /auth/logout, /auth/me 추가 (Bearer 토큰)"
```

---

## Task 5: main.py 배선 + `/chat` 보호 + 의존성/환경변수/pytest 설정

**Files:**
- Modify: `backend/main.py`, `backend/api/chat.py`, `backend/requirements.txt`, `.env.example`, `docker-compose.yml`, `pyproject.toml`, `.pre-commit-config.yaml`
- Test: `backend/tests/api/test_chat.py`

**Interfaces:**
- Consumes: Task 2의 `get_current_user`, `CurrentUser`; Task 3의 `bootstrap_users`; Task 4의 `router`(auth)

- [ ] **Step 1: 의존성 추가**

`backend/requirements.txt`에 알파벳 순서 유지하며 추가:

```
bcrypt==4.2.1
PyJWT==2.10.1
```

Run: `backend/venv/Scripts/pip.exe install bcrypt==4.2.1 PyJWT==2.10.1`

`.pre-commit-config.yaml`을 열어 mypy 훅의 `additional_dependencies` 목록에 같은 두 줄을 추가한다(정확한 위치는 파일을 읽고 기존 목록 포맷에 맞춰 추가 — 이전 구현에서 이 파일을 빠뜨려 CI가 깨진 적이 있으니 반드시 포함).

`pyproject.toml`의 `[tool.pytest.ini_options]`에서 `addopts` 값에 `--import-mode=importlib`를 추가한다:

```toml
addopts = "--verbose -m \"not integration\" --import-mode=importlib"
```

(`backend/tests/api/test_auth.py`와 `backend/tests/core/test_auth.py`가 파일명이 같아서, 이 설정 없이 `pytest backend/tests`를 한 번에 돌리면 수집 단계에서 충돌한다 — 처음부터 방지.)

- [ ] **Step 2: 환경변수 추가**

`.env.example`의 `# App` 섹션 앞에 추가:

```
# Auth
JWT_SECRET_KEY=changeme-must-be-at-least-32-characters-long
ADMIN_USERNAME=changeme
ADMIN_PASSWORD=changeme
USER_USERNAME=changeme
USER_PASSWORD=changeme
```

`docker-compose.yml`의 `backend.environment`에 추가:

```yaml
      JWT_SECRET_KEY: ${JWT_SECRET_KEY}
      ADMIN_USERNAME: ${ADMIN_USERNAME}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD}
      USER_USERNAME: ${USER_USERNAME}
      USER_PASSWORD: ${USER_PASSWORD}
```

- [ ] **Step 3: main.py에 JWT_SECRET_KEY 검증 + 라우터 등록 + 부트스트랩 배선**

`backend/main.py` 수정 — import 추가:

```python
from api.auth import router as auth_router
from core.auth import bootstrap_users
```

`lifespan` 함수 맨 앞(다른 어떤 초기화보다도 먼저)에 추가:

```python
    jwt_secret = os.getenv("JWT_SECRET_KEY", "")
    if len(jwt_secret) < 32:
        raise RuntimeError(
            "JWT_SECRET_KEY must be set to a string of at least 32 characters"
        )
```

(`import os`가 파일 상단에 없다면 추가.)

기존 pg_trgm 블록 바로 뒤, `yield` 이전에 추가:

```python
    bootstrap_users(connection)
```

라우터 등록 — 기존 `app.include_router(chat_router, tags=["Chat"])` 바로 위에 추가:

```python
app.include_router(auth_router, tags=["Auth"])
```

- [ ] **Step 4: `/chat` 보호**

`backend/api/chat.py` 수정 — import에 `from fastapi import APIRouter, Depends`로 변경하고 `from core.auth import CurrentUser, get_current_user` 추가. `chat` 함수 시그니처를 다음과 같이 변경:

```python
@router.post("/chat")
async def chat(
    request: ChatRequest, user: CurrentUser = Depends(get_current_user)
):
```

(나머지 함수 본문은 그대로. 기존 `backend/tests/api/test_chat.py`는 `chat(ChatRequest(...))`처럼 `user` 없이 직접 호출하므로 그대로 통과한다 — `Depends(...)`가 리터럴 기본값으로 바인딩될 뿐 함수 본문에서 `user`를 쓰지 않기 때문.)

- [ ] **Step 5: 인증 보호를 확인하는 HTTP 레벨 테스트 추가**

`backend/tests/api/test_chat.py` 상단 import에 추가:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import create_access_token
```

파일 끝에 추가:

```python
def test_chat_endpoint_rejects_request_without_token() -> None:
    """라우터 레벨에서 인증 없이 /chat을 호출하면 401을 받는다."""
    app = FastAPI()
    app.include_router(chat_module.router)
    client = TestClient(app)

    response = client.post("/chat", json={"query": "정가 알려줘"})

    assert response.status_code == 401


def test_chat_endpoint_accepts_request_with_valid_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """유효한 Authorization: Bearer 헤더가 있으면 /chat이 정상 응답한다."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-at-least-32-characters-long")
    openai_client = MockOpenAIClient(
        make_content_response('["sql"]'),
        make_content_response("SELECT listprice FROM production.product"),
    )
    monkeypatch.setattr(chat_module, "get_openai_client", lambda: openai_client)
    monkeypatch.setattr(
        chat_module, "get_connection", lambda: MockPostgresConnection(rows_by_name={})
    )
    app = FastAPI()
    app.include_router(chat_module.router)
    client = TestClient(app)
    token = create_access_token("kim.quality", "admin")

    response = client.post(
        "/chat",
        json={"query": "정가 알려줘"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
```

- [ ] **Step 6: 전체 백엔드 테스트 통과 확인**

Run: `backend/venv/Scripts/python.exe -m pytest backend/tests -v`
Expected: PASS (기존 테스트 + 신규 테스트 전부, 한 번의 명령으로 수집·실행 성공 — `--import-mode=importlib` 덕분에 파일명 충돌 없음)

Run: `backend/venv/Scripts/python.exe -m mypy backend`
Expected: 0 errors

- [ ] **Step 7: Commit**

```bash
git add backend/main.py backend/api/chat.py backend/requirements.txt .env.example docker-compose.yml pyproject.toml .pre-commit-config.yaml backend/tests/api/test_chat.py
git commit -m "feat: auth 라우터 배선하고 /chat을 인증 보호 대상으로 변경"
```

---

## Task 6: 프론트 의존성 + 스키마 노드 데이터 추출

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/src/lib/schemaNodes.ts`
- Modify: `frontend/src/screens/Dashboard.tsx`

**Interfaces:**
- Produces: `SCHEMA_NODES: SchemaNode[]`, `RELATIONSHIPS: SchemaRelationship[]` (from `frontend/src/lib/schemaNodes.ts`)

- [ ] **Step 1: 의존성 설치**

Run: `cd frontend && npm install axios zod react-router-dom`

- [ ] **Step 2: 스키마 노드 데이터를 공용 모듈로 추출**

`frontend/src/lib/schemaNodes.ts` 생성 — `Dashboard.tsx`의 `SCHEMA_NODES`/`RELATIONSHIPS` 상수 정의를 그대로 옮긴다:

```typescript
import type { SchemaNode, SchemaRelationship } from '@/types/query'

// 지식그래프 스키마 노드/관계 타입 정의
export const SCHEMA_NODES: SchemaNode[] = [
  {
    label: 'Lot',
    glyph: 'L',
    description: '생산 배치 단위',
    properties: ['lot_id', 'product_code', 'created_at'],
  },
  {
    label: 'Process',
    glyph: 'P',
    description: '공정 단계',
    properties: ['process_name', 'sequence'],
  },
  { label: 'Equipment', glyph: 'EQ', description: '설비', properties: ['equipment_id', 'line'] },
  {
    label: 'Material',
    glyph: 'M',
    description: '투입 자재',
    properties: ['material_code', 'lot_no'],
  },
  {
    label: 'Defect',
    glyph: 'D',
    description: '불량 기록',
    properties: ['defect_code', 'severity', 'detected_at'],
  },
]

export const RELATIONSHIPS: SchemaRelationship[] = [
  { name: 'FOLLOWS', description: '공정 순서' },
  { name: 'PROCESSED_AT', description: '설비 투입' },
  { name: 'HAS_DEFECT', description: '불량 발생' },
  { name: 'CONSUMES', description: '자재 소모' },
]
```

- [ ] **Step 3: Dashboard.tsx가 공용 모듈을 쓰도록 변경**

`frontend/src/screens/Dashboard.tsx`에서 `SCHEMA_NODES`/`RELATIONSHIPS` 상수 정의를 삭제하고, import 목록에 추가:

```typescript
import { SCHEMA_NODES, RELATIONSHIPS } from '@/lib/schemaNodes'
```

- [ ] **Step 4: 타입 체크로 확인**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: 에러 없음

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/lib/schemaNodes.ts frontend/src/screens/Dashboard.tsx
git commit -m "refactor: 스키마 노드 데이터를 공용 모듈로 추출하고 인증 관련 의존성 추가"
```

---

## Task 7: `lib/schemas.ts` + `lib/api.ts` (Bearer 토큰, sessionStorage)

**Files:**
- Create: `frontend/src/lib/schemas.ts`, `frontend/src/lib/api.ts`

**Interfaces:**
- Produces: `LoginRequestSchema`, `LoginResponseSchema`, `LoginErrorSchema`, `CurrentUserSchema` 및 대응 타입(`schemas.ts`) / `AuthError`, `TOKEN_STORAGE_KEY`, `login()`, `logout()`, `fetchMe()`(`api.ts`)

- [ ] **Step 1: zod 스키마 작성**

`frontend/src/lib/schemas.ts`:

```typescript
import { z } from 'zod'

export const LoginRequestSchema = z.object({
  username: z.string().min(1, '아이디를 입력하세요'),
  password: z.string().min(1, '비밀번호를 입력하세요'),
})
export type LoginRequest = z.infer<typeof LoginRequestSchema>

export const LoginResponseSchema = z.object({
  access_token: z.string(),
  token_type: z.literal('bearer'),
  username: z.string(),
  role: z.enum(['admin', 'user']),
})
export type LoginResponse = z.infer<typeof LoginResponseSchema>

export const CurrentUserSchema = z.object({
  username: z.string(),
  role: z.enum(['admin', 'user']),
})
export type CurrentUser = z.infer<typeof CurrentUserSchema>

export const LoginErrorSchema = z.object({
  detail: z.string(),
})
export type LoginError = z.infer<typeof LoginErrorSchema>
```

- [ ] **Step 2: axios 인스턴스와 API 함수 작성**

`frontend/src/lib/api.ts`:

```typescript
import axios, { AxiosError } from 'axios'
import {
  CurrentUserSchema,
  LoginErrorSchema,
  LoginRequestSchema,
  LoginResponseSchema,
  type CurrentUser,
  type LoginRequest,
  type LoginResponse,
} from './schemas'

export const TOKEN_STORAGE_KEY = 'kg-access-token'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '',
  timeout: 15_000,
})

// 요청마다 sessionStorage의 토큰을 Authorization 헤더로 부착한다
api.interceptors.request.use((config) => {
  const token = sessionStorage.getItem(TOKEN_STORAGE_KEY)
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

export class AuthError extends Error {}

// 로그인 요청을 보내고 성공 시 토큰과 사용자 정보를 반환한다
export async function login(payload: LoginRequest): Promise<LoginResponse> {
  const body = LoginRequestSchema.parse(payload)
  try {
    const res = await api.post('/auth/login', body)
    return LoginResponseSchema.parse(res.data)
  } catch (err) {
    const axiosErr = err as AxiosError
    if (axiosErr.response?.status === 401) {
      const parsed = LoginErrorSchema.safeParse(axiosErr.response.data)
      throw new AuthError(
        parsed.success ? parsed.data.detail : '아이디 또는 비밀번호가 올바르지 않습니다'
      )
    }
    throw err
  }
}

// 로그아웃 요청을 보낸다 (서버는 상태가 없어 실질 효과는 없음)
export async function logout(): Promise<void> {
  await api.post('/auth/logout')
}

// 현재 로그인된 사용자 정보를 조회한다(토큰 없음/무효 시 401)
export async function fetchMe(): Promise<CurrentUser> {
  const res = await api.get('/auth/me')
  return CurrentUserSchema.parse(res.data)
}
```

- [ ] **Step 3: 타입 체크로 검증**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: 에러 없음

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/schemas.ts frontend/src/lib/api.ts
git commit -m "feat: 인증 API용 zod 스키마와 axios 함수 추가 (Bearer 토큰)"
```

---

## Task 8: `useAuthStore` + `useLoginPrefsStore` (sessionStorage 토큰 관리)

**Files:**
- Create: `frontend/src/store/useAuthStore.ts`, `frontend/src/store/useLoginPrefsStore.ts`

**Interfaces:**
- Consumes: Task 7의 `login`, `logout`, `fetchMe`, `TOKEN_STORAGE_KEY`, `LoginResponse`, `CurrentUser`
- Produces: `useAuthStore` — `{ user, status, login(username, password), logout(), checkAuth() }` / `useLoginPrefsStore` — `{ rememberId, savedUsername, setRememberId, setSavedUsername }`

- [ ] **Step 1: 인증 상태 스토어 작성**

`frontend/src/store/useAuthStore.ts`:

```typescript
import { create } from 'zustand'
import {
  login as apiLogin,
  logout as apiLogout,
  fetchMe,
  TOKEN_STORAGE_KEY,
} from '@/lib/api'
import type { CurrentUser } from '@/lib/schemas'

export type AuthStatus = 'idle' | 'loading' | 'authenticated' | 'unauthenticated'

interface AuthStore {
  user: CurrentUser | null
  status: AuthStatus
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  checkAuth: () => Promise<void>
}

// 로그인 사용자 정보와 인증 상태를 관리한다
export const useAuthStore = create<AuthStore>((set) => ({
  user: null,
  status: 'idle',
  login: async (username, password) => {
    const response = await apiLogin({ username, password })
    sessionStorage.setItem(TOKEN_STORAGE_KEY, response.access_token)
    set({ user: { username: response.username, role: response.role }, status: 'authenticated' })
  },
  logout: async () => {
    try {
      await apiLogout()
    } finally {
      sessionStorage.removeItem(TOKEN_STORAGE_KEY)
      set({ user: null, status: 'unauthenticated' })
    }
  },
  checkAuth: async () => {
    if (!sessionStorage.getItem(TOKEN_STORAGE_KEY)) {
      set({ user: null, status: 'unauthenticated' })
      return
    }
    set({ status: 'loading' })
    try {
      const user = await fetchMe()
      set({ user, status: 'authenticated' })
    } catch (err) {
      console.error('checkAuth failed:', err)
      sessionStorage.removeItem(TOKEN_STORAGE_KEY)
      set({ user: null, status: 'unauthenticated' })
    }
  },
}))
```

- [ ] **Step 2: 로그인 화면 UX 설정 스토어 작성**

`frontend/src/store/useLoginPrefsStore.ts`:

```typescript
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface LoginPrefsStore {
  rememberId: boolean
  savedUsername: string
  setRememberId: (value: boolean) => void
  setSavedUsername: (value: string) => void
}

// 로그인 화면의 "아이디 기억" 설정만 localStorage에 저장한다
export const useLoginPrefsStore = create<LoginPrefsStore>()(
  persist(
    (set) => ({
      rememberId: true,
      savedUsername: '',
      setRememberId: (rememberId) => set({ rememberId }),
      setSavedUsername: (savedUsername) => set({ savedUsername }),
    }),
    { name: 'kg-login-prefs' }
  )
)
```

- [ ] **Step 3: 타입 체크로 검증**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: 에러 없음

- [ ] **Step 4: Commit**

```bash
git add frontend/src/store/useAuthStore.ts frontend/src/store/useLoginPrefsStore.ts
git commit -m "feat: 인증 상태 스토어와 로그인 화면 설정 스토어 추가 (sessionStorage 토큰)"
```

---

## Task 9: `LoginPage` + `LoginAsidePanel`

**Files:**
- Create: `frontend/src/pages/LoginPage.tsx`, `frontend/src/components/layout/LoginAsidePanel.tsx`

**Interfaces:**
- Consumes: Task 8의 `useAuthStore`, `useLoginPrefsStore`; Task 6의 `SCHEMA_NODES`; 기존 `TopBar`
- Produces: `LoginPage`(컴포넌트, 로그인 성공 시 `/`로 이동), `LoginAsidePanel`(컴포넌트)

- [ ] **Step 1: LoginAsidePanel 작성**

`frontend/src/components/layout/LoginAsidePanel.tsx`:

```typescript
import { SCHEMA_NODES } from '@/lib/schemaNodes'
import { NodeGlyphBadge } from '@/components/common/NodeGlyphBadge'

// 로그인 화면 우측 컨텍스트 패널: 도구 설명 + 스키마 노드 목록을 보여준다
export function LoginAsidePanel() {
  return (
    <aside className="hidden w-[380px] shrink-0 flex-col justify-center gap-6 border-l border-border bg-panel px-8 py-8 lg:flex">
      <div>
        <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.04em] text-text-faint">
          이 도구로 할 수 있는 것
        </div>
        <p className="text-sm leading-relaxed text-text-muted">
          Cypher를 몰라도 한국어로 질문하면 공정 지식그래프에서 다중 홉 원인 경로를 추적하고
          집계 결과를 확인할 수 있습니다.
        </p>
      </div>
      <div>
        <div className="mb-2.5 text-[11px] font-bold uppercase tracking-[0.04em] text-text-faint">
          연결된 그래프 스키마
        </div>
        <ul className="flex flex-col gap-2">
          {SCHEMA_NODES.map((node) => (
            <li key={node.label} className="flex items-center gap-2.5 text-[13px]">
              <NodeGlyphBadge nodeLabel={node.label} glyph={node.glyph} size={18} />
              <span className="font-semibold text-text">{node.label}</span>
              <span className="text-xs text-text-faint">{node.description}</span>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  )
}
```

(`NodeGlyphBadge`의 `size` prop은 `11 | 18`만 허용한다 — 실제 컴포넌트 타입을 확인하고 맞는 값을 쓴다.)

- [ ] **Step 2: LoginPage 작성**

`frontend/src/pages/LoginPage.tsx`:

```typescript
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { TopBar } from '@/components/layout/TopBar'
import { LoginAsidePanel } from '@/components/layout/LoginAsidePanel'
import { useAuthStore } from '@/store/useAuthStore'
import { useLoginPrefsStore } from '@/store/useLoginPrefsStore'
import { AuthError } from '@/lib/api'

// 로그인 화면: 아이디/비밀번호 입력 후 인증하고 성공 시 대시보드로 이동한다
export function LoginPage() {
  const navigate = useNavigate()
  const login = useAuthStore((s) => s.login)
  const { rememberId, savedUsername, setRememberId, setSavedUsername } = useLoginPrefsStore()

  const [username, setUsername] = useState(savedUsername)
  const [password, setPassword] = useState('')
  const [pwVisible, setPwVisible] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username || !password) return
    setIsSubmitting(true)
    setErrorMessage(null)
    try {
      await login(username, password)
      setSavedUsername(rememberId ? username : '')
      navigate('/')
    } catch (err) {
      setErrorMessage(
        err instanceof AuthError ? err.message : '로그인 중 오류가 발생했습니다'
      )
    } finally {
      setIsSubmitting(false)
    }
  }

  const invalidRing = errorMessage ? 'border-fail' : 'border-border-strong'

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-bg">
      <TopBar
        connected={false}
        connectionEndpoint="bolt://prod-kg-01"
        readOnly
        onNavigateHome={() => {}}
      />
      <div className="flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 items-center justify-center p-6">
          <form onSubmit={handleSubmit} className="flex w-[400px] max-w-full flex-col gap-4">
            <div>
              <h1 className="text-xl font-bold text-text">로그인</h1>
              <p className="mt-1 text-[13px] leading-normal text-text-muted">
                사내 계정으로 접속하세요. 조회 권한만 부여되며 그래프 데이터는 변경되지 않습니다.
              </p>
            </div>

            {errorMessage ? (
              <div role="alert" className="rounded-md border border-fail bg-accent-bg px-3.5 py-2.5">
                <div className="text-[12.5px] font-bold text-fail">인증 실패 · {errorMessage}</div>
              </div>
            ) : null}

            <div className="flex flex-col gap-3">
              <div>
                <label htmlFor="username" className="mb-1.5 block text-[11.5px] font-bold uppercase text-text-faint">
                  아이디
                </label>
                <input
                  id="username"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="예: kim.quality"
                  aria-invalid={Boolean(errorMessage)}
                  className={`w-full rounded-md border ${invalidRing} bg-panel px-3.5 py-2.5 font-mono text-sm text-text outline-none focus:border-info`}
                />
              </div>

              <div>
                <label htmlFor="password" className="mb-1.5 block text-[11.5px] font-bold uppercase text-text-faint">
                  비밀번호
                </label>
                <div className="relative flex">
                  <input
                    id="password"
                    type={pwVisible ? 'text' : 'password'}
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="비밀번호 입력"
                    aria-invalid={Boolean(errorMessage)}
                    className={`min-w-0 flex-1 rounded-md border ${invalidRing} bg-panel py-2.5 pl-3.5 pr-[74px] text-sm text-text outline-none focus:border-info`}
                  />
                  <button
                    type="button"
                    onClick={() => setPwVisible((v) => !v)}
                    className="absolute inset-y-1.5 right-1.5 rounded-md border border-border bg-panel-2 px-2.5 text-[11px] text-text-muted"
                  >
                    {pwVisible ? '숨기기' : '표시'}
                  </button>
                </div>
              </div>
            </div>

            <label className="flex cursor-pointer items-center gap-2 text-[12.5px] text-text">
              <input
                type="checkbox"
                checked={rememberId}
                onChange={(e) => setRememberId(e.target.checked)}
                className="size-4 accent-info"
              />
              이 기기에서 아이디 기억
            </label>

            <button
              type="submit"
              disabled={isSubmitting || !username || !password}
              className="w-full rounded-md bg-info py-3 text-sm font-semibold text-white disabled:opacity-60"
            >
              {isSubmitting ? '인증 중…' : '로그인'}
            </button>
          </form>
        </main>
        <LoginAsidePanel />
      </div>
    </div>
  )
}
```

(zustand `persist`가 localStorage를 동기적으로 하이드레이션하므로 `savedUsername`은 컴포넌트가 처음 렌더링될 때 이미 최신값이다 — `useEffect`나 렌더링 중 상태 동기화가 필요 없다. `useState(savedUsername)` 한 줄로 충분.)

- [ ] **Step 3: 타입 체크로 검증**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: 에러 없음(App.tsx에 아직 라우팅이 없어 `LoginPage`가 어디서도 쓰이지 않는 상태 — Task 10에서 연결)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/LoginPage.tsx frontend/src/components/layout/LoginAsidePanel.tsx
git commit -m "feat: 로그인 화면 UI 추가"
```

---

## Task 10: 라우팅 + `ProtectedRoute` + 로그아웃 + 수동 검증

**Files:**
- Create: `frontend/src/components/ProtectedRoute.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/components/layout/TopBar.tsx`, `frontend/src/screens/Dashboard.tsx`
- Create (필요 시): `frontend/.env.example` (`VITE_API_BASE_URL=http://localhost:8000`)

**Interfaces:**
- Consumes: Task 8의 `useAuthStore`; Task 9의 `LoginPage`

- [ ] **Step 1: ProtectedRoute 작성**

`frontend/src/components/ProtectedRoute.tsx`:

```typescript
import { Navigate } from 'react-router-dom'
import { useAuthStore } from '@/store/useAuthStore'

interface Props {
  children: React.ReactNode
}

// 인증되지 않은 사용자를 로그인 화면으로 보낸다
export function ProtectedRoute({ children }: Props) {
  const status = useAuthStore((s) => s.status)

  if (status === 'idle' || status === 'loading') {
    return <div className="flex h-screen items-center justify-center text-text-muted">로딩 중…</div>
  }
  if (status === 'unauthenticated') {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}
```

- [ ] **Step 2: App.tsx에 라우팅 연결**

`frontend/src/App.tsx` 전체 교체:

```typescript
import { useEffect } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Dashboard } from '@/screens/Dashboard'
import { LoginPage } from '@/pages/LoginPage'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { useUiStore } from '@/store/useUiStore'
import { useAuthStore } from '@/store/useAuthStore'

function App() {
  const theme = useUiStore((s) => s.theme)
  const checkAuth = useAuthStore((s) => s.checkAuth)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          }
        />
      </Routes>
    </BrowserRouter>
  )
}

export default App
```

- [ ] **Step 3: TopBar에 로그아웃 표시 추가**

`frontend/src/components/layout/TopBar.tsx`의 `TopBarProps`에 선택적 필드 추가:

```typescript
interface TopBarProps {
  connected: boolean
  connectionEndpoint: string
  readOnly: boolean
  onNavigateHome: () => void
  username?: string
  onLogout?: () => void
}
```

함수 시그니처를 `export function TopBar({ connected, connectionEndpoint, readOnly, onNavigateHome, username, onLogout }: TopBarProps) {`로 바꾸고, 다크모드 토글 버튼 뒤(닫는 `</div>` 앞)에 추가:

```typescript
        {username ? (
          <span className="text-[11.5px] text-text-muted">{username}</span>
        ) : null}
        {onLogout ? (
          <Button type="button" variant="ghost" size="sm" onClick={onLogout}>
            로그아웃
          </Button>
        ) : null}
```

- [ ] **Step 4: Dashboard.tsx에서 로그아웃 연결**

`frontend/src/screens/Dashboard.tsx` import에 추가:

```typescript
import { useAuthStore } from '@/store/useAuthStore'
```

`Dashboard` 함수 본문 상단에 추가:

```typescript
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
```

`<TopBar ... />` 호출에 props 추가:

```typescript
      <TopBar
        connected={CONNECTED}
        connectionEndpoint={CONNECTION_ENDPOINT}
        readOnly={READ_ONLY}
        onNavigateHome={handleNavigateHome}
        username={user?.username}
        onLogout={logout}
      />
```

- [ ] **Step 5: 백엔드 CORS 확인 + 프론트 API base URL**

`backend/main.py`의 CORS 설정을 확인한다 — Bearer 토큰 방식은 쿠키를 안 쓰므로 `allow_credentials=True`가 굳이 필요하지 않지만, 있어도 무해하니 굳이 지우지 않는다.

`frontend/.env.example` 파일이 없다면 생성한다:

```
VITE_API_BASE_URL=http://localhost:8000
```

로컬 검증을 위해 `frontend/.env.local`(gitignored)도 같은 값으로 만든다.

- [ ] **Step 6: 타입 체크**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: 에러 없음

- [ ] **Step 7: 브라우저로 전체 흐름 수동 검증**

1. 백엔드(`uvicorn main:app --reload`, `backend/venv` 사용)와 프론트(`npm run dev`)를 둘 다 띄운다.
2. `.env`에 `JWT_SECRET_KEY`(32자 이상), `ADMIN_USERNAME=kim.quality`, `ADMIN_PASSWORD=admin-pass`, `USER_USERNAME=lee.viewer`, `USER_PASSWORD=user-pass` 설정 후 백엔드 재시작(부트스트랩으로 계정 생성 확인, `JWT_SECRET_KEY`가 짧으면 시작 자체가 실패하는지도 확인).
3. 브라우저로 `http://localhost:5173/` 접속 → `/login`으로 리다이렉트되는지 확인
4. 잘못된 비밀번호로 로그인 → "인증 실패 · 아이디 또는 비밀번호가 올바르지 않습니다" 배너만 뜨고 잠금 관련 문구가 없는지 확인
5. `kim.quality`/`admin-pass`로 로그인 → `/`로 이동, TopBar에 `kim.quality`와 로그아웃 버튼이 보이는지 확인
6. 개발자도구 Application → Session Storage에서 `kg-access-token` 키에 JWT 문자열이 저장돼 있는지 확인
7. Network 탭에서 `/auth/me`, `/chat` 등 요청에 `Authorization: Bearer <token>` 헤더가 실제로 붙어 나가는지 확인
8. 새로고침해도 로그인 상태가 유지되는지 확인(sessionStorage 기반 — 탭을 완전히 닫으면 사라지는 것도 정상 동작임을 인지)
9. 로그아웃 클릭 → sessionStorage에서 토큰이 사라지고 `/login`으로 이동하는지 확인

- [ ] **Step 8: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/ProtectedRoute.tsx frontend/src/components/layout/TopBar.tsx frontend/src/screens/Dashboard.tsx frontend/.env.example
git commit -m "feat: 로그인 라우팅, 보호 라우트, 로그아웃 UI 연결 (Bearer 토큰)"
```
