"""비밀번호 해싱, JWT 발급/검증, 로그인 사용자 정보를 다룬다."""

import os
from datetime import UTC, datetime, timedelta

import bcrypt  # type: ignore
import jwt  # type: ignore
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
    payload = jwt.decode(token, _secret_key(), algorithms=[_ALGORITHM])
    return CurrentUser(username=payload["sub"], role=payload["role"])
