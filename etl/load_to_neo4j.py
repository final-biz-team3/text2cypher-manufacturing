"""
CSV -> Neo4j 원격 서버 적재 스크립트 (Bolt 드라이버 기반, 유일한 적재 경로)

배경: 팀이 운영하는 Neo4j는 .env의 NEO4J_URI(원격 공유 서버)이고 그 서버
파일시스템에 접근할 수 없으므로, 로컬에서 CSV를 읽어 Bolt 드라이버로 UNWIND
배치 전송해 적재한다. 실행 순서는 제약조건 -> 마스터 -> (마스터 관계 prune) ->
트랜잭션 -> 검증이다(0005 ADR 참고).

사용법 (리포 루트 기준으로 실행):
    python etl/export_to_csv.py master
    python etl/export_to_csv.py tx --before 2026-09-11   # 초기 백필
    python etl/load_to_neo4j.py --dir tx_backfill

    python etl/export_to_csv.py tx --since-last          # 워터마크 증분(기본 동작)
    python etl/load_to_neo4j.py --dir tx_incremental

    python etl/export_to_csv.py tx --month 2014-05        # 강제 재적재(backfill/reprocessing)
    python etl/load_to_neo4j.py --month 2014-05

제약조건·마스터는 MERGE 기반이라 재실행해도 안전하다. 마스터 관계 중 그룹 A는
prune까지 포함해서, 원본에서 사라진 자연키를 가진 기존 관계를 정리한다.
"""

import argparse
import csv
from pathlib import Path

from neo4j import GraphDatabase

ETL_DIR = Path(__file__).resolve().parent
ROOT_DIR = ETL_DIR.parent
IMPORT_DIR = ETL_DIR / "import"
BATCH_SIZE = 1000

CONSTRAINTS = [
    "CREATE CONSTRAINT product_id IF NOT EXISTS FOR (n:Product) REQUIRE n.productId IS UNIQUE",
    "CREATE CONSTRAINT supplier_id IF NOT EXISTS FOR (n:Supplier) REQUIRE n.supplierId IS UNIQUE",
    "CREATE CONSTRAINT product_category_id IF NOT EXISTS FOR (n:ProductCategory) REQUIRE n.categoryId IS UNIQUE",
    "CREATE CONSTRAINT product_subcategory_id IF NOT EXISTS FOR (n:ProductSubcategory) REQUIRE n.subcategoryId IS UNIQUE",
    "CREATE CONSTRAINT location_id IF NOT EXISTS FOR (n:Location) REQUIRE n.locationId IS UNIQUE",
    "CREATE CONSTRAINT scrap_reason_id IF NOT EXISTS FOR (n:ScrapReason) REQUIRE n.scrapReasonId IS UNIQUE",
    "CREATE CONSTRAINT purchase_order_id IF NOT EXISTS FOR (n:PurchaseOrder) REQUIRE n.purchaseOrderId IS UNIQUE",
    "CREATE CONSTRAINT purchase_order_line_id IF NOT EXISTS FOR (n:PurchaseOrderLine) REQUIRE n.purchaseOrderLineId IS UNIQUE",
    "CREATE CONSTRAINT sales_order_id IF NOT EXISTS FOR (n:SalesOrder) REQUIRE n.salesOrderId IS UNIQUE",
    "CREATE CONSTRAINT work_order_id IF NOT EXISTS FOR (n:WorkOrder) REQUIRE n.workOrderId IS UNIQUE",
    "CREATE CONSTRAINT routing_operation_key IF NOT EXISTS FOR (n:RoutingOperation) REQUIRE n.routingOperationKey IS UNIQUE",
]

