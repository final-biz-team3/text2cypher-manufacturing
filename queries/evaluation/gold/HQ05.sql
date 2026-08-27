-- HQ05 sql_top_work_order_locations: 서로 다른 작업지시를 가장 많이 처리한 작업장 5곳과 작업지시 수를 알려줘.
SELECT l.locationid AS "locationId",
       l.name AS "locationName",
       COUNT(DISTINCT r.workorderid) AS "workOrderCount"
FROM production.location AS l
JOIN production.workorderrouting AS r ON r.locationid = l.locationid
GROUP BY l.locationid, l.name
ORDER BY "workOrderCount" DESC, l.locationid ASC
LIMIT 5
