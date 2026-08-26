-- RQ05 sql_products_without_sell_end: 판매 종료일이 등록되지 않은 제품을 10개만 보여줘.
SELECT productid AS "productId",
       name AS "productName",
       sellenddate AS "sellEndDate"
FROM production.product
WHERE sellenddate IS NULL
ORDER BY productid ASC
LIMIT 10
