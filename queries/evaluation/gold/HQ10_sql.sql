-- HQ10 sql_product_scrap_totals: 앞 단계의 productId별 전체 작업지시 수와 총 폐기 수량을 조회한다.
SELECT p.productid AS "productId",
       p.name AS "productName",
       COUNT(DISTINCT w.workorderid) AS "workOrderCount",
       SUM(w.scrappedqty) AS "totalScrappedQty"
FROM production.product AS p
JOIN production.workorder AS w ON w.productid = p.productid
WHERE p.productid = ANY(%(productIds)s)
GROUP BY p.productid, p.name
ORDER BY "totalScrappedQty" DESC, p.productid ASC
LIMIT 5
