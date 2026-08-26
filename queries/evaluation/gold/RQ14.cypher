// RQ14 graph_supplier_impact: 공급업체 Allenson Cycles가 공급하는 부품과 그 부품을 사용하는 완제품을 알려줘.
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
