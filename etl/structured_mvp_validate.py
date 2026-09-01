"""구조화 MVP 적재의 쓰기 전(추출 결과)·쓰기 후(건수/fixture) 검증 함수.

독립 실행하면(python etl/structured_mvp_validate.py) 재적재 없이 현재
기본 Neo4j 데이터베이스 상태만 검증한다 - postgres_restore_validate.py가
복원 없이 PostgreSQL만 검증하는 것과 대칭이다.
"""

import json
import os
import sys
from typing import Any

from dotenv import load_dotenv
from neo4j import Driver, Session
from postgres_restore import ROOT_DIR
from structured_mvp_config import RELATIONSHIP_TYPES, connect_neo4j_from_env
from structured_mvp_load import BUSINESS_LABELS

# quantityPerAssembly가 PostgreSQL Decimal -> float 변환 후 Cypher에서 다시 곱셈을
# 거치므로, 수학적으로 정확히 80이어도 부동소수점 표현 오차로 79.99999999999997
# 같은 값이 나올 수 있다 - 정확 비교(!=)가 아니라 허용 오차 비교를 쓴다.
REQUIRED_QTY_EPSILON = 1e-6


def quantities_match(actual: float, expected: float) -> bool:
    """부동소수점 오차를 허용하고 두 수량이 사실상 같은지 비교한다."""
    return abs(actual - expected) <= REQUIRED_QTY_EPSILON


def find_dangling_relationship_rows(
    relationship_rows: list[dict[str, Any]],
    *,
    from_key: str,
    to_key: str,
    from_ids: set[Any],
    to_ids: set[Any],
) -> list[dict[str, Any]]:
    """관계 행 중 시작/도착 노드가 아직 추출되지 않은(고아) 행을 찾는다.

    docs/etl/2-structured_mvp_loading_rules.md 5절: "하나라도 존재하면 관계를 조용히
    버리지 않고 적재를 실패시킨다. 실패한 business key 목록을 로그에 남긴다."
    이 함수는 그 "실패한 business key 목록"을 계산하는 순수 함수다.
    """
    return [
        row
        for row in relationship_rows
        if row[from_key] not in from_ids or row[to_key] not in to_ids
    ]


def find_duplicate_key_rows(
    rows: list[dict[str, Any]], key_columns: tuple[str, ...]
) -> list[tuple[Any, ...]]:
    """key_columns 조합 기준으로 중복된 키 값을 찾는다(쓰기 전 검증용).

    MERGE는 같은 키를 가진 여러 행을 하나로 뭉개버리기 때문에, 추출 단계에서
    이미 중복이 있으면 "추출 건수 == 적재 건수" 같은 사후 비교로는 못 잡는다
    (애초에 적재 건수 자체가 줄어들어서 기대치도 같이 줄어든 것처럼 보일 수
    있음). 쓰기 전에 원본 추출 결과만 보고 중복 여부를 확정한다.
    """
    counts: dict[tuple[Any, ...], int] = {}
    for row in rows:
        key = tuple(row[col] for col in key_columns)
        counts[key] = counts.get(key, 0) + 1
    return [key for key, count in counts.items() if count > 1]


def find_rows_with_null_key(
    rows: list[dict[str, Any]], key_columns: tuple[str, ...]
) -> list[dict[str, Any]]:
    """key_columns 중 하나라도 None인 행을 찾는다(쓰기 전 검증용).

    MERGE 키가 None이면 MERGE는 실패하지 않고 "속성이 없는" 값으로 조용히
    매칭/생성해버릴 수 있어, 사전에 걸러야 한다.
    """
    return [row for row in rows if any(row[col] is None for col in key_columns)]


def count_nodes_by_label(
    driver: Driver,
    labels: list[str],
    sync_run_id: str | None = None,
    database: str | None = None,
) -> dict[str, int]:
    """라벨별 노드 건수를 센다.

    database를 주면(새 DB에 적재+검증 후 승격하는 흐름에서, 아직 기본
    데이터베이스가 아닌 새 DB를 대상으로) 그 데이터베이스만 센다. 안 주면
    드라이버의 기본 데이터베이스(독립 실행용)를 쓴다. sync_run_id를 주면
    그 실행에서 적재된 노드만 센다.
    """
    counts: dict[str, int] = {}
    with driver.session(database=database) as session:
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
    driver: Driver,
    rel_types: list[str],
    sync_run_id: str | None = None,
    database: str | None = None,
) -> dict[str, int]:
    """관계타입별 건수를 센다. database/sync_run_id 의미는 count_nodes_by_label과 동일하다."""
    counts: dict[str, int] = {}
    with driver.session(database=database) as session:
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


