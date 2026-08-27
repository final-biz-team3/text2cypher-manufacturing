// HQ10 graph_location_products: Frame Forming 작업장을 거친 제품을 조회한다.
MATCH (location:Location {name: $locationName})
      <-[:PERFORMED_AT]-(:RoutingOperation)
      <-[:HAS_OPERATION]-(workOrder:WorkOrder)
      -[:PRODUCES]->(product:Product)
RETURN DISTINCT location.locationId AS locationId,
       location.name AS locationName,
       product.productId AS productId,
       product.name AS productName
ORDER BY productId ASC
