from datetime import date
from typing import Any

import pytest

import dashboard.process as process
from dashboard.contracts import load_process_dashboard_contracts
from dashboard.service import DashboardServiceError


def test_process_contract_uses_actual_process_dates() -> None:
    contracts = load_process_dashboard_contracts()
    serialized = str(contracts).lower()

    assert "actualstartdate" in serialized
    assert "actualenddate" in serialized
    assert "workorder" in serialized
    assert "{bucket}" in contracts["trend"]["sql"]


@pytest.mark.parametrize(
    ("from_value", "to_value", "code"),
    [
        ("2014-05-01", None, "INCOMPLETE_DATE_RANGE"),
        ("not-a-date", "2014-05-31", "INVALID_DATE"),
        ("2014-06-01", "2014-05-01", "INVALID_DATE_RANGE"),
        ("2011-01-01", "2014-05-01", "DATE_OUT_OF_RANGE"),
    ],
)
def test_process_period_validation(
    from_value: str | None, to_value: str | None, code: str
) -> None:
    with pytest.raises(DashboardServiceError) as exc_info:
        process._resolve_period(
            from_value,
            to_value,
            date(2011, 6, 3),
            date(2014, 6, 28),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == code


def test_default_process_period_is_latest_thirty_days() -> None:
    result = process._resolve_period(None, None, date(2011, 6, 3), date(2014, 6, 28))

    assert result == (date(2014, 5, 30), date(2014, 6, 28))


def test_process_granularity_validation() -> None:
    with pytest.raises(DashboardServiceError) as exc_info:
        process._resolve_granularity(
            "week",
            date(2014, 5, 1),
            date(2014, 5, 31),
            93,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "INVALID_GRANULARITY"


@pytest.mark.parametrize(
    ("requested_granularity", "expected_granularity"),
    [(None, "month"), ("year", "year")],
)
async def test_process_overview_uses_requested_or_automatic_granularity(
    monkeypatch: pytest.MonkeyPatch,
    requested_granularity: str | None,
    expected_granularity: str,
) -> None:
    calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fake_execute(
        sql: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        calls.append((sql, params))
        if 'AS "minDate"' in sql:
            return [{"minDate": "2011-06-03", "maxDate": "2014-06-28"}]
        if 'AS "startedWorkOrderCount"' in sql:
            return [
                {
                    "startedWorkOrderCount": 10,
                    "completedWorkOrderCount": 9,
                    "operationCount": 20,
                    "scrappedWorkOrderCount": 2,
                    "scrappedQty": 3,
                }
            ]
        if "generate_series" in sql:
            return [
                {
                    "date": "2014-01-01",
                    "startedWorkOrderCount": 10,
                    "completedWorkOrderCount": 9,
                    "scrappedQty": 3,
                }
            ]
        return [{"locationId": 1, "operationCount": 20, "workOrderCount": 10}]

    monkeypatch.setattr(process, "_execute_with_timeout", fake_execute)
    process.clear_process_cache()

    result = await process.get_process_overview(
        "2014-01-01", "2014-06-28", requested_granularity
    )

    assert result["period"]["granularity"] == expected_granularity
    assert result["kpis"][0]["value"] == 10
    trend_query = next(sql for sql, _ in calls if "generate_series" in sql)
    assert f"date_trunc('{expected_granularity}'" in trend_query
    assert result["locations"][0]["locationId"] == 1
