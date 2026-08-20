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


def count_nodes_by_label(
    driver: Driver, labels: list[str], sync_run_id: str | None = None
) -> dict[str, int]:
    """라벨별 노드 건수를 센다.

    sync_run_id를 주면 그 실행에서 적재된 노드만 센다(prune 전 사전 검증용).
    안 주면(기본값) 전체를 센다(prune 후 사후 검증, 독립 실행용).
    """
    counts: dict[str, int] = {}
    with driver.session() as session:
        for label in labels:
            result = session.run(
                f"""
                MATCH (n:{label})
                WHERE $syncRunId IS NULL OR n.syncRunId = $syncRunId
                RETURN count(n) AS c
                """,
                syncRunId=sync_run_id,
            )
            counts[label] = result.single()["c"]
    return counts


def count_relationships_by_type(
    driver: Driver, rel_types: list[str], sync_run_id: str | None = None
) -> dict[str, int]:
    """관계타입별 건수를 센다. sync_run_id 의미는 count_nodes_by_label과 동일하다."""
    counts: dict[str, int] = {}
    with driver.session() as session:
        for rel_type in rel_types:
            result = session.run(
                f"""
                MATCH ()-[r:{rel_type}]->()
                WHERE $syncRunId IS NULL OR r.syncRunId = $syncRunId
                RETURN count(r) AS c
                """,
                syncRunId=sync_run_id,
            )
            counts[rel_type] = result.single()["c"]
    return counts


def verify_fixture_entities(
    driver: Driver, entities: dict[str, Any], sync_run_id: str | None = None
) -> list[str]:
    """query_parameters.json의 entities가 실제로 그래프에 존재하는지 확인한다.

    Q12~Q20 fixture의 "시작 노드"가 전부 존재해야 한다는 완료 조건(
    docs/etl/2-structured_mvp_loading_rules.md 7절)을 검사한다. Gold 쿼리 자체는 이번
    범위 밖이라 여기서는 시작점 존재 여부만 확인한다.

    sync_run_id를 주면 그 실행에서 적재된 노드로 존재하는지까지 확인한다(prune 전
    사전 검증용 - 이전 실행에서 남아있는 stale 노드로 검증이 통과하는 걸 막는다).
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
                """
                MATCH (p:Product {productId: $id})
                WHERE $syncRunId IS NULL OR p.syncRunId = $syncRunId
                RETURN count(p) AS c
                """,
                id=product_id,
                syncRunId=sync_run_id,
            )
            if result.single()["c"] == 0:
                failures.append(f"{entity_key}: Product {product_id} 없음")

        supplier_id = entities["supplier"]["supplierId"]
        result = session.run(
            """
            MATCH (s:Supplier {supplierId: $id})
            WHERE $syncRunId IS NULL OR s.syncRunId = $syncRunId
            RETURN count(s) AS c
            """,
            id=supplier_id,
            syncRunId=sync_run_id,
        )
        if result.single()["c"] == 0:
            failures.append(f"supplier: Supplier {supplier_id} 없음")

        work_order_id = entities["workOrder"]["workOrderId"]
        result = session.run(
            """
            MATCH (w:WorkOrder {workOrderId: $id})
            WHERE $syncRunId IS NULL OR w.syncRunId = $syncRunId
            RETURN count(w) AS c
            """,
            id=work_order_id,
            syncRunId=sync_run_id,
        )
        if result.single()["c"] == 0:
            failures.append(f"workOrder: WorkOrder {work_order_id} 없음")

    return failures


def verify_work_order_17747_fixture(
    driver: Driver, sync_run_id: str | None = None
) -> list[str]:
    """docs/etl/2-structured_mvp_loading_rules.md 7절의 구체적 fixture 검증:
    작업지시 17747의 공정 순서 1·6과 작업장 10·50이 존재해야 한다.

    sync_run_id를 주면 경로의 시작 노드·관계·중간 노드·관계·끝 노드가 전부 이번
    실행에서 적재된 것인지까지 확인한다(prune 전 사전 검증용).
    """
    failures: list[str] = []
    with driver.session() as session:
        result = session.run("""
            MATCH (wo:WorkOrder {workOrderId: 17747})-[r1:HAS_OPERATION]->(ro:RoutingOperation)
                  -[r2:PERFORMED_AT]->(loc:Location)
            WHERE $syncRunId IS NULL OR (
                wo.syncRunId = $syncRunId AND r1.syncRunId = $syncRunId
                AND ro.syncRunId = $syncRunId AND r2.syncRunId = $syncRunId
                AND loc.syncRunId = $syncRunId
            )
            RETURN collect(DISTINCT ro.sequence) AS sequences,
                   collect(DISTINCT loc.locationId) AS locationIds
            """,
            syncRunId=sync_run_id,
        )
        record = result.single()
        sequences = set(record["sequences"])
        location_ids = set(record["locationIds"])
        if not {1, 6}.issubset(sequences):
            failures.append(f"WorkOrder 17747: 공정 순서 1,6 기대, 실제 {sequences}")
        if not {10, 50}.issubset(location_ids):
            failures.append(f"WorkOrder 17747: 작업장 10,50 기대, 실제 {location_ids}")
    return failures


def verify_bom_680_to_492_quantity(
    driver: Driver, sync_run_id: str | None = None
) -> list[str]:
    """docs/etl/2-structured_mvp_loading_rules.md 7절: Product 680에서 492로 가는 필요수량이
    10개 생산 기준 80이어야 한다(bomAsOfDate=2014-08-08 유효 경로만).

    sync_run_id를 주면 경로의 시작·끝 노드와 모든 관계가 이번 실행에서 적재된
    것인지까지 확인한다(prune 전 사전 검증용). Q19 계약이 요구하는 "전체 경로 합산"
    검증(P2 항목)은 이번 변경 범위 밖이라 기존 ORDER BY ... LIMIT 1 로직은 그대로 둔다.
    """
    with driver.session() as session:
        result = session.run("""
            MATCH path = (start:Product {productId: 680})-[r:REQUIRES_COMPONENT*1..4]->
                         (end:Product {productId: 492})
            WHERE all(rel IN r WHERE
                rel.startDate <= date('2014-08-08')
                AND (rel.endDate IS NULL OR date('2014-08-08') < rel.endDate)
                AND ($syncRunId IS NULL OR rel.syncRunId = $syncRunId))
              AND ($syncRunId IS NULL OR (
                  start.syncRunId = $syncRunId AND end.syncRunId = $syncRunId
              ))
            RETURN reduce(qty = 10.0, rel IN r | qty * rel.quantityPerAssembly) AS requiredQty
            ORDER BY requiredQty DESC
            LIMIT 1
            """,
            syncRunId=sync_run_id,
        )
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
