-- RQ11 sql_top_scrapped_work_orders: 폐기 수량이 많은 작업지시 상위 5개를 제품명과 폐기사유와 함께 알려줘.
WITH scrapped_work_orders AS (
  SELECT w.workorderid,
         w.productid,
         p.name AS product_name,
         w.scrappedqty,
         r.scrapreasonid,
         r.name AS scrap_reason_name
  FROM production.workorder AS w
  JOIN production.product AS p ON p.productid = w.productid
  LEFT JOIN production.scrapreason AS r ON r.scrapreasonid = w.scrapreasonid
  WHERE w.scrappedqty > 0
)
SELECT workorderid AS "workOrderId",
       productid AS "productId",
       product_name AS "productName",
       scrappedqty AS "scrappedQty",
       scrapreasonid AS "scrapReasonId",
       scrap_reason_name AS "scrapReasonName"
FROM scrapped_work_orders
ORDER BY scrappedqty DESC, workorderid ASC
LIMIT 5
