"""대시보드 계약 실행, 부분 실패, 페이지네이션과 스냅샷 캐시."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from psycopg.rows import dict_row

from core.postgres import get_pool
from dashboard.contracts import get_card_contract, load_dashboard_contracts

QUERY_TIMEOUT_SECONDS = 3.0
OVERVIEW_TIMEOUT_SECONDS = 5.0
CACHE_TTL_SECONDS = 300.0
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@dataclass(slots=True)
class DashboardServiceError(Exception):
    status_code: int
    code: str
    message: str


_overview_cache: tuple[float, dict[str, Any]] | None = None
_cache_lock = asyncio.Lock()


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _json_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_value(value) for key, value in row.items()}


async def _execute_rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    pool = get_pool()
    async with pool.connection() as connection:
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(sql, params)
            rows = await cursor.fetchall()
    return [_json_row(dict(row)) for row in rows]


async def _execute_with_timeout(
    sql: str, params: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    return await asyncio.wait_for(
        _execute_rows(sql, params), timeout=QUERY_TIMEOUT_SECONDS
    )


def _ordered_query(contract: dict[str, Any], sort: str, direction: str) -> str:
    sort_expression = contract["allowedSorts"][sort]
    return (
        f"{contract['sql']} ORDER BY {sort_expression} {direction.upper()}, "
        f"{contract['tieBreak']} LIMIT %s OFFSET %s"
    )


async def _load_card(
    card_key: str,
    *,
    page: int,
    page_size: int,
    sort: str | None,
    direction: str,
) -> dict[str, Any]:
    contract = get_card_contract(card_key)
    if contract is None:
        raise DashboardServiceError(
            400, "INVALID_CARD_KEY", "지원하지 않는 대시보드 카드입니다."
        )
    actual_sort = sort or contract["defaultSort"]
    if actual_sort not in contract["allowedSorts"]:
        raise DashboardServiceError(
            400, "INVALID_SORT_FIELD", "지원하지 않는 정렬 필드입니다."
        )
    normalized_direction = direction.lower()
    if normalized_direction not in {"asc", "desc"}:
        raise DashboardServiceError(
            400, "INVALID_SORT_DIRECTION", "정렬 방향은 asc 또는 desc여야 합니다."
        )

    query = _ordered_query(contract, actual_sort, normalized_direction)
    count_query = (
        f"SELECT COUNT(*)::int AS total FROM ({contract['sql']}) dashboard_rows"
    )
    offset = (page - 1) * page_size
    rows_task = _execute_with_timeout(query, (page_size, offset))
    total_task = _execute_with_timeout(count_query)
    rows, total_rows = await asyncio.gather(rows_task, total_task)
    total = int(total_rows[0]["total"]) if total_rows else 0
    return {
        "key": card_key,
        "title": contract["title"],
        "kind": contract["kind"],
        "status": "ready",
        "columns": contract["columns"],
        "sortableColumns": list(contract["allowedSorts"]),
        "rows": rows,
        "page": page,
        "pageSize": page_size,
        "total": total,
        "sort": actual_sort,
        "direction": normalized_direction,
        **(
            {
                "entityType": contract["entityType"],
                "entityIdField": contract["entityIdField"],
            }
            if contract.get("entityType")
            else {}
        ),
    }


async def get_dashboard_card(
    card_key: str,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    sort: str | None = None,
    direction: str = "desc",
) -> dict[str, Any]:
    if page < 1:
        raise DashboardServiceError(400, "INVALID_PAGE", "page는 1 이상이어야 합니다.")
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise DashboardServiceError(
            400,
            "INVALID_PAGE_SIZE",
            f"pageSize는 1 이상 {MAX_PAGE_SIZE} 이하여야 합니다.",
        )
    try:
        return await _load_card(
            card_key,
            page=page,
            page_size=page_size,
            sort=sort,
            direction=direction,
        )
    except DashboardServiceError:
        raise
    except Exception as exc:
        raise DashboardServiceError(
            503, "DASHBOARD_QUERY_FAILED", "해당 정보를 불러오지 못했습니다."
        ) from exc


async def _load_kpi(key: str, contract: dict[str, Any]) -> dict[str, Any]:
    rows = await _execute_with_timeout(contract["sql"])
    value = int(rows[0]["value"]) if rows else 0
    return {
        "key": key,
        "label": contract["label"],
        "value": value,
        "unit": contract["unit"],
        "status": "ready",
    }


async def _guarded_kpi(key: str, contract: dict[str, Any]) -> dict[str, Any]:
    try:
        return await _load_kpi(key, contract)
    except Exception:
        return {
            "key": key,
            "label": contract["label"],
            "value": None,
            "unit": contract["unit"],
            "status": "error",
        }


async def _guarded_card(key: str) -> dict[str, Any]:
    contract = get_card_contract(key)
    assert contract is not None
    try:
        return await _load_card(
            key,
            page=1,
            page_size=5,
            sort=None,
            direction="desc",
        )
    except Exception:
        return {
            "key": key,
            "title": contract["title"],
            "kind": contract["kind"],
            "status": "error",
            "columns": contract["columns"],
            "sortableColumns": list(contract["allowedSorts"]),
            "rows": [],
            "total": 0,
        }


async def _build_overview() -> dict[str, Any]:
    contracts = load_dashboard_contracts()
    kpi_tasks = [
        _guarded_kpi(key, contract) for key, contract in contracts["kpis"].items()
    ]
    card_tasks = [_guarded_card(key) for key in contracts["cards"]]
    results = await asyncio.wait_for(
        asyncio.gather(*kpi_tasks, *card_tasks), timeout=OVERVIEW_TIMEOUT_SECONDS
    )
    kpis = results[: len(kpi_tasks)]
    cards = results[len(kpi_tasks) :]
    errors = [
        {
            "key": item["key"],
            "code": "DASHBOARD_QUERY_FAILED",
            "message": "해당 정보를 불러오지 못했습니다.",
        }
        for item in [*kpis, *cards]
        if item["status"] == "error"
    ]
    if len(errors) == len(kpis) + len(cards):
        raise DashboardServiceError(
            503, "DASHBOARD_QUERY_FAILED", "대시보드 정보를 불러오지 못했습니다."
        )
    return {
        "snapshot": contracts["snapshot"],
        "kpis": kpis,
        "cards": cards,
        "errors": errors,
    }


async def get_dashboard_overview() -> dict[str, Any]:
    global _overview_cache
    now = time.monotonic()
    if _overview_cache and now - _overview_cache[0] < CACHE_TTL_SECONDS:
        return _overview_cache[1]

    async with _cache_lock:
        now = time.monotonic()
        if _overview_cache and now - _overview_cache[0] < CACHE_TTL_SECONDS:
            return _overview_cache[1]
        try:
            overview = await _build_overview()
        except DashboardServiceError:
            raise
        except Exception as exc:
            raise DashboardServiceError(
                503,
                "DASHBOARD_QUERY_FAILED",
                "대시보드 정보를 불러오지 못했습니다.",
            ) from exc
        _overview_cache = (now, overview)
        return overview


def clear_dashboard_cache() -> None:
    global _overview_cache
    _overview_cache = None
