-- RQ08 sql_stock_shortage: 제품 Paint - Black의 안전재고, 실제 재고와 부족 수량을 알려줘.
SELECT p.productid AS "productId",
       p.name AS "productName",
       p.safetystocklevel AS "safetyStockLevel",
       COALESCE(SUM(i.quantity), 0) AS "actualStock",
       GREATEST(p.safetystocklevel - COALESCE(SUM(i.quantity), 0), 0)
         AS "shortageQty"
FROM production.product AS p
LEFT JOIN production.productinventory AS i ON i.productid = p.productid
WHERE p.name = %(productName)s
GROUP BY p.productid, p.name, p.safetystocklevel
ORDER BY p.productid ASC
