// RQ12 graph_component_usage: 부품 Paint - Black을 사용하는 완제품을 최대 4단계까지 알려줘.
MATCH (component:Product {name: $componentName})
MATCH path = (finished:Product)-[:REQUIRES_COMPONENT*1..4]->(component)
WHERE finished.sellableFinishedGood = true
  AND all(rel IN relationships(path)
          WHERE rel.startDate <= date($bomAsOfDate)
            AND (rel.endDate IS NULL OR date($bomAsOfDate) < rel.endDate))
  AND all(node IN nodes(path)
          WHERE single(other IN nodes(path)
                       WHERE other.productId = node.productId))
RETURN component.productId AS componentId,
       component.name AS componentName,
       finished.productId AS finishedProductId,
       finished.name AS finishedProductName,
       length(path) AS depth,
       [node IN reverse(nodes(path)) | node.productId] AS pathProductIds,
       [node IN reverse(nodes(path)) | node.name] AS pathProductNames
ORDER BY depth ASC, finishedProductId ASC, pathProductIds ASC
