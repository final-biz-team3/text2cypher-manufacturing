-- HQ02 sql_top_inventory_shortages: 안전재고보다 실제 재고가 부족한 제품을 부족 수량이 큰 순서로 5개 보여줘.
WITH product_stock AS (
  SELECT p.productid,
         p.name,
         p.safetystocklevel,
         COALESCE(SUM(i.quantity), 0) AS actual_stock
  FROM production.product AS p
  LEFT JOIN production.productinventory AS i ON i.productid = p.productid
  GROUP BY p.productid, p.name, p.safetystocklevel
)
SELECT productid AS "productId",
       name AS "productName",
       safetystocklevel AS "safetyStockLevel",
       actual_stock AS "actualStock",
       safetystocklevel - actual_stock AS "shortageQty"
FROM product_stock
WHERE actual_stock < safetystocklevel
ORDER BY "shortageQty" DESC, productid ASC
LIMIT 5
