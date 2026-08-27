-- HQ08 sql_top_scrapped_products: Thermoform temperature too low 때문에 폐기된 작업지시가 많은 제품 5개와 작업지시 수를 알려줘.
SELECT r.scrapreasonid AS "scrapReasonId",
       r.name AS "scrapReasonName",
       p.productid AS "productId",
       p.name AS "productName",
       COUNT(DISTINCT w.workorderid) AS "workOrderCount"
FROM production.workorder AS w
JOIN production.scrapreason AS r ON r.scrapreasonid = w.scrapreasonid
JOIN production.product AS p ON p.productid = w.productid
WHERE r.name = %(scrapReasonName)s
GROUP BY r.scrapreasonid, r.name, p.productid, p.name
ORDER BY "workOrderCount" DESC, p.productid ASC
LIMIT 5
