"""비밀번호 해싱, JWT 발급/검증, 로그인 사용자 정보를 다룬다."""

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt  # type: ignore
import jwt  # type: ignore
from fastapi import Cookie, Depends, HTTPException
from pydantic import BaseModel

COOKIE_NAME = "access_token"
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
    return os.getenv("JWT_SECRET_KEY", "changeme_local_jwt_secret")


def create_access_token(username: str, role: str) -> str:
    expire = datetime.now(UTC) + timedelta(hours=_EXPIRE_HOURS)
    payload = {"sub": username, "role": role, "exp": expire}
    return jwt.encode(payload, _secret_key(), algorithm=_ALGORITHM)


def decode_access_token(token: str) -> CurrentUser:
    try:
        payload = jwt.decode(token, _secret_key(), algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="인증이 필요합니다") from exc
    return CurrentUser(username=payload["sub"], role=payload["role"])


def get_current_user(
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> CurrentUser:
    if access_token is None:
        raise HTTPException(status_code=401, detail="인증이 필요합니다")
    return decode_access_token(access_token)


def require_admin(
    user: CurrentUser = Depends(get_current_user),  # noqa: B008
) -> CurrentUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다")
    return user


def bootstrap_users(connection: Any) -> None:
    """app.users 스키마·테이블을 만들고 환경변수의 시드 계정을 채운다."""
    connection.execute("CREATE SCHEMA IF NOT EXISTS app")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS app.users (
            id SERIAL PRIMARY KEY,
            username VARCHAR NOT NULL UNIQUE,
            password_hash VARCHAR NOT NULL,
            role VARCHAR NOT NULL CHECK (role IN ('admin', 'user')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
    for username_env, password_env, role in (
        ("ADMIN_USERNAME", "ADMIN_PASSWORD", "admin"),
        ("USER_USERNAME", "USER_PASSWORD", "user"),
    ):
        username = os.getenv(username_env)
        password = os.getenv(password_env)
        if not username or not password:
            continue
        connection.execute(
            "INSERT INTO app.users (username, password_hash, role) "
            "VALUES (%s, %s, %s) ON CONFLICT (username) DO NOTHING",
            (username, hash_password(password), role),
        )
    connection.commit()
