// HQ07 graph_supplier_pairs: 같은 부품을 함께 공급하는 활성 공급업체 쌍을 공통 부품 종류가 많은 순서로 5쌍 알려줘.
MATCH (supplierA:Supplier)-[:SUPPLIES]->(component:Product)
      <-[:SUPPLIES]-(supplierB:Supplier)
WHERE supplierA.active = true
  AND supplierB.active = true
  AND supplierA.supplierId < supplierB.supplierId
WITH supplierA, supplierB,
     count(DISTINCT component.productId) AS sharedComponentCount
RETURN supplierA.supplierId AS supplierIdA,
       supplierA.name AS supplierNameA,
       supplierB.supplierId AS supplierIdB,
       supplierB.name AS supplierNameB,
       sharedComponentCount AS sharedComponentCount
ORDER BY sharedComponentCount DESC, supplierIdA ASC, supplierIdB ASC
LIMIT 5
