"""인증된 사용자를 위한 전체 현황 대시보드 API."""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from core.auth import CurrentUser, get_current_user
from dashboard.process import get_process_overview
from dashboard.service import (
    DashboardServiceError,
    get_dashboard_card,
    get_dashboard_overview,
)

router = APIRouter(prefix="/dashboard")


def _error_response(error: DashboardServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"code": error.code, "message": error.message},
    )


@router.get("/overview")
async def overview(
    _user: CurrentUser = Depends(get_current_user),  # noqa: B008
):
    try:
        return await get_dashboard_overview()
    except DashboardServiceError as error:
        return _error_response(error)


@router.get("/process-overview")
async def process_overview(
    from_value: str | None = Query(default=None, alias="from"),
    to_value: str | None = Query(default=None, alias="to"),
    granularity: str | None = Query(default=None),
    _user: CurrentUser = Depends(get_current_user),  # noqa: B008
):
    try:
        return await get_process_overview(from_value, to_value, granularity)
    except DashboardServiceError as error:
        return _error_response(error)


@router.get("/cards/{card_key}")
async def card(
    card_key: str,
    page: int = Query(default=1),
    page_size: int = Query(default=20, alias="pageSize"),
    sort: str | None = Query(default=None),
    direction: str = Query(default="desc"),
    _user: CurrentUser = Depends(get_current_user),  # noqa: B008
):
    try:
        return await get_dashboard_card(
            card_key,
            page=page,
            page_size=page_size,
            sort=sort,
            direction=direction,
        )
    except DashboardServiceError as error:
        return _error_response(error)
