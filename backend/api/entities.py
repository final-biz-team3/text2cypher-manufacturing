"""대시보드와 Sigma가 공유하는 엔티티 상세 API."""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from core.auth import CurrentUser, get_current_user
from dashboard.entities import get_entity_detail, get_entity_neighbors
from dashboard.service import DashboardServiceError

router = APIRouter(prefix="/entities")


def _error(error: DashboardServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"code": error.code, "message": error.message},
    )


@router.get("/{entity_type}/{entity_id}")
async def detail(
    entity_type: str,
    entity_id: str,
    _user: CurrentUser = Depends(get_current_user),  # noqa: B008
):
    try:
        return await get_entity_detail(entity_type, entity_id)
    except DashboardServiceError as error:
        return _error(error)


@router.get("/{entity_type}/{entity_id}/neighbors")
async def neighbors(
    entity_type: str,
    entity_id: str,
    depth: int = Query(default=1),
    _user: CurrentUser = Depends(get_current_user),  # noqa: B008
):
    try:
        return await get_entity_neighbors(entity_type, entity_id, depth=depth)
    except DashboardServiceError as error:
        return _error(error)
