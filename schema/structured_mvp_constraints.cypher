// Neo4j 5.26 Community compatible DDL
// UNIQUE constraints also create backing range indexes.
//
// 원본 neo4j_structured_mvp_design/structured_mvp_constraints.cypher는 제약조건
// 11개(업무 6 + 온톨로지 5)와 인덱스 3개(product_name/supplier_name/term_normalized_text)를
// 갖고 있었다. schema/graph_schema.yaml에서 온톨로지 5노드를 뺀 것과 맞춰
// 이 파일도 업무 6개 제약 + 인덱스 2개만 남긴다 — 온톨로지 추가 시점에
// 원본 파일 기준으로 나머지를 같이 넣는다.

CREATE CONSTRAINT product_id IF NOT EXISTS
FOR (n:Product) REQUIRE n.productId IS UNIQUE;

CREATE CONSTRAINT supplier_id IF NOT EXISTS
FOR (n:Supplier) REQUIRE n.supplierId IS UNIQUE;

CREATE CONSTRAINT work_order_id IF NOT EXISTS
FOR (n:WorkOrder) REQUIRE n.workOrderId IS UNIQUE;

CREATE CONSTRAINT routing_operation_key IF NOT EXISTS
FOR (n:RoutingOperation) REQUIRE n.routingOperationKey IS UNIQUE;

CREATE CONSTRAINT location_id IF NOT EXISTS
FOR (n:Location) REQUIRE n.locationId IS UNIQUE;

CREATE CONSTRAINT scrap_reason_id IF NOT EXISTS
FOR (n:ScrapReason) REQUIRE n.scrapReasonId IS UNIQUE;

CREATE RANGE INDEX product_name IF NOT EXISTS
FOR (n:Product) ON (n.name);

CREATE RANGE INDEX supplier_name IF NOT EXISTS
FOR (n:Supplier) ON (n.name);
