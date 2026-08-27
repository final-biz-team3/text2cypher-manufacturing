"""POST /auth/login, /auth/logout, GET /auth/me 핸들러를 테스트한다."""

from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient

import api.auth as auth_module
from api.auth import LoginRequest, login, logout, me
from core.auth import CurrentUser, hash_password


class _FakeCursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class _FakeConnection:
    def __init__(self, pool: "_FakePool") -> None:
        self._pool = pool

    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> _FakeCursor:
        username = params[0]
        return _FakeCursor(self._pool.rows_by_username.get(username))


class _FakeConnectionContext:
    def __init__(self, pool: "_FakePool") -> None:
        self._pool = pool

    async def __aenter__(self) -> _FakeConnection:
        return _FakeConnection(self._pool)

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakePool:
    def __init__(self, rows_by_username: dict[str, tuple[Any, ...]]) -> None:
        self.rows_by_username = rows_by_username

    def connection(self) -> _FakeConnectionContext:
        return _FakeConnectionContext(self)


async def test_login_sets_cookie_and_returns_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-at-least-32-characters-long")
    monkeypatch.setattr(
        auth_module,
        "get_pool",
        lambda: _FakePool(
            {"kim.quality": ("kim.quality", hash_password("s3cret!"), "admin")}
        ),
    )
    response = Response()

    result = await login(
        LoginRequest(username="kim.quality", password="s3cret!"), response
    )

    assert result == {"username": "kim.quality", "role": "admin"}
    set_cookie = response.headers["set-cookie"]
    assert "access_token=" in set_cookie
    assert "HttpOnly" in set_cookie


async def test_login_rejects_wrong_password_with_generic_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        auth_module,
        "get_pool",
        lambda: _FakePool(
            {"kim.quality": ("kim.quality", hash_password("s3cret!"), "admin")}
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await login(LoginRequest(username="kim.quality", password="wrong"), Response())
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "아이디 또는 비밀번호가 올바르지 않습니다"


async def test_login_rejects_unknown_username_with_same_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_module, "get_pool", lambda: _FakePool({}))

    with pytest.raises(HTTPException) as exc_info:
        await login(LoginRequest(username="ghost", password="whatever"), Response())
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "아이디 또는 비밀번호가 올바르지 않습니다"


def test_logout_clears_cookie() -> None:
    response = Response()
    logout(response)
    set_cookie = response.headers["set-cookie"]
    assert "access_token=" in set_cookie
    assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie.lower()


def test_me_returns_current_user() -> None:
    result = me(user=CurrentUser(username="kim.quality", role="admin"))
    assert result == {"username": "kim.quality", "role": "admin"}


def test_auth_router_http_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    """/auth/login → /auth/me → /auth/logout → /auth/me를 실제 HTTP 요청으로 검증한다."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-at-least-32-characters-long")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(
        auth_module,
        "get_pool",
        lambda: _FakePool(
            {"kim.quality": ("kim.quality", hash_password("s3cret!"), "admin")}
        ),
    )
    app = FastAPI()
    app.include_router(auth_module.router)
    client = TestClient(app)

    login_response = client.post(
        "/auth/login", json={"username": "kim.quality", "password": "s3cret!"}
    )
    assert login_response.status_code == 200
    assert "access_token" in login_response.cookies

    me_response = client.get("/auth/me")
    assert me_response.status_code == 200
    assert me_response.json() == {"username": "kim.quality", "role": "admin"}

    logout_response = client.post("/auth/logout")
    assert logout_response.status_code == 204

    me_after_logout_response = client.get("/auth/me")
    assert me_after_logout_response.status_code == 401


async def test_login_calls_verify_password_for_unknown_username(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """존재하지 않는 아이디여도 verify_password가 호출되는지 확인한다(Task 4 타이밍 수정 회귀 방지)."""
    monkeypatch.setattr(auth_module, "get_pool", lambda: _FakePool({}))
    call_count = 0
    original_verify_password = auth_module.verify_password

    def _spy_verify_password(password: str, password_hash: str) -> bool:
        nonlocal call_count
        call_count += 1
        return original_verify_password(password, password_hash)

    monkeypatch.setattr(auth_module, "verify_password", _spy_verify_password)

    with pytest.raises(HTTPException):
        await login(LoginRequest(username="ghost", password="whatever"), Response())

    assert call_count == 1
