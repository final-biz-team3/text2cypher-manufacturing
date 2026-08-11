// ============================================================================
// Neo4j 적재 스크립트
// 기준: docs/adr/0004-graph-schema-v2.md (스키마 설계) / docs/adr/0005-etl-batch-loading-pipeline.md (마스터/트랜잭션 분리, 월별 일괄적재)
//
// 사용법
// 이 파일은 제약조건(1) -> 마스터 적재(2) -> 트랜잭션 적재(3, $month 파라미터 필요) -> 검증(4)을
// 한 번에 실행하는 하나의 스크립트다. 1·2번은 IF NOT EXISTS/MERGE 기반이라 재실행해도 안전하므로,
// 최초 실행이든 매달 반복 실행이든 항상 파일 전체를 그대로 돌리면 된다. 다만 3번이 $month를 쓰므로,
// 실행 시점에는 그 값에 해당하는 tx_<월> 폴더가 반드시 먼저 export되어 있어야 한다(없으면 파일을
// 못 찾아 에러가 난다).
//
//   [최초 실행 — 마스터 + 첫 달 트랜잭션을 한 번에]
//     python etl/export_to_csv.py master
//     python etl/export_to_csv.py tx --month 2014-05
//     set -a; source .env; set +a
//     docker compose exec neo4j cypher-shell -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" \
//       -P '{month: "2014-05"}' -f /etl/load.cypher
//
//   [매달 반복 — 새 달 트랜잭션만 추가]
//     python etl/export_to_csv.py tx --month 2014-06
//     set -a; source .env; set +a
//     docker compose exec neo4j cypher-shell -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" \
//       -P '{month: "2014-06"}' -f /etl/load.cypher
//     (제약조건·마스터 부분도 같이 재실행되지만 이미 있는 내용을 다시 MERGE하는 것뿐이라
//      결과에 영향 없음. 실질적으로는 2014-06 트랜잭션만 새로 추가된다)
//
// 이 파일은 git에 커밋되는 소스 코드라 etl/ 아래에 두고(생성물인 etl/import/와는 다른 폴더),
// docker-compose.yml이 ./etl 을 컨테이너의 /etl(읽기 전용)에도 같이 마운트해둔다. cypher-shell을
// 컨테이너 안에서 실행하면(neo4j:5-community 이미지에 기본 포함) 이 파일과 CSV(/import) 둘 다
// 별도 설치 없이 바로 접근 가능하다. cypher-shell을 호스트에 따로 설치했다면 -a bolt://localhost:7687
// 로 접속해서 로컬 경로의 이 파일을 그대로 -f로 넘겨도 동일하게 동작한다(LOAD CSV의 파일 경로 해석은
// 항상 서버 쪽 /import 기준이라 클라이언트가 어디서 실행되는지와 무관하다).
//
// 주의: docker-compose.yml의 볼륨 마운트는 컨테이너를 새로 만들 때만 적용된다. neo4j 컨테이너가
// 이 볼륨 설정이 추가되기 전부터 이미 떠 있었다면 `docker compose up -d neo4j`를 다시 실행해
// 재생성해야 /import, /etl 마운트가 실제로 잡힌다(보통 설정 변경을 감지해 자동 재생성되지만,
// 안 되면 `docker compose up -d --force-recreate neo4j`로 강제한다. neo4j_data는 named volume
// 이라 재생성해도 기존 데이터는 유지된다). 재생성 후 `docker compose exec neo4j ls /import`,
// `docker compose exec neo4j ls /etl`로 마운트를 먼저 확인하고 이 스크립트를 실행할 것.
//
// 근거:
//   - LOAD CSV는 dbms.directories.import(Docker에서는 /import) 아래 파일만 file:///로 읽음:
//     https://neo4j.com/docs/cypher-manual/current/clauses/load-csv/
//     https://neo4j.com/docs/operations-manual/current/docker/mounting-volumes/ (Docker 표준 마운트 포인트 /import)
//   - 대용량 임포트는 'CALL { ... } IN TRANSACTIONS OF n ROWS' 사용(Neo4j 5, PERIODIC COMMIT 대체)
//   - cypher-shell -P/--param은 맵 리터럴 문법(-P '{a: 1}')을 쓴다:
//     https://neo4j.com/docs/operations-manual/current/cypher-shell/
//
// 실행 순서: 제약조건 -> 마스터 노드 -> 마스터 관계 -> 트랜잭션 노드 -> 트랜잭션 관계
// (관계는 양쪽 노드가 먼저 있어야 MATCH 가능)
// ============================================================================