# (라벨/타입, CSV 파일명, UNWIND $rows AS row 뒤에 붙는 Cypher 본문)
MASTER_NODE_STEPS = [
    ("Product", "nodes_product.csv", """
MERGE (n:Product {productId: toInteger(row.productId)})
SET n.name = row.name, n.productNumber = row.productNumber,
    n.makeInHouse = toBoolean(row.makeInHouse), n.sellableFinishedGood = toBoolean(row.sellableFinishedGood),
    n.color = row.color, n.safetyStockLevel = toInteger(row.safetyStockLevel), n.reorderPoint = toInteger(row.reorderPoint),
    n.standardCost = toFloat(row.standardCost), n.listPrice = toFloat(row.listPrice),
    n.size = row.size, n.sizeUnit = row.sizeUnit, n.weightUnit = row.weightUnit,
    n.weight = CASE WHEN row.weight <> '' THEN toFloat(row.weight) END,
    n.daysToManufacture = toInteger(row.daysToManufacture),
    n.productLine = row.productLine, n.classCode = row.classCode, n.styleCode = row.styleCode,
    n.sellStartDate = date(row.sellStartDate),
    n.sellEndDate = CASE WHEN row.sellEndDate <> '' THEN date(row.sellEndDate) END,
    n.discontinuedDate = CASE WHEN row.discontinuedDate <> '' THEN date(row.discontinuedDate) END,
    n.rowGuid = row.rowGuid, n.modifiedAt = localdatetime(row.modifiedAt)
"""),
    ("Supplier", "nodes_supplier.csv", """
MERGE (n:Supplier {supplierId: toInteger(row.supplierId)})
SET n.accountNumber = row.accountNumber, n.name = row.name,
    n.creditRating = toInteger(row.creditRating),
    n.preferred = toBoolean(row.preferred), n.active = toBoolean(row.active),
    n.purchasingWebUrl = row.purchasingWebUrl, n.modifiedAt = localdatetime(row.modifiedAt)
"""),
    ("ProductCategory", "nodes_product_category.csv", """
MERGE (n:ProductCategory {categoryId: toInteger(row.categoryId)})
SET n.name = row.name, n.nameKo = row.nameKo, n.rowGuid = row.rowGuid,
    n.modifiedAt = localdatetime(row.modifiedAt)
"""),
    ("ProductSubcategory", "nodes_product_subcategory.csv", """
MERGE (n:ProductSubcategory {subcategoryId: toInteger(row.subcategoryId)})
SET n.name = row.name, n.nameKo = row.nameKo, n.rowGuid = row.rowGuid,
    n.modifiedAt = localdatetime(row.modifiedAt)
"""),
    ("Location", "nodes_location.csv", """
MERGE (n:Location {locationId: toInteger(row.locationId)})
SET n.name = row.name, n.nameKo = row.nameKo,
    n.costRate = toFloat(row.costRate), n.availability = toFloat(row.availability),
    n.modifiedAt = localdatetime(row.modifiedAt)
"""),
    ("ScrapReason", "nodes_scrap_reason.csv", """
MERGE (n:ScrapReason {scrapReasonId: toInteger(row.scrapReasonId)})
SET n.name = row.name, n.nameKo = row.nameKo, n.modifiedAt = localdatetime(row.modifiedAt)
"""),
]

MASTER_REL_STEPS = [
    ("SUPPLIES", "rels_supplies.csv", """
MATCH (v:Supplier {supplierId: toInteger(row.supplierId)})
MATCH (p:Product {productId: toInteger(row.productId)})
MERGE (v)-[r:SUPPLIES {supplyKey: row.supplyKey}]->(p)
SET r.averageLeadTimeDays = toInteger(row.averageLeadTimeDays),
    r.standardPrice = toFloat(row.standardPrice),
    r.lastReceiptCost = CASE WHEN row.lastReceiptCost <> '' THEN toFloat(row.lastReceiptCost) END,
    r.lastReceiptDate = CASE WHEN row.lastReceiptDate <> '' THEN date(row.lastReceiptDate) END,
    r.minOrderQty = toInteger(row.minOrderQty), r.maxOrderQty = toInteger(row.maxOrderQty),
    r.onOrderQty = CASE WHEN row.onOrderQty <> '' THEN toInteger(row.onOrderQty) END,
    r.unitCode = row.unitCode,
    r.modifiedAt = CASE WHEN row.modifiedAt <> '' THEN localdatetime(row.modifiedAt) END
"""),
    ("REQUIRES_COMPONENT", "rels_requires_component.csv", """
MATCH (a:Product {productId: toInteger(row.assemblyProductId)})
MATCH (c:Product {productId: toInteger(row.componentProductId)})
MERGE (a)-[r:REQUIRES_COMPONENT {bomId: toInteger(row.bomId)}]->(c)
SET r.startDate = date(row.startDate),
    r.endDate = CASE WHEN row.endDate <> '' THEN date(row.endDate) END,
    r.unitCode = row.unitCode, r.bomLevel = toInteger(row.bomLevel),
    r.quantityPerAssembly = toFloat(row.quantityPerAssembly),
    r.modifiedAt = localdatetime(row.modifiedAt)
"""),
    ("STOCKED_AT", "rels_stocked_at.csv", """
MATCH (p:Product {productId: toInteger(row.productId)})
MATCH (l:Location {locationId: toInteger(row.locationId)})
MERGE (p)-[r:STOCKED_AT {inventoryGuid: row.inventoryGuid}]->(l)
SET r.shelf = row.shelf, r.bin = toInteger(row.bin), r.quantity = toInteger(row.quantity),
    r.modifiedAt = localdatetime(row.modifiedAt)
"""),
    ("IN_SUBCATEGORY", "rels_in_subcategory.csv", """
MATCH (p:Product {productId: toInteger(row.productId)})
OPTIONAL MATCH (p)-[old:IN_SUBCATEGORY]->()
DELETE old
WITH p, row
MATCH (s:ProductSubcategory {subcategoryId: toInteger(row.subcategoryId)})
CREATE (p)-[:IN_SUBCATEGORY]->(s)
"""),
    ("IN_CATEGORY", "rels_in_category.csv", """
MATCH (s:ProductSubcategory {subcategoryId: toInteger(row.subcategoryId)})
OPTIONAL MATCH (s)-[old:IN_CATEGORY]->()
DELETE old
WITH s, row
MATCH (c:ProductCategory {categoryId: toInteger(row.categoryId)})
CREATE (s)-[:IN_CATEGORY]->(c)
"""),
]

