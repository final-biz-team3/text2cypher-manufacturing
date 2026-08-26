-- RQ07 sql_category_product_count: Components에 포함된 제품 수를 알려줘.
SELECT c.productcategoryid AS "categoryId",
       c.name AS "categoryName",
       COUNT(p.productid) AS "productCount"
FROM production.productcategory AS c
JOIN production.productsubcategory AS s
  ON s.productcategoryid = c.productcategoryid
JOIN production.product AS p
  ON p.productsubcategoryid = s.productsubcategoryid
WHERE c.name = %(categoryName)s
GROUP BY c.productcategoryid, c.name
ORDER BY c.productcategoryid ASC
