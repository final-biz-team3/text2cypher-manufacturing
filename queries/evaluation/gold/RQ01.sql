-- RQ01 sql_product_cost: Touring-1000 Yellow, 54의 정가와 표준원가를 알려줘.
SELECT p.productid AS "productId",
       p.name AS "productName",
       p.listprice AS "listPrice",
       p.standardcost AS "standardCost"
FROM production.product AS p
WHERE p.name = %(productName)s
ORDER BY p.productid ASC
