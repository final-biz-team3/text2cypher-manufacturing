"""구조화 MVP 적재의 사전(참조 무결성)·사후(건수/중복/fixture) 검증 함수.

독립 실행하면(python etl/structured_mvp_validate.py) 재적재 없이 현재
Neo4j 상태만 검증한다 - postgres_restore_validate.py가 복원 없이
PostgreSQL만 검증하는 것과 대칭이다.
"""

import json
import os
import sys
from typing import Any

from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase


def find_dangling_relationship_rows(
    relationship_rows: list[dict[str, Any]],
    *,
    from_key: str,
    to_key: str,
    from_ids: set[Any],
    to_ids: set[Any],
) -> list[dict[str, Any]]:
    """관계 행 중 시작/도착 노드가 아직 적재되지 않은(고아) 행을 찾는다.

    docs/etl/2-structured_mvp_loading_rules.md 5절: "하나라도 존재하면 관계를 조용히
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
    docs/etl/2-structured_mvp_loading_rules.md 7절)을 검사한다. Gold 쿼리 자체는 이번
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
    """docs/etl/2-structured_mvp_loading_rules.md 7절의 구체적 fixture 검증:
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
    """docs/etl/2-structured_mvp_loading_rules.md 7절: Product 680에서 492로 가는 필요수량이
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


def main() -> None:
    """재적재 없이 현재 Neo4j 상태만 검증한다.

    사용법(리포 루트 기준): python etl/structured_mvp_validate.py
    """
    from postgres_restore import ROOT_DIR
    from run_structured_mvp_sync import NEO4J_REQUIRED_ENV_VARS, RELATIONSHIP_TYPES
    from structured_mvp_load import BUSINESS_LABELS

    load_dotenv(ROOT_DIR / ".env")

    missing_vars = [
        name for name in NEO4J_REQUIRED_ENV_VARS if not os.environ.get(name)
    ]
    if missing_vars:
        sys.exit(f".env에 다음 값이 없습니다: {', '.join(missing_vars)}")

    print(f"대상: {os.environ['NEO4J_URI']}")

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    try:
        driver.verify_connectivity()
    except Exception as exc:
        sys.exit(f"Neo4j 접속 실패 ({os.environ['NEO4J_URI']}): {exc}")

    try:
        print("1) 건수 확인")
        node_counts = count_nodes_by_label(driver, BUSINESS_LABELS)
        rel_counts = count_relationships_by_type(driver, RELATIONSHIP_TYPES)
        print(f"   노드 건수: {node_counts}")
        print(f"   관계 건수: {rel_counts}")

        print("2) fixture 검증 (query_parameters.json 기준)")
        parameters_path = ROOT_DIR / "queries" / "query_parameters.json"
        entities = json.loads(parameters_path.read_text(encoding="utf-8"))["entities"]
        failures = verify_fixture_entities(driver, entities)
        failures += verify_work_order_17747_fixture(driver)
        failures += verify_bom_680_to_492_quantity(driver)
        if failures:
            print(f"   fixture 검증 실패 {len(failures)}건:")
            for failure in failures:
                print(f"     - {failure}")
            sys.exit(1)
        print("   fixture 검증 전부 통과")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
