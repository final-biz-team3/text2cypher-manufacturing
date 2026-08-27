-- HQ09 sql_leaf_shortages: 앞 단계의 최하위 componentId 중 안전재고보다 실제 재고가 부족한 부품과 부족 수량을 조회한다.
WITH component_stock AS (
  SELECT p.productid,
         p.name,
         p.safetystocklevel,
         COALESCE(SUM(i.quantity), 0) AS actual_stock
  FROM production.product AS p
  LEFT JOIN production.productinventory AS i ON i.productid = p.productid
  WHERE p.productid = ANY(%(componentIds)s)
  GROUP BY p.productid, p.name, p.safetystocklevel
)
SELECT productid AS "componentId",
       name AS "componentName",
       safetystocklevel AS "safetyStockLevel",
       actual_stock AS "actualStock",
       safetystocklevel - actual_stock AS "shortageQty"
FROM component_stock
WHERE actual_stock < safetystocklevel
ORDER BY "shortageQty" DESC, productid ASC
