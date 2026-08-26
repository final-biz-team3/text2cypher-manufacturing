// RQ19 graph_bom_supply: 완제품 HL Road Frame - Black, 58의 유효 BOM 경로별 필요 수량 계수와 활성 공급업체를 조회한다.
MATCH (finished:Product {name: $finishedProductName})
MATCH path = (finished)-[:REQUIRES_COMPONENT*1..4]->(component:Product)
WHERE all(rel IN relationships(path)
          WHERE rel.startDate <= date($bomAsOfDate)
            AND (rel.endDate IS NULL OR date($bomAsOfDate) < rel.endDate))
  AND all(node IN nodes(path)
          WHERE single(other IN nodes(path)
                       WHERE other.productId = node.productId))
OPTIONAL MATCH (supplier:Supplier)-[:SUPPLIES]->(component)
WHERE supplier.active = true
RETURN finished.productId AS finishedProductId,
       finished.name AS finishedProductName,
       component.productId AS componentId,
       component.name AS componentName,
       length(path) AS depth,
       [node IN nodes(path) | node.productId] AS pathProductIds,
       [rel IN relationships(path) | rel.quantityPerAssembly]
         AS quantityPerAssembly,
       supplier.supplierId AS supplierId,
       supplier.name AS supplierName
ORDER BY componentId ASC, depth ASC, pathProductIds ASC, supplierId ASC
