"""비밀번호 해싱과 JWT 발급/검증 동작을 테스트한다."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import HTTPException

from core.auth import (
    CurrentUser,
    check_jwt_secret,
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


def test_decode_access_token_rejects_token_missing_role_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """서명은 유효하지만 role 필드가 없는 토큰은 500이 아니라 401을 반환해야 한다."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-at-least-32-characters-long")
    token = jwt.encode(
        {"sub": "kim.quality"},
        "test-secret-at-least-32-characters-long",
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token)
    assert exc_info.value.status_code == 401


def test_decode_access_token_rejects_expired_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-at-least-32-characters-long")
    expired = datetime.now(UTC) - timedelta(hours=1)
    token = jwt.encode(
        {"sub": "kim.quality", "role": "admin", "exp": expired},
        "test-secret-at-least-32-characters-long",
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token)
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


def test_check_jwt_secret_rejects_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError):
        check_jwt_secret()


def test_check_jwt_secret_rejects_short_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "too-short")
    with pytest.raises(RuntimeError):
        check_jwt_secret()


def test_check_jwt_secret_accepts_long_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-at-least-32-characters-long")
    check_jwt_secret()
