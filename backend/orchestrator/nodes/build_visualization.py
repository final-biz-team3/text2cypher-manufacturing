"""composed_result 모양만으로 시각화 타입(KPI/막대/순위 진행률/히스토그램/
산점도)을 결정하는 규칙 엔진.

LLM은 이 판정에 관여하지 않는다 - composed_result는 이미 검증된 구조화
데이터이므로, 행 수·컬럼 타입만 보고 결정론적으로 차트 타입을 고른다.
"""

from decimal import Decimal
from typing import Any, cast

from orchestrator.field_labels import FIELD_LABELS
from orchestrator.state import (
    ComposedResult,
    NodeLabel,
    VisualizationKpiItem,
    VisualizationPoint,
    VisualizationRankedItem,
    VisualizationSeries,
    VisualizationSpec,
)

_MIN_BAR_ROWS = 2
_MAX_BAR_ROWS = 20
_MAX_KPI_ITEMS = 4
_MAX_RANKED_ITEMS = 5
_MIN_HISTOGRAM_ROWS = 5
_MIN_HISTOGRAM_BINS = 4
_MAX_HISTOGRAM_BINS = 10
_MIN_SCATTER_ROWS = 3

# (실제값 필드, 필요/기준값 필드) - 둘 다 숫자 컬럼에 있으면 순위 진행률
# 시각화 후보가 된다. bom_shortage.py가 만드는 requiredQty/actualStock/
# shortageQty 모양, 안전재고 조회의 safetyStockLevel/actualStock 모양 기준.
_SHORTAGE_PAIRS: list[tuple[str, str]] = [
    ("actualStock", "requiredQty"),
    ("actualStock", "safetyStockLevel"),
]
_SHORTAGE_QTY_FIELD = "shortageQty"

# <role>Name/<role>Id 필드명 규칙(schema/graph_schema.yaml outputAliases,
# ontology/manufacturing_terms.yaml entityRoles)에서 role -> 그래프 노드
# 라벨로 되짚는 매핑. 답변 카드에 "이 순위가 어떤 종류인지" 뱃지로 보여줄 때
# 쓴다. 여러 role이 같은 노드로 투영된다(component/finishedProduct/
# rootProduct는 전부 Product).
_ROLE_TO_NODE_LABEL: dict[str, NodeLabel] = {
    "product": "Product",
    "component": "Product",
    "finishedProduct": "Product",
    "rootProduct": "Product",
    "supplier": "Supplier",
    "workOrder": "WorkOrder",
    "routingOperation": "RoutingOperation",
    "location": "Location",
    "scrapReason": "ScrapReason",
}


def _node_label_for_column(key: str) -> NodeLabel | None:
    for suffix in ("Name", "Id"):
        if key.endswith(suffix):
            role = key[: -len(suffix)]
            node_label = _ROLE_TO_NODE_LABEL.get(role)
            if node_label is not None:
                return node_label
    return None


def _label_for(key: str) -> str:
    return FIELD_LABELS.get(key, key)


# 필드 -> 단위. 차트 축/범례에 "이 숫자가 뭘 세는지" 표시할 때 쓴다.
# 매핑에 없는 필드는 단위를 안 붙인다(억지로 추측하지 않음).
_FIELD_UNITS: dict[str, str] = {
    "listPrice": "원",
    "standardCost": "원",
    "priceCostGap": "원",
    "averageListPrice": "원",
    "activeSupplierCount": "개",
    "purchasedProductCount": "개",
    "productCount": "개",
    "safetyStockLevel": "개",
    "actualStock": "개",
    "shortageQty": "개",
    "totalOrderQty": "개",
    "totalRejectedQty": "개",
    "scrappedQty": "개",
    "totalScrappedQty": "개",
    "suppliedProductCount": "개",
    "workOrderCount": "건",
    "sharedComponentCount": "개",
    "quantityPerAssembly": "개",
}
_HISTOGRAM_COUNT_UNIT = "건"
_RANKED_PROGRESS_UNIT = "개"


def _unit_for(key: str) -> str | None:
    return _FIELD_UNITS.get(key)


def _is_id_column(key: str) -> bool:
    return key == "id" or key.endswith("Id")


def _is_numeric(value: Any) -> bool:
    # bool은 int의 서브클래스라 명시적으로 제외한다.
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def _to_number(value: Any) -> float | int:
    if isinstance(value, Decimal):
        return float(value)
    return value