// ---------------------------------------------------------------------------
// 1. 제약조건 (11개 노드, 유니크 키 = 인덱스 자동 생성, 이후 MATCH/MERGE 성능 확보)
// ---------------------------------------------------------------------------
CREATE CONSTRAINT product_id IF NOT EXISTS FOR (n:Product) REQUIRE n.productId IS UNIQUE;
CREATE CONSTRAINT supplier_id IF NOT EXISTS FOR (n:Supplier) REQUIRE n.supplierId IS UNIQUE;
CREATE CONSTRAINT product_category_id IF NOT EXISTS FOR (n:ProductCategory) REQUIRE n.categoryId IS UNIQUE;
CREATE CONSTRAINT product_subcategory_id IF NOT EXISTS FOR (n:ProductSubcategory) REQUIRE n.subcategoryId IS UNIQUE;
CREATE CONSTRAINT location_id IF NOT EXISTS FOR (n:Location) REQUIRE n.locationId IS UNIQUE;
CREATE CONSTRAINT scrap_reason_id IF NOT EXISTS FOR (n:ScrapReason) REQUIRE n.scrapReasonId IS UNIQUE;
CREATE CONSTRAINT purchase_order_id IF NOT EXISTS FOR (n:PurchaseOrder) REQUIRE n.purchaseOrderId IS UNIQUE;
CREATE CONSTRAINT purchase_order_line_id IF NOT EXISTS FOR (n:PurchaseOrderLine) REQUIRE n.purchaseOrderLineId IS UNIQUE;
CREATE CONSTRAINT sales_order_id IF NOT EXISTS FOR (n:SalesOrder) REQUIRE n.salesOrderId IS UNIQUE;
CREATE CONSTRAINT work_order_id IF NOT EXISTS FOR (n:WorkOrder) REQUIRE n.workOrderId IS UNIQUE;
CREATE CONSTRAINT routing_operation_key IF NOT EXISTS FOR (n:RoutingOperation) REQUIRE n.routingOperationKey IS UNIQUE;

// ---------------------------------------------------------------------------
// 2. 마스터 적재 (1회 — 전부 MERGE 기반, 재실행해도 안전)
//    입력: /import/master/*.csv
// ---------------------------------------------------------------------------

// 2-1. 마스터 노드
:auto LOAD CSV WITH HEADERS FROM 'file:///master/nodes_product.csv' AS row
CALL { WITH row
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
} IN TRANSACTIONS OF 1000 ROWS;

:auto LOAD CSV WITH HEADERS FROM 'file:///master/nodes_supplier.csv' AS row
CALL { WITH row
  MERGE (n:Supplier {supplierId: toInteger(row.supplierId)})
  SET n.accountNumber = row.accountNumber, n.name = row.name,
      n.creditRating = toInteger(row.creditRating),
      n.preferred = toBoolean(row.preferred), n.active = toBoolean(row.active),
      n.purchasingWebUrl = row.purchasingWebUrl, n.modifiedAt = localdatetime(row.modifiedAt)
} IN TRANSACTIONS OF 1000 ROWS;

:auto LOAD CSV WITH HEADERS FROM 'file:///master/nodes_product_category.csv' AS row
CALL { WITH row
  MERGE (n:ProductCategory {categoryId: toInteger(row.categoryId)})
  SET n.name = row.name, n.nameKo = row.nameKo, n.rowGuid = row.rowGuid,
      n.modifiedAt = localdatetime(row.modifiedAt)
} IN TRANSACTIONS OF 1000 ROWS;

