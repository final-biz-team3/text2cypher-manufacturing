from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.auth import CurrentUser, require_admin
from core.observability.events import emit_event
from core.postgres import get_write_pool
from core.query_failure_reviews import (
    CLASSIFICATIONS,
    STATUSES,
    get_failure_review,
    list_failure_reviews,
    update_failure_review,
)

router = APIRouter(prefix="/admin/query-failures")


class ReviewPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    status: str | None = None
    classification: str | None = None
    assignee: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=10000)
    fixture_id: str | None = Field(default=None, max_length=128)
    issue_url: str | None = Field(default=None, max_length=2000)
    pr_url: str | None = Field(default=None, max_length=2000)

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str | None) -> str | None:
        if value is not None and value not in STATUSES:
            raise ValueError("invalid review status")
        return value

    @field_validator("classification")
    @classmethod
    def valid_classification(cls, value: str | None) -> str | None:
        if value is not None and value not in CLASSIFICATIONS:
            raise ValueError("invalid classification")
        return value


@router.get("")
async def reviews(
    _: Annotated[CurrentUser, Depends(require_admin)],
    status: str | None = None,
    classification: str | None = None,
    route: str | None = None,
    tool: str | None = None,
    issue_code: str | None = None,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    return await list_failure_reviews(
        get_write_pool(),
        status=status,
        classification=classification,
        route=route,
        tool=tool,
        issue_code=issue_code,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )


@router.get("/{review_id}")
async def review_detail(
    review_id: int, _: Annotated[CurrentUser, Depends(require_admin)]
) -> dict[str, Any]:
    result = await get_failure_review(get_write_pool(), review_id)
    if result is None:
        raise HTTPException(404, "검토 항목을 찾을 수 없습니다")
    emit_event("admin.review.viewed", "admin_review", force=True, review_id=review_id)
    return result


@router.patch("/{review_id}")
async def patch_review(
    review_id: int,
    body: ReviewPatch,
    _: Annotated[CurrentUser, Depends(require_admin)],
) -> dict[str, Any]:
    result = await update_failure_review(
        get_write_pool(),
        review_id,
        body.version,
        body.model_dump(exclude={"version"}, exclude_unset=True),
    )
    if result is None:
        raise HTTPException(
            409, "다른 관리자가 먼저 수정했습니다. 최신 데이터를 다시 불러오세요"
        )
    return result
