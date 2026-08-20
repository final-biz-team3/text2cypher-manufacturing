# 0004. 그래프 스키마 설계 v2 (마스터/트랜잭션 분리, 관계 적재 그룹 A/B/C)

## 상태
폐기 (2026-08-20) — 구조화 MVP 전환으로 대체됨

> **2026-08-20 정정**: 이 문서가 정의한 11노드·13관계(마스터/트랜잭션 분리, 관계 그룹 A/B/C)는 Excel 원본 데이터를 전제로 한 스키마다. AdventureWorks(PostgreSQL)를 원본으로 하는 구조화 MVP로 전환하면서 `schema/graph_schema.yaml`을 업무 6노드·6관계(Product/Supplier/WorkOrder/RoutingOperation/Location/ScrapReason)로 완전히 교체했다 — 이 문서가 설명하는 스키마는 더 이상 `schema/graph_schema.yaml`과 일치하지 않는다. 현재 기준은 `schema/graph_schema.yaml`과 `docs/design/2-structured_mvp_source_mapping.md`, 전환 근거는 `docs/superpowers/plans/2026-08-19-structured-mvp-data-loading.md` 참고. 이 문서는 과거 결정 기록으로만 남긴다.

## 한 줄 요약

> 팀이 확정한 스키마 다이어그램 v2(arrows.app, "ITDA-neo4j2")를 그래프 스키마로 채택한다. 노드 11개·관계 13개이며 원본 DDL과 1:1 대조 검증을 마쳤다. 노드는 "마스터(1회 적재)"와 "트랜잭션(배치 적재)"으로 나누고, 관계는 자연키 유무·다건 허용 여부에 따라 그룹 A/B/C로 분류해 재적재해도 안전하게 만든다.

---

## 배경 — 왜 이 결정이 필요했나

- 앞서 검토한 다이어그램 v1에서 확인이 필요했던 두 지점 — `Location` 노드가 작업장과 재고 위치를 겸하는 게 설계 오류인지, `RoutingOperation`에 왜 합성키를 쓰는지 — 를 원본 DDL(`자전거 공정 데이터.sql`)로 재확인해 v2로 확정했다. 확인 결과 둘 다 원본 구조를 정확히 반영한 것이었다.
- 멘토 조언(월별 일괄적재 방식으로 진행하고, 발표 시연 시 한 달치를 지웠다가 재적재해 "업데이트 가능한 구조"임을 보여주는 방향)에 따라, 스키마 설계 단계에서부터 "어떤 노드/관계가 시간(월)에 묶이는가"를 미리 구분해 둘 필요가 있었다.
- 월별로 반복 적재해도 중복이 생기지 않아야 하므로(같은 달을 실수로 두 번 실행해도 안전), 관계마다 "자연키가 있는가", "한 노드가 그 타입의 관계를 여러 개 가질 수 있는가"를 스키마 문서 차원에서 먼저 분류해, 이후 적재 스크립트(`docs/adr/0005`)가 그대로 참조할 수 있는 근거를 남겨야 했다.
- 팀 회의에서 확정된 비즈니스 규칙 4가지(폐기 사유 표시, 폐기 사유 누락 처리, BOM 유효기간 필터, 비활성 공급업체 제외)를 "적재 시점"과 "조회 시점" 중 어디서 처리할지를 스키마 설계 단계에서 결정해야 했다.

## 결정 — 무엇을 어떻게 하기로 했나

### 1. 매핑 원칙