:auto LOAD CSV WITH HEADERS FROM 'file:///master/nodes_product_subcategory.csv' AS row
CALL { WITH row
  MERGE (n:ProductSubcategory {subcategoryId: toInteger(row.subcategoryId)})
  SET n.name = row.name, n.nameKo = row.nameKo, n.rowGuid = row.rowGuid,
      n.modifiedAt = localdatetime(row.modifiedAt)
} IN TRANSACTIONS OF 1000 ROWS;

:auto LOAD CSV WITH HEADERS FROM 'file:///master/nodes_location.csv' AS row
CALL { WITH row
  MERGE (n:Location {locationId: toInteger(row.locationId)})
  SET n.name = row.name, n.nameKo = row.nameKo,
      n.costRate = toFloat(row.costRate), n.availability = toFloat(row.availability),
      n.modifiedAt = localdatetime(row.modifiedAt)
} IN TRANSACTIONS OF 1000 ROWS;

:auto LOAD CSV WITH HEADERS FROM 'file:///master/nodes_scrap_reason.csv' AS row
CALL { WITH row
  MERGE (n:ScrapReason {scrapReasonId: toInteger(row.scrapReasonId)})
  SET n.name = row.name, n.nameKo = row.nameKo, n.modifiedAt = localdatetime(row.modifiedAt)
} IN TRANSACTIONS OF 1000 ROWS;

// 2-2. 마스터 관계
// 그룹 A(자연키 MERGE) — SUPPLIES, REQUIRES_COMPONENT, STOCKED_AT
:auto LOAD CSV WITH HEADERS FROM 'file:///master/rels_supplies.csv' AS row
CALL { WITH row
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
} IN TRANSACTIONS OF 1000 ROWS;

:auto LOAD CSV WITH HEADERS FROM 'file:///master/rels_requires_component.csv' AS row
CALL { WITH row
  MATCH (a:Product {productId: toInteger(row.assemblyProductId)})
  MATCH (c:Product {productId: toInteger(row.componentProductId)})
  MERGE (a)-[r:REQUIRES_COMPONENT {bomId: toInteger(row.bomId)}]->(c)
  SET r.startDate = date(row.startDate),
      r.endDate = CASE WHEN row.endDate <> '' THEN date(row.endDate) END,
      r.unitCode = row.unitCode, r.bomLevel = toInteger(row.bomLevel),
      r.quantityPerAssembly = toFloat(row.quantityPerAssembly),
      r.modifiedAt = localdatetime(row.modifiedAt)
} IN TRANSACTIONS OF 1000 ROWS;
// -> BOM 유효기간 필터(팀 결정 3)는 조회 시점에 적용:
//    WHERE r.startDate <= date() AND (r.endDate IS NULL OR date() < r.endDate)

:auto LOAD CSV WITH HEADERS FROM 'file:///master/rels_stocked_at.csv' AS row
CALL { WITH row
  MATCH (p:Product {productId: toInteger(row.productId)})
  MATCH (l:Location {locationId: toInteger(row.locationId)})
  MERGE (p)-[r:STOCKED_AT {inventoryGuid: row.inventoryGuid}]->(l)
  SET r.shelf = row.shelf, r.bin = toInteger(row.bin), r.quantity = toInteger(row.quantity),
      r.modifiedAt = localdatetime(row.modifiedAt)
} IN TRANSACTIONS OF 1000 ROWS;

