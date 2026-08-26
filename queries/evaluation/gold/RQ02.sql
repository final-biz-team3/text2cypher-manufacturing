-- RQ02 sql_inventory_locations: HL Mountain Frame - Black, 38의 재고 위치와 위치별 수량을 알려줘.
SELECT p.productid AS "productId",
       p.name AS "productName",
       l.locationid AS "locationId",
       l.name AS "locationName",
       i.shelf AS "shelf",
       i.bin AS "bin",
       i.quantity AS "quantity"
FROM production.product AS p
JOIN production.productinventory AS i ON i.productid = p.productid
JOIN production.location AS l ON l.locationid = i.locationid
WHERE p.name = %(productName)s
ORDER BY l.locationid ASC, i.shelf ASC, i.bin ASC
