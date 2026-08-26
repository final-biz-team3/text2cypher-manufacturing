-- RQ03 sql_active_supplier_count: 현재 활성 상태인 공급업체 수를 알려줘.
SELECT COUNT(*) AS "activeSupplierCount"
FROM purchasing.vendor
WHERE activeflag = true