def _classify_columns(
    rows: list[dict[str, Any]],
) -> tuple[list[str], list[str]] | None:
    """id류를 제외한 컬럼을 (텍스트 컬럼, 수치 컬럼)으로 나눈다."""
    if not rows:
        return None
    keys = [key for key in rows[0] if not _is_id_column(key)]
    text_columns: list[str] = []
    numeric_columns: list[str] = []
    for key in keys:
        non_null = [row.get(key) for row in rows if row.get(key) is not None]
        if non_null and all(_is_numeric(value) for value in non_null):
            numeric_columns.append(key)
        else:
            text_columns.append(key)
    return text_columns, numeric_columns


def _build_kpi(
    row: dict[str, Any], text_columns: list[str], numeric_columns: list[str]
) -> VisualizationSpec | None:
    if not numeric_columns or len(numeric_columns) > _MAX_KPI_ITEMS:
        return None
    items = [
        cast(
            VisualizationKpiItem,
            {"label": _label_for(key), "value": _to_number(row[key])},
        )
        for key in numeric_columns
        if row.get(key) is not None
    ]
    if not items:
        return None
    title_key = text_columns[0] if text_columns else None
    title = (
        str(row[title_key]) if title_key and row.get(title_key) is not None else None
    )
    result: VisualizationSpec = {"type": "kpi", "title": title, "items": items}
    return result


def _build_bar(
    rows: list[dict[str, Any]], text_columns: list[str], numeric_columns: list[str]
) -> VisualizationSpec | None:
    if len(text_columns) != 1 or len(numeric_columns) != 1:
        return None
    category_key = text_columns[0]
    value_key = numeric_columns[0]
    data = [
        cast(
            dict[str, Any],
            {"category": str(row[category_key]), "value": _to_number(row[value_key])},
        )
        for row in rows
        if row.get(category_key) is not None and row.get(value_key) is not None
    ]
    if len(data) < _MIN_BAR_ROWS:
        return None
    series_item: VisualizationSeries = {"key": "value", "label": _label_for(value_key)}
    unit = _unit_for(value_key)
    if unit is not None:
        series_item["unit"] = unit
    result: VisualizationSpec = {
        "type": "bar",
        "title": None,
        "categoryLabel": _label_for(category_key),
        "series": [series_item],
        "data": data,
    }
    return result


def _find_shortage_pair(numeric_columns: list[str]) -> tuple[str, str] | None:
    """숫자 컬럼이 알려진 (실제값, 필요/기준값) 페어 + 선택적 shortageQty로만
    이루어져 있으면 그 페어를 반환한다. 관계없는 숫자 컬럼이 섞여 있으면
    None을 반환해 다른 규칙(bar 등)으로 넘긴다."""
    numeric_set = set(numeric_columns)
    for actual_key, required_key in _SHORTAGE_PAIRS:
        if actual_key not in numeric_set or required_key not in numeric_set:
            continue
        allowed = {actual_key, required_key, _SHORTAGE_QTY_FIELD}
        if numeric_set <= allowed:
            return actual_key, required_key
    return None


