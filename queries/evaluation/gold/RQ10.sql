-- RQ10 sql_top_supplier_rejections: 반려 수량이 많은 공급업체 상위 5곳을 알려줘.
WITH supplier_rejections AS (
  SELECT v.businessentityid,
         v.name,
         SUM(d.rejectedqty) AS total_rejected_qty
  FROM purchasing.vendor AS v
  JOIN purchasing.purchaseorderheader AS h
    ON h.vendorid = v.businessentityid
  JOIN purchasing.purchaseorderdetail AS d
    ON d.purchaseorderid = h.purchaseorderid
  GROUP BY v.businessentityid, v.name
  HAVING SUM(d.rejectedqty) > 0
)
SELECT businessentityid AS "supplierId",
       name AS "supplierName",
       total_rejected_qty AS "totalRejectedQty"
FROM supplier_rejections
ORDER BY total_rejected_qty DESC, businessentityid ASC
LIMIT 5
