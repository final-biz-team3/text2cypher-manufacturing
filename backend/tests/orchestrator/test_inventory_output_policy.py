"""집계 재고와 위치별 재고 output policy의 의미 기반 회귀 테스트."""

import pytest

from tests.orchestrator.plan_outputs_test_support import (
    AGGREGATE_STOCK_OUTPUTS,
    LOCATION_ROW_OUTPUTS,
    complete_outputs,
)


@pytest.mark.parametrize(
    ("query", "entity", "confirmed_entity", "selected_outputs"),
    [
        pytest.param(
            "Nebula Bracket 재고 어디에 몇 개 있어?",
            {"productId": 8001, "productName": "Nebula Bracket"},
            None,
            ["productId", "productName", "actualStock"],
            id="korean-where",
        ),
        pytest.param(
            "Show the inventory location and quantity for product X",
            {"productId": 8001, "productName": "X"},
            None,
            ["actualStock", "productName"],
            id="english-location",
        ),
        pytest.param(
            "Show the inventory locations and quantity for product X",
            {"productId": 8001, "productName": "X"},
            None,
            ["actualStock", "productName"],
            id="english-locations",
        ),
        pytest.param(
            "Show wherever the inventory is and its quantity for product X",
            {"productId": 8001, "productName": "X"},
            None,
            ["actualStock", "productName"],
            id="wherever",
        ),
        pytest.param(
            "Show anywhere the inventory is and its quantity for product X",
            {"productId": 8001, "productName": "X"},
            None,
            ["actualStock", "productName"],
            id="anywhere",
        ),
        pytest.param(
            "Show inventory by location for product Location",
            {"productId": 8001, "productName": "Location"},
            None,
            ["productId", "productName", "actualStock"],
            id="matching-product-name",
        ),
        pytest.param(
            "Show inventory by location for product location",
            {"productId": 8001, "productName": "location"},
            None,
            ["productId", "productName", "actualStock"],
            id="matching-product-name-lowercase",
        ),
        pytest.param(
            "Show inventory at each location for product Location",
            {"productId": 8001, "productName": "Location"},
            None,
            ["productId", "productName", "actualStock"],
            id="each-location",
        ),
        pytest.param(
            "Show Locatoin inventory by location",
            {"productId": 8001, "productName": "Location"},
            {"productId": 8001, "productName": "Location"},
            ["productId", "productName", "actualStock"],
            id="confirmed-typo-with-location",
        ),
        pytest.param(
            "Show inventory at location Forge Bay",
            {"locationId": 91, "locationName": "Forge Bay"},
            None,
            ["locationId", "locationName", "actualStock"],
            id="named-location-english",
        ),
        pytest.param(
            "작업장 Forge Bay 재고 수량을 보여줘",
            {"locationId": 91, "locationName": "Forge Bay"},
            None,
            ["locationId", "locationName", "actualStock"],
            id="named-location-korean",
        ),
        pytest.param(
            "위치 제품의 재고를 위치별로 보여줘",
            {"productId": 8001, "productName": "위치"},
            {"productId": 8001, "productName": "위치"},
            ["productId", "productName", "actualStock"],
            id="korean-entity-and-dimension",
        ),
        pytest.param(
            "위치 제품의 재고 위치를 보여줘",
            {"productId": 8001, "productName": "위치"},
            {"productId": 8001, "productName": "위치"},
            ["productId", "productName", "actualStock"],
            id="korean-inventory-location",
        ),
        pytest.param(
            "Show inventory per warehouse location for product Location",
            {"productId": 8001, "productName": "Location"},
            None,
            ["productId", "productName", "actualStock"],
            id="warehouse-location",
        ),
        pytest.param(
            "Show Shelf inventory with shelf details",
            {"productId": 8001, "productName": "Shelf"},
            {"productId": 8001, "productName": "Shelf"},
            ["productId", "productName", "actualStock"],
            id="shelf-details",
        ),
        pytest.param(
            "Show inventory for product Bin and list the bin values",
            {"productId": 8001, "productName": "Bin"},
            None,
            ["productId", "productName", "actualStock"],
            id="bin-values",
        ),
        pytest.param(
            "What is the location of inventory for product Location?",
            {"productId": 8001, "productName": "Location"},
            None,
            ["productId", "productName", "actualStock"],
            id="location-of-inventory",
        ),
        pytest.param(
            "Show inventory location for product Locatoin",
            {"productId": 8001, "productName": "Location"},
            {"productId": 8001, "productName": "Location"},
            ["productId", "productName", "actualStock"],
            id="typo-confirmation-keeps-property",
        ),
    ],
)
def test_location_detail_contract(
    query: str,
    entity: object,
    confirmed_entity: object,
    selected_outputs: list[str],
) -> None:
    assert (
        complete_outputs(
            query=query,
            entity=entity,
            confirmed_entity=confirmed_entity,
            selected_outputs=selected_outputs,
        )
        == LOCATION_ROW_OUTPUTS
    )


