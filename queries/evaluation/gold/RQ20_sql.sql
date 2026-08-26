-- RQ20 sql_scrap_facts: 작업지시 17747의 제품, 폐기 수량과 폐기사유를 조회한다.
SELECT w.workorderid AS "workOrderId",
       w.productid AS "productId",
       p.name AS "productName",
       w.scrappedqty AS "scrappedQty",
       r.scrapreasonid AS "scrapReasonId",
       r.name AS "scrapReasonName"
FROM production.workorder AS w
JOIN production.product AS p ON p.productid = w.productid
LEFT JOIN production.scrapreason AS r ON r.scrapreasonid = w.scrapreasonid
WHERE w.workorderid = %(workOrderId)s
ORDER BY w.workorderid ASC
