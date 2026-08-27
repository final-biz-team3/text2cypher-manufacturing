"""로그인/로그아웃/현재 사용자 조회 엔드포인트."""

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from core.auth import (
    COOKIE_NAME,
    EXPIRE_HOURS,
    CurrentUser,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from core.postgres import get_pool

router = APIRouter(prefix="/auth")

_COOKIE_MAX_AGE_SECONDS = EXPIRE_HOURS * 60 * 60
_DUMMY_HASH = hash_password("dummy-password-for-timing")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str


def _cookie_secure() -> bool:
    """APP_ENV가 development가 아니면 True를 반환한다."""
    return os.getenv("APP_ENV") != "development"


async def _find_user_row(pool: Any, username: str) -> tuple[str, str, str] | None:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT username, password_hash, role FROM app.users WHERE username = %s",
            (username,),
        )
        return await cursor.fetchone()


@router.post("/login")
async def login(request: LoginRequest, response: Response) -> dict[str, str]:
    row = await _find_user_row(get_pool(), request.username)
    password_hash = row[1] if row is not None else _DUMMY_HASH
    # bcrypt.checkpw는 의도적으로 느린(~100-300ms) CPU 바운드 동기 함수라,
    # async 핸들러 안에서 직접 부르면 그동안 이벤트 루프가 통째로 막혀
    # 동시에 들어온 다른 모든 요청(예: /chat)이 대기하게 된다. 스레드풀로
    # 넘겨 이벤트 루프를 막지 않게 한다.
    password_ok = await run_in_threadpool(
        verify_password, request.password, password_hash
    )
    if row is None or not password_ok:
        raise HTTPException(
            status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다"
        )
    username, _, role = row
    token = create_access_token(username, role)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        # SameSite=Lax가 CSRF 방어를 겸한다
        samesite="lax",
        secure=_cookie_secure(),
        max_age=_COOKIE_MAX_AGE_SECONDS,
    )
    return {"username": username, "role": role}


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie(
        COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
    )


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:  # noqa: B008
    return {"username": user.username, "role": user.role}
