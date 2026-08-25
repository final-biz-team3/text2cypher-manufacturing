"""비밀번호 해싱과 JWT 발급/검증 동작을 테스트한다."""

import pytest
from fastapi import HTTPException

from core.auth import (
    CurrentUser,
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
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    token = create_access_token("kim.quality", "admin")
    user = decode_access_token(token)
    assert user == CurrentUser(username="kim.quality", role="admin")


def test_decode_access_token_rejects_garbage_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
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
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
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
