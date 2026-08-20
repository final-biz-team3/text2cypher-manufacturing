"""구조화 MVP 6노드·6관계의 추출(SQL)·적재(Cypher) 스펙을 한 곳에 모은다.

schema/graph_schema.yaml, docs/design/2-structured_mvp_source_mapping.md,
docs/design/3-structured_mvp_loading_rules.md에서 정의한 매핑을 그대로 코드로 옮긴 것이다.
extract/load 모듈은 이 스펙만 순회하므로, 새 노드/관계가 생기면 여기 한 줄만
추가하면 된다.

REQUIRES_COMPONENT의 `WHERE productassemblyid IS NOT NULL`은 실행 검증(
2026-08-20, 로컬 파이프라인 첫 실행) 중 발견한 예외 처리다. billofmaterials
2,679행 중 103행이 productassemblyid가 NULL인데, 확인해보니 전부 완제품
(finishedgoodsflag=true) 자신을 componentid로 가리키는 BOM 루트 앵커
행이었다 - 실제 "조립품이 부품을 필요로 한다"는 관계가 아니라 시작점 자체가
없는 행이라 애초에 그래프 관계로 표현이 안 된다. 제외해도 정보 손실은
없다(해당 완제품 노드 자체는 이미 Product로 적재됨).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NodeSpec:
    label: str
    unique_key: str
    extract_sql: str
    merge_cypher: str  # "UNWIND $rows AS row" 뒤에 붙는 본문
    datetime_columns: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RelationshipSpec:
    rel_type: str
    extract_sql: str
    merge_cypher: str  # "UNWIND $rows AS row" 뒤에 붙는 본문
    date_columns: tuple[str, ...] = field(default_factory=tuple)


NODE_SPECS: list[NodeSpec] = [
    NodeSpec(
        label="Product",
        unique_key="productId",
        extract_sql="""
            SELECT productid AS "productId", name,
                   finishedgoodsflag AS "sellableFinishedGood",
                   modifieddate AS "sourceModifiedAt"
            FROM production.product
        """,
        merge_cypher="""
MERGE (n:Product {productId: row.productId})
SET n.name = row.name,
    n.sellableFinishedGood = row.sellableFinishedGood,
    n.sourceModifiedAt = localdatetime(row.sourceModifiedAt),
    n.syncRunId = $syncRunId
""",
        datetime_columns=("sourceModifiedAt",),
    ),
    NodeSpec(
        label="Supplier",
        unique_key="supplierId",
        extract_sql="""
            SELECT businessentityid AS "supplierId", name,
                   activeflag AS "active",
                   modifieddate AS "sourceModifiedAt"
            FROM purchasing.vendor
        """,
        merge_cypher="""
MERGE (n:Supplier {supplierId: row.supplierId})
SET n.name = row.name,
    n.active = row.active,
    n.sourceModifiedAt = localdatetime(row.sourceModifiedAt),
    n.syncRunId = $syncRunId
""",
        datetime_columns=("sourceModifiedAt",),
    ),
    NodeSpec(
        label="WorkOrder",
        unique_key="workOrderId",
        extract_sql="""
            SELECT workorderid AS "workOrderId",
                   modifieddate AS "sourceModifiedAt"
            FROM production.workorder
        """,
        merge_cypher="""
MERGE (n:WorkOrder {workOrderId: row.workOrderId})
SET n.sourceModifiedAt = localdatetime(row.sourceModifiedAt),
    n.syncRunId = $syncRunId
""",
        datetime_columns=("sourceModifiedAt",),
    ),
    NodeSpec(
        label="RoutingOperation",
        unique_key="routingOperationKey",
        extract_sql="""
            SELECT workorderid || '-' || productid || '-' || operationsequence
                       AS "routingOperationKey",
                   operationsequence AS "sequence",
                   modifieddate AS "sourceModifiedAt"
            FROM production.workorderrouting
        """,
        merge_cypher="""
MERGE (n:RoutingOperation {routingOperationKey: row.routingOperationKey})
SET n.sequence = row.sequence,
    n.sourceModifiedAt = localdatetime(row.sourceModifiedAt),
    n.syncRunId = $syncRunId
""",
        datetime_columns=("sourceModifiedAt",),
    ),
    NodeSpec(
        label="Location",
        unique_key="locationId",
        extract_sql="""
            SELECT locationid AS "locationId", name,
                   modifieddate AS "sourceModifiedAt"
            FROM production.location
        """,
        merge_cypher="""
MERGE (n:Location {locationId: row.locationId})
SET n.name = row.name,
    n.sourceModifiedAt = localdatetime(row.sourceModifiedAt),
    n.syncRunId = $syncRunId
