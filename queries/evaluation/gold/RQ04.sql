-- RQ04 sql_purchased_product_count: 외부에서 구매하는 부품 수를 알려줘.
SELECT COUNT(*) AS "purchasedProductCount"
FROM production.product
WHERE makeflag = false
