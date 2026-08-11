// ============================================================================
// 시연용: 한 달치 트랜잭션 데이터 삭제 스크립트
//
// 목적: 발표 시연 시나리오 재현
//   1) 이 스크립트로 이미 적재된 데이터 중 한 달치 트랜잭션을 지운다
//   2) UI에서 질의 -> 그 달 데이터가 없어서 결과가 비거나 부족함을 보여준다
//   3) python etl/export_to_csv.py tx --month <YYYY-MM> 실행 -> load.cypher "3. 트랜잭션 적재" 블록 실행
//   4) 같은 질의를 다시 실행 -> 데이터가 채워진 것을 보여준다("업데이트 가능한 구조")
//
// 대상: WorkOrder/RoutingOperation, PurchaseOrder/PurchaseOrderLine, SalesOrder
//       (해당 월에 속하는 트랜잭션 노드만 DETACH DELETE. Product/Supplier 등
//        마스터 노드와 마스터 관계는 건드리지 않는다)
//
// 실행 (load.cypher와 동일하게 ./etl 이 컨테이너의 /etl:ro 에 마운트돼 있어야 함):
//   set -a; source .env; set +a
//   docker compose exec neo4j cypher-shell -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" \
//     -P '{month: "2014-05"}' -f /etl/demo_reset_month.cypher
//
// null 삭제 관련: OPTIONAL MATCH로 얻은 변수가 없는 경우(NULL) DETACH DELETE를
// 곧바로 호출하지 않고 FOREACH + CASE 패턴으로 걸러서 삭제한다.
// https://neo4j.com/docs/cypher-manual/current/clauses/foreach/
// ============================================================================

// ---------------------------------------------------------------------------
// 0. 삭제 대상 미리보기 (실제 삭제 전 확인용)
// ---------------------------------------------------------------------------
MATCH (w:WorkOrder) WHERE toString(w.startDate) STARTS WITH $month
RETURN 'WorkOrder' AS label, count(w) AS willDelete
UNION
MATCH (po:PurchaseOrder) WHERE toString(po.orderDate) STARTS WITH $month
RETURN 'PurchaseOrder' AS label, count(po) AS willDelete
UNION
MATCH (so:SalesOrder) WHERE toString(so.orderDate) STARTS WITH $month
RETURN 'SalesOrder' AS label, count(so) AS willDelete;

// ---------------------------------------------------------------------------
// 1. WorkOrder + RoutingOperation 삭제
// ---------------------------------------------------------------------------
:auto MATCH (w:WorkOrder)
WHERE toString(w.startDate) STARTS WITH $month
CALL { WITH w
  OPTIONAL MATCH (w)-[:HAS_OPERATION]->(ro:RoutingOperation)
  FOREACH (x IN CASE WHEN ro IS NOT NULL THEN [ro] ELSE [] END | DETACH DELETE x)
  DETACH DELETE w
} IN TRANSACTIONS OF 500 ROWS;

// ---------------------------------------------------------------------------
// 2. PurchaseOrder + PurchaseOrderLine 삭제
// ---------------------------------------------------------------------------
:auto MATCH (po:PurchaseOrder)
WHERE toString(po.orderDate) STARTS WITH $month
CALL { WITH po
  OPTIONAL MATCH (po)-[:HAS_LINE]->(pol:PurchaseOrderLine)
  FOREACH (x IN CASE WHEN pol IS NOT NULL THEN [pol] ELSE [] END | DETACH DELETE x)
  DETACH DELETE po
} IN TRANSACTIONS OF 500 ROWS;

// ---------------------------------------------------------------------------
// 3. SalesOrder 삭제 (CONTAINS_PRODUCT는 관계라 DETACH DELETE 시 함께 제거됨,
//    Product 노드는 다른 SalesOrder/WorkOrder에서도 참조되므로 건드리지 않음)
// ---------------------------------------------------------------------------
:auto MATCH (so:SalesOrder)
WHERE toString(so.orderDate) STARTS WITH $month
CALL { WITH so
  DETACH DELETE so
} IN TRANSACTIONS OF 500 ROWS;

// ---------------------------------------------------------------------------
// 4. 삭제 후 확인 (0번과 비교해서 0건씩 나오는지 확인)
// ---------------------------------------------------------------------------
MATCH (w:WorkOrder) WHERE toString(w.startDate) STARTS WITH $month
RETURN 'WorkOrder' AS label, count(w) AS remaining
UNION
MATCH (po:PurchaseOrder) WHERE toString(po.orderDate) STARTS WITH $month
RETURN 'PurchaseOrder' AS label, count(po) AS remaining
UNION
MATCH (so:SalesOrder) WHERE toString(so.orderDate) STARTS WITH $month
RETURN 'SalesOrder' AS label, count(so) AS remaining;

// 마스터 데이터가 보존됐는지 함께 확인(예: Product 노드 수가 그대로인지)
MATCH (p:Product) RETURN count(p) AS productCountShouldBeUnchanged;
