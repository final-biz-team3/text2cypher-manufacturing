"""비밀번호 해싱, JWT 발급/검증, 로그인 사용자 정보를 다룬다."""

import os
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
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


def check_jwt_secret() -> None:
    """JWT_SECRET_KEY가 32자 이상인지 검사하고 아니면 예외를 던진다."""
    jwt_secret = os.getenv("JWT_SECRET_KEY", "")
    if len(jwt_secret) < 32:
        raise RuntimeError(
            "JWT_SECRET_KEY must be set to a string of at least 32 characters"
        )


def create_access_token(username: str, role: str) -> str:
    expire = datetime.now(UTC) + timedelta(hours=_EXPIRE_HOURS)
    payload = {"sub": username, "role": role, "exp": expire}
    return jwt.encode(payload, _secret_key(), algorithm=_ALGORITHM)


def decode_access_token(token: str) -> CurrentUser:
    try:
        payload = jwt.decode(token, _secret_key(), algorithms=[_ALGORITHM])
        return CurrentUser(username=payload["sub"], role=payload["role"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="인증이 필요합니다") from exc


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