def _build_ranked_progress(
    rows: list[dict[str, Any]], text_columns: list[str], numeric_columns: list[str]
) -> VisualizationSpec | None:
    """실제값/필요값 페어가 있는 다중 행 결과를, 부족률 내림차순 Top-N
    순위 진행률 카드로 만든다(예: bom_shortage.py의 부품별 부족 수량)."""
    if not text_columns:
        return None
    pair = _find_shortage_pair(numeric_columns)
    if pair is None:
        return None
    actual_key, required_key = pair
    title_key = text_columns[0]
    has_shortage_column = _SHORTAGE_QTY_FIELD in numeric_columns

    candidates: list[VisualizationRankedItem] = []
    for row in rows:
        title = row.get(title_key)
        actual = row.get(actual_key)
        required = row.get(required_key)
        if title is None or actual is None or required is None:
            continue
        required_num = _to_number(required)
        if required_num <= 0:
            continue
        actual_num = _to_number(actual)
        shortage_qty = (
            _to_number(row[_SHORTAGE_QTY_FIELD])
            if has_shortage_column and row.get(_SHORTAGE_QTY_FIELD) is not None
            else required_num - actual_num
        )
        fulfillment_pct = max(0.0, min(100.0, round(actual_num / required_num * 100)))
        candidates.append(
            cast(
                VisualizationRankedItem,
                {
                    "rank": 0,
                    "title": str(title),
                    "actual": actual_num,
                    "required": required_num,
                    "shortageQty": shortage_qty,
                    "fulfillmentPct": fulfillment_pct,
                },
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: item["shortageQty"], reverse=True)
    top_items = candidates[:_MAX_RANKED_ITEMS]
    for index, item in enumerate(top_items, start=1):
        item["rank"] = index
    result: VisualizationSpec = {
        "type": "ranked_progress",
        "title": None,
        "rankedItems": top_items,
        "unit": _RANKED_PROGRESS_UNIT,
    }
    node_label = _node_label_for_column(title_key)
    if node_label is not None:
        result["entityLabel"] = node_label
    return result


def _format_bin_bound(value: float) -> str:
    rounded = round(value, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:g}"


def _build_histogram(
    rows: list[dict[str, Any]], numeric_key: str
) -> VisualizationSpec | None:
    """숫자 컬럼이 1개뿐이고 행이 많아 bar/ranked_progress 어느 쪽에도 안
    맞는 경우, 값 분포를 구간(bin)으로 나눠 히스토그램으로 보여준다."""
    values = [
        _to_number(row[numeric_key]) for row in rows if row.get(numeric_key) is not None
    ]
    if len(values) < _MIN_HISTOGRAM_ROWS:
        return None
    min_value, max_value = min(values), max(values)
    if min_value == max_value:
        return None
    bin_count = min(
        _MAX_HISTOGRAM_BINS, max(_MIN_HISTOGRAM_BINS, round(len(values) ** 0.5))
    )
    bin_width = (max_value - min_value) / bin_count
    counts = [0] * bin_count
    for value in values:
        index = int((value - min_value) / bin_width)
        counts[min(index, bin_count - 1)] += 1
    data = [
        cast(
            dict[str, Any],
            {
                "category": (
                    f"{_format_bin_bound(min_value + i * bin_width)}~"
                    f"{_format_bin_bound(min_value + (i + 1) * bin_width)}"
                ),
                "value": counts[i],
            },
        )
        for i in range(bin_count)
    ]
    result: VisualizationSpec = {
        "type": "histogram",
        "title": None,
        "categoryLabel": f"{_label_for(numeric_key)} 구간",
        "series": [{"key": "value", "label": "건수", "unit": _HISTOGRAM_COUNT_UNIT}],
        "data": data,
    }
    return result


def _build_scatter(
    rows: list[dict[str, Any]], text_columns: list[str], numeric_columns: list[str]
) -> VisualizationSpec | None:
    """숫자 컬럼이 서로 무관한 2개인 경우(목표/실제 페어가 아님) 두 값의
    관계를 산점도로 보여준다."""
    x_key, y_key = numeric_columns[0], numeric_columns[1]
    title_key = text_columns[0] if text_columns else None
    points: list[VisualizationPoint] = []
    for row in rows:
        x_value = row.get(x_key)
        y_value = row.get(y_key)
        if x_value is None or y_value is None:
            continue
        point = cast(
            VisualizationPoint, {"x": _to_number(x_value), "y": _to_number(y_value)}
        )
        if title_key and row.get(title_key) is not None:
            point["label"] = str(row[title_key])
        points.append(point)
    if len(points) < _MIN_SCATTER_ROWS:
        return None
    result: VisualizationSpec = {
        "type": "scatter",
        "title": None,
        "xLabel": _label_for(x_key),
        "yLabel": _label_for(y_key),
        "points": points,
    }
    x_unit = _unit_for(x_key)
    if x_unit is not None:
        result["xUnit"] = x_unit
    y_unit = _unit_for(y_key)
    if y_unit is not None:
        result["yUnit"] = y_unit
    return result


def build_visualization_spec(
    composed_result: ComposedResult,
) -> VisualizationSpec | None:
    """composed_result 모양을 보고 KPI/막대/순위 진행률/히스토그램/산점도
    시각화 스펙을 만들거나, 적합하지 않으면 None을 반환한다(이 경우
    지금처럼 텍스트/표로만 보여준다)."""
    if composed_result["mode"] == "separate":
        return None
    rows = composed_result["rows"]
    classified = _classify_columns(rows)
    if classified is None:
        return None
    text_columns, numeric_columns = classified

    if len(rows) == 1:
        return _build_kpi(rows[0], text_columns, numeric_columns)
    ranked = _build_ranked_progress(rows, text_columns, numeric_columns)
    if ranked is not None:
        return ranked
    if _MIN_BAR_ROWS <= len(rows) <= _MAX_BAR_ROWS:
        bar = _build_bar(rows, text_columns, numeric_columns)
        if bar is not None:
            return bar
    if len(numeric_columns) == 1:
        return _build_histogram(rows, numeric_columns[0])
    if len(numeric_columns) == 2:
        return _build_scatter(rows, text_columns, numeric_columns)
    return None
