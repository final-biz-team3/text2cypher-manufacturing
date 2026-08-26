// RQ13 graph_bom_hierarchy: 완제품 HL Road Frame - Black, 58의 하위 부품을 계층 구조로 알려줘.
MATCH (root:Product {name: $finishedProductName})
MATCH path = (root)-[:REQUIRES_COMPONENT*1..4]->(component:Product)
WHERE all(rel IN relationships(path)
          WHERE rel.startDate <= date($bomAsOfDate)
            AND (rel.endDate IS NULL OR date($bomAsOfDate) < rel.endDate))
  AND all(node IN nodes(path)
          WHERE single(other IN nodes(path)
                       WHERE other.productId = node.productId))
RETURN root.productId AS rootProductId,
       root.name AS rootProductName,
       component.productId AS componentId,
       component.name AS componentName,
       length(path) AS depth,
       [node IN nodes(path) | node.productId] AS pathProductIds,
       [node IN nodes(path) | node.name] AS pathProductNames
ORDER BY depth ASC, componentId ASC, pathProductIds ASC
