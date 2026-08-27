// DDL syntax is Community-compatible (UNIQUE only, no NODE KEY/RELATIONSHIP KEY).
// UNIQUE constraints also create backing range indexes.
// Actual runtime environment is neo4j:5-enterprise (evaluation license, latest
// patch tracked, not pinned) - docker-compose.yml and the remote shared server
// both run Enterprise (decided 2026-08-20, PR #16 review). DDL syntax choice
// and runtime edition are independent: this file stays Community-compatible
// syntax on purpose, it is not evidence that Community edition is targeted.
//
// 업무 그래프 6개 제약과 이슈 #22 온톨로지 seed의 3개 제약을 함께 선언한다.
// seed와 같은 이름·키를 사용하므로 어느 경로를 먼저 실행해도 재실행 가능하다.

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

CREATE CONSTRAINT ontology_term_normalized IF NOT EXISTS
FOR (n:Term) REQUIRE n.normalizedText IS UNIQUE;

CREATE CONSTRAINT ontology_business_concept_id IF NOT EXISTS
FOR (n:BusinessConcept) REQUIRE n.conceptId IS UNIQUE;

CREATE CONSTRAINT ontology_action_concept_id IF NOT EXISTS
FOR (n:ActionConcept) REQUIRE n.conceptId IS UNIQUE;

CREATE RANGE INDEX product_name IF NOT EXISTS
FOR (n:Product) ON (n.name);

CREATE RANGE INDEX supplier_name IF NOT EXISTS
FOR (n:Supplier) ON (n.name);
