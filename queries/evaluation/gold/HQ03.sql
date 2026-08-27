-- HQ03 sql_top_active_suppliers: 활성 공급업체 중 가장 다양한 부품을 공급하는 곳 5곳과 부품 종류 수를 알려줘.
SELECT v.businessentityid AS "supplierId",
       v.name AS "supplierName",
       COUNT(DISTINCT pv.productid) AS "suppliedProductCount"
FROM purchasing.vendor AS v
JOIN purchasing.productvendor AS pv
  ON pv.businessentityid = v.businessentityid
WHERE v.activeflag = true
GROUP BY v.businessentityid, v.name
ORDER BY "suppliedProductCount" DESC, v.businessentityid ASC
LIMIT 5
