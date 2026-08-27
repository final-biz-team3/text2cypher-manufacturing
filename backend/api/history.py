"""대화기록 조회 엔드포인트."""

from fastapi import APIRouter, Depends

from core.auth import CurrentUser, get_current_user
from core.history import list_history
from core.postgres import get_pool

router = APIRouter()


@router.get("/history")
async def get_history(
    user: CurrentUser = Depends(get_current_user),  # noqa: B008
) -> list[dict]:
    return await list_history(get_pool(), user)