@pytest.mark.parametrize(
    ("query", "entity", "confirmed_entity", "expected"),
    [
        pytest.param(
            "Show total inventory for product Location",
            {"productId": 8001, "productName": "Location"},
            None,
            AGGREGATE_STOCK_OUTPUTS,
            id="product-named-location",
        ),
        pytest.param(
            "Show total inventory for product Location; is location in stock?",
            {"productId": 8001, "productName": "Location"},
            None,
            AGGREGATE_STOCK_OUTPUTS,
            id="case-varied-repeat",
        ),
        pytest.param(
            "Show total inventory for product Locations",
            {"productId": 8001, "productName": "Location"},
            {"productId": 8001, "productName": "Location"},
            AGGREGATE_STOCK_OUTPUTS,
            id="plural-confirmed-name",
        ),
        pytest.param(
            "Show location inventory; is location in stock?",
            {"productId": 8001, "productName": "location"},
            None,
            AGGREGATE_STOCK_OUTPUTS,
            id="lowercase-product-name",
        ),
        pytest.param(
            "Show total inventory for supplier location",
            {"supplierId": 91, "supplierName": "location"},
            None,
            [*AGGREGATE_STOCK_OUTPUTS, "supplierId", "supplierName"],
            id="supplier-named-location",
        ),
        pytest.param(
            "Compare total inventory for Frame and Location Frame",
            [
                {"productId": 8001, "productName": "Frame"},
                {"productId": 8002, "productName": "Location Frame"},
            ],
            None,
            AGGREGATE_STOCK_OUTPUTS,
            id="overlapping-product-names",
        ),
    ],
)
def test_entity_names_do_not_create_location_detail_intent(
    query: str,
    entity: object,
    confirmed_entity: object,
    expected: list[str],
) -> None:
    assert (
        complete_outputs(
            query=query,
            entity=entity,
            confirmed_entity=confirmed_entity,
            selected_outputs=["productId", "productName", "actualStock"],
        )
        == expected
    )


@pytest.mark.parametrize(
    ("query", "entity", "selected_outputs", "expected"),
    [
        pytest.param(
            "Show the inventory locations, quantities, and standard cost for product X",
            {"productId": 8001, "productName": "X"},
            ["actualStock", "productName", "standardCost"],
            [*LOCATION_ROW_OUTPUTS, "standardCost"],
            id="english-scalar",
        ),
        pytest.param(
            "Nebula Bracket 재고가 어디에 몇 개 있고 원가는 얼마야?",
            {"productId": 8001, "productName": "Nebula Bracket"},
            ["productId", "productName", "actualStock", "standardCost"],
            [*LOCATION_ROW_OUTPUTS, "standardCost"],
            id="korean-scalar",
        ),
        pytest.param(
            "Show inventory by location and safety-stock shortage for product X",
            {"productId": 8001, "productName": "X"},
            [
                "productId",
                "productName",
                "safetyStockLevel",
                "actualStock",
                "shortageQty",
            ],
            [
                *LOCATION_ROW_OUTPUTS,
                "safetyStockLevel",
                "actualStock",
                "shortageQty",
            ],
            id="derived-shortage",
        ),
        pytest.param(
            "Show inventory locations and average list price",
            None,
            ["productName", "averageListPrice"],
            [*LOCATION_ROW_OUTPUTS, "averageListPrice"],
            id="aggregate-extra",
        ),
    ],
)
def test_location_detail_preserves_requested_non_location_outputs(
    query: str,
    entity: object,
    selected_outputs: list[str],
    expected: list[str],
) -> None:
    assert (
        complete_outputs(
            query=query,
            entity=entity,
            selected_outputs=selected_outputs,
        )
        == expected
    )


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("Show stock allocation for product X", id="allocation"),
        pytest.param(
            "Show product name and total inventory for product X",
            id="product-owner-term",
        ),
        pytest.param(
            "Show total production inventory for product X",
            id="production-owner-term",
        ),
    ],
)
def test_non_location_terms_keep_aggregate_stock(query: str) -> None:
    assert (
        complete_outputs(
            query=query,
            entity={"productId": 8001, "productName": "X"},
            selected_outputs=AGGREGATE_STOCK_OUTPUTS,
        )
        == AGGREGATE_STOCK_OUTPUTS
    )
