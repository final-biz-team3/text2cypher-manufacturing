"""구조화 MVP 적재의 사전(참조 무결성)·사후(건수/중복/fixture) 검증 함수."""

from typing import Any

from neo4j import Driver


def find_dangling_relationship_rows(
    relationship_rows: list[dict[str, Any]],
    *,
    from_key: str,
    to_key: str,
    from_ids: set[Any],
    to_ids: set[Any],
) -> list[dict[str, Any]]:
    """관계 행 중 시작/도착 노드가 아직 적재되지 않은(고아) 행을 찾는다.

    structured_mvp_loading_rules.md 5절: "하나라도 존재하면 관계를 조용히
    버리지 않고 적재를 실패시킨다. 실패한 business key 목록을 로그에 남긴다."
    이 함수는 그 "실패한 business key 목록"을 계산하는 순수 함수다.
    """
    return [
        row
        for row in relationship_rows
        if row[from_key] not in from_ids or row[to_key] not in to_ids
    ]


def counts_are_equal(first: dict[str, int], second: dict[str, int]) -> bool:
    """두 스냅샷의 라벨/관계타입별 건수가 완전히 같은지 비교한다(멱등성 재검증용)."""
    return first == second


def count_nodes_by_label(driver: Driver, labels: list[str]) -> dict[str, int]:
    """라벨별 노드 건수를 센다."""
    counts: dict[str, int] = {}
    with driver.session() as session:
        for label in labels:
            result = session.run(f"MATCH (n:{label}) RETURN count(n) AS c")
            counts[label] = result.single()["c"]
    return counts


def count_relationships_by_type(driver: Driver, rel_types: list[str]) -> dict[str, int]:
    """관계타입별 건수를 센다."""
    counts: dict[str, int] = {}
    with driver.session() as session:
        for rel_type in rel_types:
            result = session.run(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS c")
            counts[rel_type] = result.single()["c"]
    return counts


def verify_fixture_entities(driver: Driver, entities: dict[str, Any]) -> list[str]:
    """query_parameters.json의 entities가 실제로 그래프에 존재하는지 확인한다.

    Q12~Q20 fixture의 "시작 노드"가 전부 존재해야 한다는 완료 조건(
    structured_mvp_loading_rules.md 7절)을 검사한다. Gold 쿼리 자체는 이번
    범위 밖이라 여기서는 시작점 존재 여부만 확인한다.
    """
    failures: list[str] = []
    with driver.session() as session:
        product_checks = [
            ("pricedProduct", "productId"),
            ("multiLocationProduct", "productId"),
            ("riskComponent", "productId"),
            ("finishedProduct", "productId"),
            ("deepComponent", "productId"),
            ("comparisonProductA", "productId"),
            ("comparisonProductB", "productId"),
        ]
        for entity_key, id_field in product_checks:
            product_id = entities[entity_key][id_field]
            result = session.run(
                "MATCH (p:Product {productId: $id}) RETURN count(p) AS c", id=product_id
            )
            if result.single()["c"] == 0:
                failures.append(f"{entity_key}: Product {product_id} 없음")

        supplier_id = entities["supplier"]["supplierId"]
        result = session.run(
            "MATCH (s:Supplier {supplierId: $id}) RETURN count(s) AS c", id=supplier_id
        )
        if result.single()["c"] == 0:
            failures.append(f"supplier: Supplier {supplier_id} 없음")

        work_order_id = entities["workOrder"]["workOrderId"]
        result = session.run(
            "MATCH (w:WorkOrder {workOrderId: $id}) RETURN count(w) AS c",
            id=work_order_id,
        )
        if result.single()["c"] == 0:
            failures.append(f"workOrder: WorkOrder {work_order_id} 없음")

    return failures


def verify_work_order_17747_fixture(driver: Driver) -> list[str]:
    """structured_mvp_loading_rules.md 7절의 구체적 fixture 검증:
    작업지시 17747의 공정 순서 1·6과 작업장 10·50이 존재해야 한다."""
    failures: list[str] = []
    with driver.session() as session:
        result = session.run("""
            MATCH (:WorkOrder {workOrderId: 17747})-[:HAS_OPERATION]->(ro:RoutingOperation)
                  -[:PERFORMED_AT]->(loc:Location)
            RETURN collect(DISTINCT ro.sequence) AS sequences,
                   collect(DISTINCT loc.locationId) AS locationIds
            """)
        record = result.single()
        sequences = set(record["sequences"])
        location_ids = set(record["locationIds"])
        if not {1, 6}.issubset(sequences):
            failures.append(f"WorkOrder 17747: 공정 순서 1,6 기대, 실제 {sequences}")
        if not {10, 50}.issubset(location_ids):
            failures.append(f"WorkOrder 17747: 작업장 10,50 기대, 실제 {location_ids}")
    return failures


def verify_bom_680_to_492_quantity(driver: Driver) -> list[str]:
    """structured_mvp_loading_rules.md 7절: Product 680에서 492로 가는 필요수량이
    10개 생산 기준 80이어야 한다(bomAsOfDate=2014-08-08 유효 경로만)."""
    with driver.session() as session:
        result = session.run("""
            MATCH path = (:Product {productId: 680})-[r:REQUIRES_COMPONENT*1..4]->
                         (:Product {productId: 492})
            WHERE all(rel IN r WHERE
                rel.startDate <= date('2014-08-08')
                AND (rel.endDate IS NULL OR date('2014-08-08') < rel.endDate))
            RETURN reduce(qty = 10.0, rel IN r | qty * rel.quantityPerAssembly) AS requiredQty
            ORDER BY requiredQty DESC
            LIMIT 1
            """)
        record = result.single()
        if record is None:
            return ["Product 680 -> 492 유효 BOM 경로가 없음"]
        if record["requiredQty"] != 80:
            return [
                f"Product 680 -> 492 필요수량 80 기대, 실제 {record['requiredQty']}"
            ]
    return []
