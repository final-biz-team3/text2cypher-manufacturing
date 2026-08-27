-- HQ01 sql_price_cost_gap: Mountain-100 Silver, 38과 Mountain-100 Black, 38의 정가-표준원가 차액을 비교해 큰 순서로 보여줘.
SELECT p.productid AS "productId",
       p.name AS "productName",
       p.listprice AS "listPrice",
       p.standardcost AS "standardCost",
       p.listprice - p.standardcost AS "priceCostGap"
FROM production.product AS p
WHERE p.name = ANY(%(productNames)s)
ORDER BY "priceCostGap" DESC, p.productid ASC
