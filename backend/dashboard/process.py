"""선택 기간의 작업지시·공정 현황을 조회한다."""

from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta
from typing import Any

from dashboard.contracts import load_process_dashboard_contracts
from dashboard.service import DashboardServiceError, _execute_with_timeout

PROCESS_CACHE_TTL_SECONDS = 300.0
MAX_CACHE_ENTRIES = 128
PROCESS_GRANULARITIES = {"day", "month", "year"}

_process_cache: dict[tuple[date, date, str], tuple[float, dict[str, Any]]] = {}
_process_cache_lock = asyncio.Lock()


def _parse_date(value: str | None, field: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DashboardServiceError(
            400, "INVALID_DATE", f"{field}는 YYYY-MM-DD 형식이어야 합니다."
        ) from exc


async def _load_available_range() -> tuple[date, date]:
    sql = load_process_dashboard_contracts()["range"]["sql"]
    rows = await _execute_with_timeout(sql)
    if not rows or rows[0]["minDate"] is None or rows[0]["maxDate"] is None:
        raise DashboardServiceError(
            503, "PROCESS_DATA_UNAVAILABLE", "공정 데이터 기간을 확인할 수 없습니다."
        )
    return date.fromisoformat(rows[0]["minDate"]), date.fromisoformat(
        rows[0]["maxDate"]
    )


def _resolve_period(
    from_value: str | None,
    to_value: str | None,
    available_from: date,
    available_to: date,
) -> tuple[date, date]:
    if (from_value is None) != (to_value is None):
        raise DashboardServiceError(
            400,
            "INCOMPLETE_DATE_RANGE",
            "from과 to는 함께 입력해야 합니다.",
        )

    if from_value is None:
        return max(available_from, available_to - timedelta(days=29)), available_to

    from_date = _parse_date(from_value, "from")
    to_date = _parse_date(to_value, "to")
    assert from_date is not None and to_date is not None
    if from_date > to_date:
        raise DashboardServiceError(
            400, "INVALID_DATE_RANGE", "from은 to보다 늦을 수 없습니다."
        )
    if from_date < available_from or to_date > available_to:
        raise DashboardServiceError(
            400,
            "DATE_OUT_OF_RANGE",
            "선택한 기간이 공정 데이터 범위를 벗어났습니다.",
        )
    return from_date, to_date


def _resolve_granularity(
    value: str | None,
    from_date: date,
    to_date: date,
    daily_max_days: int,
) -> str:
    if value is not None:
        if value not in PROCESS_GRANULARITIES:
            raise DashboardServiceError(
                400,
                "INVALID_GRANULARITY",
                "granularity는 day, month, year 중 하나여야 합니다.",
            )
        return value

    day_count = (to_date - from_date).days + 1
    return "day" if day_count <= daily_max_days else "month"


async def _build_process_overview(
    from_date: date,
    to_date: date,
    available_from: date,
    available_to: date,
    granularity: str,
) -> dict[str, Any]:
    contracts = load_process_dashboard_contracts()
    trend_sql = contracts["trend"]["sql"].format(bucket=granularity)
    params = (from_date, to_date)

    summary_rows, trend_rows, location_rows = await asyncio.gather(
        _execute_with_timeout(contracts["summary"]["sql"], params),
        _execute_with_timeout(trend_sql, params),
        _execute_with_timeout(contracts["locations"]["sql"], params),
    )
    summary = summary_rows[0] if summary_rows else {}
    kpis = [
        {
            **metric,
            "value": int(summary.get(metric["key"], 0)),
            "status": "ready",
        }
        for metric in contracts["summary"]["metrics"]
    ]
    return {
        "availableRange": {
            "from": available_from.isoformat(),
            "to": available_to.isoformat(),
        },
        "period": {
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "granularity": granularity,
        },
        "kpis": kpis,
        "trend": trend_rows,
        "locations": location_rows,
        "errors": [],
    }


async def get_process_overview(
    from_value: str | None = None,
    to_value: str | None = None,
    granularity: str | None = None,
) -> dict[str, Any]:
    try:
        available_from, available_to = await _load_available_range()
        from_date, to_date = _resolve_period(
            from_value, to_value, available_from, available_to
        )
        contracts = load_process_dashboard_contracts()
        resolved_granularity = _resolve_granularity(
            granularity,
            from_date,
            to_date,
            contracts["trend"]["dailyMaxDays"],
        )
        cache_key = (from_date, to_date, resolved_granularity)
        cached = _process_cache.get(cache_key)
        now = time.monotonic()
        if cached and now - cached[0] < PROCESS_CACHE_TTL_SECONDS:
            return cached[1]

        async with _process_cache_lock:
            cached = _process_cache.get(cache_key)
            now = time.monotonic()
            if cached and now - cached[0] < PROCESS_CACHE_TTL_SECONDS:
                return cached[1]
            result = await _build_process_overview(
                from_date,
                to_date,
                available_from,
                available_to,
                resolved_granularity,
            )
            if len(_process_cache) >= MAX_CACHE_ENTRIES:
                oldest_key = min(_process_cache, key=lambda key: _process_cache[key][0])
                _process_cache.pop(oldest_key, None)
            _process_cache[cache_key] = (now, result)
            return result
    except DashboardServiceError:
        raise
    except Exception as exc:
        raise DashboardServiceError(
            503, "DASHBOARD_QUERY_FAILED", "공정 현황을 불러오지 못했습니다."
        ) from exc


def clear_process_cache() -> None:
    _process_cache.clear()
