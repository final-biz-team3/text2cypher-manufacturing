// RQ18 graph_impact: 활성 공급업체 Allenson Cycles의 공급 부품과 영향 완제품 경로를 조회한다.
MATCH (supplier:Supplier {name: $supplierName})-[:SUPPLIES]->(component:Product)
MATCH path = (finished:Product)-[:REQUIRES_COMPONENT*1..4]->(component)
WHERE supplier.active = true
  AND finished.sellableFinishedGood = true
  AND all(rel IN relationships(path)
          WHERE rel.startDate <= date($bomAsOfDate)
            AND (rel.endDate IS NULL OR date($bomAsOfDate) < rel.endDate))
  AND all(node IN nodes(path)
          WHERE single(other IN nodes(path)
                       WHERE other.productId = node.productId))
RETURN supplier.supplierId AS supplierId,
       supplier.name AS supplierName,
       component.productId AS componentId,
       component.name AS componentName,
       finished.productId AS finishedProductId,
       finished.name AS finishedProductName,
       length(path) AS depth,
       [node IN reverse(nodes(path)) | node.productId] AS pathProductIds
ORDER BY componentId ASC, depth ASC, finishedProductId ASC, pathProductIds ASC
