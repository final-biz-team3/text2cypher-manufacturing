"""검증된 BOM source 결과를 외부 구매 부품 부족량으로 변환한다."""

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from orchestrator.planning import (
    BOM_SHORTAGE_GRAPH_OUTPUTS,
    BOM_SHORTAGE_SQL_OUTPUTS,
)

_QUANTUM = Decimal("0.000001")


def _identifier(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label}는 정수 식별자여야 합니다.")
    return value


def _name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label}는 비어 있지 않은 문자열이어야 합니다.")
    return value


def _decimal(value: Any, label: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal | str):
        raise ValueError(f"{label}는 숫자여야 합니다.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label}를 Decimal로 변환할 수 없습니다.") from exc
    if not result.is_finite() or (positive and result <= 0):
        condition = "유한한 양수" if positive else "유한한 숫자"
        raise ValueError(f"{label}는 {condition}여야 합니다.")
    return result


def _normalized(value: Decimal) -> Decimal:
    return value.quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)


def _require_fields(row: dict[str, Any], fields: frozenset[str], label: str) -> None:
    missing = sorted(fields - set(row))
    if missing:
        raise ValueError(f"{label}에 필수 필드가 없습니다: {', '.join(missing)}")


def calculate_bom_shortages(
    graph_rows: list[dict[str, Any]],
    sql_rows: list[dict[str, Any]],
    *,
    production_qty: int | float,
) -> list[dict[str, Any]]:
    """전체 source를 검증한 뒤 component별 shortage를 결정적으로 계산한다."""
    production = _decimal(production_qty, "productionQty", positive=True)
    components: dict[int, dict[str, Any]] = {}
    graph_domain: set[int] = set()
    supplier_names: dict[int, str] = {}
    finished_products: set[tuple[int, str]] = set()
    path_quantities: dict[tuple[int, tuple[int, ...]], tuple[Decimal, ...]] = {}

    for row_index, row in enumerate(graph_rows):
        label = f"GRAPH {row_index}번 행"
        if not isinstance(row, dict):
            raise ValueError(f"{label}이 객체가 아닙니다.")
        _require_fields(row, BOM_SHORTAGE_GRAPH_OUTPUTS, label)
        finished_id = _identifier(
            row["finishedProductId"], f"{label}.finishedProductId"
        )
        finished_name = _name(
            row["finishedProductName"], f"{label}.finishedProductName"
        )
        finished_products.add((finished_id, finished_name))
        component_id = _identifier(row["componentId"], f"{label}.componentId")
        component_name = _name(row["componentName"], f"{label}.componentName")
        depth = row["depth"]
        if isinstance(depth, bool) or not isinstance(depth, int) or depth < 1:
            raise ValueError(f"{label}.depth는 1 이상의 정수여야 합니다.")
        raw_path = row["pathProductIds"]
        raw_quantities = row["quantityPerAssembly"]
        if not isinstance(raw_path, list):
            raise ValueError(f"{label}.pathProductIds는 배열이어야 합니다.")
        if not isinstance(raw_quantities, list):
            raise ValueError(f"{label}.quantityPerAssembly는 배열이어야 합니다.")
        path = tuple(
            _identifier(value, f"{label}.pathProductIds[{index}]")
            for index, value in enumerate(raw_path)
        )
        quantities = tuple(
            _decimal(
                value,
                f"{label}.quantityPerAssembly[{index}]",
                positive=True,
            )
            for index, value in enumerate(raw_quantities)
        )
        if len(path) != len(quantities) + 1:
            raise ValueError(
                f"{label}의 pathProductIds 길이는 quantityPerAssembly보다 1 커야 합니다."
            )
        if depth != len(quantities):
            raise ValueError(f"{label}.depth가 quantityPerAssembly 길이와 다릅니다.")
        if path[0] != finished_id or path[-1] != component_id:
            raise ValueError(f"{label}의 path 방향 또는 양 끝 식별자가 잘못됐습니다.")

        supplier_id_raw = row["supplierId"]
        supplier_name_raw = row["supplierName"]
        if (supplier_id_raw is None) != (supplier_name_raw is None):
            raise ValueError(f"{label}의 supplier ID와 이름은 함께 null이어야 합니다.")
        supplier: tuple[int, str] | None = None
        if supplier_id_raw is not None:
            supplier_id = _identifier(supplier_id_raw, f"{label}.supplierId")
            supplier_name = _name(supplier_name_raw, f"{label}.supplierName")
            prior_name = supplier_names.setdefault(supplier_id, supplier_name)
            if prior_name != supplier_name:
                raise ValueError(
                    f"supplierId {supplier_id}에 서로 다른 이름이 있습니다."
                )
            supplier = (supplier_id, supplier_name)

        component = components.setdefault(
            component_id,
            {
                "finishedProductId": finished_id,
                "finishedProductName": finished_name,
                "componentName": component_name,
                "paths": {},
                "suppliers": set(),
            },
        )
        identity = (
            component["finishedProductId"],
            component["finishedProductName"],
            component["componentName"],
        )
        if identity != (finished_id, finished_name, component_name):
            raise ValueError(f"componentId {component_id}의 식별 정보가 충돌합니다.")
        path_key = (component_id, path)
        prior_quantities = path_quantities.setdefault(path_key, quantities)
        if prior_quantities != quantities:
            raise ValueError(
                "동일 BOM 경로에 서로 다른 quantityPerAssembly가 있습니다: "
                f"componentId={component_id}, pathProductIds={list(path)}"
            )
        path_required = production
        for quantity in quantities:
            path_required *= quantity
        component["paths"].setdefault(path_key, path_required)
        if supplier is not None:
            component["suppliers"].add(supplier)
        graph_domain.add(component_id)

    if len(finished_products) > 1:
        raise ValueError("GRAPH 결과에 서로 다른 finished product가 있습니다.")

    sql_by_component: dict[int, tuple[bool, Decimal]] = {}
    for row_index, row in enumerate(sql_rows):
        label = f"SQL {row_index}번 행"
        if not isinstance(row, dict):
            raise ValueError(f"{label}이 객체가 아닙니다.")
        _require_fields(row, BOM_SHORTAGE_SQL_OUTPUTS, label)
        component_id = _identifier(row["componentId"], f"{label}.componentId")
        if component_id in sql_by_component:
            raise ValueError(f"SQL에 componentId {component_id} 행이 중복됐습니다.")
        make_flag = row["makeFlag"]
        if not isinstance(make_flag, bool):
            raise ValueError(f"{label}.makeFlag는 bool이어야 합니다.")
        actual_stock = _decimal(row["actualStock"], f"{label}.actualStock")
        sql_by_component[component_id] = (make_flag, actual_stock)

    sql_domain = set(sql_by_component)
    if graph_domain != sql_domain:
        missing_sql = sorted(graph_domain - sql_domain)
        extra_sql = sorted(sql_domain - graph_domain)
        raise ValueError(
            "GRAPH와 SQL component domain이 다릅니다: "
            f"missingSql={missing_sql}, extraSql={extra_sql}"
        )

    results: list[dict[str, Any]] = []
    for component_id, component in components.items():
        make_flag, actual_stock = sql_by_component[component_id]
        if make_flag is not False:
            continue
        required = sum(component["paths"].values(), Decimal(0))
        shortage = max(required - actual_stock, Decimal(0))
        if shortage == 0:
            continue
        suppliers = [
            {"supplierId": supplier_id, "supplierName": supplier_name}
            for supplier_id, supplier_name in sorted(component["suppliers"])
        ]
        results.append(
            {
                "finishedProductId": component["finishedProductId"],
                "finishedProductName": component["finishedProductName"],
                "productionQty": _normalized(production),
                "componentId": component_id,
                "componentName": component["componentName"],
                "requiredQty": _normalized(required),
                "actualStock": _normalized(actual_stock),
                "shortageQty": _normalized(shortage),
                "suppliers": suppliers,
            }
        )
    results.sort(key=lambda row: (-row["shortageQty"], row["componentId"]))
    return results