// 그룹 C(마스터↔마스터, 값이 바뀔 수 있어 지우고-다시-만들기 필요) — IN_SUBCATEGORY, IN_CATEGORY
:auto LOAD CSV WITH HEADERS FROM 'file:///master/rels_in_subcategory.csv' AS row
CALL { WITH row
  MATCH (p:Product {productId: toInteger(row.productId)})
  OPTIONAL MATCH (p)-[old:IN_SUBCATEGORY]->()
  DELETE old
  WITH p, row
  MATCH (s:ProductSubcategory {subcategoryId: toInteger(row.subcategoryId)})
  CREATE (p)-[:IN_SUBCATEGORY]->(s)
} IN TRANSACTIONS OF 1000 ROWS;

:auto LOAD CSV WITH HEADERS FROM 'file:///master/rels_in_category.csv' AS row
CALL { WITH row
  MATCH (s:ProductSubcategory {subcategoryId: toInteger(row.subcategoryId)})
  OPTIONAL MATCH (s)-[old:IN_CATEGORY]->()
  DELETE old
  WITH s, row
  MATCH (c:ProductCategory {categoryId: toInteger(row.categoryId)})
  CREATE (s)-[:IN_CATEGORY]->(c)
} IN TRANSACTIONS OF 1000 ROWS;

// ---------------------------------------------------------------------------
// 3. 트랜잭션 적재 (매달 반복 — 실행 전 -P '{month: "YYYY-MM"}' 설정 필요)
//    입력: /import/tx_<month>/*.csv
// ---------------------------------------------------------------------------

// 3-1. 트랜잭션 노드
:auto LOAD CSV WITH HEADERS FROM ('file:///tx_' + $month + '/nodes_purchase_order.csv') AS row
CALL { WITH row
  MERGE (n:PurchaseOrder {purchaseOrderId: toInteger(row.purchaseOrderId)})
  SET n.revisionNumber = toInteger(row.revisionNumber), n.statusCode = toInteger(row.statusCode),
      n.employeeId = toInteger(row.employeeId), n.shipMethodId = toInteger(row.shipMethodId),
      n.orderDate = date(row.orderDate),
      n.shipDate = CASE WHEN row.shipDate <> '' THEN date(row.shipDate) END,
      n.subTotal = toFloat(row.subTotal), n.taxAmount = toFloat(row.taxAmount), n.freight = toFloat(row.freight),
      n.modifiedAt = CASE WHEN row.modifiedAt <> '' THEN localdatetime(row.modifiedAt) END
} IN TRANSACTIONS OF 1000 ROWS;

:auto LOAD CSV WITH HEADERS FROM ('file:///tx_' + $month + '/nodes_purchase_order_line.csv') AS row
CALL { WITH row
  MERGE (n:PurchaseOrderLine {purchaseOrderLineId: toInteger(row.purchaseOrderLineId)})
  SET n.dueDate = date(row.dueDate), n.orderQty = toInteger(row.orderQty),
      n.unitPrice = toFloat(row.unitPrice), n.receivedQty = toFloat(row.receivedQty),
      n.rejectedQty = toFloat(row.rejectedQty),
      n.modifiedAt = CASE WHEN row.modifiedAt <> '' THEN localdatetime(row.modifiedAt) END
} IN TRANSACTIONS OF 1000 ROWS;

:auto LOAD CSV WITH HEADERS FROM ('file:///tx_' + $month + '/nodes_sales_order.csv') AS row
CALL { WITH row
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
} IN TRANSACTIONS OF 1000 ROWS;

:auto LOAD CSV WITH HEADERS FROM ('file:///tx_' + $month + '/nodes_work_order.csv') AS row
CALL { WITH row
  MERGE (n:WorkOrder {workOrderId: toInteger(row.workOrderId)})
  SET n.orderQty = toInteger(row.orderQty), n.stockedQty = toInteger(row.stockedQty),
      n.scrappedQty = toInteger(row.scrappedQty), n.startDate = date(row.startDate),
      n.endDate = CASE WHEN row.endDate <> '' THEN date(row.endDate) END,
      n.dueDate = date(row.dueDate), n.modifiedAt = localdatetime(row.modifiedAt)
} IN TRANSACTIONS OF 1000 ROWS;

