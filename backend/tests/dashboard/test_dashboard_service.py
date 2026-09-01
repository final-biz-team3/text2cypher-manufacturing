from typing import Any

import pytest

import dashboard.service as service
from dashboard.contracts import load_dashboard_contracts
from dashboard.service import DashboardServiceError


def test_dashboard_contract_has_required_kpis_and_cards() -> None:
    contracts = load_dashboard_contracts()

    assert list(contracts["kpis"]) == [
        "product_count",
        "active_supplier_count",
        "purchased_product_count",
        "low_stock_product_count",
        "work_order_count",
        "scrapped_work_order_count",
    ]
    assert len(contracts["cards"]) == 7
    low_stock_sql = contracts["cards"]["low_stock_top5"]["sql"].lower()
    assert "left join inventory" in low_stock_sql
    assert "coalesce(i.actual_stock, 0)" in low_stock_sql


def test_contract_uses_only_supported_rejection_measure() -> None:
    serialized = str(load_dashboard_contracts()).lower()

    assert "rejectedqty" in serialized
    assert "receivedqty" not in serialized
    assert "leadtime" not in serialized
    assert "반려율" not in serialized


async def test_card_query_uses_whitelisted_sort_and_deterministic_tie_break(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fake_execute(
        sql: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        calls.append((sql, params))
        if "COUNT(*)" in sql:
            return [{"total": 1}]
        return [{"productId": 1, "shortageQty": 10}]

    monkeypatch.setattr(service, "_execute_with_timeout", fake_execute)

    result = await service.get_dashboard_card(
        "low_stock_top5",
        page=2,
        page_size=20,
        sort="shortageQty",
        direction="desc",
    )

    assert result["page"] == 2
    assert result["total"] == 1
    row_query, row_params = calls[0]
    assert 'ORDER BY "shortageQty" DESC, "productId" ASC' in row_query
    assert row_params == (20, 20)


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"page": 0}, "INVALID_PAGE"),
        ({"page_size": 101}, "INVALID_PAGE_SIZE"),
        ({"sort": "DROP TABLE production.product"}, "INVALID_SORT_FIELD"),
        ({"direction": "sideways"}, "INVALID_SORT_DIRECTION"),
    ],
)
async def test_card_parameters_are_rejected_before_sql_execution(
    kwargs: dict[str, Any], code: str
) -> None:
    with pytest.raises(DashboardServiceError) as exc_info:
        await service.get_dashboard_card("low_stock_top5", **kwargs)
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == code


async def test_overview_keeps_successful_sections_when_one_query_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_kpi(key: str, contract: dict[str, Any]) -> dict[str, Any]:
        return {
            "key": key,
            "label": contract["label"],
            "value": None if key == "product_count" else 1,
            "unit": contract["unit"],
            "status": "error" if key == "product_count" else "ready",
        }

    async def fake_card(key: str) -> dict[str, Any]:
        contract = load_dashboard_contracts()["cards"][key]
        return {
            "key": key,
            "title": contract["title"],
            "kind": "table",
            "status": "ready",
            "columns": contract["columns"],
            "rows": [],
            "total": 0,
        }

    monkeypatch.setattr(service, "_guarded_kpi", fake_kpi)
    monkeypatch.setattr(service, "_guarded_card", fake_card)

    result = await service._build_overview()

    assert len(result["kpis"]) == 6
    assert len(result["cards"]) == 7
    assert result["errors"] == [
        {
            "key": "product_count",
            "code": "DASHBOARD_QUERY_FAILED",
            "message": "해당 정보를 불러오지 못했습니다.",
        }
    ]
