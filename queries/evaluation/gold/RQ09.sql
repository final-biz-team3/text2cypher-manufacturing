-- RQ09 sql_top_finished_sales: 판매량이 가장 많은 완제품 상위 5개를 알려줘.
WITH product_sales AS (
  SELECT p.productid,
         p.name,
         SUM(d.orderqty) AS total_order_qty
  FROM production.product AS p
  JOIN sales.salesorderdetail AS d ON d.productid = p.productid
  WHERE p.finishedgoodsflag = true
  GROUP BY p.productid, p.name
)
SELECT productid AS "productId",
       name AS "productName",
       total_order_qty AS "totalOrderQty"
FROM product_sales
ORDER BY total_order_qty DESC, productid ASC
LIMIT 5
