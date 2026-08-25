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
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    token = create_access_token("kim.quality", "admin")
    user = decode_access_token(token)
    assert user == CurrentUser(username="kim.quality", role="admin")


def test_decode_access_token_rejects_garbage_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    with pytest.raises(Exception):  # noqa: B017
        decode_access_token("not-a-real-token")