TX_NODE_STEPS = [
    ("PurchaseOrder", "nodes_purchase_order.csv", """
MERGE (n:PurchaseOrder {purchaseOrderId: toInteger(row.purchaseOrderId)})
SET n.revisionNumber = toInteger(row.revisionNumber), n.statusCode = toInteger(row.statusCode),
    n.employeeId = toInteger(row.employeeId), n.shipMethodId = toInteger(row.shipMethodId),
    n.orderDate = date(row.orderDate),
    n.shipDate = CASE WHEN row.shipDate <> '' THEN date(row.shipDate) END,
    n.subTotal = toFloat(row.subTotal), n.taxAmount = toFloat(row.taxAmount), n.freight = toFloat(row.freight),
    n.modifiedAt = CASE WHEN row.modifiedAt <> '' THEN localdatetime(row.modifiedAt) END
"""),
    ("PurchaseOrderLine", "nodes_purchase_order_line.csv", """
MERGE (n:PurchaseOrderLine {purchaseOrderLineId: toInteger(row.purchaseOrderLineId)})
SET n.dueDate = date(row.dueDate), n.orderQty = toInteger(row.orderQty),
    n.unitPrice = toFloat(row.unitPrice), n.receivedQty = toFloat(row.receivedQty),
    n.rejectedQty = toFloat(row.rejectedQty),
    n.modifiedAt = CASE WHEN row.modifiedAt <> '' THEN localdatetime(row.modifiedAt) END
"""),
    ("SalesOrder", "nodes_sales_order.csv", """
MERGE (n:SalesOrder {salesOrderId: toInteger(row.salesOrderId)})
SET n.revisionNumber = toInteger(row.revisionNumber), n.salesOrderNumber = row.salesOrderNumber,
    n.orderDate = date(row.orderDate), n.dueDate = date(row.dueDate),
    n.shipDate = CASE WHEN row.shipDate <> '' THEN date(row.shipDate) END,
    n.statusCode = toInteger(row.statusCode), n.onlineOrder = toBoolean(row.onlineOrder),
    n.purchaseOrderNumber = row.purchaseOrderNumber, n.accountNumber = row.accountNumber,
    n.customerId = toInteger(row.customerId),
    n.salesPersonId = CASE WHEN row.salesPersonId <> '' THEN toInteger(row.salesPersonId) END,
    n.salesTerritoryId = CASE WHEN row.salesTerritoryId <> '' THEN toInteger(row.salesTerritoryId) END,
    n.shipMethodId = toInteger(row.shipMethodId),
    n.subTotal = toFloat(row.subTotal), n.taxAmount = toFloat(row.taxAmount), n.freight = toFloat(row.freight),
    n.totalDue = toFloat(row.totalDue), n.rowGuid = row.rowGuid, n.modifiedAt = localdatetime(row.modifiedAt)
"""),
    ("WorkOrder", "nodes_work_order.csv", """
MERGE (n:WorkOrder {workOrderId: toInteger(row.workOrderId)})
SET n.orderQty = toInteger(row.orderQty), n.stockedQty = toInteger(row.stockedQty),
    n.scrappedQty = toInteger(row.scrappedQty), n.startDate = date(row.startDate),
    n.endDate = CASE WHEN row.endDate <> '' THEN date(row.endDate) END,
    n.dueDate = date(row.dueDate), n.modifiedAt = localdatetime(row.modifiedAt)
"""),
    ("RoutingOperation", "nodes_routing_operation.csv", """
MERGE (n:RoutingOperation {routingOperationKey: row.routingOperationKey})
SET n.sequence = toInteger(row.sequence),
    n.plannedStartDate = date(row.plannedStartDate), n.plannedEndDate = date(row.plannedEndDate),
    n.actualStartDate = CASE WHEN row.actualStartDate <> '' THEN date(row.actualStartDate) END,
    n.actualEndDate = CASE WHEN row.actualEndDate <> '' THEN date(row.actualEndDate) END,
    n.actualHours = CASE WHEN row.actualHours <> '' THEN toFloat(row.actualHours) END,
    n.plannedCost = toFloat(row.plannedCost),
    n.actualCost = CASE WHEN row.actualCost <> '' THEN toFloat(row.actualCost) END,
    n.modifiedAt = localdatetime(row.modifiedAt)
"""),
]

