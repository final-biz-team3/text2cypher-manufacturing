"""build_visualization_spec의 규칙 기반 시각화 판정을 테스트한다."""

from typing import Any, cast

from orchestrator.nodes.build_visualization import build_visualization_spec
from orchestrator.state import ComposedResult


def _composed_result(rows: list[dict[str, Any]], **overrides: Any) -> ComposedResult:
    result: ComposedResult = {
        "mode": "joined",
        "rows": rows,
        "sections": {},
        "error": None,
        "empty_reason": None,
        "total_count": len(rows),
        "truncated": False,
    }
    return cast(ComposedResult, {**result, **overrides})


def test_single_row_with_two_numeric_fields_becomes_kpi() -> None:
    composed = _composed_result(
        [
            {
                "productId": 956,
                "productName": "Touring-1000 Yellow, 54",
                "listPrice": 2384.07,
                "standardCost": 1912.42,
            }
        ]
    )

    spec = build_visualization_spec(composed)

    assert spec == {
        "type": "kpi",
        "title": "Touring-1000 Yellow, 54",
        "items": [
            {"label": "정가", "value": 2384.07},
            {"label": "표준원가", "value": 1912.42},
        ],
    }


def test_single_row_with_three_numeric_fields_becomes_kpi() -> None:
    composed = _composed_result(
        [
            {
                "productId": 492,
                "productName": "Paint - Black",
                "safetyStockLevel": 100,
                "actualStock": 40,
                "shortageQty": 60,
            }
        ]
    )

    spec = build_visualization_spec(composed)

    assert spec is not None
    assert spec["type"] == "kpi"
    assert spec["items"] == [
        {"label": "안전재고", "value": 100},
        {"label": "실제재고", "value": 40},
        {"label": "부족 수량", "value": 60},
    ]


def test_single_row_without_numeric_fields_has_no_visualization() -> None:
    composed = _composed_result(
        [
            {
                "productId": 956,
                "productName": "Touring-1000 Yellow, 54",
                "productNumber": "BK-M18B-58",
                "color": "Black",
                "size": "58",
            }
        ]
    )

    assert build_visualization_spec(composed) is None


def test_single_row_with_too_many_numeric_fields_has_no_visualization() -> None:
    row = {"productId": 1, "a": 1, "b": 2, "c": 3, "d": 4, "e": 5}

    assert build_visualization_spec(_composed_result([row])) is None


def test_ranked_rows_with_one_label_and_one_numeric_column_becomes_bar() -> None:
    rows = [
        {"productId": 1, "productName": "Product A", "totalOrderQty": 8420},
        {"productId": 2, "productName": "Product B", "totalOrderQty": 6830},
        {"productId": 3, "productName": "Product C", "totalOrderQty": 5210},
    ]

    spec = build_visualization_spec(_composed_result(rows))

    assert spec == {
        "type": "bar",
        "title": None,
        "categoryLabel": "제품명",
        "series": [{"key": "value", "label": "판매량", "unit": "개"}],
        "data": [
            {"category": "Product A", "value": 8420},
            {"category": "Product B", "value": 6830},
            {"category": "Product C", "value": 5210},
        ],
    }


def test_bar_rows_with_two_text_columns_has_no_visualization() -> None:
    rows = [
        {
            "workOrderId": 1,
            "productId": 10,
            "productName": "Product A",
            "scrappedQty": 54,
            "scrapReasonId": 13,
            "scrapReasonName": "Thermoform temperature too low",
        },
        {
            "workOrderId": 2,
            "productId": 20,
            "productName": "Product B",
            "scrappedQty": 30,
            "scrapReasonId": 13,
            "scrapReasonName": "Thermoform temperature too low",
        },
    ]

    assert build_visualization_spec(_composed_result(rows)) is None


def test_bar_rows_with_two_numeric_columns_has_no_visualization() -> None:
    rows = [
        {
            "categoryId": 1,
            "categoryName": "Components",
            "productCount": 12,
            "averageListPrice": 45.5,
        },
        {
            "categoryId": 2,
            "categoryName": "Bikes",
            "productCount": 8,
            "averageListPrice": 1200.0,
        },
    ]

    assert build_visualization_spec(_composed_result(rows)) is None


