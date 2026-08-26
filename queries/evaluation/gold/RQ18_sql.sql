-- RQ18 sql_stock: 앞 단계에서 확인한 componentId별 현재 재고를 조회한다.
SELECT p.productid AS "componentId",
       COALESCE(SUM(i.quantity), 0) AS "actualStock"
FROM production.product AS p
LEFT JOIN production.productinventory AS i ON i.productid = p.productid
WHERE p.productid = ANY(%(componentIds)s)
GROUP BY p.productid
ORDER BY p.productid ASC
