-- HQ04 sql_category_average_price: 제품 분류별 평균 정가와 제품 수를 평균 정가가 높은 순서로 보여줘.
SELECT c.productcategoryid AS "categoryId",
       c.name AS "categoryName",
       COUNT(p.productid) AS "productCount",
       AVG(p.listprice) AS "averageListPrice"
FROM production.productcategory AS c
JOIN production.productsubcategory AS s
  ON s.productcategoryid = c.productcategoryid
JOIN production.product AS p
  ON p.productsubcategoryid = s.productsubcategoryid
GROUP BY c.productcategoryid, c.name
ORDER BY "averageListPrice" DESC, c.productcategoryid ASC