def test_more_than_twenty_rows_becomes_histogram() -> None:
    rows = [
        {"productId": i, "productName": f"Product {i}", "totalOrderQty": i}
        for i in range(21)
    ]

    spec = build_visualization_spec(_composed_result(rows))

    assert spec is not None
    assert spec["type"] == "histogram"
    assert sum(bin_["value"] for bin_ in spec["data"]) == 21


def test_shortage_pair_rows_become_ranked_progress_sorted_by_shortage() -> None:
    rows = [
        {
            "componentId": 1,
            "componentName": "Bolt M6",
            "requiredQty": 100,
            "actualStock": 90,
            "shortageQty": 10,
        },
        {
            "componentId": 2,
            "componentName": "Frame Weld",
            "requiredQty": 50,
            "actualStock": 10,
            "shortageQty": 40,
        },
        {
            "componentId": 3,
            "componentName": "Seat Post",
            "requiredQty": 20,
            "actualStock": 20,
            "shortageQty": 0,
        },
    ]

    spec = build_visualization_spec(_composed_result(rows))

    assert spec == {
        "type": "ranked_progress",
        "title": None,
        "rankedItems": [
            {
                "rank": 1,
                "title": "Frame Weld",
                "actual": 10,
                "required": 50,
                "shortageQty": 40,
                "fulfillmentPct": 20.0,
            },
            {
                "rank": 2,
                "title": "Bolt M6",
                "actual": 90,
                "required": 100,
                "shortageQty": 10,
                "fulfillmentPct": 90.0,
            },
            {
                "rank": 3,
                "title": "Seat Post",
                "actual": 20,
                "required": 20,
                "shortageQty": 0,
                "fulfillmentPct": 100.0,
            },
        ],
        "unit": "개",
        "entityLabel": "Product",
    }


def test_ranked_progress_caps_at_five_items() -> None:
    rows = [
        {
            "componentId": i,
            "componentName": f"Part {i}",
            "requiredQty": 100,
            "actualStock": 100 - i,
            "shortageQty": i,
        }
        for i in range(1, 8)
    ]

    spec = build_visualization_spec(_composed_result(rows))

    assert spec is not None
    assert spec["type"] == "ranked_progress"
    ranked_items = spec["rankedItems"]
    assert len(ranked_items) == 5
    assert [item["rank"] for item in ranked_items] == [1, 2, 3, 4, 5]
    assert ranked_items[0]["title"] == "Part 7"


def test_ranked_progress_computes_shortage_when_column_missing() -> None:
    rows = [
        {
            "productId": 1,
            "productName": "Product A",
            "safetyStockLevel": 100,
            "actualStock": 60,
        },
        {
            "productId": 2,
            "productName": "Product B",
            "safetyStockLevel": 40,
            "actualStock": 10,
        },
    ]

    spec = build_visualization_spec(_composed_result(rows))

    assert spec is not None
    assert spec["type"] == "ranked_progress"
    ranked_items = spec["rankedItems"]
    assert ranked_items[0]["title"] == "Product A"
    assert ranked_items[0]["shortageQty"] == 40
    assert ranked_items[1]["title"] == "Product B"
    assert ranked_items[1]["shortageQty"] == 30


def test_ranked_progress_excludes_rows_with_nonpositive_required() -> None:
    rows = [
        {
            "productId": 1,
            "productName": "Product A",
            "requiredQty": 0,
            "actualStock": 0,
        },
        {
            "productId": 2,
            "productName": "Product B",
            "requiredQty": 50,
            "actualStock": 10,
        },
    ]

    spec = build_visualization_spec(_composed_result(rows))

    assert spec is not None
    assert spec["type"] == "ranked_progress"
    assert len(spec["rankedItems"]) == 1
    assert spec["rankedItems"][0]["title"] == "Product B"


def test_shortage_pair_with_extra_numeric_column_has_no_visualization() -> None:
    rows = [
        {
            "productId": 1,
            "productName": "Product A",
            "requiredQty": 100,
            "actualStock": 90,
            "listPrice": 12.5,
        },
        {
            "productId": 2,
            "productName": "Product B",
            "requiredQty": 50,
            "actualStock": 10,
            "listPrice": 8.0,
        },
    ]

    spec = build_visualization_spec(_composed_result(rows))

    assert spec is None


