-- RQ06 sql_product_attributes: Touring-1000 Yellow, 54의 제품번호와 색상, 크기를 알려줘.
SELECT productid AS "productId",
       name AS "productName",
       productnumber AS "productNumber",
       color AS "color",
       size AS "size"
FROM production.product
WHERE name = %(productName)s
ORDER BY productid ASC