TX_REL_STEPS = [
    ("CONTAINS_PRODUCT", "rels_contains_product.csv", """
MATCH (so:SalesOrder {salesOrderId: toInteger(row.salesOrderId)})
MATCH (p:Product {productId: toInteger(row.productId)})
MERGE (so)-[r:CONTAINS_PRODUCT {salesOrderLineId: toInteger(row.salesOrderLineId)}]->(p)
SET r.carrierTrackingNumber = row.carrierTrackingNumber, r.orderQty = toInteger(row.orderQty),
    r.specialOfferId = toInteger(row.specialOfferId), r.unitPrice = toFloat(row.unitPrice),
    r.unitPriceDiscount = toFloat(row.unitPriceDiscount), r.lineTotal = toFloat(row.lineTotal),
    r.rowGuid = row.rowGuid, r.modifiedAt = localdatetime(row.modifiedAt)
"""),
    ("HAS_LINE", "rels_has_line.csv", """
MATCH (po:PurchaseOrder {purchaseOrderId: toInteger(row.purchaseOrderId)})
MATCH (pol:PurchaseOrderLine {purchaseOrderLineId: toInteger(row.purchaseOrderLineId)})
MERGE (po)-[:HAS_LINE]->(pol)
"""),
    ("HAS_OPERATION", "rels_has_operation.csv", """
MATCH (w:WorkOrder {workOrderId: toInteger(row.workOrderId)})
MATCH (ro:RoutingOperation {routingOperationKey: row.routingOperationKey})
MERGE (w)-[:HAS_OPERATION]->(ro)
"""),
    ("PLACED_WITH", "rels_placed_with.csv", """
MATCH (po:PurchaseOrder {purchaseOrderId: toInteger(row.purchaseOrderId)})
MATCH (s:Supplier {supplierId: toInteger(row.supplierId)})
MERGE (po)-[:PLACED_WITH]->(s)
"""),
    ("FOR_PRODUCT", "rels_for_product.csv", """
MATCH (pol:PurchaseOrderLine {purchaseOrderLineId: toInteger(row.purchaseOrderLineId)})
MATCH (p:Product {productId: toInteger(row.productId)})
MERGE (pol)-[:FOR_PRODUCT]->(p)
"""),
    ("PRODUCES", "rels_produces.csv", """
MATCH (w:WorkOrder {workOrderId: toInteger(row.workOrderId)})
MATCH (p:Product {productId: toInteger(row.productId)})
MERGE (w)-[:PRODUCES]->(p)
"""),
    ("PERFORMED_AT", "rels_performed_at.csv", """
MATCH (ro:RoutingOperation {routingOperationKey: row.routingOperationKey})
MATCH (l:Location {locationId: toInteger(row.locationId)})
MERGE (ro)-[:PERFORMED_AT]->(l)
"""),
    ("SCRAPPED_DUE_TO", "rels_scrapped_due_to.csv", """
MATCH (w:WorkOrder {workOrderId: toInteger(row.workOrderId)})
MATCH (r:ScrapReason {scrapReasonId: toInteger(row.scrapReasonId)})
MERGE (w)-[:SCRAPPED_DUE_TO]->(r)
"""),
]

NODE_LABELS = [
    "Product", "Supplier", "ProductCategory", "ProductSubcategory", "Location", "ScrapReason",
    "PurchaseOrder", "PurchaseOrderLine", "SalesOrder", "WorkOrder", "RoutingOperation",
]