def test_histogram_bins_cover_all_values_and_use_field_label() -> None:
    # 텍스트 컬럼이 없어 bar 규칙(텍스트 1개+숫자 1개)에 안 걸리는 경우다 -
    # productName이 있었다면 12행은 bar가 먼저 채간다.
    rows = [{"productId": i, "listPrice": float(i * 10)} for i in range(1, 13)]

    spec = build_visualization_spec(_composed_result(rows))

    assert spec is not None
    assert spec["type"] == "histogram"
    assert spec["categoryLabel"] == "정가 구간"
    assert spec["series"] == [{"key": "value", "label": "건수", "unit": "건"}]
    assert sum(bin_["value"] for bin_ in spec["data"]) == 12
    assert 4 <= len(spec["data"]) <= 10


def test_histogram_requires_minimum_rows() -> None:
    rows = [
        {"workOrderId": 1, "productId": 10, "locationId": 1, "scrappedQty": 5},
        {"workOrderId": 2, "productId": 20, "locationId": 1, "scrappedQty": 8},
        {"workOrderId": 3, "productId": 30, "locationId": 1, "scrappedQty": 3},
    ]

    assert build_visualization_spec(_composed_result(rows)) is None


def test_histogram_with_constant_values_has_no_visualization() -> None:
    rows = [
        {"workOrderId": i, "productId": i, "locationId": 1, "scrappedQty": 10}
        for i in range(1, 8)
    ]

    assert build_visualization_spec(_composed_result(rows)) is None


def test_two_unrelated_numeric_columns_becomes_scatter() -> None:
    rows = [
        {
            "productId": 1,
            "productName": "Product A",
            "listPrice": 1200.0,
            "standardCost": 800.0,
        },
        {
            "productId": 2,
            "productName": "Product B",
            "listPrice": 900.0,
            "standardCost": 650.0,
        },
        {
            "productId": 3,
            "productName": "Product C",
            "listPrice": 1500.0,
            "standardCost": 1100.0,
        },
    ]

    spec = build_visualization_spec(_composed_result(rows))

    assert spec == {
        "type": "scatter",
        "title": None,
        "xLabel": "정가",
        "yLabel": "표준원가",
        "points": [
            {"x": 1200.0, "y": 800.0, "label": "Product A"},
            {"x": 900.0, "y": 650.0, "label": "Product B"},
            {"x": 1500.0, "y": 1100.0, "label": "Product C"},
        ],
        "xUnit": "원",
        "yUnit": "원",
    }


def test_scatter_requires_minimum_points() -> None:
    rows = [
        {
            "productId": 1,
            "productName": "Product A",
            "listPrice": 1200.0,
            "standardCost": 800.0,
        },
        {
            "productId": 2,
            "productName": "Product B",
            "listPrice": 900.0,
            "standardCost": 650.0,
        },
    ]

    assert build_visualization_spec(_composed_result(rows)) is None


def test_ranked_progress_entity_label_from_supplier_title_column() -> None:
    rows = [
        {
            "supplierId": 1,
            "supplierName": "Acme Metals",
            "requiredQty": 100,
            "actualStock": 40,
        },
        {
            "supplierId": 2,
            "supplierName": "Global Fasteners",
            "requiredQty": 50,
            "actualStock": 20,
        },
    ]

    spec = build_visualization_spec(_composed_result(rows))

    assert spec is not None
    assert spec["type"] == "ranked_progress"
    assert spec["entityLabel"] == "Supplier"


def test_ranked_progress_omits_entity_label_for_unknown_title_column() -> None:
    rows = [
        {"batchLabel": "Batch A", "requiredQty": 100, "actualStock": 40},
        {"batchLabel": "Batch B", "requiredQty": 50, "actualStock": 20},
    ]

    spec = build_visualization_spec(_composed_result(rows))

    assert spec is not None
    assert spec["type"] == "ranked_progress"
    assert "entityLabel" not in spec


def test_separate_mode_has_no_visualization() -> None:
    composed = _composed_result([], mode="separate")

    assert build_visualization_spec(composed) is None


def test_empty_rows_has_no_visualization() -> None:
    assert build_visualization_spec(_composed_result([])) is None
