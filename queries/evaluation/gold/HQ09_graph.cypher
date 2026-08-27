// HQ09 graph_leaf_components: HL Road Frame - Black, 58의 유효 BOM에서 최하위 부품과 최소 깊이를 조회한다.
MATCH (root:Product {name: $finishedProductName})
MATCH path = (root)-[:REQUIRES_COMPONENT*1..4]->(component:Product)
WHERE all(rel IN relationships(path)
          WHERE rel.startDate <= date($bomAsOfDate)
            AND (rel.endDate IS NULL OR date($bomAsOfDate) < rel.endDate))
  AND all(node IN nodes(path)
          WHERE single(other IN nodes(path)
                       WHERE other.productId = node.productId))
  AND NOT EXISTS {
    MATCH (component)-[childRel:REQUIRES_COMPONENT]->(:Product)
    WHERE childRel.startDate <= date($bomAsOfDate)
      AND (childRel.endDate IS NULL OR date($bomAsOfDate) < childRel.endDate)
  }
WITH component, min(length(path)) AS minDepth
RETURN component.productId AS componentId,
       component.name AS componentName,
       minDepth AS minDepth
ORDER BY minDepth DESC, componentId ASC
