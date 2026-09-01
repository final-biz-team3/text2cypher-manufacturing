"""SQL 상세 정보와 고정 Cypher 1-hop 이웃 조회."""

from __future__ import annotations

from typing import Any

from neo4j.graph import Node, Relationship
from psycopg.rows import dict_row

from core.postgres import get_pool
from dashboard.service import DashboardServiceError, _json_row
from orchestrator.execution.cypher_executor import get_reader_driver

SUPPORTED_ENTITY_TYPES = {
    "product",
    "supplier",
    "work-order",
    "routing-operation",
    "location",
    "scrap-reason",
}

_GRAPH_ENTITY_MAP: dict[str, tuple[str, str]] = {
    "product": ("Product", "productId"),
    "supplier": ("Supplier", "supplierId"),
    "work-order": ("WorkOrder", "workOrderId"),
    "routing-operation": ("RoutingOperation", "routingOperationKey"),
    "location": ("Location", "locationId"),
    "scrap-reason": ("ScrapReason", "scrapReasonId"),
}


async def _query(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    async with get_pool().connection() as connection:
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(sql, params)
            rows = await cursor.fetchall()
    return [_json_row(dict(row)) for row in rows]


def _field(key: str, label: str, value: Any) -> dict[str, Any]:
    return {"key": key, "label": label, "value": value}


def _group(title: str, fields: list[dict[str, Any]]) -> dict[str, Any]:
    return {"title": title, "fields": fields}


def _action(label: str, question: str) -> dict[str, str]:
    return {"type": "chat-draft", "label": label, "question": question}


def _numeric_entity_id(entity_type: str, entity_id: str) -> int:
    try:
        return int(entity_id)
    except ValueError as exc:
        raise DashboardServiceError(
            400, "INVALID_ENTITY_ID", "엔티티 ID 형식이 올바르지 않습니다."
        ) from exc


async def _product_detail(entity_id: int) -> dict[str, Any] | None:
    base_rows = await _query(
        """
        SELECT p.productid AS "productId", p.name AS "productName",
               p.productnumber AS "productNumber", p.makeflag AS "makeFlag",
               p.finishedgoodsflag AS "finishedGoodsFlag", p.color AS color,
               p.size AS size, p.sellenddate AS "sellEndDate",
               p.listprice AS "listPrice", p.standardcost AS "standardCost",
               p.safetystocklevel::int AS "safetyStockLevel",
               COALESCE(SUM(pi.quantity), 0)::int AS "actualStock",
               pc.productcategoryid AS "categoryId", pc.name AS "categoryName",
               psc.productsubcategoryid AS "subcategoryId",
               psc.name AS "subcategoryName"
        FROM production.product p
        LEFT JOIN production.productinventory pi ON pi.productid = p.productid
        LEFT JOIN production.productsubcategory psc
          ON psc.productsubcategoryid = p.productsubcategoryid
        LEFT JOIN production.productcategory pc
          ON pc.productcategoryid = psc.productcategoryid
        WHERE p.productid = %s
        GROUP BY p.productid, p.name, p.productnumber, p.makeflag,
                 p.finishedgoodsflag, p.color, p.size, p.sellenddate,
                 p.listprice, p.standardcost, p.safetystocklevel,
                 pc.productcategoryid, pc.name,
                 psc.productsubcategoryid, psc.name
        """,
        (entity_id,),
    )
    if not base_rows:
        return None
    product = base_rows[0]
    locations = await _query(
        """
        SELECT l.locationid AS "locationId", l.name AS "locationName",
               pi.shelf AS shelf, pi.bin::int AS bin, pi.quantity::int AS quantity
        FROM production.productinventory pi
        JOIN production.location l ON l.locationid = pi.locationid
        WHERE pi.productid = %s
        ORDER BY l.locationid, pi.shelf, pi.bin
        """,
        (entity_id,),
    )
    suppliers = await _query(
        """
        SELECT v.businessentityid AS "supplierId", v.name AS "supplierName",
               v.activeflag AS active
        FROM purchasing.productvendor pv
        JOIN purchasing.vendor v ON v.businessentityid = pv.businessentityid
        WHERE pv.productid = %s AND v.activeflag = true
        ORDER BY v.businessentityid
        """,
        (entity_id,),
    )
    bom = await _query(
        """
        SELECT b.billofmaterialsid AS "bomId", child.productid AS "componentId",
               child.name AS "componentName", b.perassemblyqty AS "quantityPerAssembly",
               b.startdate AS "startDate", b.enddate AS "endDate"
        FROM production.billofmaterials b
        JOIN production.product child ON child.productid = b.componentid
        WHERE b.productassemblyid = %s
          AND b.startdate <= DATE '2014-08-08'
          AND (b.enddate IS NULL OR b.enddate > DATE '2014-08-08')
        ORDER BY child.productid
        """,
        (entity_id,),
    )
    return {
        "entity": {
            "type": "product",
            "id": product["productId"],
            "label": product["productName"],
        },
        "groups": [
            _group(
                "제품 정보",
                [
                    _field("productNumber", "제품번호", product["productNumber"]),
                    _field("categoryName", "분류", product["categoryName"]),
                    _field("subcategoryName", "하위 분류", product["subcategoryName"]),
                    _field("makeFlag", "조달 방식", product["makeFlag"]),
                    _field(
                        "finishedGoodsFlag", "완제품 여부", product["finishedGoodsFlag"]
                    ),
                    _field("color", "색상", product["color"]),
                    _field("size", "크기", product["size"]),
                    _field("sellEndDate", "판매 종료일", product["sellEndDate"]),
                ],
            ),
            _group(
                "가격·재고",
                [
                    _field("listPrice", "정가", product["listPrice"]),
                    _field("standardCost", "표준원가", product["standardCost"]),
                    _field("safetyStockLevel", "안전재고", product["safetyStockLevel"]),
                    _field("actualStock", "실제재고", product["actualStock"]),
                ],
            ),
            _group(
                "재고 위치", [_field("inventoryLocations", "위치별 재고", locations)]
            ),
            _group("활성 공급업체", [_field("activeSuppliers", "공급업체", suppliers)]),
            _group("1단계 BOM 구성", [_field("bomComponents", "하위 구성품", bom)]),
        ],
        "actions": [
            _action(
                "AI Chat에서 분석",
                f"{product['productName']}의 재고 위치와 수량, 활성 공급업체를 알려줘.",
            )
        ],
    }


async def _supplier_detail(entity_id: int) -> dict[str, Any] | None:
    rows = await _query(
        """
        SELECT v.businessentityid AS "supplierId", v.name AS "supplierName",
               v.activeflag AS active,
               COUNT(DISTINCT pv.productid)::int AS "suppliedProductCount",
               COALESCE(rejected.total_rejected_qty, 0) AS "totalRejectedQty"
        FROM purchasing.vendor v
        LEFT JOIN purchasing.productvendor pv ON pv.businessentityid = v.businessentityid
        LEFT JOIN (
          SELECT poh.vendorid, SUM(pod.rejectedqty) AS total_rejected_qty
          FROM purchasing.purchaseorderheader poh
          JOIN purchasing.purchaseorderdetail pod
            ON pod.purchaseorderid = poh.purchaseorderid
          GROUP BY poh.vendorid
        ) rejected ON rejected.vendorid = v.businessentityid
        WHERE v.businessentityid = %s
        GROUP BY v.businessentityid, v.name, v.activeflag, rejected.total_rejected_qty
        """,
        (entity_id,),
    )
    if not rows:
        return None
    supplier = rows[0]
    products = await _query(
        """
        SELECT p.productid AS "productId", p.name AS "productName",
               p.productnumber AS "productNumber"
        FROM purchasing.productvendor pv
        JOIN production.product p ON p.productid = pv.productid
        WHERE pv.businessentityid = %s
        ORDER BY p.productid
        """,
        (entity_id,),
    )
    return {
        "entity": {
            "type": "supplier",
            "id": entity_id,
            "label": supplier["supplierName"],
        },
        "groups": [
            _group(
                "공급업체 정보",
                [
                    _field("active", "활성 여부", supplier["active"]),
                    _field(
                        "suppliedProductCount",
                        "공급 제품 수",
                        supplier["suppliedProductCount"],
                    ),
                    _field(
                        "totalRejectedQty",
                        "구매주문 반려수량 합계",
                        supplier["totalRejectedQty"],
                    ),
                ],
            ),
            _group("공급 제품", [_field("products", "제품 목록", products)]),
        ],
        "actions": [
            _action(
                "AI Chat에서 분석",
                f"{supplier['supplierName']}의 공급 제품과 반려수량을 분석해줘.",
            )
        ],
    }


async def _work_order_detail(entity_id: int) -> dict[str, Any] | None:
    rows = await _query(
        """
        SELECT wo.workorderid AS "workOrderId", wo.productid AS "productId",
               p.name AS "productName", wo.scrappedqty::int AS "scrappedQty",
               sr.scrapreasonid AS "scrapReasonId", sr.name AS "scrapReasonName",
               COUNT(wor.operationsequence)::int AS "operationCount"
        FROM production.workorder wo
        JOIN production.product p ON p.productid = wo.productid
        LEFT JOIN production.scrapreason sr ON sr.scrapreasonid = wo.scrapreasonid
        LEFT JOIN production.workorderrouting wor ON wor.workorderid = wo.workorderid
        WHERE wo.workorderid = %s
        GROUP BY wo.workorderid, wo.productid, p.name, wo.scrappedqty,
                 sr.scrapreasonid, sr.name
        """,
        (entity_id,),
    )
    if not rows:
        return None
    work_order = rows[0]
    operations = await _query(
        """
        SELECT (wor.workorderid::text || '-' || wor.productid::text || '-' ||
                wor.operationsequence::text) AS "routingOperationKey",
               wor.operationsequence::int AS sequence,
               l.locationid AS "locationId", l.name AS "locationName"
        FROM production.workorderrouting wor
        JOIN production.location l ON l.locationid = wor.locationid
        WHERE wor.workorderid = %s
        ORDER BY wor.operationsequence
        """,
        (entity_id,),
    )
    return {
        "entity": {
            "type": "work-order",
            "id": entity_id,
            "label": f"작업지시 {entity_id}",
        },
        "groups": [
            _group(
                "작업지시 정보",
                [
                    _field(
                        "product",
                        "생산 제품",
                        {
                            "productId": work_order["productId"],
                            "productName": work_order["productName"],
                        },
                    ),
                    _field("scrappedQty", "폐기수량", work_order["scrappedQty"]),
                    _field(
                        "scrapReasonName", "폐기사유", work_order["scrapReasonName"]
                    ),
                    _field("operationCount", "공정 수", work_order["operationCount"]),
                ],
            ),
            _group("공정 순서", [_field("operations", "작업장 순서", operations)]),
        ],
        "actions": [
            _action(
                "AI Chat에서 분석",
                f"작업지시 {entity_id}의 제품, 공정과 폐기 정보를 알려줘.",
            )
        ],
    }


async def _routing_detail(entity_id: str) -> dict[str, Any] | None:
    rows = await _query(
        """
        SELECT (wor.workorderid::text || '-' || wor.productid::text || '-' ||
                wor.operationsequence::text) AS "routingOperationKey",
               wor.workorderid AS "workOrderId", wor.productid AS "productId",
               p.name AS "productName", wor.operationsequence::int AS sequence,
               l.locationid AS "locationId", l.name AS "locationName"
        FROM production.workorderrouting wor
        JOIN production.product p ON p.productid = wor.productid
        JOIN production.location l ON l.locationid = wor.locationid
        WHERE (wor.workorderid::text || '-' || wor.productid::text || '-' ||
               wor.operationsequence::text) = %s
        """,
        (entity_id,),
    )
    if not rows:
        return None
    operation = rows[0]
    return {
        "entity": {
            "type": "routing-operation",
            "id": entity_id,
            "label": f"공정 {entity_id}",
        },
        "groups": [
            _group(
                "공정 정보",
                [
                    _field("workOrderId", "작업지시 ID", operation["workOrderId"]),
                    _field(
                        "product",
                        "제품",
                        {
                            "productId": operation["productId"],
                            "productName": operation["productName"],
                        },
                    ),
                    _field("sequence", "공정 순서", operation["sequence"]),
                    _field(
                        "location",
                        "작업장",
                        {
                            "locationId": operation["locationId"],
                            "locationName": operation["locationName"],
                        },
                    ),
                ],
            )
        ],
        "actions": [
            _action(
                "AI Chat에서 분석",
                f"공정 {entity_id}가 속한 작업지시와 작업장을 알려줘.",
            )
        ],
    }


async def _location_detail(entity_id: int) -> dict[str, Any] | None:
    rows = await _query(
        """
        SELECT l.locationid AS "locationId", l.name AS "locationName",
               COUNT(DISTINCT wor.workorderid)::int AS "workOrderCount",
               COUNT(DISTINCT (wor.workorderid, wor.productid, wor.operationsequence))::int AS "operationCount"
        FROM production.location l
        LEFT JOIN production.workorderrouting wor ON wor.locationid = l.locationid
        WHERE l.locationid = %s
        GROUP BY l.locationid, l.name
        """,
        (entity_id,),
    )
    if not rows:
        return None
    location = rows[0]
    inventory = await _query(
        """
        SELECT p.productid AS "productId", p.name AS "productName",
               SUM(pi.quantity)::int AS quantity
        FROM production.productinventory pi
        JOIN production.product p ON p.productid = pi.productid
        WHERE pi.locationid = %s
        GROUP BY p.productid, p.name
        ORDER BY quantity DESC, p.productid ASC
        """,
        (entity_id,),
    )
    return {
        "entity": {
            "type": "location",
            "id": entity_id,
            "label": location["locationName"],
        },
        "groups": [
            _group(
                "작업장 정보",
                [
                    _field(
                        "workOrderCount",
                        "처리한 작업지시 수",
                        location["workOrderCount"],
                    ),
                    _field(
                        "operationCount", "관련 공정 수", location["operationCount"]
                    ),
                ],
            ),
            _group("보관 제품", [_field("inventory", "제품별 재고", inventory)]),
        ],
        "actions": [
            _action(
                "AI Chat에서 분석",
                f"{location['locationName']} 작업장의 작업지시와 보관 재고를 알려줘.",
            )
        ],
    }


async def _scrap_reason_detail(entity_id: int) -> dict[str, Any] | None:
    rows = await _query(
        """
        SELECT sr.scrapreasonid AS "scrapReasonId", sr.name AS "scrapReasonName",
               COUNT(wo.workorderid)::int AS "workOrderCount",
               COALESCE(SUM(wo.scrappedqty), 0)::int AS "totalScrappedQty"
        FROM production.scrapreason sr
        LEFT JOIN production.workorder wo ON wo.scrapreasonid = sr.scrapreasonid
        WHERE sr.scrapreasonid = %s
        GROUP BY sr.scrapreasonid, sr.name
        """,
        (entity_id,),
    )
    if not rows:
        return None
    reason = rows[0]
    work_orders = await _query(
        """
        SELECT wo.workorderid AS "workOrderId", p.productid AS "productId",
               p.name AS "productName", wo.scrappedqty::int AS "scrappedQty"
        FROM production.workorder wo
        JOIN production.product p ON p.productid = wo.productid
        WHERE wo.scrapreasonid = %s
        ORDER BY wo.scrappedqty DESC, wo.workorderid ASC
        LIMIT 20
        """,
        (entity_id,),
    )
    return {
        "entity": {
            "type": "scrap-reason",
            "id": entity_id,
            "label": reason["scrapReasonName"],
        },
        "groups": [
            _group(
                "폐기사유 정보",
                [
                    _field(
                        "workOrderCount", "관련 작업지시 수", reason["workOrderCount"]
                    ),
                    _field(
                        "totalScrappedQty", "폐기수량 합계", reason["totalScrappedQty"]
                    ),
                ],
            ),
            _group(
                "폐기수량 상위 작업지시",
                [_field("workOrders", "작업지시", work_orders)],
            ),
        ],
        "actions": [
            _action(
                "AI Chat에서 분석",
                f"폐기사유 {reason['scrapReasonName']}의 관련 작업지시를 분석해줘.",
            )
        ],
    }


async def get_entity_detail(entity_type: str, entity_id: str) -> dict[str, Any]:
    if entity_type not in SUPPORTED_ENTITY_TYPES:
        raise DashboardServiceError(
            400, "INVALID_ENTITY_TYPE", "지원하지 않는 엔티티 유형입니다."
        )
    try:
        if entity_type == "routing-operation":
            result = await _routing_detail(entity_id)
        else:
            numeric_id = _numeric_entity_id(entity_type, entity_id)
            loaders = {
                "product": _product_detail,
                "supplier": _supplier_detail,
                "work-order": _work_order_detail,
                "location": _location_detail,
                "scrap-reason": _scrap_reason_detail,
            }
            result = await loaders[entity_type](numeric_id)
    except DashboardServiceError:
        raise
    except Exception as exc:
        raise DashboardServiceError(
            503, "ENTITY_QUERY_FAILED", "해당 정보를 불러오지 못했습니다."
        ) from exc
    if result is None:
        raise DashboardServiceError(
            404, "ENTITY_NOT_FOUND", "해당 정보를 찾지 못했습니다."
        )
    return result


def _graph_value(value: Any) -> Any:
    if hasattr(value, "iso_format"):
        return value.iso_format()
    return value


def _node_payload(node: Node) -> dict[str, Any]:
    properties = {key: _graph_value(value) for key, value in dict(node).items()}
    entity_id = next(
        (
            properties.get(key)
            for key in (
                "productId",
                "supplierId",
                "workOrderId",
                "routingOperationKey",
                "locationId",
                "scrapReasonId",
            )
            if properties.get(key) is not None
        ),
        node.element_id,
    )
    return {
        "id": str(entity_id),
        "labels": sorted(node.labels),
        "properties": properties,
    }


def _edge_payload(edge: Relationship) -> dict[str, Any]:
    start_node = edge.start_node
    end_node = edge.end_node
    if start_node is None or end_node is None:
        raise ValueError("그래프 관계의 시작 또는 끝 노드가 없습니다.")
    return {
        "id": edge.element_id,
        "source": start_node.element_id,
        "target": end_node.element_id,
        "type": edge.type,
        "properties": {key: _graph_value(value) for key, value in dict(edge).items()},
    }


async def get_entity_neighbors(
    entity_type: str, entity_id: str, *, depth: int
) -> dict[str, Any]:
    if entity_type not in _GRAPH_ENTITY_MAP:
        raise DashboardServiceError(
            400, "INVALID_ENTITY_TYPE", "지원하지 않는 엔티티 유형입니다."
        )
    if depth != 1:
        raise DashboardServiceError(400, "INVALID_DEPTH", "depth는 1만 허용합니다.")
    label, key = _GRAPH_ENTITY_MAP[entity_type]
    value: str | int = (
        entity_id
        if entity_type == "routing-operation"
        else _numeric_entity_id(entity_type, entity_id)
    )
    cypher = (
        f"MATCH (center:{label} {{{key}: $entityId}}) "
        "OPTIONAL MATCH (center)-[relationship]-(neighbor) "
        "RETURN center, relationship, neighbor LIMIT 101"
    )

    async def read_neighbors(transaction: Any) -> list[Any]:
        result = await transaction.run(cypher, entityId=value)
        return await result.fetch(101)

    try:
        async with get_reader_driver().session() as session:
            records = await session.execute_read(read_neighbors)
    except Exception as exc:
        raise DashboardServiceError(
            503, "ENTITY_NEIGHBORS_FAILED", "관계 정보를 불러오지 못했습니다."
        ) from exc
    if not records:
        raise DashboardServiceError(
            404, "ENTITY_NOT_FOUND", "해당 정보를 찾지 못했습니다."
        )

    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    for record in records[:100]:
        center = record["center"]
        neighbor = record["neighbor"]
        relationship = record["relationship"]
        for node in (center, neighbor):
            if node is not None:
                payload = _node_payload(node)
                payload["elementId"] = node.element_id
                nodes[node.element_id] = payload
        if relationship is not None:
            edges[relationship.element_id] = _edge_payload(relationship)
    return {
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "truncated": len(records) > 100,
    }