""",
        datetime_columns=("sourceModifiedAt",),
    ),
    NodeSpec(
        label="ScrapReason",
        unique_key="scrapReasonId",
        extract_sql="""
            SELECT scrapreasonid AS "scrapReasonId", name,
                   modifieddate AS "sourceModifiedAt"
            FROM production.scrapreason
        """,
        merge_cypher="""
MERGE (n:ScrapReason {scrapReasonId: row.scrapReasonId})
SET n.name = row.name,
    n.sourceModifiedAt = localdatetime(row.sourceModifiedAt),
    n.syncRunId = $syncRunId
""",
        datetime_columns=("sourceModifiedAt",),
    ),
]

RELATIONSHIP_SPECS: list[RelationshipSpec] = [
    RelationshipSpec(
        rel_type="SUPPLIES",
        extract_sql="""
            SELECT pv.businessentityid AS "supplierId",
                   pv.productid AS "productId",
                   pv.businessentityid || '-' || pv.productid AS "supplyKey"
            FROM purchasing.productvendor pv
            JOIN purchasing.vendor v ON v.businessentityid = pv.businessentityid
            WHERE v.activeflag = true
        """,
        merge_cypher="""
MATCH (s:Supplier {supplierId: row.supplierId})
MATCH (p:Product {productId: row.productId})
MERGE (s)-[r:SUPPLIES {supplyKey: row.supplyKey}]->(p)
SET r.syncRunId = $syncRunId
""",
    ),
    RelationshipSpec(
        rel_type="REQUIRES_COMPONENT",
        extract_sql="""
            SELECT billofmaterialsid AS "bomId",
                   productassemblyid AS "assemblyProductId",
                   componentid AS "componentProductId",
                   perassemblyqty AS "quantityPerAssembly",
                   startdate::date AS "startDate",
                   enddate::date AS "endDate"
            FROM production.billofmaterials
            WHERE productassemblyid IS NOT NULL
        """,
        merge_cypher="""
MATCH (assembly:Product {productId: row.assemblyProductId})
MATCH (component:Product {productId: row.componentProductId})
MERGE (assembly)-[r:REQUIRES_COMPONENT {bomId: row.bomId}]->(component)
SET r.quantityPerAssembly = row.quantityPerAssembly,
    r.startDate = date(row.startDate),
    r.endDate = CASE WHEN row.endDate IS NULL THEN NULL ELSE date(row.endDate) END,
    r.syncRunId = $syncRunId
""",
        date_columns=("startDate", "endDate"),
    ),
    RelationshipSpec(
        rel_type="PRODUCES",
        extract_sql="""
            SELECT workorderid AS "workOrderId", productid AS "productId"
            FROM production.workorder
        """,
        merge_cypher="""
MATCH (wo:WorkOrder {workOrderId: row.workOrderId})
MATCH (p:Product {productId: row.productId})
MERGE (wo)-[r:PRODUCES]->(p)
SET r.syncRunId = $syncRunId
""",
    ),
    RelationshipSpec(
        rel_type="HAS_OPERATION",
        extract_sql="""
            SELECT workorderid AS "workOrderId",
                   workorderid || '-' || productid || '-' || operationsequence
                       AS "routingOperationKey"
            FROM production.workorderrouting
        """,
        merge_cypher="""
MATCH (wo:WorkOrder {workOrderId: row.workOrderId})
MATCH (ro:RoutingOperation {routingOperationKey: row.routingOperationKey})
MERGE (wo)-[r:HAS_OPERATION]->(ro)
SET r.syncRunId = $syncRunId
""",
    ),
    RelationshipSpec(
        rel_type="PERFORMED_AT",
        extract_sql="""
            SELECT workorderid || '-' || productid || '-' || operationsequence
                       AS "routingOperationKey",
                   locationid AS "locationId"
            FROM production.workorderrouting
        """,
        merge_cypher="""
MATCH (ro:RoutingOperation {routingOperationKey: row.routingOperationKey})
MATCH (loc:Location {locationId: row.locationId})
MERGE (ro)-[r:PERFORMED_AT]->(loc)
SET r.syncRunId = $syncRunId
""",
    ),
    RelationshipSpec(
        rel_type="SCRAPPED_DUE_TO",
        extract_sql="""
            SELECT workorderid AS "workOrderId",
                   scrapreasonid AS "scrapReasonId"
            FROM production.workorder
            WHERE scrappedqty > 0 AND scrapreasonid IS NOT NULL
        """,
        merge_cypher="""
MATCH (wo:WorkOrder {workOrderId: row.workOrderId})
MATCH (sr:ScrapReason {scrapReasonId: row.scrapReasonId})
MERGE (wo)-[r:SCRAPPED_DUE_TO]->(sr)
SET r.syncRunId = $syncRunId
""",
    ),
]