# 그룹 A 중 마스터 관계 전용 prune 대상 (라벨, CSV 파일명, 자연키 속성명, 파이썬 타입).
# 마스터 export는 매번 그 시점의 전체 현황이므로, 이번 CSV에 없는 자연키를 가진
# 기존 관계는 원본에서 사라진 것으로 보고 지운다(0005 ADR "결정 2" 참고). 트랜잭션
# 쪽 naturalKeyMerge 관계(CONTAINS_PRODUCT)는 워터마크 기반 부분 export라 여기
# 포함하지 않는다 — 적용하면 다른 시점의 정상 데이터를 지우게 된다.
MASTER_REL_PRUNE_STEPS = [
    ("SUPPLIES", "rels_supplies.csv", "supplyKey", str),
    ("REQUIRES_COMPONENT", "rels_requires_component.csv", "bomId", int),
    ("STOCKED_AT", "rels_stocked_at.csv", "inventoryGuid", str),
]


def load_env() -> dict:
    env_path = ROOT_DIR / ".env"
    env = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"CSV를 찾을 수 없습니다: {path}")
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def run_step(session, label: str, path: Path, cypher_body: str) -> None:
    rows = read_rows(path)
    query = "UNWIND $rows AS row\n" + cypher_body
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        session.execute_write(lambda tx, b=batch, q=query: tx.run(q, rows=b).consume())
    print(f"  {label}: {len(rows)} rows")


def prune_master_relationship(session, rel_type: str, path: Path, natural_key: str, key_type: type) -> None:
    """이번 export(CSV)에 없는 자연키를 가진 기존 rel_type 관계를 삭제한다."""
    rows = read_rows(path)
    keys = [key_type(row[natural_key]) for row in rows]
    query = f"""
MATCH ()-[r:{rel_type}]->()
WHERE NOT r.{natural_key} IN $keys
DELETE r
"""
    before = session.run(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS c").single()["c"]
    session.execute_write(lambda tx: tx.run(query, keys=keys).consume())
    after = session.run(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS c").single()["c"]
    print(f"  {rel_type} prune: {before} -> {after} (-{before - after})")


def main() -> None:
    parser = argparse.ArgumentParser()
    tx_group = parser.add_mutually_exclusive_group(required=True)
    tx_group.add_argument("--month", help="YYYY-MM (etl/import/tx_<month>/ 를 적재. 강제 재적재)")
    tx_group.add_argument("--dir", help="etl/import/ 아래 트랜잭션 폴더명 (예: tx_backfill, tx_incremental)")
    args = parser.parse_args()

    tx_dir_name = f"tx_{args.month}" if args.month else args.dir
    tx_dir = IMPORT_DIR / tx_dir_name
    master_dir = IMPORT_DIR / "master"

    env = load_env()
    uri, user, password = env["NEO4J_URI"], env["NEO4J_USER"], env["NEO4J_PASSWORD"]
    print(f"연결: {uri}")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        with driver.session() as session:
            print("1. 제약조건")
            for stmt in CONSTRAINTS:
                session.run(stmt).consume()

            print("2. 마스터 노드")
            for label, filename, body in MASTER_NODE_STEPS:
                run_step(session, label, master_dir / filename, body)

            print("2. 마스터 관계")
            for label, filename, body in MASTER_REL_STEPS:
                run_step(session, label, master_dir / filename, body)

            print("2. 마스터 관계 prune (원본에서 사라진 자연키 정리)")
            for rel_type, filename, natural_key, key_type in MASTER_REL_PRUNE_STEPS:
                prune_master_relationship(session, rel_type, master_dir / filename, natural_key, key_type)

            print(f"3. 트랜잭션 노드 ({tx_dir_name})")
            for label, filename, body in TX_NODE_STEPS:
                run_step(session, label, tx_dir / filename, body)

            print(f"3. 트랜잭션 관계 ({tx_dir_name})")
            for label, filename, body in TX_REL_STEPS:
                run_step(session, label, tx_dir / filename, body)

            print("4. 검증 (노드)")
            for lbl in NODE_LABELS:
                count = session.run(f"MATCH (n:{lbl}) RETURN count(n) AS c").single()["c"]
                print(f"  {lbl}: {count}")

            print("4. 검증 (관계)")
            for row in session.run("MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count ORDER BY type"):
                print(f"  {row['type']}: {row['count']}")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