Neo4j 공식 가이드([Modeling: relational to graph](https://neo4j.com/docs/getting-started/data-modeling/relational-to-graph-modeling/), [Import from a relational database into Neo4j](https://neo4j.com/docs/getting-started/data-import/relational-to-graph-import/))를 기준으로 삼았다.

- 마스터/차원 테이블 → 노드
- FK만 있는 순수 조인 테이블 → 관계
- 속성이 있는 조인 테이블(BOM 수량, 구매/판매 상세의 수량·단가 등) → 속성을 가진 관계
- 자체 PK가 없는 상세/라우팅 테이블(구매주문_상세, 공정순서_라우팅)은 팀 결정에 따라 별도 노드로 분리한다. 관계 속성으로 눌러버리면 라인아이템 단위 조회·집계가 안 되는데, 질의셋 요구사항에 그런 질의가 있어서다.

### 2. 마스터 / 트랜잭션 분리

11개 노드를 "시간에 묶이지 않는 마스터"와 "월에 묶이는 트랜잭션"으로 나눈다. 마스터는 1회 적재하고, 트랜잭션은 배치 단위로 지속 적재한다(구체적인 적재 주기·방식은 0005 참고).

| 노드 | 레이블 | 고유 키 | 원본 테이블 | 월 기준 컬럼 |
| --- | --- | --- | --- | --- |
| 마스터 | `Product` | productId | production.product | - |
| 마스터 | `Supplier` | supplierId | purchasing.vendor | - |
| 마스터 | `ProductCategory` | categoryId | production.productcategory | - |
| 마스터 | `ProductSubcategory` | subcategoryId | production.productsubcategory | - |
| 마스터 | `Location` | locationId | production.location(작업장·재고위치 겸용) | - |
| 마스터 | `ScrapReason` | scrapReasonId | production.scrapreason | - |
| 트랜잭션 | `PurchaseOrder` | purchaseOrderId | purchasing.purchaseorderheader | orderDate |
| 트랜잭션 | `PurchaseOrderLine` | purchaseOrderLineId | purchasing.purchaseorderdetail | 부모 PurchaseOrder.orderDate 동반 적재 |
| 트랜잭션 | `SalesOrder` | salesOrderId | sales.salesorderheader | orderDate |
| 트랜잭션 | `WorkOrder` | workOrderId | production.workorder | startDate |
| 트랜잭션 | `RoutingOperation` | routingOperationKey(합성) | production.workorderrouting | 부모 WorkOrder.startDate 동반 적재 |

`Location`은 원본 DDL 코멘트("생산 공정이 수행되거나 제품 재고가 보관되는 위치를 관리한다")를 근거로 작업장과 재고 위치를 한 노드로 통합했다. `RoutingOperation`은 원본 PK가 `(workorderid, productid, operationsequence)` 복합키라 자체 정수 PK가 없으므로, 세 값을 이어붙인 문자열을 합성키로 쓴다: `routingOperationKey = f"{workOrderId}-{productId}-{operationSequence}"`.

### 3. 노드 속성 요약

| 레이블 | 주요 속성 |
| --- | --- |
| `Product` | name, productNumber, makeInHouse, sellableFinishedGood, color, safetyStockLevel, reorderPoint, standardCost, listPrice, size, sizeUnit, weightUnit, weight, daysToManufacture, productLine, classCode, styleCode, sellStartDate, sellEndDate, discontinuedDate, rowGuid, modifiedAt |
| `Supplier` | accountNumber, name, creditRating, preferred, active, purchasingWebUrl, modifiedAt |
| `ProductCategory` | name, nameKo, rowGuid, modifiedAt |
| `ProductSubcategory` | name, nameKo, rowGuid, modifiedAt |
| `Location` | name, nameKo, costRate, availability, modifiedAt |
| `ScrapReason` | name, nameKo, modifiedAt |
| `PurchaseOrder` | revisionNumber, statusCode, employeeId, shipMethodId, orderDate, shipDate, subTotal, taxAmount, freight, modifiedAt |
| `PurchaseOrderLine` | dueDate, orderQty, unitPrice, receivedQty, rejectedQty, modifiedAt |
| `SalesOrder` | revisionNumber, salesOrderNumber, orderDate, dueDate, shipDate, statusCode, onlineOrder, purchaseOrderNumber, accountNumber, customerId, salesPersonId, salesTerritoryId, shipMethodId, subTotal, taxAmount, freight, totalDue, rowGuid, modifiedAt |
| `WorkOrder` | orderQty, stockedQty, scrappedQty, startDate, endDate, dueDate, modifiedAt |
| `RoutingOperation` | sequence, plannedStartDate, plannedEndDate, actualStartDate, actualEndDate, actualHours, plannedCost, actualCost, modifiedAt |

### 4. 관계 13개 — 적재 전략 그룹 A/B/C

주기적(배치) 적재를 반복 실행해도 안전하도록, 관계를 "자연키 유무"와 "한 노드가 여러 개 가질 수 있는지"로 나눠 그룹별로 다른 MERGE 방식을 쓴다. (실제 Cypher 구현은 0005 참고.)

| 그룹 | 관계 | 방향 | 자연키/속성 | 원본 | 비고 |
| --- | --- | --- | --- | --- | --- |
| A(자연키 MERGE) | `SUPPLIES` | Supplier → Product | supplyKey(합성), averageLeadTimeDays, standardPrice, lastReceiptCost, lastReceiptDate, minOrderQty, maxOrderQty, onOrderQty, unitCode, modifiedAt | 부품-공급업체 | 비활성 공급업체(active=false)는 적재 전 제외 |
| A | `REQUIRES_COMPONENT` | Product → Product(자기참조) | bomId, startDate, endDate, unitCode, bomLevel, quantityPerAssembly, modifiedAt | 자재명세서BOM | 유효기간 필터는 조회 시점에 적용 |
| A | `STOCKED_AT` | Product → Location | inventoryGuid, shelf, bin, quantity, modifiedAt | 재고 | 현재 재고 스냅샷(마스터와 함께 갱신) |
| A | `CONTAINS_PRODUCT` | SalesOrder → Product | salesOrderLineId, carrierTrackingNumber, orderQty, specialOfferId, unitPrice, unitPriceDiscount, lineTotal, rowGuid, modifiedAt | 판매주문_상세 | 트랜잭션(배치) |
| B(단순 MERGE, 다건 허용) | `HAS_LINE` | PurchaseOrder → PurchaseOrderLine | 속성 없음 | 구매주문_상세.구매주문ID | 트랜잭션(배치) |
| B | `HAS_OPERATION` | WorkOrder → RoutingOperation | 속성 없음 | 공정순서_라우팅.작업지시ID | 트랜잭션(배치) |
| C(단일 타깃, 마스터↔마스터 — 진짜 delete-then-create 필요) | `IN_SUBCATEGORY` | Product → ProductSubcategory | 속성 없음 | product.중분류ID | 값이 바뀔 수 있는 마스터 속성 |
| C | `IN_CATEGORY` | ProductSubcategory → ProductCategory | 속성 없음 | productsubcategory.대분류ID | 값이 바뀔 수 있는 마스터 속성 |
| C(단일 타깃, 트랜잭션→마스터 — 배치 적재에서는 단순 MERGE로 다운그레이드) | `PLACED_WITH` | PurchaseOrder → Supplier | 속성 없음 | 구매주문_헤더.공급업체ID | 생성 시 FK 고정, 재변경 없음 |
| C | `FOR_PRODUCT` | PurchaseOrderLine → Product | 속성 없음 | 구매주문_상세.제품ID | 생성 시 FK 고정 |
| C | `PRODUCES` | WorkOrder → Product | 속성 없음 | 생산작업지시.제품ID | 생성 시 FK 고정 |
| C | `PERFORMED_AT` | RoutingOperation → Location | 속성 없음 | 공정순서_라우팅.작업장ID | 생성 시 FK 고정 |
| C | `SCRAPPED_DUE_TO` | WorkOrder → ScrapReason | 속성 없음 | 생산작업지시.폐기사유ID | NULL이면 관계 생성 생략(팀 확인: 현재 데이터엔 누락 없음) |

### 5. 비즈니스 규칙 (팀 결정) 반영 위치

| 결정 | 반영 위치 |
| --- | --- |
| 폐기 사유 표시는 조회 시점 `OPTIONAL MATCH`로 처리 | 적재 스크립트는 단순 `MERGE`로 유지, 조회 Cypher에서 `OPTIONAL MATCH (w)-[:SCRAPPED_DUE_TO]->(r) RETURN coalesce(r.name, "해당없음")` 패턴 사용 |
| 현재 데이터엔 폐기 사유 누락 없음 | `scrappedQty > 0`인데 `scrapReasonId`가 없는 행이 없음을 확인, 적재 스크립트에 별도 예외 분기 없음 |
| BOM 유효기간은 조회 시점 필터 | `REQUIRES_COMPONENT.startDate <= date() AND (endDate IS NULL OR date() < endDate)` 필터를 조회 쿼리에 적용, 적재 시점엔 필터링하지 않음 |
| 비활성 공급업체는 적재 전 제외 | `Supplier.active = false`인 공급업체는 `SUPPLIES` 관계를 export 단계에서 제외(조회 시점 필터가 아니라 적재 시점 필터) |

### 6. 배치 적재 대응

- 마스터 6종 + 관계 5종(`SUPPLIES`, `REQUIRES_COMPONENT`, `STOCKED_AT`, `IN_SUBCATEGORY`, `IN_CATEGORY`)은 1회 적재로 분리.
- 트랜잭션 5종(`PurchaseOrder`, `PurchaseOrderLine`, `SalesOrder`, `WorkOrder`, `RoutingOperation`) + 관계 8종(`HAS_LINE`, `PLACED_WITH`, `FOR_PRODUCT`, `CONTAINS_PRODUCT`, `HAS_OPERATION`, `PERFORMED_AT`, `PRODUCES`, `SCRAPPED_DUE_TO`)은 배치 단위로 지속 적재하는 쪽으로 분리.
- 실제 적재 실행 방식(초기 백필·실시간 증분·강제 재적재 구분, 삭제 후 재적재 시연 스크립트 등)은 이 스키마 분리를 그대로 전제로 하며, 세부 내용은 0005를 참고한다.

### 7. 스키마 다이어그램 자동 생성

arrows.app은 자동 레이아웃/DB 연결/JSON 임포트 UI를 지원하지 않는다([공식 README](https://github.com/neo4j-labs/arrows.app/blob/main/README.md) Anti-features). 대신:

- **적재 전**: 이 문서의 표를 [Mermaid `erDiagram`](https://mermaid.js.org/syntax/entityRelationshipDiagram.html) 코드로 변환하는 스크립트로 다이어그램을 자동 생성한다.
- **적재 후**: `CALL db.schema.visualization()`(Neo4j 3.1+ 내장, Community 지원, APOC 불필요)으로 실제 적재된 라벨·관계 타입 기준 다이어그램을 뽑아 이 문서와 어긋나지 않는지 검증한다.

## 검토했으나 채택하지 않은 대안

**`Location`을 작업장(WorkCenter)과 재고 위치(StorageLocation) 2개 노드로 분리.** 두 역할이 개념적으로 다르니 분리하는 게 더 "정규화"된 모델처럼 보일 수 있다. 하지만 원본 `production.location` 테이블이 이미 하나의 테이블로 두 역할을 겸하고 있고(DDL 코멘트로 확인), 분리하면 원본 데이터에 없는 구분을 임의로 만들어내는 것이라 기각했다.

**`RoutingOperation`에 새 자동증가 ID를 만들어 부여.** 원본 복합키(workorderid, productid, operationsequence) 세 값을 각각 별도 속성으로 두고 새 정수 ID를 만드는 방법도 검토했다. 그러나 Neo4j의 유니크 제약(`IS UNIQUE`)은 단일 속성 기준이라, 복합키를 그대로 쓰려면 별도 처리가 필요하다. 문자열 합성키(`workOrderId-productId-sequence`) 하나로 묶는 게 제약 설정과 `MERGE` 매칭 둘 다 단순해서 이 방식을 택했다. (합성키의 리스크는 "확실하지 않은 부분" 참고.)

**관계에 문서화 목적의 메타데이터 속성(카디널리티, 조인규칙, 검증규칙 등)을 유지.** v1 검토 당시 고려했으나, v2 다이어그램에는 이런 필드가 전혀 없다. 다이어그램에 남은 속성이 곧 실제 적재 대상이라는 원칙을 세우면 적재 스크립트에 "이 속성은 걸러야 하나?" 판단 로직이 필요 없어지므로, 메타데이터는 이 ADR 같은 문서 쪽에만 남기고 다이어그램/스키마에는 넣지 않기로 했다.

**arrows.app의 자동 레이아웃/DB 연결/JSON 임포트 기능 사용.** 공식 README에 Anti-features로 명시돼 있어 애초에 불가능했다. 대신 Mermaid 스크립트(적재 전)와 `db.schema.visualization()`(적재 후)으로 대체했다.

## 결과 및 트레이드오프

- 그룹 A/B 관계는 전부 `MERGE` 기반이라 재실행에 안전하고, 그룹 C 중 진짜 delete-then-create가 필요한 건 마스터-마스터 관계 2개뿐이라 반복 부하와 무관하다.
- 반면 그룹 C의 트랜잭션 쪽 5개(`PLACED_WITH` 등)는 "생성 시점에 새 ID만 부여되고 FK가 재배정되지 않는다"는 전제에 의존한 다운그레이드다. 이 전제가 깨지는 시나리오(예: 이미 적재된 PurchaseOrder의 공급업체가 사후 정정되는 경우)가 생기면 재검토가 필요하다.
- 이 스키마 설계를 실제 코드로 구현한 것은 `etl/export_to_csv.py`·`etl/load_to_neo4j.py`이며, "왜 이렇게 구현했는가"의 배경·대안 검토는 0005에 별도로 정리돼 있다.
- `schema/graph_schema.yaml`(백엔드·프론트엔드·LLM 프롬프트가 공유하는 기준 스키마 문서, `docs/adr/0001` 근거)에 이 표의 내용을 그대로 옮기는 작업은 이 ADR의 결정 범위 밖이며, 별도로 진행됐다.

## 확실하지 않은 부분

- 월 기준 컬럼(예: `WorkOrder.startDate`)이 진행 중인 항목의 상태 변경(시작은 이번 달, 종료는 다음 달)을 반영하지 못하는 문제. 지금 다루는 정적 과거 이력 데이터에는 해당하지 않지만, 실제 운영 DB에 연결하는 시점에는 재검토가 필요하다. (자세한 내용: `docs/adr/0005` "확실하지 않은 부분")
- `RoutingOperationKey`(합성키)가 공정 순서(`operationSequence`) 재조정 시 안정성이 깨질 수 있는 문제. 마찬가지로 지금 데이터에는 해당하지 않지만, 원본 시스템에 독립적인 불변 식별자가 있다면 그걸 키로 쓰는 게 근본적인 해결책이다.

## 참고 자료

- Neo4j Docs, "Guidance for modeling relational data in a graph database" — 마스터/조인 테이블 매핑 원칙
- Neo4j Docs, "Import from a relational database into Neo4j" — 관계형→그래프 임포트 절차
- Neo4j Cypher Manual, "LOAD CSV" — `IN TRANSACTIONS OF n ROWS` 배치 임포트
- arrows.app GitHub README, Anti-features 섹션 — 자동 레이아웃/DB연결/JSON임포트 미지원 근거
- Mermaid.js, "Entity Relationship Diagrams" 문법 — 다이어그램 자동화 대체 방법
- Neo4j GitHub Issue #12417 — `db.schema.visualization()` Community Edition 동작 확인
- 자전거 공정 데이터.sql(팀 확정 DDL) — Location 통합, RoutingOperation 복합 PK 근거