def _entity_exists(
    session: Session,
    label: str,
    key_field: str,
    key_value: object,
    sync_run_id: str | None,
) -> bool:
    """라벨+키로 노드가 1건 이상 있는지. sync_run_id를 주면 그 실행분만 본다.

    verify_fixture_entities의 Product 루프·supplier·workOrder가 같은 모양의
    존재 확인 쿼리를 세 벌 복제하고 있어 하나로 모은다.
    """
    result = session.run(
        f"""
        MATCH (n:{label} {{{key_field}: $id}})
        WHERE $syncRunId IS NULL OR n.syncRunId = $syncRunId
        RETURN count(n) AS c
        """,
        id=key_value,
        syncRunId=sync_run_id,
    )
    return result.single()["c"] > 0


def verify_fixture_entities(
    driver: Driver,
    entities: dict[str, Any],
    sync_run_id: str | None = None,
    database: str | None = None,
) -> list[str]:
    """query_parameters.json의 entities가 실제로 그래프에 존재하는지 확인한다.

    Q12~Q20 fixture의 "시작 노드"가 전부 존재해야 한다는 완료 조건(
    docs/etl/2-structured_mvp_loading_rules.md 7절)을 검사한다. Gold 쿼리 자체는 이번
    범위 밖이라 여기서는 시작점 존재 여부만 확인한다.

    database/sync_run_id 의미는 count_nodes_by_label과 동일하다.
    """
    failures: list[str] = []
    with driver.session(database=database) as session:
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
            if not _entity_exists(
                session, "Product", "productId", product_id, sync_run_id
            ):
                failures.append(f"{entity_key}: Product {product_id} 없음")

        supplier_id = entities["supplier"]["supplierId"]
        if not _entity_exists(
            session, "Supplier", "supplierId", supplier_id, sync_run_id
        ):
            failures.append(f"supplier: Supplier {supplier_id} 없음")

        work_order_id = entities["workOrder"]["workOrderId"]
        if not _entity_exists(
            session, "WorkOrder", "workOrderId", work_order_id, sync_run_id
        ):
            failures.append(f"workOrder: WorkOrder {work_order_id} 없음")

    return failures


def verify_work_order_17747_fixture(
    driver: Driver,
    sync_run_id: str | None = None,
    database: str | None = None,
) -> list[str]:
    """docs/etl/2-structured_mvp_loading_rules.md 7절의 구체적 fixture 검증:
    작업지시 17747의 공정 순서 1·6과 작업장 10·50이 존재해야 한다.

    database/sync_run_id 의미는 count_nodes_by_label과 동일하다.
    """
    failures: list[str] = []
    with driver.session(database=database) as session:
        result = session.run(
            """
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
    driver: Driver,
    sync_run_id: str | None = None,
    database: str | None = None,
) -> list[str]:
    """docs/etl/2-structured_mvp_loading_rules.md 7절 + RQ19 계약: Product 680에서
    492로 가는 필요수량이 10개 생산 기준 80이어야 한다(bomAsOfDate=2014-08-08
    유효 경로만). RQ19 businessRules: "같은 componentId에 도달하는 모든 경로
    필요수량을 합산한다" - 680->492 사이에 유효 경로가 여러 개면 그 전부를
    더해야 한다(680->492는 실제로 유효 경로가 1개뿐이라 합산해도 기댓값은
    80 그대로다).

    database/sync_run_id 의미는 count_nodes_by_label과 동일하다.
    """
    with driver.session(database=database) as session:
        result = session.run(
            """
            MATCH path = (start:Product {productId: 680})-[r:REQUIRES_COMPONENT*1..4]->
                         (end:Product {productId: 492})
            WHERE all(rel IN r WHERE
                rel.startDate <= date('2014-08-08')
                AND (rel.endDate IS NULL OR date('2014-08-08') < rel.endDate)
                AND ($syncRunId IS NULL OR rel.syncRunId = $syncRunId))
              AND ($syncRunId IS NULL OR (
                  start.syncRunId = $syncRunId AND end.syncRunId = $syncRunId
              ))
            RETURN sum(reduce(qty = 10.0, rel IN r | qty * rel.quantityPerAssembly))
                       AS requiredQty,
                   count(*) AS pathCount
            """,
            syncRunId=sync_run_id,
        )
        record = result.single()
        if record is None or record["pathCount"] == 0:
            return ["Product 680 -> 492 유효 BOM 경로가 없음"]
        if not quantities_match(record["requiredQty"], 80):
            return [
                f"Product 680 -> 492 필요수량(전체 경로 합산) 80 기대, "
                f"실제 {record['requiredQty']} (경로 {record['pathCount']}개)"
            ]
    return []


def main() -> None:
    """재적재 없이 현재 기본 Neo4j 데이터베이스 상태만 검증한다.

    사용법(리포 루트 기준): python etl/structured_mvp_validate.py
    """
    load_dotenv(ROOT_DIR / ".env")

    driver = connect_neo4j_from_env()
    print(f"대상: {os.environ['NEO4J_URI']}")

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