:auto LOAD CSV WITH HEADERS FROM ('file:///tx_' + $month + '/nodes_routing_operation.csv') AS row
CALL { WITH row
  MERGE (n:RoutingOperation {routingOperationKey: row.routingOperationKey})
  SET n.sequence = toInteger(row.sequence),
      n.plannedStartDate = date(row.plannedStartDate), n.plannedEndDate = date(row.plannedEndDate),
      n.actualStartDate = CASE WHEN row.actualStartDate <> '' THEN date(row.actualStartDate) END,
      n.actualEndDate = CASE WHEN row.actualEndDate <> '' THEN date(row.actualEndDate) END,
      n.actualHours = CASE WHEN row.actualHours <> '' THEN toFloat(row.actualHours) END,
      n.plannedCost = toFloat(row.plannedCost),
      n.actualCost = CASE WHEN row.actualCost <> '' THEN toFloat(row.actualCost) END,
      n.modifiedAt = localdatetime(row.modifiedAt)
} IN TRANSACTIONS OF 1000 ROWS;

// 3-2. 트랜잭션 관계
// 그룹 A(자연키 MERGE) — CONTAINS_PRODUCT
:auto LOAD CSV WITH HEADERS FROM ('file:///tx_' + $month + '/rels_contains_product.csv') AS row
CALL { WITH row
  MATCH (so:SalesOrder {salesOrderId: toInteger(row.salesOrderId)})
  MATCH (p:Product {productId: toInteger(row.productId)})
  MERGE (so)-[r:CONTAINS_PRODUCT {salesOrderLineId: toInteger(row.salesOrderLineId)}]->(p)
  SET r.carrierTrackingNumber = row.carrierTrackingNumber, r.orderQty = toInteger(row.orderQty),
      r.specialOfferId = toInteger(row.specialOfferId), r.unitPrice = toFloat(row.unitPrice),
      r.unitPriceDiscount = toFloat(row.unitPriceDiscount), r.lineTotal = toFloat(row.lineTotal),
      r.rowGuid = row.rowGuid, r.modifiedAt = localdatetime(row.modifiedAt)
} IN TRANSACTIONS OF 1000 ROWS;

// 그룹 B(속성 없음, 다건 허용, 단순 MERGE) — HAS_LINE, HAS_OPERATION
:auto LOAD CSV WITH HEADERS FROM ('file:///tx_' + $month + '/rels_has_line.csv') AS row
CALL { WITH row
  MATCH (po:PurchaseOrder {purchaseOrderId: toInteger(row.purchaseOrderId)})
  MATCH (pol:PurchaseOrderLine {purchaseOrderLineId: toInteger(row.purchaseOrderLineId)})
  MERGE (po)-[:HAS_LINE]->(pol)
} IN TRANSACTIONS OF 1000 ROWS;

:auto LOAD CSV WITH HEADERS FROM ('file:///tx_' + $month + '/rels_has_operation.csv') AS row
CALL { WITH row
  MATCH (w:WorkOrder {workOrderId: toInteger(row.workOrderId)})
  MATCH (ro:RoutingOperation {routingOperationKey: row.routingOperationKey})
  MERGE (w)-[:HAS_OPERATION]->(ro)
} IN TRANSACTIONS OF 1000 ROWS;

// 그룹 C(단일 타깃이지만, 트랜잭션 쪽 FK는 생성 시 고정되고 월별 배치에서 재방문되지 않으므로
//        단순 MERGE로 다운그레이드) — PLACED_WITH, FOR_PRODUCT, PRODUCES, PERFORMED_AT, SCRAPPED_DUE_TO
:auto LOAD CSV WITH HEADERS FROM ('file:///tx_' + $month + '/rels_placed_with.csv') AS row
CALL { WITH row
  MATCH (po:PurchaseOrder {purchaseOrderId: toInteger(row.purchaseOrderId)})
  MATCH (s:Supplier {supplierId: toInteger(row.supplierId)})
  MERGE (po)-[:PLACED_WITH]->(s)
} IN TRANSACTIONS OF 1000 ROWS;

