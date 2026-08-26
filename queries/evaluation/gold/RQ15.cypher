// RQ15 graph_work_order_operations: 작업지시 17747이 거친 작업장과 공정 순서를 알려줘.
MATCH (workOrder:WorkOrder {workOrderId: $workOrderId})
      -[:HAS_OPERATION]->(operation:RoutingOperation)
      -[:PERFORMED_AT]->(location:Location)
RETURN workOrder.workOrderId AS workOrderId,
       operation.routingOperationKey AS routingOperationKey,
       operation.sequence AS sequence,
       location.locationId AS locationId,
       location.name AS locationName
ORDER BY sequence ASC, routingOperationKey ASC
