"""비밀번호 해싱과 JWT 발급/검증 동작을 테스트한다."""

from typing import Any

import pytest
from fastapi import HTTPException

from core.auth import (
    CurrentUser,
    bootstrap_users,
    create_access_token,
    decode_access_token,
    get_current_user,
    hash_password,
    require_admin,
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
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token("not-a-real-token")
    assert exc_info.value.status_code == 401


def test_get_current_user_rejects_missing_cookie() -> None:
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(access_token=None)
    assert exc_info.value.status_code == 401


def test_get_current_user_rejects_invalid_token() -> None:
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(access_token="not-a-real-token")
    assert exc_info.value.status_code == 401


def test_get_current_user_accepts_valid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-at-least-32-characters-long")
    token = create_access_token("kim.quality", "user")
    user = get_current_user(access_token=token)
    assert user == CurrentUser(username="kim.quality", role="user")


def test_require_admin_rejects_non_admin_role() -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_admin(user=CurrentUser(username="kim.quality", role="user"))
    assert exc_info.value.status_code == 403


def test_require_admin_accepts_admin_role() -> None:
    admin = CurrentUser(username="park.admin", role="admin")
    assert require_admin(user=admin) == admin


class _FakeConnection:
    """bootstrap_users가 실행한 SQL 문을 순서대로 기록하는 가짜 연결."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.committed = False

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        self.statements.append((query, params))

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
        params
        for query, params in connection.statements
        if "INSERT INTO app.users" in query
    ]
    assert len(insert_statements) == 2
    assert insert_statements[0][0] == "kim.quality"
    assert insert_statements[0][2] == "admin"
    assert insert_statements[1][0] == "lee.viewer"
    assert insert_statements[1][2] == "user"
    assert connection.committed is True


def test_bootstrap_users_skips_seed_when_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("USER_USERNAME", raising=False)
    monkeypatch.delenv("USER_PASSWORD", raising=False)
    connection = _FakeConnection()

    bootstrap_users(connection)

    insert_statements = [
        params
        for query, params in connection.statements
        if "INSERT INTO app.users" in query
    ]
    assert insert_statements == []
