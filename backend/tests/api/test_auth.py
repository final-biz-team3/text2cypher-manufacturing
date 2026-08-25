"""POST /auth/login, /auth/logout, GET /auth/me 핸들러를 테스트한다."""

from typing import Any

import pytest
from fastapi import HTTPException, Response

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


def test_login_sets_cookie_and_returns_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setattr(
        auth_module,
        "get_connection",
        lambda: _FakeConnection(
            {"kim.quality": ("kim.quality", hash_password("s3cret!"), "admin")}
        ),
    )
    response = Response()

    result = login(LoginRequest(username="kim.quality", password="s3cret!"), response)

    assert result == {"username": "kim.quality", "role": "admin"}
    set_cookie = response.headers["set-cookie"]
    assert "access_token=" in set_cookie
    assert "HttpOnly" in set_cookie


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
        login(LoginRequest(username="kim.quality", password="wrong"), Response())
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "아이디 또는 비밀번호가 올바르지 않습니다"


def test_login_rejects_unknown_username_with_same_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_module, "get_connection", lambda: _FakeConnection({}))

    with pytest.raises(HTTPException) as exc_info:
        login(LoginRequest(username="ghost", password="whatever"), Response())
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
