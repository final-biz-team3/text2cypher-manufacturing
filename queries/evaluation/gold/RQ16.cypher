// RQ16 graph_all_bom_paths: 완제품 HL Road Frame - Black, 58과 부품 Metal Sheet 5가 어떤 BOM 경로로 연결되는지 알려줘.
MATCH (finished:Product {name: $finishedProductName}),
      (component:Product {name: $componentName})
MATCH path = (finished)-[:REQUIRES_COMPONENT*1..4]->(component)
WHERE all(rel IN relationships(path)
          WHERE rel.startDate <= date($bomAsOfDate)
            AND (rel.endDate IS NULL OR date($bomAsOfDate) < rel.endDate))
  AND all(node IN nodes(path)
          WHERE single(other IN nodes(path)
                       WHERE other.productId = node.productId))
RETURN finished.productId AS finishedProductId,
       finished.name AS finishedProductName,
       component.productId AS componentId,
       component.name AS componentName,
       length(path) AS depth,
       [node IN nodes(path) | node.productId] AS pathProductIds,
       [node IN nodes(path) | node.name] AS pathProductNames
ORDER BY depth ASC, pathProductIds ASC