:auto LOAD CSV WITH HEADERS FROM ('file:///tx_' + $month + '/rels_for_product.csv') AS row
CALL { WITH row
  MATCH (pol:PurchaseOrderLine {purchaseOrderLineId: toInteger(row.purchaseOrderLineId)})
  MATCH (p:Product {productId: toInteger(row.productId)})
  MERGE (pol)-[:FOR_PRODUCT]->(p)
} IN TRANSACTIONS OF 1000 ROWS;

:auto LOAD CSV WITH HEADERS FROM ('file:///tx_' + $month + '/rels_produces.csv') AS row
CALL { WITH row
  MATCH (w:WorkOrder {workOrderId: toInteger(row.workOrderId)})
  MATCH (p:Product {productId: toInteger(row.productId)})
  MERGE (w)-[:PRODUCES]->(p)
} IN TRANSACTIONS OF 1000 ROWS;

:auto LOAD CSV WITH HEADERS FROM ('file:///tx_' + $month + '/rels_performed_at.csv') AS row
CALL { WITH row
  MATCH (ro:RoutingOperation {routingOperationKey: row.routingOperationKey})
  MATCH (l:Location {locationId: toInteger(row.locationId)})
  MERGE (ro)-[:PERFORMED_AT]->(l)
} IN TRANSACTIONS OF 1000 ROWS;

// 팀 결정 1·2: 폐기 사유 NULL은 export 단계에서 이미 dropna로 제외됨(현재 데이터엔 누락 없음 확인).
// 조회 시점에는 OPTIONAL MATCH로 "해당없음"까지 함께 보여준다.
:auto LOAD CSV WITH HEADERS FROM ('file:///tx_' + $month + '/rels_scrapped_due_to.csv') AS row
CALL { WITH row
  MATCH (w:WorkOrder {workOrderId: toInteger(row.workOrderId)})
  MATCH (r:ScrapReason {scrapReasonId: toInteger(row.scrapReasonId)})
  MERGE (w)-[:SCRAPPED_DUE_TO]->(r)
} IN TRANSACTIONS OF 1000 ROWS;

// ---------------------------------------------------------------------------
// 4. 무결성 검증 (적재 후 실행)
// docker-compose.yml의 neo4j 서비스에는 APOC 플러그인이 설치돼 있지 않으므로(NEO4J_PLUGINS
// 미설정) apoc.cypher.run 대신 라벨별 UNION ALL로 직접 센다. 제약조건(1번) 11개 라벨과 순서 동일.
// ---------------------------------------------------------------------------
MATCH (n:Product) RETURN 'Product' AS label, count(n) AS count
UNION ALL
MATCH (n:Supplier) RETURN 'Supplier' AS label, count(n) AS count
UNION ALL
MATCH (n:ProductCategory) RETURN 'ProductCategory' AS label, count(n) AS count
UNION ALL
MATCH (n:ProductSubcategory) RETURN 'ProductSubcategory' AS label, count(n) AS count
UNION ALL
MATCH (n:Location) RETURN 'Location' AS label, count(n) AS count
UNION ALL
MATCH (n:ScrapReason) RETURN 'ScrapReason' AS label, count(n) AS count
UNION ALL
MATCH (n:PurchaseOrder) RETURN 'PurchaseOrder' AS label, count(n) AS count
UNION ALL
MATCH (n:PurchaseOrderLine) RETURN 'PurchaseOrderLine' AS label, count(n) AS count
UNION ALL
MATCH (n:SalesOrder) RETURN 'SalesOrder' AS label, count(n) AS count
UNION ALL
MATCH (n:WorkOrder) RETURN 'WorkOrder' AS label, count(n) AS count
UNION ALL
MATCH (n:RoutingOperation) RETURN 'RoutingOperation' AS label, count(n) AS count
ORDER BY label;

MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count ORDER BY type;
