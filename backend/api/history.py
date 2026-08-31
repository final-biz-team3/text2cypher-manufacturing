"""대화기록 조회·삭제 엔드포인트."""

from fastapi import APIRouter, Depends, HTTPException

from core.auth import CurrentUser, get_current_user
from core.history import delete_conversation, list_history
from core.postgres import get_pool, get_write_pool

router = APIRouter()


@router.get("/history")
async def get_history(
    user: CurrentUser = Depends(get_current_user),  # noqa: B008
) -> list[dict]:
    # get_pool()의 실제 반환 타입(psycopg_pool.AsyncConnectionPool)은
    # core.history.Pool Protocol과 런타임에는 호환되지만, connection()이
    # @asynccontextmanager로 구현돼 있어 mypy가 구조적 일치를 못 잡아낸다
    # (core/history.py의 Pool Protocol 주석 참고 - 실측 확인된 mypy 한계).
    return await list_history(get_pool(), user)  # type: ignore[arg-type]


@router.delete("/history/{history_id}", status_code=204)
async def delete_history(
    history_id: int,
    user: CurrentUser = Depends(get_current_user),  # noqa: B008
) -> None:
    # get_write_pool() 관련 mypy 한계는 위 get_history()와 동일하다.
    deleted = await delete_conversation(
        get_write_pool(),  # type: ignore[arg-type]
        user,
        history_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="대화기록을 찾을 수 없습니다")
