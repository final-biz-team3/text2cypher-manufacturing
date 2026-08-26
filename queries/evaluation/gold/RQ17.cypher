// RQ17 graph_common_components: Road-650 Black, 58과 Mountain-100 Black, 38이 공통으로 사용하는 하위 부품을 최대 4단계까지 알려줘.
MATCH (finishedA:Product {name: $finishedProductNameA}),
      (finishedB:Product {name: $finishedProductNameB})
MATCH pathA = (finishedA)-[:REQUIRES_COMPONENT*1..4]->(component:Product)
WHERE all(rel IN relationships(pathA)
          WHERE rel.startDate <= date($bomAsOfDate)
            AND (rel.endDate IS NULL OR date($bomAsOfDate) < rel.endDate))
  AND all(node IN nodes(pathA)
          WHERE single(other IN nodes(pathA)
                       WHERE other.productId = node.productId))
WITH finishedA, finishedB, component, min(length(pathA)) AS minDepthA
MATCH pathB = (finishedB)-[:REQUIRES_COMPONENT*1..4]->(component)
WHERE all(rel IN relationships(pathB)
          WHERE rel.startDate <= date($bomAsOfDate)
            AND (rel.endDate IS NULL OR date($bomAsOfDate) < rel.endDate))
  AND all(node IN nodes(pathB)
          WHERE single(other IN nodes(pathB)
                       WHERE other.productId = node.productId))
RETURN finishedA.productId AS finishedProductIdA,
       finishedB.productId AS finishedProductIdB,
       component.productId AS componentId,
       component.name AS componentName,
       minDepthA AS minDepthA,
       min(length(pathB)) AS minDepthB
ORDER BY componentId ASC
